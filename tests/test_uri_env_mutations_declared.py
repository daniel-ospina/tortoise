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


def _uri_mutation_sites():
    """Yield (file_name, line_number, line) for every mutation site in a
    NON-allowlist (migrated) test file. The executable census — the plan's
    Step 1 grep is the enumeration, this is its machine form."""
    exempt = _exempt_stems()
    for f in sorted(_TESTS_ROOT.glob("test_*.py")) \
            + sorted((_TESTS_ROOT / "e2e").glob("test_*.py")):
        if f.stem in exempt:
            continue
        src = f.read_text(encoding="utf-8")
        for m in _MUTATION_RE.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            yield f.name, line, src.split("\n")[line - 1].strip()


def _is_declared(fname: str, line: str) -> bool:
    """The declaration-guard matching logic (factored for pinning): a
    mutation line is declared when the file's DELIBERATE_URI_MUTATIONS
    entry exists and at least one declared regex matches the line."""
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
    for fname in DELIBERATE_URI_MUTATIONS:
        assert (_TESTS_ROOT / fname).exists() \
            or (_TESTS_ROOT / "e2e" / fname).exists(), \
            f"DELIBERATE_URI_MUTATIONS key {fname!r} is not a test module"


# ═══════════════════════════════════════════════════════════════════════
# Restoration guard (#2084) — static scope/evidence analysis helpers
# ═══════════════════════════════════════════════════════════════════════

def _scope_map(src: str) -> list[tuple[str, str | None]]:
    """Per-line scope classification: ("top", None) for module-level
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
    stack: list[tuple[int, str, str]] = []  # (indent, kind, name)
    paren = 0
    pending_fixture = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("@") and paren == 0:
            # precise fixture-marker test: `.fixture(` / `.fixture\n` — a
            # bare "fixture" substring (usefixtures, parametrize ids) must
            # NOT set pending_fixture (#2084 review P2)
            if re.search(r'\.fixture\b', stripped):
                pending_fixture = True
            scopes[i] = stack[-1][1:] if stack else ("top", None)
        elif re.match(r'^(\s*)(?:async\s+)?def\s+\w+', line) and paren == 0:
            ind = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= ind:
                stack.pop()
            name = re.match(r'^(\s*)(?:async\s+)?def\s+(\w+)', line).group(2)
            stack.append((ind, "fixture" if pending_fixture else "fn", name))
            pending_fixture = False
            scopes[i] = stack[-1][1:]
        elif stripped and paren == 0 \
                and not stripped.startswith(("#", "def", "@")):
            ind = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= ind:
                stack.pop()
            if ind == 0:
                pending_fixture = False
            scopes[i] = stack[-1][1:] if stack else ("top", None)
        else:
            scopes[i] = stack[-1][1:] if stack else ("top", None)
        # paren balance for continuation detection (brackets in string
        # literals are a documented heuristic limitation)
        for ch in line:
            if ch in "([{":
                paren += 1
            elif ch in ")]}":
                paren = max(0, paren - 1)
    return scopes


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
    """(name, src) for every scan-relevant test module (same census as the
    declaration guard — carve-outs and this task's own files exempt)."""
    exempt = _exempt_stems()
    for f in sorted(_TESTS_ROOT.glob("test_*.py")) \
            + sorted((_TESTS_ROOT / "e2e").glob("test_*.py")):
        if f.stem not in exempt:
            yield f.name, f.read_text(encoding="utf-8")


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
