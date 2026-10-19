# tests/test_uri_env_mutations_declared.py
"""Epic #1647 Task 7 Step 5: TORTOISE_DB_URI env-mutation routing guard.

Every delenv/setenv/os.environ mutation of TORTOISE_DB_URI in a NON-allowlist
(migrated) test file must be DECLARED in DELIBERATE_URI_MUTATIONS with its
lane intent. An undeclared mutation reds: a docker-half test that silently
delenvs the URI constructs embedded and passes green — O1/T1's "≥90% on
docker" math then counts it as docker-covered while it never touches the
server (the #942 vacuity vector, cycle-8 P1-3).

SCOPE (#9061): every test module in the tree, at any depth —
`tests/**/test_*.py` (1076 modules as of 2026-10-18), and the carve-out
exemption applies to the MODULE its stem resolves to — `tests/<stem>.py`, or the
registry's subdirectory module where that is where it lives
(`test_smoke_embedded` → `tests/bench/`), never to a same-named file elsewhere.
It used to be
`tests/test_*.py` plus `tests/e2e/test_*.py`, which left
`tests/longmem_eval/`, `tests/e2e/auth/`, `tests/e2e/hosted/`, `tests/eval/…`,
`tests/bench/` and `tests/mini_beir/` outside a guard whose whole purpose is
to notice a mutation wherever it is written: 72 modules and 7 live mutation
sites were unscanned while it reported green. `test_both_guards_scan_the_
recursive_population` binds the two GUARDS to that enumeration (a pin on the
helper alone stayed green when both guards were reverted to the flat glob),
and `test_a_subdirectory_module_cannot_inherit_a_carve_out_exemption` keeps
the exemption from following a stem into a subdirectory.

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

## Restoration guard (#2084) — the #2062 pop-without-restore class

The declaration check above validates DECLARATION only. The #2062 incident
(2026-09-01): test_index_restore.py + test_index_cli.py module-scoped autouse
fixtures popped TORTOISE_DB_URI at setup AND teardown — never restoring.
The guard stayed green (the pops WERE declared) but every docker-lane test
AFTER them in the same pytest process constructed embedded SDKs on the
shared default path (15 deterministic CI failures). PR #2073 fixed the two
instances (MonkeyPatch + mp.undo) but the guard itself could not catch the
CLASS. This file's second guard additionally requires that every RAW
(non-monkeypatch) TORTOISE_DB_URI mutation at MODULE scope or inside a
FIXTURE body is paired with a restoration mechanism:

  * pytest.MonkeyPatch() … mp.undo() — auto-undo (the #2073 fixed pattern);
    monkeypatch fixture-param calls (monkeypatch.delenv/setenv) are equally
    safe — pytest auto-undoes at teardown (function-scoped monkeypatch is
    restored before the next test, and a module-scoped fixture cannot even
    take the function-scoped monkeypatch param — ScopeMismatch). NOTE: the
    bare-instance call forms (mp.delenv/mp.setenv) are intentionally NOT in
    the declaration census regex (_MUTATION_RE) — the restoration guard's
    undo requirement is their coverage (pinned in
    test_declaration_regex_census_surface).
  * save-restore: `_old_uri = os.environ.get("TORTOISE_DB_URI")` captured
    (fixture body or module top) and assigned BACK (in a finally / after
    yield) — the module-level live-probe pattern.
  * carve-out exemption: carve-out stems are embedded-only by design — no
    redirect fires for them, so their URI pops are no-ops w.r.t.
    lane-flipping. They run in the URI-less carve-out lane
    (TORTOISE_TEST_CARVE_OUT=1) and never co-run with docker-lane files in
    the same pytest process, so a leaked pop cannot poison docker-lane
    tests. **Exemption decision (#2084):** the comparison is normalized to
    TEST-MODULE STEMS (``f.name[:-3]``) so the documented
    TEST_NO_REDIRECT_STEMS carve-out actually fires — the stem list is
    the canonical carve-out registry (shared with test_markers.py /
    test_derived_names.py, which use the same exempt-set shape). This is
    deliberate and DOCUMENTED — it is NOT a blanket license for
    module-scoped pops in docker-lane (allowlisted) files, which the
    fixture/scope check above catches (a pop in test_index_restore.py /
    test_export_cli.py etc. still reds).

Static heuristic, by design (the guard is a regex-based census today): a
file with a fixture-scope raw mutation must show an in-body restore of a
captured URI var; a top-level raw mutation must show a file-level
save-restore. This is not dataflow analysis — it catches the pop-without-
restore CLASS, not every leak.

## Empty-means-unset READ census (#3094) — the #2589/#2930 trap

The third arm fails closed on a `TORTOISE_DB_URI` READ that carries a default
a set-but-empty variable defeats, unless the site carries a reasoned inline
exemption. See the `#3094` section below for the contract, the scope boundary
and the `empty-uri-default-ok:` marker.
"""
from __future__ import annotations

import ast
import re
import sys
import warnings
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_ROOT.parent


def _iter_test_module_paths() -> list[Path]:
    """Every test module in the tree, at ANY depth (#9061).

    A non-recursive `glob("test_*.py")` plus `e2e/` reached 1004 of the 1076
    test modules and stated no boundary, so `tests/longmem_eval/`,
    `tests/e2e/auth/`, `tests/e2e/hosted/`, `tests/eval/…`, `tests/bench/` were
    all silently outside a guard whose whole purpose is to notice a mutation
    wherever it is written. It also made the census's guarantee depend on a
    filename's directory, which nothing enforces.

    Recursive by construction, and `test_the_scan_is_recursive` pins that so a
    later "simplify" cannot quietly restore the blind spot.
    """
    return sorted(_TESTS_ROOT.glob("**/test_*.py"))


def _scan_key(path: Path) -> str:
    """The declaration key for *path*: `tests/`-relative, so a subdirectory
    file cannot share a declaration with a same-named top-level one."""
    return path.relative_to(_TESTS_ROOT).as_posix()

# ── DELIBERATE_URI_MUTATIONS (cycle-8 P1-3) ─────────────────────────────────
# dict[file, list[regex]] — every mutation line in `file` must match at least
# one declared regex. The regexes are site-CLASS patterns (robust to line
# drift); the lane intent is documented per entry.
DELIBERATE_URI_MUTATIONS: dict[str, list[str]] = {
    # ── DELIBERATE_EMBEDDED_LANE: SDK-level tests force the embedded lane so
    #    the constructions never ride the URI (the delenv IS the point) ──────
    # #2944: the two embedded allow-path tests force the embedded lane so the
    # REAL wipe+replay runs against a temp DB file — a URI redirect would
    # fold the assertion onto a shared server graph. The delenv IS the point;
    # the fixture-param monkeypatch auto-restores (no lane leak).
    "test_destructive_wipe_guard.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #2724 churn-wave hygiene (2026-09-09): the two #2600 Phase 1 Task 1
    # apikey-verify unit tests (TestRegistryApikeyVerifyRawCreatedBy at
    # :163/:184) force the embedded lane (delenv the URI, then pin
    # TORTOISE_DB_PATH to a tmp_path db) — the delenv IS the point, and the
    # fixture-param monkeypatch auto-restores at teardown (no lane leak).
    "test_attribution_actor.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # embedded lane via delenv (the test_billing pattern — db_path-pinned store, so the constructions never ride the URI)
    "test_audit.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                       r'monkeypatch\.setenv\(\s*$'],
    "test_billing.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #7983: the read ledger's default path is keyed by the STORE TARGET (URI +
    # graph name), so this test drives two different URIs and the unset case to
    # prove two graphs on one host do not pool into a single ledger. The
    # setenv/delenv IS the point — the assertion is about WHICH ledger path the
    # key resolves to — and the fixture-param monkeypatch auto-restores (no leak).
    "test_read_ledger.py": [r'monkeypatch\.(?:setenv|delenv)\(\s*"TORTOISE_DB_URI"'],
    # #2291 battery campaign (hermetic per-run store tests force the
    # embedded lane; the delenv IS the point — a URI redirect would fold
    # graphs per test):
    "test_battery_lane_matrix.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # hermetic env-strip test (fixture-param monkeypatch — auto-undo)
    "test_body_cap_sweep.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # #2032: embedded lane via delenv (the test_billing pattern — registry-lane determinism for register/agent mints)
    "test_graph_storage.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # #5331: embedded lane via delenv (the test_billing pattern — the fixture pins TORTOISE_DB_PATH to a tmp_path db, so the construction never rides the URI)
    "test_bridge_mcp.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_selfhost_health_probe_executor.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],  # #3331: the probe-lane behavioural tests force the EMBEDDED lane (setenv "" IS the point — the selfhost health handlers are driven without a live server; the fixture-param monkeypatch auto-restores, no lane leak)
    "test_onboarding_selfhost_w12.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],  # #2007 W12: the `db_env` fixture (:71) and `_client` (:471) force the EMBEDDED lane (setenv "" + TORTOISE_DB_PATH to a tmp_path db — the selfhost lane is embedded-only and the env control IS the point; both sit in fixture/helper bodies whose monkeypatch auto-restores, no lane leak)
    "test_chain_enforcer.py": [r'monkeypatch\.delenv\("TORTOISE_DB_URI"'],
    "test_github_index_lifecycle.py": [r'monkeypatch\.delenv\("TORTOISE_DB_URI"',
                                        r'monkeypatch\.setenv\("TORTOISE_DB_URI",\s*"docker:'],
    "test_commit_schema.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_embedded_concurrency.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                                      r"os\.environ\.pop\(\s*['\"]TORTOISE_DB_URI['\"]"],
    "test_ep_directed_nand.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # MCP-tool + SDK shared-store contract (PR #1684)
    "test_ep_mitigation.py": [r'os\.environ\[\s*["\']TORTOISE_DB_URI["\']\]\s*=',
                               r'os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']',
                               r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],  # #2315: docker-lane EP mitigation tests force the URI (the setenv IS the point — live-EP delta + schema-reject assertions against the real server)
    "test_extractor_reliability.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_hard_reject.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_hosted_api.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',  # #1686: register/provision journal tests force the embedded lane (the delenv IS the point)
                             r'monkeypatch\.setenv\(\s*$',  # #2251: URI-mode namespace-derivation envelope tests force the docker branch of _make_sdk/_registry_anchor with a RECORD-ONLY __init__ spy (the setenv IS the point — the (db_path=None, namespace=...) construction contract is pinned without touching a server)
                             r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*$'],  # #4661: the registry-probe connection-reuse tests force the URI branch, where the URI string IS the cache key (and `_make_sdk` is faked, so no server is contacted) — DELIBERATE_URI
    "test_index_cli.py": [r'os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']'],  # embedded-file-contract module fixture (PR #1684)
    "test_index_restore.py": [r'os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']'],  # embedded-file-contract module fixture (PR #1684)
    "test_issue_4010_sessions_unlimited.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # #4010: resolver/clearing unit tests force the embedded lane (db_path-pinned store; the delenv IS the point — no lane leak, monkeypatch auto-undo)
    "test_mcp_client.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_mcp_http.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_mcp_server_auth_modes.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                                          r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],  # C2 #2111: tenant-mode MCP tests force the registry/embedded lane (delenv IS the point) + #2657 ask-exposure: per-test fresh-URI live probe (setenv IS the test input)
    # #4488: the embed-metering tests force the EMBEDDED registry lane (the
    # delenv IS the point — the local ``reg_org`` fixture pins TORTOISE_DB_PATH
    # and drives the real writer+reader in ONE store, because ``metering._reg_sdk``
    # resolves the registry from that path; a URI redirect would split the write
    # from the read and the test would prove nothing). The fixture-param
    # monkeypatch auto-restores at teardown, so no lane leaks into a later
    # docker-lane test (the test_metering_period_window.py precedent).
    "test_embed_metering.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #4489: the registry-lane ledger fixture forces the EMBEDDED lane (the
    # delenv IS the point — the fixture pins TORTOISE_DB_PATH so the writer and
    # reader address the same store; a URI redirect would split them). The
    # fixture-param monkeypatch auto-restores at teardown (no lane leak).
    "test_retrieval_metering.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_metering.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #3825: the metering-WINDOW tests force the EMBEDDED registry lane (the
    # delenv IS the point — the fixture puts a real billing anchor on an org's
    # `:Team` node and drives the ledger in the same store; a URI redirect
    # would split the anchor from the ledger and the test would prove nothing
    # about the writer). The fixture-param monkeypatch auto-restores at
    # teardown, so no lane leaks into a later docker-lane test.
    "test_metering_period_window.py": [
        r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_migration_consumers.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_onboarding_integration.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_pack_state.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_quota.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_selfhost.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_selfhost_rest.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_selfhost_backup.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],  # #8544: the lifespan + production-seam tests force the EMBEDDED lane (setenv "" IS the point — the selfhost daemon boots and dumps without a live server; the fixture-param monkeypatch auto-restores, no lane leak)
    "test_selfhost_volunteer_context.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],  # #2103 (W4C) — selfhost volunteer forces the embedded lane (the selfhost_rest pattern)
    "test_turnstile_signup.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_value_extractor.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #7760: the cross-embedded-DB isolation test forces the EMBEDDED lane (the
    # delenv IS the point — both ``TortoiseSDK(<path>)`` constructions must be
    # genuinely embedded, because on a URI-bearing session the path is redirected
    # to a server and derives a per-path graph name, so the ``(graph_name,
    # namespace)`` collision the test guards would not be exercised at all and it
    # would pass without the fix). The fixture-param monkeypatch auto-restores at
    # teardown, so no lane leaks into a later docker-lane test.
    "test_fallback_snapshot.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # ── DELIBERATE_URI: module-level live-FalkorDB probes (set + restore at
    #    import; the probe asserts the docker lane) ──────────────────────────
    "test_aggregative_facet_coverage.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                            r'monkeypatch\.setenv\s*\(\s*"TORTOISE_DB_URI"'],  # #2521: module-level live probe + per-test isolated-graph fixture (DELIBERATE_URI — probe/setenv IS the test input, mirrors #2518's entity-key pattern)
    # #2165 connected-assembly (merged via #2657): the assembly fixture/sdk
    # modules run a module-level live-FalkorDB FULLTEXT probe (set+restore at
    # import; DELIBERATE_URI — the probe asserts the docker lane) and a
    # per-test fresh-URI sdk fixture (monkeypatch.setenv IS the test input —
    # dedicated per-test graph, DELETED at teardown).
    "test_assembly_fixtures.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                    r'monkeypatch\.setenv\s*\(\s*"TORTOISE_DB_URI"'],
    "test_assembly_pure.py": [r'monkeypatch\.setenv\s*\(\s*"TORTOISE_DB_URI"'],  # _docker_sdk fixture: per-test fresh-URI graph (setenv IS the test input)
    "test_assembly_sdk.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                              r'monkeypatch\.setenv\s*\(\s*"TORTOISE_DB_URI"'],
    "test_directional_impl.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_directional_impl_fix.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # #7108: module-level live probe (os.environ set + restore, DELIBERATE_URI —
    # the probe is what asserts the docker lane is reachable at all) + a per-test
    # fresh-URI fixture (raw os.environ set + save/restore) giving each test its
    # OWN graph, so the operator-scope assertions cannot see a neighbour's
    # residue. No monkeypatch site is declared, because this file has none.
    "test_7108_operator_scope_predicate.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # #6151: the annotation band's graph read. Module-level live probe
    # (os.environ set + save/restore at import; DELIBERATE_URI — the probe IS
    # what asserts the docker lane, because the mechanism's whole claim is the
    # weight the READER derives from the dimensions stored on the node, which a
    # pure function test cannot observe: it is handed the value directly) plus a
    # per-test fresh-URI fixture whose `monkeypatch.setenv` auto-restores at
    # teardown, so no lane leaks into a later docker-lane test.
    "test_6151_annotation_band.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                      r'monkeypatch\.setenv\s*\(\s*"TORTOISE_DB_URI"'],
    # C3-1 #2567: docker-lane coverage-loop tests — module-level live probe
    # (set + restore) + per-test fresh-graph fixture (the test_entity_key_
    # expansion pattern; the monkeypatch.setenv is auto-restored).
    "test_coverage_loop.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                               r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # C4 #2517: docker-lane source-session re-injection tests — module-level
    # live probe (set + restore) + per-test fresh-graph fixture (the
    # test_coverage_loop pattern; the monkeypatch.setenv auto-restores).
    "test_session_reinjection.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                     r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_ep_directional.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                               r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_event_provenance.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_entity_key_expansion.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                      r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],  # #2518 entity-key expansion: module live-FalkorDB probe (DELIBERATE_URI) + per-test docker force
    # C6 #2520: time-aware query expansion — module-level live-FalkorDB probe
    # (set + restore) + per-test fresh-graph fixture (the test_coverage_loop
    # pattern; the monkeypatch.setenv auto-restores).
    "test_time_aware_sdk_2520.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_time_aware_eval_2520.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                       r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_time_aware_run_2520.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_hnsw_vector_index.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_ingest.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_integration_search.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_search_engine.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_session_capture_e2e.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # #9061: keyed by tests/-relative path — this module lives in tests/e2e/,
    # and a bare-name key matched it only because a lookup fallback stripped
    # the directory, which authorizes ANY same-named file in any directory (the
    # fallback is gone; `test_declaration_path_rejects_undeclared` pins that a
    # subdirectory twin is not authorized).
    "e2e/test_billing_upgrade.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # ── DELIBERATE_URI: fixtures/tests that force the docker lane directly ──
    "test_consolidation_4way.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_doctor.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # #3065 (bounded-socket resolver): the indexer case FORCES the docker lane
    # (setenv IS the input — the real `_graph_entity_keywords` construction must
    # be observed carrying the resolved read bound, and a URI-less construction
    # would take the embedded lane and prove nothing), and the doctor-probe case
    # STRIPS the env URI (DELIBERATE_EMBEDDED_LANE — the probe is driven by an
    # explicit `--db`, so a leaked ambient URI must not shadow it). Both sites
    # are the fixture-param monkeypatch, which auto-restores at teardown — no
    # lane leaks into a later docker-lane test.
    "test_bounded_raw_clients_3065.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                                          r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #3892: the read-path status test points the endpoint at a DECLARED but
    # UNREACHABLE docker URI (127.0.0.1:1) to drive the `degraded` composite
    # without mocking `_get_proj` — the setenv IS the test input.
    "test_read_status.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # #3554: the retrofit verb's `--uri` reporting test sets a docker:// URI so
    # `_get_proj` → `_derive_graph_name` has a graph name to resolve, while the
    # SDK itself is REPLACED by a fake module (`fake_sdk_module`) — nothing
    # connects, and the monkeypatch fixture auto-restores at teardown, so no
    # lane leaks. The setenv IS the test input, the same "pinned without
    # touching a server" shape as test_hosted_api.py's #2251 spy entry.
    "test_session_schema_3554.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # #3039: the ACL admin-client decode pin forces a docker:// URI so
    # `_admin_client` takes the redis path; redis.Redis is stubbed, never
    # connects. The setenv IS the test input (deliberate docker lane).
    "test_from_uri_userinfo.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                                     # :950 and :1012 write `monkeypatch.setenv(` and put the
                                     # URI literal on the FOLLOWING line, so the site's own line
                                     # matches only the trailing branch - the same multi-line
                                     # shape and the same remedy as test_backup.py above.
                                     r'monkeypatch\.setenv\(\s*$'],
    "test_namespace_uri_mode.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                     r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                                     r'monkeypatch\.setenv\(\s*$'],
    "test_session_index_health.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_tortoise_client.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_2206_confidence_surfaces.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],  # #2206: module-level live-FalkorDB probe + per-test isolated-graph fixture (set/restore, never leaves a mutation)
    # ── E2E-8 conformance (Task 8): the leg env control IS the test input —
    #    the embedded leg delenvs the URI, the docker leg setenvs it (the
    #    E2E-1 pattern); declared so the guard stays green ────────────────
    "test_divergence_conformance.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                                         r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # DELIBERATE_URI: the docker-calibrated cross-lens test (T8 D9) forces
    # the docker lane — its setenv IS the test input.
    "test_cross_lens.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # #1370: the subject-binding suite's URI-mode construction case forces the
    # docker lane so `_graph_report` takes its URI branch. The URI is
    # DELIBERATELY unreachable (port 16610) — the test pins the construction
    # contract (no positional db_path, namespace passed through) against a
    # spy SDK, so it never needs a server and proves nothing about one.
    # Fixture-param monkeypatch, so pytest auto-undoes at teardown (no lane
    # leak into a later docker-lane test).
    "test_subject_binding_1370.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
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
    # ── #3458 main-red reconciliation (2026-09-13): these two sites were
    #    introduced by #3414 and #3056 and red'd every PR until declared.
    #    Both are deliberate, but NOT both DELIBERATE_URI — see the per-entry
    #    notes below (test_backup.py's :282 delenv is the embedded-lane case).
    # #3414: module-level live-FalkorDB probe (set + try/finally restore)
    # PLUS an autouse per-test isolated-graph fixture (set/restore); never
    # leaves a mutation behind.
    "test_3276_has_ep_measured.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # #3056/#2974: the BGSAVE tests deliberately point the endpoint at a
    # URI (never the embedded defaults) — fixture-param monkeypatch, so
    # pytest auto-undoes every mutation at teardown. INTENT IS NOT UNIFORM
    # within this file: the `setenv` sites toward a URI are DELIBERATE_URI,
    # while `test_backup.py:282`'s `delenv("TORTOISE_DB_URI")` is
    # DELIBERATE_EMBEDDED_LANE — dropping the env control to exercise the
    # embedded-mode `"skipped:"` path IS the point of that test. The single
    # file-level entry cannot express both labels; recorded here so the
    # intent table is not read as claiming they are the same case. The
    # trailing `setenv\(\s*$` branch covers the multi-line call at :209
    # where the URI literal sits on the following line.
    "test_backup.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"',
                       r'monkeypatch\.setenv\(\s*$'],
    # #7767: the restore-rollback suite forces the EMBEDDED lane for the same
    # reason #2944's wipe guard does — the behaviour under test is FILESYSTEM
    # rollback (the destination journal, DB and AOF dirs are renamed aside and
    # put back on a refusal), so under a URI the `FalkorProjection(path)`
    # redirect touches no file and every file assertion in the suite would be
    # vacuous. The delenv IS the point; the autouse fixture-param monkeypatch
    # auto-restores at teardown (no lane leak).
    "test_7767_restore_rollback.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #7935: the destination-lock suite forces the EMBEDDED lane for the same
    # reason #7767's rollback suite does — the subject is a flock on the
    # destination STORE's `<db>.tortoise-construct.lock` sidecar plus a
    # `<events>.restore.lock` sidecar, and the aside protocol is
    # filesystem-scoped, so under a URI the file assertions would be vacuous.
    # The delenv IS the point; the autouse fixture-param monkeypatch
    # auto-restores at teardown (no lane leak).
    "test_7935_restore_destination_lock.py": [
        r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #7951: the hosted-session fixtures force the EMBEDDED lane for the same
    # reason #7767's rollback suite does — the behaviour under test is the
    # JOURNAL (`derived = replay(journal)` around a session delete) against a
    # temp DB + temp journal, so an ambient docker URI would divert the graph
    # away from the journal the suite is about and every assertion would be
    # vacuous. The delenv IS the point; the fixture-param monkeypatch
    # auto-restores at teardown (no lane leak).
    "test_7951_delete_resurrection.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #5222/#5188 + #5222's hygiene gate: both files are HERMETIC (the SDK / the
    # reaper is faked, no server is contacted), and both force the embedded or a
    # test-pinned lane on purpose — the delenv/setenv IS the input, so the site
    # must not be read as riding the ambient URI.
    #   * backfill guard: `delenv` twice, so the run cannot inherit a developer's
    #     ambient URI and accidentally target a real graph (the guard under test
    #     resolves the namespace, not the URI path — #5188).
    #   * server hygiene: `setenv` points the teardown probe at the test matrix
    #     so the reaper's own path is exercised; fixture-param monkeypatch, so
    #     pytest auto-undoes it at teardown (no lane leak).
    "test_backfill_ghost_members_guard.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #4503: the edge-accounting guards control TORTOISE_DB_URI deliberately in
    # three places, and the env control IS the input in each:
    #   * two `delenv` sites force the EMBEDDED lane (DELIBERATE_EMBEDDED_LANE)
    #     so that a cap count read from a SECOND, EMPTY database is
    #     distinguishable from a legitimately-empty org. With the URI in place,
    #     `count_org_usage` resolves the `org_<org>` URI graph and the
    #     wrong-graph read these guards exist to catch would be invisible (both
    #     sides would agree).
    #   * one `setenv` + its assertion site (DELIBERATE_URI) pin that
    #     `_open_sdk` RESTORES a caller's URI — `main()` is called in-process,
    #     so a leaked mutation re-points the next invocation.
    # Fixture-param monkeypatch throughout, so pytest auto-undoes every site at
    # teardown — no lane leak.
    "test_4503_edge_relationship_accounting.py": [
        r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
        r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
        r'os\.environ\["TORTOISE_DB_URI"\]\s*='],
    # #4125/#8229: the operator-CLI test forces the EMBEDDED lane deliberately —
    # the target under test is a SCHEME-LESS `--db <path>` store, so the env
    # control IS the test input (a URI-shaped value would send the fixture write
    # through the #1647 test redirect to a server graph while the CLI reads the
    # embedded file — the exact lane divergence this declaration documents).
    # It is set BEFORE the fixture SDK is constructed, so both the write and the
    # CLI read address the SAME embedded store in every lane; the fixture-param
    # monkeypatch auto-restores at teardown (no lane leak).
    "test_4125_legacy_session_source_migration.py": [
        r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*\w+\)'],
    "test_server_hygiene_gate.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                                     # the URI literal sits on the FOLLOWING line at
                                     # :251 (`setenv(` then the string), so the site's
                                     # own line matches only the trailing branch — the
                                     # same multi-line shape and the same remedy as
                                     # `test_backup.py` above.
                                     r'monkeypatch\.setenv\(\s*$'],
    # #3815: the mitigation-weight-move suite runs a module-level live-FalkorDB
    # probe (set + try/finally restore at import) PLUS an autouse per-test
    # isolated-graph fixture whose monkeypatch.setenv IS the test input — the
    # same shape as test_3276_has_ep_measured.py above, and it never leaves a
    # mutation behind (the probe restores in finally, pytest undoes the fixture).
    # DELIBERATE_URI.
    "test_3815_mitigation_moves_weight.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                             r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # #2500: main() assigns os.environ["TORTOISE_DB_URI"] before constructing the
    # SDK, so the test pins the docker URI and restores it at teardown — the
    # comment at the site says the restore exists precisely because otherwise it
    # leaks a live-server URI into every later module (the sdk_factory lane flip).
    # DELIBERATE_URI: the setenv IS the test input.
    "test_2500_terminal_ep_backfill.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # #7017: the engine-probe suite is hermetic — every probe is injected, so
    # the URI's SOLE role is as the input to `graph_target_from_env()`, and the
    # endpoint-resolution tests set/del it precisely to pin which endpoint
    # `main` picks (the flag-override, TLS-refusal and malformed-port cases all
    # turn on the URI's shape). The env control IS the test input; every site is
    # a function-scoped `monkeypatch` call, auto-undone by pytest at teardown, so
    # no lane flip can leak. DELIBERATE_URI.
    "test_engine_probe.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                              r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #8746: the SHARED-URI-DEFAULT refusal arm, on BOTH lanes. `drop_test_graph`
    # derives the default graph name from `tests/_embedded._uri_default_graph_name()`,
    # which reads TORTOISE_DB_URI from the environment with NO injection seam —
    # so the env control IS the test input, and the URI's PATH
    # (`/tortoise_test_matrix`) is the assertion: the name is inside the
    # canonical `tortoise_test` family (so the prefix gate admits it) yet must
    # still be refused. That refusal ALONE attributes nothing — the mint-shape
    # arm refuses it too — so the discrimination comes from a second probe whose
    # URI DEFAULT is itself a mint-shaped name (`/test_proj125_ab12cd34`):
    # refused with `db.calls == []`, then ADMITTED once the URI points
    # elsewhere. The `delenv` case is the
    # URI-LESS lane (live_proj runs there too, since it skips on a TCP probe):
    # with no URI the default-graph arm cannot fire at all, so the same test
    # pins the MINT-SHAPE arm that closes the class — a shared fixed name is
    # refused, a per-run one still reaches the guard. Every probe drives
    # `_FakeProj`, whose `db` only RECORDS calls, so no server is contacted; the
    # function-scoped `monkeypatch` restores the URI at teardown, so no lane can
    # leak into a later module. DELIBERATE_URI.
    "test_projection.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                           r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # #8939/#8870: the lane-gate unit pins for the version-anchor live file. The
    # setenv is DELIBERATE_URI — a CONFIGURED docker URI is the test input, and
    # the assertion is that `_live_uri()` uses it AS-IS instead of probing a
    # second endpoint to decide whether to run (the pre-fix gate probed the
    # legacy port and then dialled the URI). The two delenvs are
    # DELIBERATE_EMBEDDED_LANE — the URI-less cases: the docker-test-lane
    # fallback, and the no-endpoint-at-all skip that must route through
    # `_live_utils.LIVE_URI_SKIP_REASON` rather than a hand-written reason.
    # Every site is a function-scoped `monkeypatch` call, auto-undone by pytest
    # at teardown, so no lane flip can leak into a later module.
    # MIXED INTENT: DELIBERATE_URI + DELIBERATE_EMBEDDED_LANE.
    "test_8870_version_anchor_write_order.py": [
        r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
        r'monkeypatch\.setenv\(\s*$',
        r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # ── #9061: sites the census did not previously scan at all. Keys are
    #    `tests/`-relative because these live in a subdirectory, so they can
    #    never share a declaration with a top-level file of the same name.
    # The module-level LIVE-PROBE pattern the census docstring documents
    # ("save-restore: `_old_uri = os.environ.get(...)` captured and assigned
    # BACK in a finally"): `_PROBE_GRAPH` is installed before the import-time
    # FTS probe runs and the `finally` puts the saved `_OLD_URI` back. The
    # restore sites are declared as well as the set, because the census counts
    # every matching line — that is what makes the pairing auditable rather
    # than assumed. DELIBERATE_URI (the probe needs a THROWAWAY graph name, not
    # the embedded lane); the `monkeypatch` site is the function-scoped `sdk`
    # fixture, auto-undone at teardown.
    "longmem_eval/test_assembly_arm.py": [
        r'os\.environ\["TORTOISE_DB_URI"\]\s*=',
        r'os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']',
        r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # The arm's `--db` scoping tests (#1349 isolation): an explicit `--db` must
    # beat a stale `TORTOISE_DB_URI`, so the stale value is set to a DIFFERENT
    # endpoint and then the store's own `_temporary_env_var` proves the scoped
    # value wins and is restored. MIXED INTENT: the `setenv`s are
    # DELIBERATE_URI (the stale env IS the input); the `delenv` is
    # DELIBERATE_EMBEDDED_LANE — it establishes the no-stale-URI baseline that
    # the first arm asserts against. All function-scoped `monkeypatch`, so no
    # lane flip can leak into a later module.
    "longmem_eval/test_vector_arm.py": [
        r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
        r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
}

# Carve-out TEST-MODULE stems (Task 5 wires these into TEST_NO_REDIRECT_STEMS;
# a carve-out's env mutation cannot flip a lane — no redirect fires for it).
_EXEMPT_FROM_ENV_MUTATION_GUARD = frozenset() | {
    # the epic's own env-control seam unit surfaces — env control IS the input
    "test_redirect_seam.py",
    "test_wipe_server.py",
    "test_round_trip_parity.py",
    "test_loopback_predicate_single_source.py",
    # documented lifecycle carve-outs (Task 9 carve-out set)
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

# ── Restoration guard (#2084) ─────────────────────────────────────────────
# RAW mutations = direct os.environ writes/pops/dels. The monkeypatch
# fixture-PARAM form (monkeypatch.delenv/setenv) is NOT raw — pytest
# auto-undoes it at teardown. A RAW mutation at module scope or inside a
# fixture body has no auto-undo: it leaks into every later test in the same
# pytest process unless paired with a save-restore (the #2062 class).
_RAW_URI_MUTATION_RE = re.compile(
    r'(?P<assign>(?:os|_os)\.environ\["TORTOISE_DB_URI"\]\s*=(?!=))'
    r'|(?P<pop>(?:os|_os)\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\'])'
    r'|(?P<del>del\s+(?:os|_os)\.environ\[["\']TORTOISE_DB_URI["\']])'
)
# self-restoring inline assignment (`os.environ[...] = os.environ.get(...)`
# — reads the old value and writes it back): a restore form, not a leak.
_URI_GET_RE = re.compile(
    r'(?:os|_os)\.environ\.get\(\s*["\']TORTOISE_DB_URI["\']'
)
# bare-instance evidence: a fixture that creates pytest.MonkeyPatch() must
# call .undo() in its body (pytest does NOT auto-undo bare instances — only
# the fixture-param form).
_MONKEYPATCH_INST_RE = re.compile(
    r'\b\w+\s*=\s*pytest\.MonkeyPatch\(\)'
)
# save-restore evidence: `_old_uri = os.environ.get("TORTOISE_DB_URI")`
# captured (the value to restore) …
_URI_SAVE_RE = re.compile(
    r'([A-Za-z_]\w*)\s*=\s*(?:os|_os)\.environ\.get\(\s*["\']TORTOISE_DB_URI["\']'
)
# … and assigned back: `os.environ["TORTOISE_DB_URI"] = _old_uri`.
_URI_RESTORE_RE = re.compile(
    r'(?:os|_os)\.environ\["TORTOISE_DB_URI"\]\s*=\s*([A-Za-z_]\w*)'
)


def _exempt_stems() -> frozenset[str]:
    """Test-module STEMS exempt from the env-mutation guards.

    _EXEMPT_FROM_ENV_MUTATION_GUARD holds .py names; TEST_NO_REDIRECT_STEMS
    holds stems (the canonical carve-out registry shared with
    test_markers/test_derived_names and conftest's TORTOISE_TEST_NO_REDIRECT
    export). Normalized to stems so the DOCUMENTED carve-out exemption
    actually fires (#2084 exemption decision — see the module docstring)."""
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    return frozenset(
        name[:-3] if name.endswith(".py") else name
        for name in _EXEMPT_FROM_ENV_MUTATION_GUARD) | frozenset(
            TEST_NO_REDIRECT_STEMS)


def _exempt_paths() -> frozenset[Path]:
    """The exempt MODULES: each carve-out STEM resolved to ONE path (#9061).

    The exempt set is a list of carve-out STEMS, and a stem is not a path. Two
    wrong answers were available here, and both were live at some point:

    * matching on `path.stem` let the exemption follow the NAME into any
      directory, so a same-named module anywhere was silently outside both
      guards; and
    * assuming `tests/<stem>.py` dropped the exemption of every carve-out whose
      module does not sit at the top level — `test_smoke_embedded` is
      `tests/bench/test_smoke_embedded.py`: it is in `config/ci-surfaces.yml`'s
      carve-out set (`a carve_out file is excluded from EVERY docker leg`) and
      `tests/_embedded.py` names it as a stem precisely because "tests/bench
      rides the P2 flip via the push_extra distribution, so its URI job must
      keep the file embedded". A stem-only predicate called that file exempt;
      a top-level-only predicate called it scanned. Neither read the registry.

    So the stem is RESOLVED: the top-level module when one exists, otherwise
    the module the registry's stem names. A stem that resolves to more than one
    module is ambiguous, and `test_every_carve_out_stem_resolves_to_one_module`
    reds on it — that is what keeps a same-stem file in another directory from
    inheriting the exemption.
    """
    by_stem: dict[str, list[Path]] = {}
    for p in _iter_test_module_paths():
        by_stem.setdefault(p.stem, []).append(p)
    exempt: set[Path] = set()
    for stem in _exempt_stems():
        top = _TESTS_ROOT / f"{stem}.py"
        candidates = [top] if top.is_file() else by_stem.get(stem, [])
        if len(candidates) == 1:
            exempt.add(candidates[0])
        # 0 candidates (a dead stem) and 2+ (an ambiguous one) are BOTH red in
        # `test_every_carve_out_stem_resolves_to_one_module`. Until they are
        # fixed the exemption fails CLOSED — nothing is exempted, so an
        # ambiguity can only over-report, never silently exempt two modules.
    return frozenset(exempt)


def _is_exempt_module(path: Path) -> bool:
    """True only for the carve-out MODULE the registry names (#9061).

    By PATH, never by name: the exemption's stated rationale ("carve-out
    modules run in the URI-less lane and never co-run with docker-lane files")
    is a property of one module, so it is granted to that module and to nothing
    that merely shares its name. `_exempt_paths` resolves the registry; this is
    the single-path reading of it.
    """
    return path in _exempt_paths()


def _scanned_modules():
    """Yield (key, src) for every scanned module — the ONE scan population.

    Both guards read this, so they cannot narrow independently, and
    `test_both_guards_scan_the_recursive_population` pins the population to the
    recursive enumeration (#9061).

    The exempt set is resolved ONCE here: resolving per module would re-glob
    the tree 1076 times."""
    exempt = _exempt_paths()
    for f in _iter_test_module_paths():
        if f in exempt:
            continue
        yield _scan_key(f), f.read_text(encoding="utf-8")


def _uri_mutation_sites():
    """Yield (key, line_number, line) for every mutation site in a
    NON-allowlist (migrated) test file. The executable census — the plan's
    Step 1 grep is the enumeration, this is its machine form."""
    for key, src in _scanned_modules():
        for m in _MUTATION_RE.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            yield key, line, src.split("\n")[line - 1].strip()


def _is_declared(fname: str, line: str) -> bool:
    """The declaration-guard matching logic (factored for pinning): a
    mutation line is declared when the file's DELIBERATE_URI_MUTATIONS
    entry exists and at least one declared regex matches the line.

    *fname* is the `tests/`-relative key (`longmem_eval/test_vector_arm.py`),
    which for a top-level module IS its bare name — so the 98 pre-#9061 bare
    entries keep working without a second lookup path. There is deliberately NO
    bare-name fallback: it was tried, and it re-opened cross-file laundering —
    `sub/test_config.py` matched the declaration written for
    `tests/test_config.py`, so a declaration for one file authorized any
    same-named file in any directory. `test_declaration_keys_are_unambiguous`
    keeps bare keys honest; `test_declaration_path_rejects_undeclared` pins that
    a same-named subdirectory module is NOT authorized."""
    declared = DELIBERATE_URI_MUTATIONS.get(fname)
    return bool(declared) and any(re.search(p, line) for p in declared)


def test_uri_env_mutations_declared():
    """Cycle-8 P1-3: every TORTOISE_DB_URI mutation in a migrated test file
    is declared in DELIBERATE_URI_MUTATIONS — an undeclared delenv/setenv on
    a docker half silently constructs embedded and passes green (the #942
    vacuity vector). A NEW mutation in a migrated file reds until triaged."""
    undeclared = []
    for fname, lineno, line in _uri_mutation_sites():
        if not _is_declared(fname, line):
            undeclared.append(f"{fname}:{lineno}: {line}")
    assert not undeclared, (
        "undeclared TORTOISE_DB_URI mutation(s) — triage into "
        "DELIBERATE_URI_MUTATIONS (DELIBERATE_URI or "
        "DELIBERATE_EMBEDDED_LANE):\n" + "\n".join(undeclared))


def test_uri_mutations_declared_files_exist():
    """The table's file keys must resolve to real test modules — a typo'd
    key silently exempts nothing (and the guard's missing-file red would be
    the only signal)."""
    scanned = {_scan_key(p) for p in _iter_test_module_paths()}
    scanned_names = {p.name for p in _iter_test_module_paths()}
    for fname in DELIBERATE_URI_MUTATIONS:
        assert fname in scanned or fname in scanned_names, \
            f"DELIBERATE_URI_MUTATIONS key {fname!r} is not a test module"


def test_the_scan_is_recursive():
    """#9061: the census must see EVERY directory, not `tests/` + `tests.e2e/`.

    Pins the enumeration itself, because the guard's only failure mode when it
    narrows is silence: 72 modules and 7 mutation sites were outside it while
    it reported green.
    """
    scanned = {_scan_key(p) for p in _iter_test_module_paths()}
    for required in ("longmem_eval/test_assembly_arm.py",
                     "longmem_eval/test_vector_arm.py",
                     "e2e/auth/test_bff_flow.py"):
        assert required in scanned, (
            f"the census scan does not reach {required!r} — the glob stopped "
            f"covering the tree (#9061)")


def test_both_guards_scan_the_recursive_population():
    """#9061: the PINS above are worthless unless the GUARDS use the population.

    `test_the_scan_is_recursive` passed even with both guards reverted to the
    pre-#9061 flat glob, because the 72 newly-visible modules hold no violation
    today — a pin with nothing to see is silent, not safe. So bind each guard's
    population to the recursive enumeration and require that neither can scan
    independently:

      * both guards yield from the SAME seam (`_scanned_modules`), and
      * that seam IS the recursive enumeration minus the carve-out modules.
    """
    exempt = _exempt_paths()
    expected = {_scan_key(p) for p in _iter_test_module_paths()
                if p not in exempt}
    restored = {name for name, _src in _non_exempt_test_files()}
    assert restored == expected, (
        "the restoration guard's population drifted from the recursive scan "
        f"— missing {sorted(expected - restored)[:5]}")
    # The declaration guard's output is SITES, not a population, so a narrowed
    # scan would not show up in its results — pin that it READS the seam
    # functionally (`test_both_guards_consume_the_one_population`).


def test_both_guards_consume_the_one_population(monkeypatch):
    """#9061: a guard that iterates its OWN glob can narrow without reddening.

    Functional, not textual: the population seam is replaced by one synthetic
    module and each guard must report on THAT module. A guard holding its own
    `glob(...)` would not see the sentinel and reds here, whatever its source
    looks like — the previous version of this pin scanned the file's own text
    for `_scanned_modules()`, which a mere annotation on the def line defeated
    (the string then matched the TEST's literal instead of the function).
    """
    sentinel = "synthetic/sub/test_planted_9061.py"
    planted = 'x = 1\nos.environ.pop("TORTOISE_DB_URI", None)\n'
    monkeypatch.setattr(sys.modules[__name__], "_scanned_modules",
                        lambda: iter([(sentinel, planted)]))
    sites = list(_uri_mutation_sites())
    files = list(_non_exempt_test_files())
    assert [s[0] for s in sites] == [sentinel], (
        "the declaration guard did not read the one scan population — it is "
        f"iterating its own glob (#9061): {sites}")
    assert [name for name, _src in files] == [sentinel], (
        "the restoration guard did not read the one scan population — it is "
        f"iterating its own glob (#9061): {files}")


def test_a_subdirectory_module_cannot_inherit_a_carve_out_exemption():
    """#9061: the exempt set is a list of STEMS; a stem is not a path.

    Matching on `path.stem` exempted ANY module whose name appeared in the
    carve-out registry, so the moment the scan went recursive a new same-named
    file in any directory was silently outside both guards. The exemption is a
    property of the module the registry NAMES, so it is granted by path — and
    the stem is RESOLVED to that module rather than assumed to be top-level
    (`test_smoke_embedded` is `tests/bench/test_smoke_embedded.py`: a predicate
    of `tests/<stem>.py` would have revoked a live carve-out exemption instead
    of fixing the inheritance).
    """
    stack = _TESTS_ROOT / "bench" / "test_smoke_embedded.py"
    assert stack.is_file(), "the module this pin reads has moved"
    assert _is_exempt_module(stack), (
        "the module the carve-out registry names by stem lost its exemption — "
        "`test_smoke_embedded` resolves to tests/bench/, not to a top-level "
        "file (#9061)")
    # …and the NAME buys nothing in another directory: a same-stem module
    # elsewhere does not inherit it (nothing resolves by name any more).
    for other in ("mini_beir", "eval/retrieval"):
        intruder = _TESTS_ROOT / other / "test_smoke_embedded.py"
        assert not _is_exempt_module(intruder), (
            f"a module in {other}/ inherited the carve-out exemption by STEM "
            "— the exemption must be granted by path (#9061)")
    assert not _is_exempt_module(stack.parent / "test_embedded_lifecycle.py"), (
        "a sibling module in the carve-out's directory must not inherit its "
        "exemption")


def test_every_carve_out_stem_resolves_to_one_module():
    """Every exempt stem must name exactly ONE module — the registry's target.

    `_exempt_paths` resolves a stem to the module the carve-out registry names,
    and that is only sound while the resolution is UNAMBIGUOUS: a stem matching
    two modules (a top-level file plus an `e2e/` twin, say) must red here
    instead of silently exempting both. A stem naming NO module is a dead entry
    — the exemption it claims is granted to nothing, which reads as registered
    coverage in `TEST_NO_REDIRECT_STEMS` while doing no work.
    """
    by_stem: dict[str, list[str]] = {}
    for p in _iter_test_module_paths():
        by_stem.setdefault(p.stem, []).append(_scan_key(p))
    exempt = _exempt_stems()
    ambiguous = {s: by_stem[s] for s in exempt if len(by_stem.get(s, [])) > 1}
    assert not ambiguous, (
        "carve-out stem(s) resolving to more than one module — the exemption "
        "would land on the wrong file: "
        + "; ".join(f"{s} -> {v}" for s, v in sorted(ambiguous.items())))
    dead = sorted(s for s in exempt if s not in by_stem)
    assert not dead, (
        f"carve-out stem(s) naming no test module: {dead} — a dead exemption "
        "looks like registered coverage and does no work")
    # …and the resolution must reach the module the stem names, not skip it.
    assert (_TESTS_ROOT / "bench" / "test_smoke_embedded.py") in _exempt_paths(), (
        "`test_smoke_embedded` resolves to tests/bench/ — the resolved exempt "
        "set must contain it (#9061)")


def test_declaration_keys_are_unambiguous():
    """A bare-name key must match exactly ONE module.

    Keys are looked up by `tests/`-relative key, which for a top-level module IS
    its bare name, so the 98 pre-#9061 entries need no second lookup path. The
    hazard a bare name still carries is REASSIGNMENT: a key written for
    `tests/test_foo.py` keeps its name when that file is deleted and a
    `tests/sub/test_foo.py` appears, and nothing about the string changes.
    """
    by_name: dict[str, list[str]] = {}
    for p in _iter_test_module_paths():
        by_name.setdefault(p.name, []).append(_scan_key(p))
    ambiguous = {k: v for k, v in by_name.items() if len(v) > 1}
    offenders = sorted(k for k in DELIBERATE_URI_MUTATIONS if k in ambiguous)
    assert not offenders, (
        "bare-name declaration key(s) matching more than one module — key "
        "them by their tests/-relative path instead: "
        + "; ".join(f"{k} -> {ambiguous[k]}" for k in offenders))
    # …and a bare key must still BE the top-level module it was written for.
    # `by_name` catches a key that matches two paths TODAY; it cannot see a
    # reassignment, and a bare key whose module is gone matches NO site at all —
    # a dead declaration that reads as coverage while authorizing nothing.
    moved = sorted(k for k in DELIBERATE_URI_MUTATIONS
                   if "/" not in k and not (_TESTS_ROOT / k).is_file())
    assert not moved, (
        "bare-name declaration key(s) whose top-level module no longer exists "
        f"— dead declarations: {moved}")


def test_the_cost_skip_cannot_hide_a_uri_agnostic_leak():
    """#9061: the restoration guard's fast-path must not skip the bare-instance
    check, which does not depend on the URI string.

    The guard skips a module that mentions neither `TORTOISE_DB_URI` nor a bare
    `pytest.MonkeyPatch()`. That skip is sound by construction — a module whose
    source contains neither cannot hold a site this guard reports, and the
    second predicate IS the URI-agnostic check's trigger, so it cannot hide a
    module that check would flag. A URI-ONLY skip is still wrong: a fixture that
    sets some OTHER variable through a bare MonkeyPatch and never calls
    `.undo()` leaks exactly like the #2062 pop did, and its source contains no
    URI at all. So the fixture below is a real violation with no URI in it — if
    the skip is ever narrowed back to the URI alone, this reports nothing and
    the test reds.
    """
    src = (
        "import pytest\n"
        "\n\n"
        "@pytest.fixture\n"
        "def port_fixture():\n"
        "    mp = pytest.MonkeyPatch()\n"
        '    mp.setenv("TORTOISE_TEST_DOCKER_PORT", "1234")\n'
        "    yield\n"
    )
    assert "TORTOISE_DB_URI" not in src, "the fixture must contain no URI string"
    violations = _restoration_violations([("synthetic_no_uri.py", src)])
    assert violations, (
        "a fixture-created bare MonkeyPatch() with no .undo() must be reported "
        "even when the file contains no TORTOISE_DB_URI — the cost skip must "
        "not gate the URI-agnostic check (#9061)")
    assert "undo" in " ".join(violations), violations


# ═══════════════════════════════════════════════════════════════════════
# Restoration guard (#2084) — static scope/evidence analysis helpers
# ═══════════════════════════════════════════════════════════════════════

def _scope_map(src: str) -> list[tuple[str, str | None]]:
    """Per-line scope classification, with a FAIL-CLOSED fallback.

    `_scope_map_pass` carries a single-quoted literal across a backslash
    continuation. If the source ends with that literal still open it cannot
    tokenize at all, so the carry established nothing about where the code ends
    — and honouring it would attribute every remaining line to the function
    that opened the literal, HIDING violations rather than surfacing them. The
    second pass disables the carry, which is the pre-carry behaviour."""
    try:
        # A test file may carry a deprecation-shaped escape sequence; compiling
        # it here must not print a warning the guard never printed before.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(src, "<scope-map>", "exec")
    except (SyntaxError, ValueError):
        # The source does not parse, so NOTHING it says about where a literal
        # ends can be trusted — and a carry on an unterminated literal
        # attributes the rest of the file to the function that opened it,
        # HIDING violations instead of surfacing them. Fail CLOSED: no carry,
        # which over-reports rather than under-reports.
        scopes, _ = _scope_map_pass(src, allow_single_carry=False)
        return scopes
    scopes, _ = _scope_map_pass(src, allow_single_carry=True)
    return scopes


def _scope_map_pass(
    src: str, *, allow_single_carry: bool
) -> tuple[list[tuple[str, str | None]], str | tuple[str, str] | None]:
    """One pass. ("top", None) for module-level
    code, ("fixture", <name>) inside a @pytest.fixture-decorated
    function, ("fn", <name>) inside any other function.

    Forward indentation-stack parse (layout-independent): a statement at
    indent <= a function's indent closes it, so a module-level try/finally
    probe that appears AFTER a top-level def stays classified "top" (the
    backward first-def-wins scan misclassified that shape). Continuation
    lines (open parens) and decorator stacks are handled; function-scoped
    monkeypatch fixtures are restored by pytest at teardown, the #2062
    leak class lives in the "top" and "fixture" scopes."""
    lines = src.split("\n")
    scopes: list[tuple[str, str | None]] = [("top", None)] * len(lines)
    # Continuation state comes from CODE brackets only — a bracket inside a
    # string literal or comment is copy, not an open expression (#8995).
    stack: list[tuple[int, str, str]] = []  # (indent, kind, name)
    paren = 0
    pending_fixture = False
    state: str | tuple[str, str] | None = None
    for i, line in enumerate(lines):
        # Count brackets in CODE ONLY. A bracket inside a string literal or a
        # comment is not a continuation: `assert "(as of" in text` left the
        # counter stuck at 1 for the rest of the file, so every later `def`
        # (guarded on `paren == 0`) was skipped and its body was attributed to
        # the LAST function that opened — measured on
        # `tests/test_time_aware_sdk_2520.py`, where a compliant
        # `@contextmanager` helper was reported as a mutation inside an
        # unrelated test (#8937 / #8487).
        code, state = _code_only(line, state, allow_single_carry=allow_single_carry)
        stripped = code.strip()
        if stripped.startswith("@") and paren == 0:
            # precise fixture-marker test: `.fixture(` / `.fixture\n` — a
            # bare "fixture" substring (usefixtures, parametrize ids) must
            # NOT set pending_fixture (#2084 review P2)
            if re.search(r'\.fixture\b', stripped):
                pending_fixture = True
            scopes[i] = stack[-1][1:] if stack else ("top", None)
        elif re.match(r'^(\s*)(?:async\s+)?def\s+\w+', code) and paren == 0:
            ind = len(code) - len(code.lstrip())
            while stack and stack[-1][0] >= ind:
                stack.pop()
            name = re.match(r'^(\s*)(?:async\s+)?def\s+(\w+)', code).group(2)
            stack.append((ind, "fixture" if pending_fixture else "fn", name))
            pending_fixture = False
            scopes[i] = stack[-1][1:]
        elif stripped and paren == 0 \
                and not stripped.startswith(("#", "def", "@")):
            ind = len(code) - len(code.lstrip())
            while stack and stack[-1][0] >= ind:
                stack.pop()
            if ind == 0:
                pending_fixture = False
            scopes[i] = stack[-1][1:] if stack else ("top", None)
        else:
            scopes[i] = stack[-1][1:] if stack else ("top", None)
        # Paren balance over `code`, the `_code_only` output — code brackets
        # only, so a bracket inside copy cannot freeze the continuation state
        # (#8995). NOT over the raw line (#8487), and NOT over a second,
        # `tokenize`-based counter: `tokenize` emits ONE column for the
        # `FSTRING_MIDDLE` holding `{{`, so the second brace of `f"a {{ b"`
        # reaches a tokenize-span counter as code and opens a paren that
        # nothing closes. Measured on this tree by diffing the two scope maps
        # over the whole guard scan set — every module `_scanned_modules()`
        # scans (`_iter_test_module_paths()`, not a flat glob): 8 files / 9,263
        # mislabelled lines, every one of them a `{{` on an f-string line —
        # `tests/test_ship_test_onboarding.py` alone loses 5,845 lines from its
        # `{{` at L546. `_code_only` blanks the whole literal, f-string braces
        # included, in one column-aligned pass.
        for ch in code:
            if ch in "([{":
                paren += 1
            elif ch in ")]}":
                paren = max(0, paren - 1)
    return scopes, state


def _code_only(
    line: str,
    state: str | tuple[str, str] | None,
    *,
    allow_single_carry: bool = True,
) -> tuple[str, str | tuple[str, str] | None]:
    """The CODE part of one line: string literals and comments replaced by
    spaces, so a bracket inside them cannot unbalance a continuation count.

    COLUMN-ALIGNED: the returned text is the same length as `line`, so the
    leading-whitespace count of the code is the code's real indent.

    `state` is the carried lexer state: `None`, a triple-quote delimiter, or a
    ``('single', quote)`` pair for a single-quoted literal still open at end of
    line. That last case is a BACKSLASH CONTINUATION — the only way such a
    literal spans lines in valid code — and it carries because without it the
    continuation line reads as CODE: measured, ``q = "MATCH (n )\\`` followed
    by an INDENTED ``    (foo"`` leaves the ``(`` counted, so a later
    module-level statement is attributed to the function above it — HIDING it
    (review r11 P2). At COLUMN 0 the same shape fails the other way: the
    continuation closes the function, its body reads as module scope, and the
    guard over-reports instead. It carries ONLY when `allow_single_carry` — `_scope_map` turns it OFF for a source
    that does not compile, because there an unclosed literal proves nothing and
    a carry would swallow the tail.

    KNOWN LIMIT (pinned by `test_pep701_nested_quote_is_a_KNOWN_limitation`):
    an f-string nesting the SAME quote kind in a replacement field
    (``f"{d["a(b"]}"``) closes at the nested quote, so that field's brackets
    are read as code. 3.12 accepts the shape; no file in this tree uses it.

    Tracks a triple-quoted region across lines (docstrings are the common
    case), honouring backslash escapes on the closing delimiter exactly as
    `_toggle_triple_quotes` does. The two lexers answer different questions
    ("which columns are code" here, "is this position inside a string" there)
    and so cannot be folded into one pass — the shared escape rule is pinned
    equal by
    `test_the_two_triple_quote_lexers_agree_on_escaped_delimiters`.
    Single/double-quoted literals are consumed whole, honouring backslash
    escapes; a `#` outside a string starts a comment."""
    out: list[str] = []
    i = 0
    n = len(line)
    triple = state if isinstance(state, str) else None
    carried = state[1] if isinstance(state, tuple) else None
    if carried is not None:
        quote = carried
        while i < n:
            if line[i] == "\\":
                out.append(" " * min(2, n - i))
                i += 2
                continue
            if line[i] == quote:
                out.append(" ")
                i += 1
                carried = None
                break
            out.append(" ")
            i += 1
        if carried is not None:
            return "".join(out), ("single", quote)
    while i < n:
        if triple is not None:
            end = _find_unescaped(line, triple, i)
            if end == -1:
                out.append(" " * (n - i))
                return "".join(out), triple
            # Pad the WHOLE in-string span, closing delimiter included, so the
            # result stays column-aligned. Padding only the 3 delimiter columns
            # collapsed the string's content and shifted any code after a
            # mid-line close to the left, so a line that resumes code after
            # closing a region measured an indent of 3+ instead of its real
            # column (#8937 review r7, nit b).
            out.append(" " * (end + len(triple) - i))
            i = end + len(triple)
            triple = None
            continue
        ch = line[i]
        if ch == "#":
            out.append(" " * (n - i))
            break
        if line.startswith('"""', i) or line.startswith("'''", i):
            triple = line[i:i + 3]
            out.append("   ")
            i += 3
            continue
        if ch in "\"'":
            quote = ch
            out.append(" ")
            i += 1
            closed = False
            while i < n:
                if line[i] == "\\":
                    out.append(" " * min(2, n - i))
                    i += 2
                    continue
                if line[i] == quote:
                    out.append(" ")
                    i += 1
                    closed = True
                    break
                out.append(" ")
                i += 1
            if not closed:
                # Unterminated at end of line. In a source that COMPILES this
                # is necessarily a backslash continuation — no other unclosed
                # single-quoted literal is legal — so the parity of the trailing
                # backslashes needs no test of its own: `_scope_map` only
                # enables the carry for sources that compile (measured: adding
                # a parity test changes NONE of the scan set's scope maps,
                # because every shape it would reject is already unparsable).
                if allow_single_carry:
                    return "".join(out), ("single", quote)
                return "".join(out), triple
            continue
        out.append(ch)
        i += 1
    return "".join(out), triple


def _find_unescaped(line: str, delim: str, start: int) -> int:
    """Index of the first `delim` at/after `start` that is NOT backslash-
    escaped, or -1. Same escape rule as `_toggle_triple_quotes`: an odd number
    of immediately preceding backslashes escapes the delimiter, an even number
    does not. A bare `str.find` closed a region on an escaped delimiter, which
    made the two triple-quote lexers disagree about the same line."""
    i = start
    while True:
        found = line.find(delim, i)
        if found == -1:
            return -1
        backslashes = 0
        j = found - 1
        while j >= 0 and line[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            return found
        i = found + 1


def _scope_at(scopes: list, lineno: int) -> tuple[str, str | None]:
    """Look up the (kind, name) scope for a 1-based line number."""
    return scopes[lineno - 1]


def _in_triple_quoted_string(src: str, lineno: int, col: int = 0) -> bool:
    """True when the (line, col) position sits inside a triple-quoted
    string region.

    Subprocess child-code strings ("import os,time; os.environ.pop(...)")
    mutate the CHILD process env, never this pytest process — they must not
    count as raw process-level mutation sites. Delimiter-aware: the
    opposite quote appearing as CONTENT inside a region (a single-quote
    triple inside a double-quote triple) does not corrupt the state;
    column-aware so a same-line open+close is correctly treated as string
    content."""
    lines = src.split("\n")
    open_delim: str | None = None
    for i in range(lineno - 1):
        open_delim = _toggle_triple_quotes(lines[i], open_delim, None)
    open_delim = _toggle_triple_quotes(lines[lineno - 1], open_delim, col)
    return open_delim is not None


def _toggle_triple_quotes(line: str, open_delim: str | None,
                          col: int | None) -> str | None:
    """Toggle the triple-quote region state across `line` (up to `col`
    when given — exclusive). Returns the open delimiter or None.

    Single left-to-right pass: while a region is open only the matching
    delimiter closes it (the opposite quote is content — across lines AND
    within one line); from state None the first delimiter encountered
    opens a region."""
    end = col if col is not None else len(line)
    i = 0
    while i < end:
        backslashes = 0
        j = i - 1
        while j >= 0 and line[j] == "\\":
            backslashes += 1
            j -= 1
        escaped = backslashes % 2 == 1
        if open_delim is not None:
            if not escaped and line.startswith(open_delim, i):
                open_delim = None
                i += 3
                continue
            i += 1
            continue
        opened = False
        for tq in ('"""', "'''"):
            if not escaped and line.startswith(tq, i):
                open_delim = tq
                i += 3
                opened = True
                break
        if not opened:
            i += 1
    return open_delim


def _fixture_body(src: str, name: str) -> str:
    """The source text of a fixture's body (def line through the next
    same-level def/statement) — the evidence window for in-body restores.
    The break condition uses the def's OWN indent so a class-method
    fixture's window is bounded by the next method (a later method's
    restore cannot launder a class-method fixture's pop-without-restore)."""
    lines = src.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)(?:async\s+)?def\s+\w+', line)
        if m and re.search(r'\bdef\s+' + re.escape(name) + r'\b', line):
            def_indent = len(m.group(1))
            body = [line]
            for j in range(i + 1, len(lines)):
                nl = lines[j]
                if (nl.strip() and not nl.strip().startswith("@")
                        and len(nl) - len(nl.lstrip()) <= def_indent):
                    break
                body.append(nl)
            return "\n".join(body)
    return ""


def _restoration_violations(files) -> list[str]:
    """Return human-readable violations for RAW (non-monkeypatch)
    TORTOISE_DB_URI mutations at module scope or inside fixture bodies that
    lack a paired restoration mechanism (#2084 — the #2062 class).

    Evidence per leak-scope site:
      * top-level site  → the FILE's TOP-LEVEL code has a save-restore (a
        URI var captured via os.environ.get and assigned back outside any
        function — the module-level live-probe pattern). Function/fixture
        bodies are excluded so an unrelated in-test save-restore cannot
        launder a stray import-time leak.
      * fixture site    → the FIXTURE BODY assigns back a captured URI var
        (the capture may live in the fixture body or at module top, e.g.
        the test_hnsw_vector_index _graph fixture).
      * bare-instance   → a fixture body that instantiates
        pytest.MonkeyPatch() must call .undo() in its body (pytest auto-
        undoes only the fixture-PARAM form; a bare instance with a forgotten
        undo reproduces the #2062 leak through the post-fix mechanism).

    monkeypatch.X (fixture-param) sites are skipped entirely — pytest
    auto-undoes them at teardown. Subprocess-code strings (triple-quoted),
    comment lines, and self-restoring inline assignments are skipped — they
    never mutate this process's URI. The save/restore EVIDENCE census is
    filtered the same way (comment/docstring text cannot launder evidence).
    Static heuristic by design: catches the pop-without-restore CLASS, not
    perfect dataflow.
    """
    violations: list[str] = []
    for fname, src in files:
        # #9061: `_scope_map` (`_code_only`'s one column-aligned pass plus
        # the per-line bracket scan) is the whole cost of this guard, so it has
        # to run on the modules that can hold a violation, not on the size of
        # the tree. The skip is SOUND BY CONSTRUCTION, which is why it may
        # narrow the analysis at all: its two predicates are this guard's OWN —
        # a module whose source mentions
        # neither `TORTOISE_DB_URI` (the raw-mutation census below) nor a bare
        # `pytest.MonkeyPatch(` (the URI-agnostic bare-instance arm) cannot hold
        # a site this guard would report, so nothing it would flag is skipped.
        # Cost is the secondary argument, and on this shared box the absolute
        # is not even stable: the whole file measured 20s, 221s and 261s on
        # 2026-10-19 over the same population — fleet load, not tree size. The
        # skip is therefore justified by what it cannot hide, not by what it
        # saves. Cost scales with SITES.
        #
        # BOTH strings are required here: the bare-instance check below
        # (`pytest.MonkeyPatch()` without `.undo()`) is URI-AGNOSTIC — a fixture
        # that sets TORTOISE_TEST_DOCKER_PORT through a bare MonkeyPatch has no
        # `TORTOISE_DB_URI` in its source and still leaks if it never undoes. A
        # URI-only skip silently stopped looking at the FOUR modules that use
        # `pytest.MonkeyPatch(` AND carry no URI string today (18 capture an
        # instance across the population; the other 14 mention the URI and are
        # let in by that alone — no violation among any of them yet, which is
        # what made it a latent hole rather than a live one), which is why
        # `test_the_cost_skip_cannot_hide_a_uri_agnostic_leak` plants a URI-free
        # leak and asserts it is still reported.
        if "TORTOISE_DB_URI" not in src and not _MONKEYPATCH_INST_RE.search(src):
            continue
        lines = src.split("\n")
        scopes = _scope_map(src)
        # leak-scope raw sites: (lineno, scope_kind, scope_name)
        sites: list[tuple[int, str, str | None]] = []
        for m in _RAW_URI_MUTATION_RE.finditer(src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            lineno = src.count("\n", 0, m.start()) + 1
            col = m.start() - line_start
            line = lines[lineno - 1]
            if line.lstrip().startswith("#"):
                continue  # comment — never executes
            if _in_triple_quoted_string(src, lineno, col):
                continue  # child-env code — never this process
            if m.lastgroup == "assign" and _URI_GET_RE.search(line):
                continue  # self-restoring inline assignment
            scope_kind, scope_name = _scope_at(scopes, lineno)
            if scope_kind in ("top", "fixture", "fn"):
                sites.append((lineno, scope_kind, scope_name))
        # save-restore evidence: file-wide for fixture-site captures; the
        # TOP-LEVEL-only census for top-level sites. Both censuses skip
        # comment lines and string regions (non-executable text cannot
        # count as restoration evidence).
        saved: set[str] = set()
        for m in _URI_SAVE_RE.finditer(src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            lineno = src.count("\n", 0, m.start()) + 1
            col = m.start() - line_start
            line = lines[lineno - 1]
            if line.lstrip().startswith("#") \
                    or _in_triple_quoted_string(src, lineno, col):
                continue
            saved.add(m.group(1))
        top_saved: set[str] = set()
        for m in _URI_SAVE_RE.finditer(src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            lineno = src.count("\n", 0, m.start()) + 1
            col = m.start() - line_start
            line = lines[lineno - 1]
            if line.lstrip().startswith("#") \
                    or _in_triple_quoted_string(src, lineno, col):
                continue
            if _scope_at(scopes, lineno)[0] == "top":
                top_saved.add(m.group(1))
        top_restored: set[str] = set()
        for m in _URI_RESTORE_RE.finditer(src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            lineno = src.count("\n", 0, m.start()) + 1
            col = m.start() - line_start
            line = lines[lineno - 1]
            if line.lstrip().startswith("#") \
                    or _in_triple_quoted_string(src, lineno, col):
                continue
            if _scope_at(scopes, lineno)[0] == "top":
                top_restored.add(m.group(1))
        file_restored = top_saved & top_restored
        # Module-level autouse save/restore fixture: bounds EVERY test in the
        # file (runs setup+teardown around each test at any scope), so a raw
        # fn-scope pop inside a test body is restored before the next test —
        # the test_tortoise_client._restore_db_env pattern (#2084 review P2).
        autouse_bounded = False
        for m in re.finditer(
                r'@pytest\.fixture\([^)]*autouse\s*=\s*True[^)]*\)', src):
            # find the def name following this decorator
            after = src[m.end():]
            dm = re.match(r'\s*(?:async\s+)?def\s+(\w+)', after)
            if not dm:
                continue
            body = _fixture_body(src, dm.group(1))
            saved_in_body = {g for g in _URI_SAVE_RE.findall(body)}
            restored_in_body = {g for g in _URI_RESTORE_RE.findall(body)}
            if saved_in_body & restored_in_body:
                autouse_bounded = True
                break
        for lineno, scope_kind, scope_name in sites:
            if scope_kind == "top":
                if not file_restored:
                    violations.append(
                        f"{fname}:{lineno}: top-level raw TORTOISE_DB_URI "
                        "mutation with no TOP-LEVEL save-restore (capture "
                        "via os.environ.get + assign back outside any "
                        "function) — restore the URI (try/finally or "
                        "MonkeyPatch) or the leak poisons every later test "
                        "in the pytest process (#2062)")
            elif scope_kind == "fixture":
                body = _fixture_body(src, scope_name)
                in_body_restored = {m.group(1)
                                    for m in _URI_RESTORE_RE.finditer(body)}
                if not (saved & in_body_restored):
                    violations.append(
                        f"{fname}:{lineno}: fixture {scope_name!r} performs a "
                        "raw TORTOISE_DB_URI mutation but never restores it "
                        "in its body (no save-restore of a captured URI var) "
                        "— a module-scoped pop-without-restore silently "
                        "flips every later docker-lane test to embedded "
                        "(#2062). Save the old value and restore it in a "
                        "finally/after yield, or use monkeypatch/"
                        "pytest.MonkeyPatch + undo.")
            else:  # fn — test/helper body
                body = _fixture_body(src, scope_name)
                in_body_restored = {m.group(1)
                                    for m in _URI_RESTORE_RE.finditer(body)}
                if not (saved & in_body_restored) and not autouse_bounded:
                    violations.append(
                        f"{fname}:{lineno}: fn {scope_name!r} performs a raw "
                        "TORTOISE_DB_URI mutation with no in-body restore "
                        "and no module-level autouse save/restore fixture "
                        "bounding every test — the pop leaks into later "
                        "tests in the pytest process (#2084 review P2). "
                        "Restore in-fn (finally) or bound the module with an "
                        "autouse save/restore fixture.")
        # bare-instance check: pytest.MonkeyPatch() in a fixture body must
        # be paired with .undo() in the same body; at module top it must be
        # paired with .undo() somewhere in the file (pytest auto-undoes
        # ONLY the fixture-param form).
        for m in _MONKEYPATCH_INST_RE.finditer(src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            lineno = src.count("\n", 0, m.start()) + 1
            col = m.start() - line_start
            if _in_triple_quoted_string(src, lineno, col):
                continue
            scope_kind, scope_name = _scope_at(scopes, lineno)
            if scope_kind == "fixture":
                body = _fixture_body(src, scope_name)
                if ".undo(" not in body:
                    violations.append(
                        f"{fname}:{lineno}: fixture {scope_name!r} creates "
                        "pytest.MonkeyPatch() but never calls .undo() in its "
                        "body — pytest auto-undoes ONLY the fixture-param "
                        "form; a bare instance with a forgotten undo leaks "
                        "the TORTOISE_DB_URI mutation into every later test "
                        "(#2084)")
            elif scope_kind == "top" and ".undo(" not in src:
                violations.append(
                    f"{fname}:{lineno}: top-level pytest.MonkeyPatch() "
                    "without any .undo() in the file — bare instances are "
                    "never auto-undone; the URI mutation leaks at import "
                    "(#2084)")
    return violations


def _non_exempt_test_files():
    """(name, src) for every scan-relevant test module — the same population
    as the declaration guard (`_scanned_modules`), so the two guards cannot
    narrow independently (#9061)."""
    yield from _scanned_modules()


def test_uri_mutations_restored():
    """#2084: every RAW (non-monkeypatch) TORTOISE_DB_URI mutation at module
    scope or inside a fixture body must be paired with a restoration
    mechanism. The declaration guard (above) cannot see a declared-but-
    unrestored pop — the #2062 incident class: module-scoped autouse
    fixtures popped the URI at setup AND teardown, stayed green under
    declaration, and poisoned every docker-lane test later in the same
    pytest process. This ADDITIONAL guard reds on that class; it is not a
    replacement for the declaration census."""
    violations = _restoration_violations(_non_exempt_test_files())
    assert not violations, (
        "unrestored module-scope/fixture TORTOISE_DB_URI mutation(s) — pair "
        "each with a save-restore (capture os.environ.get + assign back in "
        "a finally/after yield) or monkeypatch auto-undo:\n"
        + "\n".join(violations))


def test_a_def_inside_a_docstring_is_not_a_function_start():
    """A `def`-looking line inside a STRING is copy, not a function start.

    `_code_only` blanks string/comment spans before a line is matched, so a
    docstring body cannot open a scope. Without that blanking the line below is
    matched as a real `def`, and the scope map then contains a function that
    exists only as text — statements up to the next dedent are attributed to
    `fake` rather than to `test_first`, so any report about them names a scope
    the file does not have (the wrong-name class #8487 produced).

    Discriminating by construction: a counter that blanks only BRACKETS and
    not string literals — the pre-#8487 raw-line count — maps line 3 to
    ``("fn", "fake")``, while the `_code_only`-based map keeps it in
    ``test_first``.
    """
    src = (
        "def test_first():\n"
        "    \"\"\"Doc.\n"
        "\n"
        "    def fake():\n"
        "    \"\"\"\n"
        "    os.environ[\"TORTOISE_DB_URI\"] = 'docker://x/y'\n"
    )
    scopes = _scope_map(src)
    assert scopes[3] == ("fn", "test_first"), scopes[3]
    assert ("fn", "fake") not in scopes, scopes


def test_a_fixture_decorator_inside_a_docstring_is_not_a_fixture():
    """`@pytest.fixture` quoted in a docstring must not arm the decorator state.

    `_code_only` gates the decorator test too (`stripped.startswith("@")`), so a
    docstring that MENTIONS the decorator cannot set ``pending_fixture``. When
    it does, the NEXT real function is classified ``fixture``, and the guard's
    fixture arm then demands a ``.undo()`` from a function that is not a
    fixture — a false RED on correct code.

    Discriminating by construction: routed through the raw line, the guard
    reports ``synthetic_quoted_decorator.py:10: fixture 'helper' creates
    pytest.MonkeyPatch() but never calls .undo()``; through `_code_only` the
    file is clean.
    """
    src = (
        "def test_first():\n"
        "    \"\"\"Doc.\n"
        "\n"
        "    @pytest.fixture\n"
        "    \"\"\"\n"
        "    pass\n"
        "\n"
        "\n"
        "def helper():\n"
        "    mp = pytest.MonkeyPatch()\n"
        "    mp.setenv(\"TORTOISE_DB_URI\", 'docker://x/y')\n"
    )
    scopes = _scope_map(src)
    assert scopes[8] == ("fn", "helper"), (
        f"a quoted decorator must not reclassify the next def — got {scopes[8]}")
    assert _restoration_violations([("synthetic_quoted_decorator.py", src)]) == [], (
        "a helper preceded by a QUOTED decorator was policed as a fixture — "
        "the docstring armed pending_fixture")


def test_code_only_stays_column_aligned_after_a_mid_line_triple_close():
    """A triple-quoted region that CLOSES mid-line, with code after it, must
    not shift that code's column (#8937 review r7, nit b).

    `_code_only` padded only the 3 delimiter columns when closing, which
    collapsed the region's content: on a line that starts INSIDE a region and
    resumes code after the closing delimiter, the padding was 3 columns short
    per collapsed span, so `_scope_map` read a smaller indent than the code's
    real column — the statement could be attributed to an enclosing function
    instead of closing it. Unreachable in this tree today; pinned so it stays
    that way."""
    line = 'abc""" + 1'
    code, triple = _code_only(line, '"""')
    assert triple is None
    assert len(code) == len(line), (
        "the code-only text is not column-aligned with the line", code)
    assert len(code) - len(code.lstrip()) == line.index("+"), (
        "the resumed code was shifted left of its real column", code)


def test_the_two_triple_quote_lexers_agree_on_escaped_delimiters():
    """`_code_only` and `_toggle_triple_quotes` answer different questions and
    must not drift on the ESCAPE rule (#8937 review r7, nit a).

    A backslash-escaped delimiter does not close a region in Python, and
    `_toggle_triple_quotes` already honoured that. `_code_only` used a bare
    `str.find`, so it closed on the escaped delimiter and then treated the
    trailing `\\"\\"\\"` as a region OPENER — two answers for one line."""
    line = 'z = """a\\""" + 1"""'
    assert _toggle_triple_quotes(line, None, None) is None, (
        "the position lexer must not close on an escaped delimiter")
    code, triple = _code_only(line, None)
    assert triple is None, (
        "the code lexer closed on the ESCAPED delimiter and reopened at the "
        "real one — it disagrees with _toggle_triple_quotes", triple)
    assert len(code) == len(line)
    assert code.strip() == "z ="


def test_scope_map_reads_code_brackets_only():
    """#8995: a bracket inside a string literal is COPY, not an open
    expression — and counting it poisoned every `def` after it.

    This is the #8995 regression reproduced as a minimal source: an assert on a
    literal containing an unmatched `(` (test_time_aware_sdk_2520.py:115,
    `assert "(as of" in t`) left the scope map's continuation counter open for
    the rest of the file. Every later `def` was classified as a continuation of
    the function above it, so `_ku_sdk`'s `finally` restore landed outside
    `test_off_path_embeds_the_bare_query`'s evidence window and
    ``test_uri_mutations_restored`` reded — naming the WRONG function — on a
    mutation that is restored exactly as the guard requires. Pre-fix, the
    middle assertion below fails: `second` is reported as `first`.
    """
    src = (
        'import os\n'
        'def first():\n'
        '    assert "(unbalanced" in _text()\n'
        '\n'
        '\n'
        'def second():\n'
        '    old = os.environ.get("TORTOISE_DB_URI")\n'
        '    os.environ["TORTOISE_DB_URI"] = _uri()\n'
        '    try:\n'
        '        yield\n'
        '    finally:\n'
        '        os.environ["TORTOISE_DB_URI"] = old\n'
    )
    scopes = _scope_map(src)
    assert scopes[1] == ("fn", "first"), scopes[1]
    assert scopes[5] == ("fn", "second"), (
        "the def after a string literal holding an unmatched bracket must be "
        f"its OWN scope, not a continuation of `first` — got {scopes[5]}")
    assert _restoration_violations([("synthetic_8995.py", src)]) == [], (
        "a mutation restored in a finally must stay green even when an earlier "
        "line carries an unmatched bracket inside copy")
    # Falsifiability: the same source WITHOUT the restore must still red, so the
    # assertion above cannot pass by the guard going blind to this shape.
    unrestored = src.replace('os.environ["TORTOISE_DB_URI"] = old\n', '')
    assert _restoration_violations([("synthetic_8995.py", unrestored)]), (
        "the guard must still catch an unrestored mutation in the same shape")


def test_a_comment_cannot_open_a_string_or_freeze_the_scope_map():
    """A `#` comment is COPY for both lexers (#8937 review — the COMMENT arm
    had no pin left once the interim raw-line counter was replaced).

    Two arms, each discriminating against a DIFFERENT pre-fix shape:

    * `_code_only` truncates at `#` BEFORE it looks at quotes, so a comment
      that MENTIONS a triple quote cannot open a region. Without that branch
      the `\"\"\"` in the comment sets the triple state, every following line
      is blanked as string content, and the next real `def` is not recognised
      — so statements after it are attributed to the function above it.
    * the continuation counter runs over `_code_only`'s output, so an
      unmatched `(` in a comment cannot freeze it for the rest of the file —
      the raw-line count (#8487/#8995) does freeze it. That arm is observed on
      the `def` AFTER the comment line, never on the comment line itself:
      `_scope_map` assigns a line's scope BEFORE applying that line's bracket
      delta, so a frozen counter can only show up downstream. Pre-fix scope
      maps are identical to this one for the comment LINE, which is why the
      pin here is `scopes[5]`.
    """
    line = '    note = 1  # a """ triple quote and a ( bracket in prose'
    code, triple = _code_only(line, None)
    assert triple is None, (
        f"a triple quote inside a COMMENT opened a string region — {code!r}")
    assert "#" not in code and "(" not in code and '"""' not in code, (
        f"comment content survived into the code text — {code!r}")
    assert len(code) == len(line), (
        "blanking the comment shifted the column alignment", code)
    assert code.strip() == "note = 1", code

    src = (
        'def _helper():\n'
        '    note = 1  # a """ triple quote and a ( bracket in prose\n'
        '    return note\n'
        '\n'
        '\n'
        'def second():\n'
        '    return 2\n'
    )
    scopes = _scope_map(src)
    assert scopes[1] == ("fn", "_helper"), scopes[1]
    assert scopes[5] == ("fn", "second"), (
        "comment copy swallowed the next def — it was attributed to "
        f"`_helper` — got {scopes[5]}")


def test_scope_map_ignores_an_escaped_brace_in_an_fstring():
    """`{{` in an f-string is ONE brace of COPY, not an open bracket (#8995).

    A `tokenize`-span counter sees one column for the `FSTRING_MIDDLE` that
    holds `{{`, so the SECOND brace reaches the counter as code and opens a
    paren nothing closes: `f"a {{ b"` nets `+1`. Measured on this tree (the
    whole guard scan set — every `_scanned_modules()` module — with the two
    scope maps diffed against the tokenize-span counter), that mislabelled 8
    files / 9,263 lines from their first `{{` — and a COMPLIANT test appended
    downstream reded with two violations naming the wrong function.
    `_code_only` blanks the whole literal, escaped braces included.

    Only a NET-POSITIVE surplus is observable: `}}` nets `-1` and the clamp
    absorbs it, so a `}}` line alone cannot discriminate (a `{{`/`}}`-balanced
    line is invisible to the clamp as well). This source therefore carries the
    `{{` arm ONLY.
    """
    src = (
        'import os\n'
        'def first():\n'
        '    assert f"a {{ b" in _text()\n'
        '\n'
        '\n'
        'def second():\n'
        '    old = os.environ.get("TORTOISE_DB_URI")\n'
        '    os.environ["TORTOISE_DB_URI"] = _uri()\n'
        '    try:\n'
        '        yield\n'
        '    finally:\n'
        '        os.environ["TORTOISE_DB_URI"] = old\n'
    )
    scopes = _scope_map(src)
    assert scopes[5] == ("fn", "second"), (
        "an escaped brace in copy must not freeze the continuation counter — "
        f"got {scopes[5]}")
    assert _restoration_violations([("synthetic_8995_f.py", src)]) == [], (
        "a mutation restored in a finally must stay green when an earlier line "
        "carries `{{` inside an f-string")


def test_code_only_carries_a_backslash_continued_literal():
    """A single-quoted literal continued with a backslash spans LINES (r11 P2).

    `_code_only` is a per-line lexer, so it carries the open quote in its state
    or the continuation line reads as CODE. Measured before the carry:
    ``q = "MATCH (n )\\`` followed by an INDENTED ``    (foo"`` leaves the
    ``(`` counted and a later module-level statement attributed to the function
    above it — HIDING it, the #8995 class rebuilt inside its own fix. At column
    0 the shape over-reports instead (the continuation closes the function, so
    its body reads as module scope), which is why this pin uses the INDENTED
    form on purpose.
    """
    src = (
        'def f():\n'
        '    q = "MATCH (n )\\\n'
        '    (foo"\n'
        '    return 1\n'
        '\n'
        '\n'
        'def g():\n'
        '    pass\n'
    )
    compile(src, "<test>", "exec")  # the shape is VALID code, not a stray
    scopes = _scope_map(src)
    assert scopes[6] == ("fn", "g"), (
        "the continuation line was read as code and froze the scope map — "
        f"got {scopes[6]}")
    line = '    q = "MATCH (n )\\'
    code, state = _code_only(line, None)
    assert len(code) == len(line), code
    assert state == ("single", '"'), state


def test_an_unterminated_literal_does_not_swallow_the_rest_of_the_file():
    """A source that does not PARSE must not carry a literal across it (r11 P2).

    The carry is gated on `compile()`: in a source that parses, an unclosed
    single-quoted literal at end of line is necessarily a backslash
    continuation, but in one that does NOT parse it proves nothing — and
    honouring it attributes every later line to the function that opened the
    literal, HIDING a top-level mutation the raw count surfaces. Fail closed.

    Each shape below is a SyntaxError; in every one the module-level statement
    after it must stay `("top", None)`. Non-vacuous against the gate: with the
    carry enabled unconditionally, all five arms move to `('fn','f')`.
    """
    strays = (
        '    x = "oops\n',            # no trailing backslash
        "    x = \"oops\\\\\n",          # EVEN run: an escaped backslash, continues nothing
        '    x = "oops\\ \n',        # whitespace after the backslash
        '    x = "oops (\n',          # a bracket inside the stray literal
        '    x = "oops\\\n',         # ODD run, but the literal never closes
    )
    for stray in strays:
        src = 'def f():\n' + stray + 'import os\nos.environ.pop("TORTOISE_DB_URI")\n'
        try:
            compile(src, "<test>", "exec")
        except SyntaxError:
            pass
        else:  # pragma: no cover - the shapes are chosen to be unparsable
            raise AssertionError(f"{stray!r} parses; the arm proves nothing")
        scope = _scope_map(src)[2]
        assert scope == ("top", None), (
            f"{stray!r} carried across an unparsable source and attributed a "
            f"top-level mutation to the function above it — got {scope}")


def test_pep701_nested_quote_is_a_KNOWN_limitation():
    """PEP-701 nesting is NOT handled, and this pin says so out loud (r11 P2).

    3.12 accepts ``f"{d["a(b"]}"`` and the compiler reads it, but `_code_only`
    closes the literal at the NESTED quote, so that replacement field's
    brackets are counted as code and everything after the line is attributed to
    the scope above it. Documented in `_code_only`'s docstring; no file in this
    tree uses the shape today.

    If this test FAILS, the lexer gained nesting awareness: delete this pin and
    the docstring's KNOWN LIMIT paragraph, and assert the correct scope here.
    """
    src = (
        'def f():\n'
        '    d = {"a(b": 1}\n'
        '    x = f"{d["a(b"]}"\n'
        '\n'
        '\n'
        'def g():\n'
        '    pass\n'
    )
    compile(src, "<test>", "exec")  # PEP-701 is valid 3.12
    scopes = _scope_map(src)
    assert scopes[6] == ("fn", "f"), (
        "the PEP-701 shape is now handled — see this test's docstring")
    code, _ = _code_only('    x = f"{d["a(b"]}"', None)
    assert "(" in code, (
        "the nested quote no longer leaks the bracket — update the docstring")


def test_module_scoped_pop_without_restore_reds():
    """#2084 negative: the pre-#2073 test_index_restore pattern — a
    module-scoped autouse fixture that pops TORTOISE_DB_URI at setup and
    never restores — MUST red under the restoration guard. This is the
    exact class that caused 15 deterministic CI failures on 2026-09-01.
    Also pins the mirror-image forms: a fixture that SETS the URI and never
    restores, a del-form, and a module-top pop with no top-level
    save-restore (all leak into later tests in the same pytest process)."""
    pop_without_restore = (
        'import os\n'
        'import pytest\n'
        '\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'def _embedded_local_file_lane():\n'
        '    os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    os.environ.pop("TORTOISE_TEST_EXPECT_URI", None)\n'
        '    yield\n'
        '    os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    os.environ.pop("TORTOISE_TEST_EXPECT_URI", None)\n'
    )
    set_without_restore = (
        'import os\n'
        'import pytest\n'
        '\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'def _docker_lane():\n'
        '    os.environ["TORTOISE_DB_URI"] = _working_uri\n'
        '    yield\n'
    )
    del_without_restore = (
        'import os\n'
        'import pytest\n'
        '\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'def _embedded_local_file_lane():\n'
        '    del os.environ["TORTOISE_DB_URI"]\n'
        '    yield\n'
    )
    top_level_pop = (
        'import os\n'
        'os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    os_alias_pop = (
        'import os as _os\n'
        'import pytest\n'
        '\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'def _embedded_local_file_lane():\n'
        '    _os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    yield\n'
    )
    laundered_top_pop = (
        'import os\n'
        'os.environ.pop("TORTOISE_DB_URI", None)\n'
        'def test_launder():\n'
        '    _old = os.environ.get("TORTOISE_DB_URI")\n'
        '    os.environ["TORTOISE_DB_URI"] = _old\n'
    )
    laundered_class_fixture = (
        'import os\n'
        'import pytest\n'
        '_old_uri = os.environ.get("TORTOISE_DB_URI")\n'
        'class TestX:\n'
        '    @pytest.fixture(autouse=True)\n'
        '    def _lane(self):\n'
        '        os.environ.pop("TORTOISE_DB_URI", None)\n'
        '        yield\n'
        '    def test_launder(self):\n'
        '        os.environ["TORTOISE_DB_URI"] = _old_uri\n'
    )
    nested_delimiter_line = (
        'import os\n'
        '_SNIPPET = """has \'\'\' inside"""\n'
        'os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    top_level_mp_without_undo = (
        'import pytest\n'
        'mp = pytest.MonkeyPatch()\n'
        'mp.delenv("TORTOISE_DB_URI", raising=False)\n'
    )
    # #2084 review P2: an async fixture with a pop-without-restore must red
    # (pre-fix the scope parser missed `async def` → body classified "top"
    # and could be laundered green by an unrelated module-level probe).
    async_fixture_pop_no_restore = (
        'import os\n'
        'import pytest\n'
        '_old_uri = os.environ.get("TORTOISE_DB_URI")\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'async def _embedded_local_file_lane():\n'
        '    os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    yield\n'
    )
    # #2084 review P2: a raw pop inside a TEST BODY with no in-fn restore
    # and no module-level autouse save/restore fixture must red (the #2062
    # leak mechanism one scope level down).
    fn_scope_pop_no_restore = (
        'import os\n'
        'def test_leak():\n'
        '    os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    # fn-scope pop in a module with an autouse save/restore fixture bounding
    # every test is SAFE (the test_tortoise_client pattern) — must stay green.
    fn_scope_pop_autouse_bounded = (
        'import os\n'
        'import pytest\n'
        '@pytest.fixture(autouse=True)\n'
        'def _restore_db_env():\n'
        '    saved_uri = os.environ.get("TORTOISE_DB_URI")\n'
        '    yield\n'
        '    if saved_uri is None:\n'
        '        os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    else:\n'
        '        os.environ["TORTOISE_DB_URI"] = saved_uri\n'
        'def test_leak():\n'
        '    os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    for label, src in (
            ("pop-without-restore", pop_without_restore),
            ("set-without-restore", set_without_restore),
            ("del-without-restore", del_without_restore),
            ("top-level pop no save-restore", top_level_pop),
            ("os-alias pop (test_hnsw pattern)", os_alias_pop),
            ("top pop laundered by in-test save-restore", laundered_top_pop),
            ("class-method fixture laundered by later method",
             laundered_class_fixture),
            ("top pop after same-line nested-delimiter string",
             nested_delimiter_line),
            ("top-level MonkeyPatch without undo",
             top_level_mp_without_undo),
            ("async fixture pop-without-restore (P2)",
             async_fixture_pop_no_restore),
            ("fn-scope pop without restore (P2)",
             fn_scope_pop_no_restore)):
        violations = _restoration_violations(
            [("test_index_restore.py", src)])
        assert violations, (
            f"{label} must be flagged by the restoration guard — the "
            "#2062 class slipped through")
    # the autouse-bounded fn-scope pop must NOT red (test_tortoise_client
    # pattern — the module-level fixture restores around every test)
    bounded_violations = _restoration_violations(
        [("test_tortoise_client.py", fn_scope_pop_autouse_bounded)])
    assert not bounded_violations, (
        "fn-scope pop bounded by a module-level autouse save/restore "
        "fixture must stay green (#2084 review P2): "
        + "; ".join(bounded_violations))


def test_bare_monkeypatch_without_undo_reds():
    """#2084 negative: pytest auto-undoes ONLY the fixture-param form — a
    fixture that instantiates pytest.MonkeyPatch() and forgets .undo()
    leaks the mutation just like the #2062 raw pop. The restoration guard
    must red on the bare instance without an in-body undo (the #2073 fixed
    pattern stays green only when mp.undo() is present)."""
    mp_without_undo = (
        'import pytest\n'
        '\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'def _embedded_local_file_lane():\n'
        '    mp = pytest.MonkeyPatch()\n'
        '    mp.delenv("TORTOISE_DB_URI", raising=False)\n'
        '    yield\n'
    )
    violations = _restoration_violations(
        [("test_index_restore.py", mp_without_undo)])
    assert violations, (
        "pytest.MonkeyPatch() without .undo() must be flagged — the "
        "forgotten-undo variant of the #2062 class")


def test_battery_fixture_undo_is_evidence_not_comment():
    """#2558 pin: the converted battery _force_embedded_lane fixtures
    (bare pytest.MonkeyPatch + mp.undo at module teardown) must keep the
    guard's bare-instance undo check FIRING on the REAL call — comment text
    inside the fixture body must not launder the evidence (a naive '.undo('
    substring scan would stay green if a comment mentioned the call and the
    real mp.undo() were later deleted, silently re-admitting the #2062
    leak class). Loads the real module source, strips the real call, and
    asserts the guard reds; the real source (with the call) stays green.
    (2026-09-09: grew 4 → 7 — #2674 converted executor_v2 +
    exposure_liveness; r1_seed was converted by the #2558 heal but never
    added to the pin.)"""
    battery_fixtures = ("test_battery_ep_outcome", "test_battery_executor_v2",
                        "test_battery_exposure_liveness", "test_battery_provenance",
                        "test_battery_r1_seed", "test_battery_seed_ingest",
                        "test_battery_write_channel")
    real = [(f"{name}.py", (_TESTS_ROOT / f"{name}.py").read_text(encoding="utf-8"))
            for name in battery_fixtures]
    # real source: the undo call is present and in the fixture body — green
    assert not _restoration_violations(real), (
        "converted battery fixtures with mp.undo() present must stay green")
    # strip the real call: the guard must RED even though the comments
    # mention the undo pattern — a comment is not a restoration mechanism
    stripped = []
    for fname, src in real:
        marker = "\n    mp.undo()\n"
        assert marker in src, f"mp.undo() call not found in {fname}"
        stripped.append((fname, src.replace(marker, "\n", 1)))
    violations = _restoration_violations(stripped)
    assert len(violations) == len(battery_fixtures), (
        "stripping the real mp.undo() must red ALL converted battery "
        "fixtures (comments must not launder the evidence): "
        + "; ".join(violations))


def test_restoration_patterns_green():
    """#2084 positive: the restoration forms that count as restored stay
    green — (a) pytest.MonkeyPatch auto-undo with .undo() (the #2073 fixed
    pattern), (b) module-level live-probe try/finally save-restore,
    (c) a fixture with module-top capture + in-body restore, and the
    non-leaks that must never flag: subprocess child-code strings
    (multi-line and single-line), `==` comparison asserts, comment lines,
    self-restoring inline assignments, and newline-spanning multiline
    mutations that are properly restored."""
    monkeypatch_fixture = (
        'import pytest\n'
        '\n'
        '@pytest.fixture(scope="module", autouse=True)\n'
        'def _embedded_local_file_lane():\n'
        '    mp = pytest.MonkeyPatch()\n'
        '    mp.delenv("TORTOISE_DB_URI", raising=False)\n'
        '    yield\n'
        '    mp.undo()\n'
    )
    probe_save_restore = (
        'import os\n'
        '_OLD_URI = os.environ.get("TORTOISE_DB_URI")\n'
        'try:\n'
        '    os.environ["TORTOISE_DB_URI"] = _DB_URI\n'
        'finally:\n'
        '    if _OLD_URI is not None:\n'
        '        os.environ["TORTOISE_DB_URI"] = _OLD_URI\n'
        '    else:\n'
        '        os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    top_capture_in_body_restore = (
        'import os\n'
        'import pytest\n'
        '_old_uri = os.environ.get("TORTOISE_DB_URI")\n'
        '\n'
        '@pytest.fixture(autouse=True)\n'
        'def _graph():\n'
        '    os.environ["TORTOISE_DB_URI"] = _working_uri\n'
        '    yield\n'
        '    if _old_uri is None:\n'
        '        os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    else:\n'
        '        os.environ["TORTOISE_DB_URI"] = _old_uri\n'
    )
    subprocess_string_multiline = (
        'import os\n'
        'def _spawn():\n'
        '    code = f"""\n'
        'import os, time\n'
        'os.environ.pop("TORTOISE_DB_URI", None)\n'
        '"""\n'
        '    subprocess.Popen([sys.executable, "-c", code])\n'
    )
    subprocess_string_singleline = (
        'import os\n'
        'def _spawn():\n'
        '    code = """os.environ.pop(\'TORTOISE_DB_URI\', None)"""\n'
        '    subprocess.Popen([sys.executable, "-c", code])\n'
    )
    equality_assert = (
        'import os\n'
        'assert os.environ["TORTOISE_DB_URI"] == "docker://x/y"\n'
    )
    comment_line = (
        'import os\n'
        '# os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    inline_self_restore = (
        'import os\n'
        'os.environ["TORTOISE_DB_URI"] = os.environ.get("TORTOISE_DB_URI") or ""\n'
    )
    multiline_save_restore = (
        'import os\n'
        '_old_uri = os.environ.get("TORTOISE_DB_URI")\n'
        'try:\n'
        '    os.environ["TORTOISE_DB_URI"] = \n'
        '        "docker://:pw@localhost:6379/g"\n'
        'finally:\n'
        '    os.environ["TORTOISE_DB_URI"] = \n'
        '        _old_uri\n'
    )
    probe_after_def = (
        'import os\n'
        'def _helper():\n'
        '    return 1\n'
        '_OLD_URI = os.environ.get("TORTOISE_DB_URI")\n'
        'try:\n'
        '    os.environ["TORTOISE_DB_URI"] = _DB_URI\n'
        'finally:\n'
        '    if _OLD_URI is not None:\n'
        '        os.environ["TORTOISE_DB_URI"] = _OLD_URI\n'
        '    else:\n'
        '        os.environ.pop("TORTOISE_DB_URI", None)\n'
    )
    os_alias_restore = (
        'import os as _os\n'
        '_old_uri = _os.environ.get("TORTOISE_DB_URI")\n'
        'try:\n'
        '    _os.environ["TORTOISE_DB_URI"] = _uri\n'
        'finally:\n'
        '    if _old_uri is None:\n'
        '        _os.environ.pop("TORTOISE_DB_URI", None)\n'
        '    else:\n'
        '        _os.environ["TORTOISE_DB_URI"] = _old_uri\n'
    )
    mixed_delimiter_string = (
        'import os\n'
        'def _spawn():\n'
        '    code = f"""\n'
        'note with \'\'\' quotes\n'
        'os.environ.pop("TORTOISE_DB_URI", None)\n'
        '"""\n'
        '    subprocess.Popen([sys.executable, "-c", code])\n'
    )
    for label, src in (
            ("MonkeyPatch auto-undo", monkeypatch_fixture),
            ("module probe save-restore", probe_save_restore),
            ("top capture + in-body restore", top_capture_in_body_restore),
            ("subprocess child-code string (multiline)",
             subprocess_string_multiline),
            ("subprocess child-code string (single-line)",
             subprocess_string_singleline),
            ("equality assert", equality_assert),
            ("comment line", comment_line),
            ("self-restoring inline assignment", inline_self_restore),
            ("multiline save-restore", multiline_save_restore),
            ("probe after a top-level def", probe_after_def),
            ("os-alias save-restore (test_hnsw pattern)",
             os_alias_restore),
            ("mixed-delimiter child-code string",
             mixed_delimiter_string)):
        violations = _restoration_violations([("smoke.py", src)])
        assert not violations, f"{label} must stay green: {violations}"


def test_declaration_regex_census_surface():
    """#2084 review pin: the declaration census's _MUTATION_RE must match
    every mutation form it claims to census — pop, delenv, setenv (incl.
    the multiline continuation form), direct assignment, and del. A
    regression that narrows the regex (e.g. dropping the pop alternative)
    would silently blind the declaration guard while the repo census stays
    green — this pins the regex surface directly."""
    forms = [
        'os.environ.pop("TORTOISE_DB_URI", None)',
        'monkeypatch.delenv("TORTOISE_DB_URI", raising=False)',
        'monkeypatch.setenv("TORTOISE_DB_URI", uri)',
        'monkeypatch.setenv(',
        'os.environ["TORTOISE_DB_URI"] = _uri',
        'del os.environ["TORTOISE_DB_URI"]',
        # _os alias: the declaration regex matches it via substring
        # (os.environ appears inside _os.environ) — pin that too.
        '_os.environ["TORTOISE_DB_URI"] = _uri',
    ]
    for form in forms:
        assert _MUTATION_RE.search(form), (
            f"_MUTATION_RE must census the form {form!r}")
    # and the restoration guard's RAW regex must NOT match the monkeypatch
    # fixture-param forms (auto-undoed by pytest, not raw leaks).
    for form in ('monkeypatch.delenv("TORTOISE_DB_URI")',
                 'monkeypatch.setenv("TORTOISE_DB_URI", uri)'):
        assert not _RAW_URI_MUTATION_RE.search(form), (
            f"_RAW_URI_MUTATION_RE must not flag the auto-undo form {form!r}")
    # Documented boundary (#2084 review P2): bare-instance call forms
    # (mp.delenv/mp.setenv — the #2073 fixed pattern in test_index_restore/
    # test_index_cli/test_export_cli/test_import_endpoint) are NOT censused
    # by the declaration regex; they are covered by the restoration guard's
    # undo requirement instead. Pin the boundary so a future widening does
    # not silently red those files, and a new mp.-form file is understood
    # to be censused by the restoration guard only.
    for form in ('mp.delenv("TORTOISE_DB_URI", raising=False)',
                 'mp.setenv("TORTOISE_DB_URI", uri)'):
        assert not _MUTATION_RE.search(form), (
            f"_MUTATION_RE intentionally does not census the bare-instance "
            f"call form {form!r} (restoration guard covers it)")


def test_declaration_path_rejects_undeclared():
    """#2084 review pin (requirement 5 — declaration check intact): the
    declaration matching logic must still reject an undeclared mutation
    (no allowlist entry / no matching declared regex) and accept the
    allowlisted files. Factored via _is_declared so the path is pinned
    without mutating the real repo census."""
    assert not _is_declared(
        "test_unknown.py", 'os.environ.pop("TORTOISE_DB_URI", None)')
    assert not _is_declared(
        "test_unknown.py", 'os.environ["TORTOISE_DB_URI"] = _uri')
    # allowlisted patterns still match their file's mutation lines
    assert _is_declared(
        "test_index_restore.py", 'os.environ.pop("TORTOISE_DB_URI", None)')
    assert _is_declared(
        "test_hosted_api.py", 'monkeypatch.delenv("TORTOISE_DB_URI")')
    assert _is_declared(
        "test_directional_impl.py", 'os.environ["TORTOISE_DB_URI"] = _uri')
    # #9061: a declaration is bound to ONE module. Before the bare-name lookup
    # fallback was removed, a same-named module in a subdirectory inherited its
    # top-level twin's declaration — a declaration written for one file
    # authorizing another, which is the census's own failure class one level up.
    assert not _is_declared(
        "sub/test_config.py", 'monkeypatch.delenv("TORTOISE_DB_URI")')
    assert not _is_declared(
        "e2e/test_config.py", 'monkeypatch.delenv("TORTOISE_DB_URI")')


def test_restoration_exemption_boundary():
    """#2084 pin: the carve-out exemption is applied by TEST-MODULE STEM
    (the documented TEST_NO_REDIRECT_STEMS registry, normalized so the
    documented carve-out mechanism actually fires) and is deliberately NOT
    blanket — allowlisted docker-lane files (test_index_* etc.) stay in the
    census. This is the "documented, not blanket" property from the issue:
    a carve-out's pops are no-ops in its URI-less lane, but the same pop in
    a docker-lane file must still red (pinned by
    test_module_scoped_pop_without_restore_reds via the non-exempt name)."""
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    exempt = _exempt_stems()
    census_names = {name for name, _ in _non_exempt_test_files()}
    # allowlisted docker-lane fixtures stay in the scan set …
    for allowlisted in ("test_index_restore.py", "test_index_cli.py",
                        "test_export_cli.py", "test_hosted_api.py",
                        "test_directional_impl.py"):
        assert allowlisted in census_names, (
            f"allowlisted docker-lane file {allowlisted} must stay in the "
            "restoration census")
    # … while carve-out stems are excluded by name. test_guard is a
    # TEST_NO_REDIRECT_STEMS-only stem (NOT in _EXEMPT_FROM_ENV_MUTATION_
    # GUARD) — it exercises the stem-normalized carve-out mechanism.
    assert "test_guard" in TEST_NO_REDIRECT_STEMS
    assert "test_guard" not in _EXEMPT_FROM_ENV_MUTATION_GUARD
    assert (_TESTS_ROOT / "test_guard.py").exists()
    for carve_out in ("test_guard", "test_reaper", "test_embedded_lifecycle",
                      "test_flip_gate", "test_ops_safety"):
        assert carve_out in exempt, (
            f"carve-out stem {carve_out} must be in the exemption set")
        assert f"{carve_out}.py" not in census_names, (
            f"carve-out file {carve_out}.py must not be in the restoration "
            "census")


# ── Empty-means-unset READ census (#3094) ──────────────────────────────────
# The #2589 → #2930 → #3094 class. `os.environ.get("TORTOISE_DB_URI", D)`
# reaches `D` only when the variable is ABSENT. CI's tier-2 legs export
# `TORTOISE_DB_URI=""` (present-but-empty) and epic #1647's lane contract is
# **empty == unset** (`is_db_uri("")` is False; the redirect seam is
# truthy-gated), so a set-but-empty value defeats the default exactly where it
# is needed: the probe builds a scheme-less URI, `FalkorProjection.from_uri`
# raises `ValueError: Unsupported scheme`, and the module skips with an
# availability-family reason. `tools/skip-guard.py` then reds every tier-2 PR
# — docs-only included — while the job's provisioned falkordb service is up.
#
# The honoring READ is `os.environ.get("TORTOISE_DB_URI") or <default>`: the
# empty string is falsy, so the fallback is reached. (An explicit
# `pytest.skip` on the empty value is the other honest shape — this census
# has nothing to say about it.)
#
# SCOPE BOUNDARY — deliberate, and pinned by the tests below:
#   * ROOTS: `tests/**` and `tools/longmem_eval/**`. The closed #2840 census
#     scanned `tests/**` only, so it was blind to its own
#     `tools/longmem_eval/run.py` fix site: a `tools/longmem_eval/`-only PR
#     selects the `eval` surface and never runs a `core`-only registration.
#     The guard file is therefore registered on `core`, `eval` AND
#     `onboarding`
#     (`test_empty_default_uri_census_is_registered_on_every_surface_it_covers`
#     is the ratchet). `onboarding` is needed because `tests/**` is NOT all
#     `core`: `tests/_html_links.py` is an EXACT `SOURCE_PATTERNS["onboarding"]`
#     entry, and a SOURCE_PATTERNS match REPLACES the `core` fallback in
#     `select()` — so a `tests/_html_links.py`-only change selects `onboarding`
#     and would otherwise skip a census that scans the file. `tools/**` OUTSIDE
#     `longmem_eval/` is not covered — those paths select `api`/`sdk`/`onboarding`,
#     so widening the roots means widening the registration in the same change.
#   * READ spellings only (`get` / `getenv`). The WRITE form
#     `os.environ.setdefault("TORTOISE_DB_URI", v)` — a no-op for the same
#     set-but-empty shape, and the cause of a real #2815 regression — is a
#     separate census with its own issue (#2857: `tortoise/__main__.py` + this
#     same `tools/longmem_eval/run.py`).
#   * the guard governs how the variable is READ, never whether a module may
#     skip (#2815 was resolved by declaring the skips; re-deciding run-vs-skip
#     is #3094's explicit non-goal).
_URI_ENV = "TORTOISE_DB_URI"

#: Inline exemption token. The text after it IS the permission slip — a marker
#: with no stated reason exempts nothing (pinned by the positive control).
_EMPTY_DEFAULT_MARKER = "empty-uri-default-ok:"
_EMPTY_DEFAULT_REASON_MIN = 10

#: The roots the census covers. `tools/longmem_eval/` is a
#: `SOURCE_PATTERNS["eval"]` entry and most of `tests/**` selects `core` —
#: EXCEPT `tests/_html_links.py`, an exact `SOURCE_PATTERNS["onboarding"]`
#: entry whose match REPLACES the `core` fallback, which is why the guard is
#: registered on `onboarding` too.
#:
#: NOT COVERED, ON PURPOSE: `graph-scripts/`. Measured 2026-10-13 at
#: `e797cbd41`: 14 reads of this exact class live there (`decide_licensing.py:43`
#: raises `ValueError: Unsupported scheme` AT IMPORT under
#: `TORTOISE_DB_URI=""`; `decide.py:147`, `parity_sample.py:267`,
#: `pre_migration_snapshot.py:261`, `context_removal_audit.py:143` and the
#: `--uri` argparse defaults of the backfill/fix scripts are the rest — the
#: counted baseline is #8520).
#:
#: Do NOT widen this tuple to reach them. Adding `graph-scripts` would red the
#: census on 14 files the tree rules **kept and frozen**
#: (`docs/plans/2026-08-05-remove-context-field-plan.md` §8.6, `OVERRIDES:`),
#: whose repair §8.6.1 withholds "pending the owner's ruling on #4812" — a gate
#: over them "may say 'no new debt', never 'class fixed'", and #4730 is that
#: gate's designated home. The boundary is a decision with a reason, not an
#: oversight: leave it until #4812 rules.
_CENSUS_ROOTS = ("tests", "tools/longmem_eval")


def _census_sources(
        roots: tuple[str, ...] = _CENSUS_ROOTS,
) -> list[tuple[str, str]]:
    """``[(repo-relative path, source)]`` for every ``*.py`` under ``roots``."""
    out: list[tuple[str, str]] = []
    for root in roots:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            out.append((path.relative_to(_REPO_ROOT).as_posix(),
                        path.read_text(encoding="utf-8", errors="replace")))
    return out


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Top-level ``NAME = "literal"`` / ``NAME: T = "literal"`` map.

    Lets the census resolve the spelling an author is most likely to copy —
    ``KEY = "TORTOISE_DB_URI"; os.environ.get(KEY, D)`` — instead of only the
    bare literal. Module scope only: a function-local rebinding cannot be
    resolved without dataflow analysis, and the census is deliberately static.
    """
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Constant)
                and isinstance(value.value, str)):
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                consts[tgt.id] = value.value
    return consts


def _is_uri_key(node, consts: dict[str, str]) -> bool:
    """True when ``node`` names the URI variable — literal or constant-held."""
    if isinstance(node, ast.Constant):
        return node.value == _URI_ENV
    if isinstance(node, ast.Name):
        return consts.get(node.id) == _URI_ENV
    return False


def _statically_empty_default(node) -> bool:
    """True only for a default a set-but-empty variable does not defeat.

    ``None`` (an omitted default, or ``default=None``) and the empty-string
    literal are the only defaults whose value is the same as what ``dict.get``
    yields for a set-but-empty variable — so their reachability cannot matter.
    Everything else is a default the ABSENT case reaches and the SET-BUT-EMPTY
    case silently loses. Fail closed.
    """
    if node is None:
        return True
    return isinstance(node, ast.Constant) and node.value in ("", None)


def _or_repaired_call_ids(tree: ast.Module) -> set[int]:
    """``id()``s of calls whose own result the enclosing ``or`` repairs.

    In ``a or b`` a falsy ``a`` falls through to ``b``, so the empty value a
    call hands on is repaired **only when that value is what makes the operand
    falsy**. The operand must therefore *be* the call, or reach it through a
    chain that carries the call's value outward unchanged:

    * ``os.environ.get(K, D) or D`` — the operand IS the call (honoring read);
    * ``os.environ.get(K, D).strip() or D`` — a method called on the result
      (:attr:`ast.Attribute` receiver via :attr:`ast.Call.func`).

    A call nested as an ARGUMENT (``_norm(call)``) or inside a CONTAINER
    (``{"u": call}``, ``(call,)``, ``[call]``) is NOT repaired: the wrapper's
    truthiness, not the call's, decides whether the fallback is reached — a
    dict / 1-tuple / list is truthy by construction, so the default is dead
    and the fallback unreachable. Walking every descendant of the operand (the
    pre-review shape) exempted exactly those.

    The LAST operand is never repaired — ``X or os.environ.get(K, D)`` still
    hands the empty value on.
    """
    repaired: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        for operand in node.values[:-1]:
            # Follow the receiver chain only: `x.attr` (Attribute.value) and a
            # method call on x (Call.func). A node of any other kind ends the
            # walk without exempting anything nested below it.
            current = operand
            while isinstance(current, (ast.Attribute, ast.Call)):
                if isinstance(current, ast.Call):
                    repaired.add(id(current))
                    if not isinstance(current.func, ast.Attribute):
                        # `_norm(call)`: the call is the WRAPPER, and the get()
                        # it encloses is an ARGUMENT — not reached.
                        break
                    current = current.func.value
                else:
                    current = current.value
    return repaired


def _marked_exempt(lines: list[str], node: ast.Call) -> bool:
    """True when the offending call carries a REASONED inline marker.

    The marker may sit on any line of the call's own span — the multi-line
    probe shape puts it on the key line. ``# empty-uri-default-ok:`` with no
    text after it is not a reason, so it exempts nothing.
    """
    for i in range(node.lineno - 1, min(node.end_lineno, len(lines))):
        if _EMPTY_DEFAULT_MARKER not in lines[i]:
            continue
        reason = lines[i].split(_EMPTY_DEFAULT_MARKER, 1)[1].strip()
        if len(reason) >= _EMPTY_DEFAULT_REASON_MIN:
            return True
    return False


def _empty_default_uri_reads(
        files: list[tuple[str, str]] | None = None,
) -> list[str]:
    """AST census: UNMARKED ``TORTOISE_DB_URI`` reads with a dead default.

    Flags every ``get``/``getenv`` spelling whose default a set-but-empty
    variable defeats — positional or ``key=``/``default=`` keyword, literal or
    module-constant key, literal or non-literal default (a docker-URI string,
    a module constant, an f-string, a name) — and is `or`-REPAIRED: the
    sanctioned ``os.environ.get("TORTOISE_DB_URI") or <default>`` shape stays
    green (see :func:`_or_repaired_call_ids`).

    ``files`` is an injectable ``[(relpath, source)]`` list (the positive
    control uses it); the default scans :data:`_CENSUS_ROOTS`.

    A file that does not parse is SKIPPED rather than reported: it cannot be
    collected by pytest either, so it reds the run on its own — the census
    does not need to double-report it. (Measured 2026-10-13: zero files in the
    scan set fail to parse, so the branch is a tolerance, not a live hole.)
    """
    if files is None:
        files = _census_sources()
    out: list[str] = []
    for rel, src in files:
        # Cheap prefilter: the census parses ~1,200 files, and only a few
        # percent mention the variable. Keyed on "TORTOISE_DB" (not the full
        # name) so an implicit concatenation — os.environ.get("TORTOISE_DB_"
        # "URI", D), which the parser folds into one constant — still reaches
        # the parser.
        if "TORTOISE_DB" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # Only the `get`/`getenv` calls can be candidates, so the (more
        # expensive) constant and `or`-repair passes run only on the files that
        # have at least one — most files that mention the variable do not.
        candidates = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (getattr(node.func, "attr", None)
                 or getattr(node.func, "id", None)) in ("get", "getenv")
        ]
        if not candidates:
            continue
        consts = _module_string_constants(tree)
        repaired = _or_repaired_call_ids(tree)
        lines = src.splitlines()
        for node in candidates:
            key = node.args[0] if node.args else None
            if key is None:
                for kw in node.keywords:
                    if kw.arg == "key":
                        key = kw.value
                        break
            if not _is_uri_key(key, consts):
                continue
            default = node.args[1] if len(node.args) > 1 else None
            if default is None:
                for kw in node.keywords:
                    if kw.arg == "default":
                        default = kw.value
                        break
            if _statically_empty_default(default):
                continue
            if id(node) in repaired:
                continue
            if _marked_exempt(lines, node):
                continue
            out.append(f"{rel}:{node.lineno}")
    return out


def test_no_empty_default_tortoise_db_uri_reads():
    """#3094 pin: no `TORTOISE_DB_URI` read may carry a dead default.

    Before this arm the class had been found twice (#2589, then #2930 as
    "#2589 redux") and fixed twice by hand, in a different file each time. A
    static census is what makes the third recurrence a review-time red instead
    of another red `python-ci-gate` on every open PR.

    A non-empty default is not automatically a bug in the file where it sits
    today — the affected modules DO skip on tier-2, and that policy was decided
    on #2815. It is a bug in the file where the #1436 skip-guard reds: the same
    read, copy-pasted into a module whose skip reason is not in the exempt
    family, tops the matrix. This census is therefore about the READ, not about
    permission to skip: a site whose empty case is handled by a declared skip
    carries `# empty-uri-default-ok: <reason>` and stays green.
    """
    sources = _census_sources()
    # A collapsed scan set would make the assertion below vacuous — the
    # failure mode a green census can hide.
    assert len(sources) > 100, (
        f"the census scan set collapsed to {len(sources)} file(s) — a green "
        "run here would prove nothing")
    offenders = _empty_default_uri_reads(sources)
    assert not offenders, (
        "these files read TORTOISE_DB_URI with a default that a set-but-empty "
        'value defeats (#2589/#2930/#3094). dict.get\'s default is reached only '
        "when the variable is ABSENT, and the lane contract is empty == unset "
        '(CI exports TORTOISE_DB_URI=""). Use the honoring read — '
        'os.environ.get("TORTOISE_DB_URI") or <default> — or, when the empty '
        "case is handled by a declared skip, mark the site "
        "`# empty-uri-default-ok: <reason>`:\n  " + "\n  ".join(offenders))


def test_empty_default_uri_reads_flags_the_class():
    """#3094 positive control: a census nobody has seen red is not a census.

    Pins the detector's surface — every in-scope spelling reds, every
    sanctioned shape stays green, and a marker exempts only WITH a reason — so
    a future narrowing (dropping `getenv`, literal-only keys, or the marker's
    reason requirement) cannot keep the repo census green while the class walks
    back in.
    """
    rows: list[tuple[str, bool]] = [
        ("import os", False),
        ("import tests._live_utils as lu", False),
        ('a = os.environ.get("TORTOISE_DB_URI", "docker://x")', True),
        ('b = os.getenv("TORTOISE_DB_URI", "docker://x")', True),
        ('c = os.environ.get("TORTOISE_DB_URI", default="docker://x")', True),
        ('d = os.environ.get(key="TORTOISE_DB_URI", default="docker://x")', True),
        ('e = os.environ.get("TORTOISE_DB_URI", lu.docker_uri("g"))', True),
        ('f = os.environ.get("TORTOISE_DB_URI", f"docker://{host}/g")', True),
        ('g = os.environ.get("TORTOISE_DB_URI", "docker://x").strip()', True),
        ('KEY = "TORTOISE_DB_URI"', False),
        ('h = os.environ.get(KEY, "docker://x")', True),
        ('i = os.environ.get("TORTOISE_DB_" "URI", "docker://x")', True),
        ("from os import getenv", False),
        ('j = getenv("TORTOISE_DB_URI", "docker://x")', True),
        ('ANN: str = "TORTOISE_DB_URI"', False),
        ('k = os.environ.get(ANN, "docker://x")', True),
        ("# sanctioned shapes stay green:", False),
        ('m = os.environ.get("TORTOISE_DB_URI")', False),
        ('n = os.environ.get("TORTOISE_DB_URI", "")', False),
        ('o = os.environ.get("TORTOISE_DB_URI", None)', False),
        ('p = os.environ.get("TORTOISE_DB_URI", "docker://x") '
         'or "docker://y"', False),
        ('q = (os.environ.get("TORTOISE_DB_URI", "docker://x").strip() or "y")',
         False),
        # A call nested in a truthy-by-construction CONTAINER is NOT repaired:
        # the wrapper is what the `or` inspects, so the default is dead.
        ('q1 = {"u": os.environ.get("TORTOISE_DB_URI", "docker://x")} or {}',
         True),
        ('q2 = (os.environ.get("TORTOISE_DB_URI", "docker://x"),) or ("y",)',
         True),
        ('q3 = [os.environ.get("TORTOISE_DB_URI", "docker://x")] or ["y"]',
         True),
        # ... nor is a call passed as an ARGUMENT to an arbitrary wrapper:
        # `_norm("")` need not be falsy, so the fallback need not be reached.
        ('q4 = _norm(os.environ.get("TORTOISE_DB_URI", "docker://x")) or "y"',
         True),
        # ... while a RECEIVER chain that carries the value outward stays
        # repaired (`call.strip()` on "" is "", so the `or` fires).
        ('q5 = os.environ.get("TORTOISE_DB_URI", "docker://x").strip() or "y"',
         False),
        ('q6 = [_norm(os.environ.get("TORTOISE_DB_URI", "docker://x"))] or []',
         True),
        ('r = os.environ.get("OTHER_VAR", "docker://x")', False),
        # Boundary: a presence-based read yields "" for a set-but-empty
        # variable, so its fallback is never reached either — but nothing is
        # handed to a caller, so there is no dead DEFAULT to census.
        ('s = os.environ["TORTOISE_DB_URI"] if "TORTOISE_DB_URI" in '
         'os.environ else "d"', False),
        ("# a marker with no reason is not an exemption:", False),
        ('t = os.environ.get("TORTOISE_DB_URI", "docker://x")  '
         '# empty-uri-default-ok:', True),
        ("# a REASONED marker is:", False),
        ('u = os.environ.get("TORTOISE_DB_URI", "docker://x")  '
         '# empty-uri-default-ok: #2815 skip declared; converting flips '
         'run-vs-skip', False),
    ]
    src = "\n".join(code for code, _ in rows)
    flagged = {
        int(hit.rsplit(":", 1)[-1])
        for hit in _empty_default_uri_reads([("tests/planted.py", src)])
    }
    expected = {i for i, (_, should_flag) in enumerate(rows, 1) if should_flag}
    assert flagged == expected, (
        "the #3094 census must flag every in-scope dead-default spelling and "
        f"nothing else — expected lines {sorted(expected)}, got "
        f"{sorted(flagged)}")


def test_empty_default_uri_census_is_registered_on_every_surface_it_covers():
    """#3094: a census wider than its registrations is blind where it matters.

    The #2840 census scanned `tests/**` and was registered in `core` alone, so
    its own `tools/longmem_eval/run.py` fix site selected `eval` and never ran
    it (the #2938/#4367 silent-drop class). This pins the property, not the
    declaration: it enumerates EVERY `*.py` under the census roots and requires
    that a PR changing that file alone actually puts this guard in the run set
    (directly, or via a `full` selection, which runs everything).

    Sampling a representative file per root (the pre-review shape) is exactly
    how `tests/_html_links.py` stayed uncovered: it is an EXACT
    `SOURCE_PATTERNS["onboarding"]` entry, so a change to it selects
    `onboarding` and REPLACES the `core` fallback — one arm per ROOT was not
    the complete set.
    """
    import sys

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from tools.ci_selection import load_manifest, select

    assert _CENSUS_ROOTS == ("tests", "tools/longmem_eval"), (
        "the census roots changed — re-check that the guard is registered on "
        "every surface they select (`_CENSUS_ROOTS`)")
    manifest = load_manifest()
    guard = Path(__file__).name
    covered = [
        path.relative_to(_REPO_ROOT).as_posix()
        for root in _CENSUS_ROOTS
        for path in sorted((_REPO_ROOT / root).rglob("*.py"))
    ]
    # A collapsed enumeration would make the loop below vacuous — the failure
    # mode a green ratchet can hide.
    assert len(covered) > 100, (
        f"the census roots enumerated only {len(covered)} file(s) — a green "
        "run here would prove nothing")
    # Named so the onboarding-SOURCE_PATTERNS regression stays diagnosable: a
    # per-root sample of two files never reached it.
    assert "tests/_html_links.py" in covered
    blind = []
    for path in covered:
        selection = select([path], "pull_request", manifest)
        if selection.get("full"):
            continue
        if guard not in (selection.get("test_files") or []):
            blind.append(f"{path} -> {selection['surfaces']}")
    assert not blind, (
        f"{len(blind)} census-covered file(s) do NOT run {guard} on a change "
        "to them — the census would be blind to a dead-default read it covers "
        "(#3094):\n  " + "\n  ".join(blind))
