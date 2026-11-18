"""Hermetic tests for .github/scripts/check-migration-drift (#1095).

The script reads DRIFT_API_URL / DRIFT_TOKEN / DRIFT_CURL / DRIFT_MIGRATIONS_DIR
env seams so tests run with zero network: DRIFT_CURL points at a stub that
returns a fixture JSON version list, and DRIFT_MIGRATIONS_DIR points at a
fixture migrations dir.

Exit contract (mirrors verify-cutover): 0 clean/warn-only, 1 blocking drift,
2 could-not-determine.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-migration-drift"

_FIXTURE_TMP = None


def _tmp() -> Path:
    """One temp dir per test-run (cleaned by the OS)."""
    global _FIXTURE_TMP
    if _FIXTURE_TMP is None:
        _FIXTURE_TMP = Path(tempfile.mkdtemp(prefix="migration-drift-"))
    return _FIXTURE_TMP


FIXTURES = _tmp()


def _write_fixture_migrations(files: list[str]) -> Path:
    """Create a fixture migrations dir with the given filenames."""
    d = FIXTURES / "migrations"
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*.sql"):
        f.unlink()
    for f in files:
        (d / f).write_text("-- fixture\n")
    return d


def _stub_curl(versions: list[str], http_code: int = 201, body: str | None = None) -> Path:
    """Write a stub curl executable returning the fixture remote version list."""
    stub = FIXTURES / "stub-curl.sh"
    if body is None:
        rows = "".join(f'{{"version":"{v}"}},' for v in versions).rstrip(",")
        body = f"[{rows}]"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n{http_code}\\n\' \'{body}\'\n'
        # emulate curl -w '\n%{http_code}': body then a newline then the code
    )
    stub.chmod(0o755)
    return stub


def _run(env_extra: dict[str, str], files: list[str], versions: list[str]) -> subprocess.CompletedProcess:
    """Run the script with the seams pointed at fixtures; tokens popped from env."""
    mig_dir = _write_fixture_migrations(files)
    stub = _stub_curl(versions)
    env = dict(os.environ)
    # pop tokens so test_missing_token_exit_2 can't see a dev-exported token
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.pop("DRIFT_TOKEN", None)
    env.update(
        {
            "DRIFT_CURL": str(stub),
            "DRIFT_MIGRATIONS_DIR": str(mig_dir),
            "DRIFT_API_URL": "https://api.supabase.invalid",
            "DRIFT_TOKEN": "test-token",
        }
    )
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_clean_exit_zero():
    r = _run({}, ["0001_base.sql", "20260813000004_claim.sql"], ["0001", "20260813000004"])
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_repo_ahead_table_blocks():
    # The 14:41 incident: repo has 20260813000004 (claim_membership), prod lacks it.
    r = _run({}, ["0001_base.sql", "20260813000004_claim.sql"], ["0001"])
    assert r.returncode == 1, r.stderr
    assert "20260813000004" in r.stdout
    assert "BLOCKING" in r.stdout


def test_index_only_warns():
    # 20260813000003 is a plain non-unique CREATE INDEX — warn, not block.
    mig = _write_fixture_migrations(["20260813000003_idx.sql"])
    (mig / "20260813000003_idx.sql").write_text("CREATE INDEX IF NOT EXISTS idx_x ON public.t (a);\n")
    stub = _stub_curl(["0001"])
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig),
                "DRIFT_TOKEN": "test-token"})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "warn-only" in r.stdout.lower()


def test_unique_index_blocks():
    mig = _write_fixture_migrations(["20260813000005_uq.sql"])
    (mig / "20260813000005_uq.sql").write_text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_x ON public.t (a);\n"
    )
    stub = _stub_curl(["0001"])
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig),
                "DRIFT_TOKEN": "test-token"})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 1, r.stderr
    assert "20260813000005" in r.stdout


def test_mixed_content_blocks():
    # CREATE TABLE + plain CREATE INDEX in one migration → block wins (block-first precedence).
    mig = _write_fixture_migrations(["20260813000006_mixed.sql"])
    (mig / "20260813000006_mixed.sql").write_text(
        "CREATE TABLE IF NOT EXISTS public.x (id text);\nCREATE INDEX idx_x ON public.x (id);\n"
    )
    stub = _stub_curl(["0001"])
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig),
                "DRIFT_TOKEN": "test-token"})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 1, r.stderr
    assert "20260813000006" in r.stdout


def test_remote_ahead_warns():
    # Remote has a version repo lacks (the repaired 0000 baseline case) → warn, exit 0.
    r = _run({}, ["0001_base.sql"], ["0001", "0000"])
    assert r.returncode == 0, r.stderr
    assert "remote-ahead" in r.stdout.lower()


def test_missing_token_exit_2():
    mig = _write_fixture_migrations(["0001_base.sql"])
    stub = _stub_curl(["0001"])
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.pop("DRIFT_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig)})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "SUPABASE_ACCESS_TOKEN" in r.stderr


def test_api_error_exit_2():
    # Stub returns HTTP 500 → exit 2 (cannot determine).
    mig = _write_fixture_migrations(["0001_base.sql"])
    stub = _stub_curl([], http_code=500, body='{"error":"boom"}')
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig),
                "DRIFT_TOKEN": "test-token"})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_query_error_exit_2():
    # Stub returns a JSON object (error), not an array → exit 2.
    mig = _write_fixture_migrations(["0001_base.sql"])
    stub = _stub_curl([], http_code=201, body='{"message":"failed"}')
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig),
                "DRIFT_TOKEN": "test-token"})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout


def test_unparseable_migration_blocks():
    # Fixture migration with an unclassifiable statement → block (fail-closed).
    mig = _write_fixture_migrations(["20260813000007_weird.sql"])
    (mig / "20260813000007_weird.sql").write_text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;\n")
    stub = _stub_curl(["0001"])
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.update({"DRIFT_CURL": str(stub), "DRIFT_MIGRATIONS_DIR": str(mig),
                "DRIFT_TOKEN": "test-token"})
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 1, r.stderr
    assert "20260813000007" in r.stdout


# ── #1235: duplicate version prefixes are detected + BLOCK (not masked) ──
# Before this fix, LOCAL_VERSIONS used `sort -u` — a duplicate prefix (the
# 0012x2/0015x2 class) was collapsed, so the drift check reported "OK" while
# prod lacked the migration entirely (the 20260813000005 incident).


def test_duplicate_prefix_blocks():
    # Two files share prefix 20260813000005 (dashboard_login + inviter_email
    # collision, #1235) — prod has only the applied one.
    r = _run({}, ["20260813000005_dashboard_login.sql", "20260813000005_inviter_email.sql"],
             ["20260813000005"])
    assert r.returncode == 1, r.stderr
    assert "duplicate" in r.stdout.lower(), r.stdout
    assert "20260813000005" in r.stdout
    # Both file names surfaced so the operator knows which pair collided.
    assert "dashboard_login" in r.stdout and "inviter_email" in r.stdout


def test_duplicate_prefix_blocks_even_when_remote_missing_both():
    # Neither applied in prod — duplicate still blocks (db push would abort).
    r = _run({}, ["20260813000005_a.sql", "20260813000005_b.sql"], ["0001"])
    assert r.returncode == 1, r.stderr
    assert "duplicate" in r.stdout.lower()


def test_duplicate_free_set_still_clean():
    # No duplicates → unchanged behavior: remote has both, so nothing pending.
    r = _run({}, ["20260813000005_dashboard_login.sql", "20260813000006_inviter_email.sql"],
             ["20260813000005", "20260813000006"])
    assert r.returncode == 0, r.stderr


def test_duplicate_free_but_pending_blocks_normally():
    # No duplicates, but one migration pending → normal repo-ahead BLOCK (the
    # duplicate-detection must not suppress the existing drift gate).
    r = _run({}, ["20260813000005_dashboard_login.sql", "20260813000006_inviter_email.sql"],
             ["20260813000005"])
    assert r.returncode == 1, r.stderr
    assert "20260813000006" in r.stdout  # pending migration surfaced


# ── #4278: the block must not print a remediation it just refused ──────────
# The gate blocked correctly but printed ONE unconditional remediation for both
# the ordinary pending case and the out-of-order case — a blocking version
# OLDER than prod's newest applied version, where "apply migrations first"
# lands the migration on top of its successor (`supabase db push --include-all`
# applies the whole repo-pending set in filename order). The BLOCKING list also
# silently omitted warn-class (index-only) repo-ahead migrations, which are
# still pushed. Against the pre-#4278 script (`origin/main` @ 877fa52d1, which
# predates this change) the behaviour tests below fail. Re-run it against that
# revision in a THROWAWAY worktree — never by overwriting the tracked script,
# which mutates the tree under review and races a concurrent reviewer (see the
# ⛔ repo-ahead block below for the recipe and the measured incident):
#     git worktree add /tmp/pre4278 877fa52d1
#     cp tests/test_migration_drift_gate.py /tmp/pre4278/tests/   # REQUIRED: at
#         # 877fa52d1 this file holds 14 tests and NONE of the named ones, so the
#         # -k selects nothing (pytest exit 5 = "nothing ran", not a red)
#     cd /tmp/pre4278 && TORTOISE_TEST_CARVE_OUT=1 uv run pytest \
#         tests/test_migration_drift_gate.py
# Do not use `HEAD`, which already contains the fix.
# `test_clean_run_has_no_remediation_markers`
# and `test_duplicate_branch_never_executes_supabase` are guards rather than
# fail-on-current tests; `test_report_never_executes_supabase` fails there too,
# but on its stdout assertion — see the docstrings.


def _seam_env(
    mig: Path, stub: Path, path_prefix: Path | None = None
) -> dict[str, str]:
    """Build the subprocess env with the gate's test seams at fixtures.

    Tokens are popped so a dev-exported token cannot leak in; PATH is prefixed
    only when a test needs a stub executable to win. Shared by the two fixture
    runners below so a seam added here reaches both.
    """
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.pop("DRIFT_TOKEN", None)
    env.update(
        {
            "DRIFT_CURL": str(stub),
            "DRIFT_MIGRATIONS_DIR": str(mig),
            "DRIFT_API_URL": "https://api.supabase.invalid",
            "DRIFT_TOKEN": "test-token",
        }
    )
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    return env


def _run_fixture(files: dict[str, str], versions: list[str]) -> subprocess.CompletedProcess:
    """Write fixture migrations with explicit content, then run the gate."""
    mig = _write_fixture_migrations(list(files))
    for name, body in files.items():
        (mig / name).write_text(body)
    env = _seam_env(mig, _stub_curl(versions))
    return subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT
    )


def test_out_of_order_block_does_not_instruct_apply():
    """A blocking version OLDER than prod's newest applied version must never be
    told to "apply migrations first" — that is the action the gate refused."""
    r = _run_fixture(
        {
            "20260917000001_old.sql": "CREATE TABLE IF NOT EXISTS public.m (org_id text);\n",
            "20260918000001_new.sql": "ALTER TABLE public.m ADD PRIMARY KEY (org_id);\n",
        },
        # Prod already applied the SUCCESSOR; the older one is still repo-ahead.
        ["20260918000001"],
    )
    assert r.returncode == 1, r.stdout
    assert "20260917000001" in r.stdout
    assert "OUT OF ORDER" in r.stdout, r.stdout
    assert "20260918000001" in r.stdout, "the newest applied version must be named"
    assert "supabase migration repair --linked --status applied" in r.stdout
    # The repair is printed on this branch too, and the ALERT WRAPPER KEEPS ONLY
    # THE REPORT'S LAST 6000 BYTES (`deploy-api-alert.sh`), so the shared note's
    # OWNER-ONLY preamble can be cut while this line survives: the inline
    # qualifier is what must travel with the command itself (round-3 finding).
    assert "#2240" in r.stdout, r.stdout
    assert "(OWNER ACTION — a lane must not run prod DDL or repair, #2240)" in r.stdout, r.stdout
    # The refused action is withheld, not merely labelled — INCLUDING the
    # "dispatch is the sanctioned lane path" line, which is a forward reference
    # that must not print when no dispatch line follows it (round-7 finding).
    assert "apply migrations first" not in r.stdout, r.stdout
    assert "gh workflow run supabase-deploy.yml" not in r.stdout, r.stdout
    assert "supabase-deploy" not in r.stdout, r.stdout


def test_in_order_block_still_instructs_apply():
    """The ordinary case (blocking version newer than everything applied) keeps
    the apply remediation and is NOT flagged out of order."""
    r = _run_fixture(
        {"20260813000004_claim.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n"},
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert "apply migrations first" in r.stdout, r.stdout
    assert "OUT OF ORDER" not in r.stdout, r.stdout
    assert "COMPLETE" in r.stdout, r.stdout
    assert "FILTERED" not in r.stdout, r.stdout


# ── #9613: the block named a cause it cannot know, and withheld the remedy ──
# Repo-ahead is a set-diff of version STRINGS against `schema_migrations`. It
# proves prod has no ROW; it cannot prove prod lacks the OBJECTS. The in-order
# report said "NOT applied to prod" and prescribed only `db push` — while the
# out-of-order branch of the SAME script already documents the other case
# ("already in prod with no schema_migrations row: record it WITHOUT running
# DDL — migration repair --status applied"). Measured live on #9613: prod served
# `blog_posts.author_image_url` — a column `20261021000001` adds — from
# /blog/state-of-agentic-memory-2026 (rendered per request by the Pages
# Function, reading project ybetwichurajbfswfeqa, which the page itself names in
# its JSON-LD storage URL), while this gate reported that same version as "NOT
# applied to prod". So the red could not be cleared by the action it named and
# was not cleared by any other: the DDL-free remedy was printed only as a
# trailing `# rename-repair case:` comment. All three tests below fail against
# the pre-#9613 script; `test_in_order_block_still_instructs_apply` above is the
# guard that keeps the apply path from being REPLACED rather than joined.
#
# ⛔ To measure the "before" side, use a THROWAWAY COPY — never overwrite the
# script in a working tree:
#     git worktree add /tmp/pre9613 origin/main
#     cp tests/test_migration_drift_gate.py /tmp/pre9613/tests/   # REQUIRED: the
#         # new tests do not exist at origin/main, so a -k filter selects nothing
#         # and pytest exits 5 — which reads as "nothing to see", not as a red
#     cd /tmp/pre9613 && TORTOISE_TEST_CARVE_OUT=1 uv run pytest \
#         tests/test_migration_drift_gate.py
# Writing `origin/main`'s script over the tracked file in place mutates the PR
# under review and RACES a concurrent reviewer: measured on this PR, a peer's
# suite read the reverted script mid-run and reported 3 failed where the branch
# is green (round-2 review finding).


def test_in_order_block_does_not_claim_prod_lacks_the_objects():
    """The heading may state only what was measured: prod has no version row.

    Asserts on the heading LINE, not on a substring some sibling note supplies —
    otherwise rewording the note would silently un-pin the heading (#9613 review
    finding, so the assertion below is a single heading line).
    """
    r = _run_fixture(
        {"20260813000004_claim.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n"},
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert "NOT applied to prod" not in r.stdout, r.stdout
    heading = next(
        ln for ln in r.stdout.splitlines() if ln.startswith("  BLOCKING repo-ahead")
    )
    # The TABLE, not the schema: `supabase_migrations` is the schema and an
    # operator who queries it lands on a schema-vs-table error.
    assert "supabase_migrations.schema_migrations" in heading, heading
    assert "has NO row" in heading, heading


def test_in_order_block_offers_the_ddl_free_remedy_for_bookkeeping_drift():
    """Both outcomes are printed as peers, and the repair is gated on prod.

    Naming only `db push` in this branch is what left #9613 red: for a version
    whose objects are already in prod it re-runs DDL, and the remedy that fits
    (repair) was not offered. Naming only `repair` would be the #1001 defect —
    a blanket repair hides real drift — so the report must also carry the
    read-only verification that tells the two cases apart.

    The repair assertion is STRUCTURAL on purpose: the pre-#9613 script already
    contained the string `supabase migration repair --linked --status applied`
    in a trailing `# rename-repair case:` comment, so a substring check passes
    against the very report this change replaces (#9613 review finding). The
    `(b)` outcome marker does not exist in that form.
    """
    r = _run_fixture(
        {"20260813000004_claim.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n"},
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    out = r.stdout
    # (a) the apply path survives, verbatim, and comes first.
    assert "apply migrations first" in out, out
    assert "gh workflow run supabase-deploy.yml --ref main" in out, out
    # (b) the DDL-free path is a first-class outcome, not a trailing comment.
    marker = "(b) the objects are PRESENT and only the version is unrecorded"
    assert marker in out, out
    assert out.index(marker) < out.index("repair --linked"), out
    assert out.index("apply migrations first") < out.index(marker), out
    # ... and it is not a blanket instruction: the report names the read-only
    # checks that distinguish "absent" from "unrecorded", and the #1001 limit.
    assert "db diff --linked" in out, out
    assert "migration list --linked" in out, out
    assert "#1001" in out, out
    # The DIAGNOSIS is presence-pinned HERE, on the ordinary BLOCKING report: a
    # mutant deleting the `$BLOCKING` disjunct of its guard left the whole suite
    # green before this assertion existed, and the report it dropped the note from
    # is the one #9613 exists for (round-5 review finding).
    assert "note: 'repo-ahead' means prod has no" in out, out
    assert "produces exactly this report" in out, out
    # The inline owner qualifier is pinned LITERALLY: the alert wrapper embeds only
    # the report's last 6000 bytes, so a back-reference to the note above can be cut
    # while the pointer survives (round-5 review finding).
    assert "(OWNER ACTION — a lane must not run prod DDL or repair, #2240)" in out, out
    # ... and it is owner-only: #2240 forbids a LANE running prod DDL or repair,
    # and rules out auto-dispatch on this path (docs/infra-runbook.md §8.6 states
    # the same bound for the audience that reads this gate's output).
    assert "#2240" in out, out
    assert "OWNER-ONLY" in out, out


def test_repo_ahead_note_accompanies_the_duplicate_prefix_branch():
    """The duplicate-prefix exit prescribes a rename + apply, so it must carry
    the same 'repo-ahead proves no ROW, not missing objects' note — that is the
    exit where a DDL-free repair is most tempting (#9613 review finding)."""
    r = _run_fixture(
        {
            "20260813000005_a.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n",
            "20260813000005_b.sql": "CREATE TABLE IF NOT EXISTS public.b (id text);\n",
        },
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert "duplicate" in r.stdout.lower(), r.stdout
    assert "schema_migrations ROW" in r.stdout, r.stdout
    # A duplicate report is NOT the repo-ahead report, even when its files are
    # block-class (BLOCKING non-empty): the sentence that names the report must
    # not appear (round-6 review finding).
    assert "produces exactly this report" not in r.stdout, r.stdout
    # The #2240 owner bound rides the OWNER BOUND statement (a separate one from
    # the repo-ahead diagnosis since round 4), so every red exit carries it; all
    # THREE repair SITES also carry an inline qualifier, because the alert wrapper
    # keeps only the report's last 6000 bytes.
    assert "#2240" in r.stdout, r.stdout


def test_diagnosis_reaches_the_duplicate_branch_without_a_blocking_member():
    """The `$DUP_LOCAL_VERSIONS` disjunct of the DIAGNOSIS gate is load-bearing too.

    Index-only duplicate pair whose version is NOT recorded: BLOCKING is empty
    (index-only is warn class), DUP and repo-ahead are set. The two-line caveat
    must still print — that branch is where a DDL-free repair is most tempting.
    The "produces exactly this report" sentence must NOT: this report is a
    duplicate-prefix report, which bookkeeping drift cannot produce (round-5
    review findings — measured: deleting the disjunct left the suite green).
    """
    r = _run_fixture(
        {
            "20260813000005_first.sql": "CREATE INDEX IF NOT EXISTS i1 ON public.t (a);\n",
            "20260813000005_second.sql": "CREATE INDEX IF NOT EXISTS i2 ON public.t (b);\n",
        },
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert "duplicate" in r.stdout.lower(), r.stdout
    assert "note: 'repo-ahead' means prod has no" in r.stdout, r.stdout
    assert "produces exactly this report" not in r.stdout, r.stdout
    # The rename operand is the ACTUAL second file (the fixture's names are
    # descriptive on purpose — the repo's duplicates all are, e.g.
    # 20261022000001_blog_indexnow / _record_session_capture_redactions_integer),
    # carries the migrations path so it runs from the repo root, and the
    # destination is the one the command uses.
    assert (
        "git mv supabase/migrations/20260813000005_second.sql"
        " supabase/migrations/NEXTFREE1_second.sql"
    ) in r.stdout, r.stdout
    # Nothing shape-specific to a `_a`/`_b` fixture pair, and never the old
    # rename-onto-the-other-duplicate form ("fatal: destination exists").
    assert "_b.sql" not in r.stdout, r.stdout
    assert "git mv 20260813000005_a.sql" not in r.stdout, r.stdout
    # The ordering bound applies here too: `--include-all` applies the whole
    # pending set in filename order, so a rename to a prefix BELOW prod's newest
    # applied version is the #4278 hazard this gate refuses elsewhere.
    assert "sorts AFTER 0001" in r.stdout, r.stdout

    # Every colliding prefix gets its own operand: naming one of two leaves the
    # next run red.
    r2 = _run_fixture(
        {
            "20260813000005_first.sql": "CREATE INDEX IF NOT EXISTS i1 ON public.t (a);\n",
            "20260813000005_second.sql": "CREATE INDEX IF NOT EXISTS i2 ON public.t (b);\n",
            "20260813000006_first.sql": "CREATE INDEX IF NOT EXISTS i3 ON public.t (c);\n",
            "20260813000006_second.sql": "CREATE INDEX IF NOT EXISTS i4 ON public.t (d);\n",
        },
        ["0001"],
    )
    assert r2.returncode == 1, r2.stdout
    assert "git mv supabase/migrations/20260813000005_second.sql" in r2.stdout, r2.stdout
    assert "git mv supabase/migrations/20260813000006_second.sql" in r2.stdout, r2.stdout
    # One operand index per operand ACROSS prefixes, so the tokens differ.
    assert "NEXTFREE1_" in r2.stdout and "NEXTFREE2_" in r2.stdout, r2.stdout


def test_the_printed_rename_command_actually_runs(tmp_path):
    """The report's only purpose is that someone runs the line it prints.

    Round 8 measured the line aborting two ways — unquoted space name
    (`fatal: destination 'gamma.sql' is not a directory`) and, worse, a name
    containing `$(…)` executing on paste. This test EXECUTES what the report
    printed, with only the NEXTFREE<n> token substituted, in a throwaway git repo
    — the only pin that survives future rewording of the text around it.
    """
    mig = tmp_path / "migrations"
    mig.mkdir()
    nasty = "20260813000005_beta $(touch PWNED) gamma.sql"
    (mig / nasty).write_text(
        "CREATE INDEX IF NOT EXISTS i1 ON public.t (a);\n", encoding="utf-8"
    )
    (mig / "20260813000005_alpha.sql").write_text(
        "CREATE INDEX IF NOT EXISTS i2 ON public.t (b);\n", encoding="utf-8"
    )
    env = _seam_env(mig, _stub_curl(["0001"]))
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT
    )
    assert r.returncode == 1, r.stdout
    # Two files at the prefix, so the report prints BOTH choices as (a)/(b); this
    # test runs (a) — keep the first listed file, move the other.
    lines = [
        ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("(a) git mv ")
    ]
    assert len(lines) == 1, r.stdout
    assert any(ln.strip().startswith("(b) git mv ") for ln in r.stdout.splitlines()), r.stdout
    # Two files: `(b)` IS the alternative to `(a)`, and the guidance must say so
    # (it is printed only for this arity, so a prefix with three files gets no (b)).
    assert "run (b) INSTEAD of (a)" in r.stdout, r.stdout
    cmd = lines[0][len("(a) ") :].replace("NEXTFREE1", "20261024000001")

    work = tmp_path / "repo"
    (work / "supabase" / "migrations").mkdir(parents=True)
    for f in mig.iterdir():
        (work / "supabase" / "migrations" / f.name).write_bytes(f.read_bytes())
    subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    run = subprocess.run(
        ["bash", "-c", cmd], cwd=work, capture_output=True, text=True
    )
    assert run.returncode == 0, f"{cmd!r} -> {run.stderr}"
    names = sorted(p.name for p in (work / "supabase" / "migrations").iterdir())
    # Exactly one file left at the old prefix; the other moved with its name
    # INTACT — pinned to the exact expected name, because a quoting bug that ate
    # the slug still satisfies a bare `startswith` (round-9 finding).
    assert names.count("20260813000005_alpha.sql") == 1, names
    expected = "20261024000001_" + nasty[len("20260813000005_") :]
    assert expected in names, (expected, names)
    assert nasty not in names, names
    # ... and pasting it did NOT run the command substitution in the filename.
    assert not (work / "PWNED").exists(), "the printed command executed a filename"

    # N files at one prefix need N-1 renames, so N-1 operands; and a filename with
    # a space must survive whole (no `basename`/`xargs` word-splitting) — both
    # round-7 findings, both of which made the printed command unrunnable.
    r3 = _run_fixture(
        {
            "20260813000005_alpha.sql": "CREATE INDEX IF NOT EXISTS i1 ON public.t (a);\n",
            "20260813000005_beta gamma.sql": "CREATE INDEX IF NOT EXISTS i2 ON public.t (b);\n",
            "20260813000005_delta.sql": "CREATE INDEX IF NOT EXISTS i3 ON public.t (c);\n",
        },
        ["0001"],
    )
    assert r3.returncode == 1, r3.stdout
    # SHELL-QUOTED (round-8 finding): unquoted, a name with a space prints a
    # command that aborts with "destination 'gamma.sql' is not a directory", and
    # a name containing `$(…)` EXECUTES it on copy-paste. `%q` is the pin.
    assert "git mv supabase/migrations/20260813000005_beta\\ gamma.sql" in r3.stdout, r3.stdout
    assert "git mv supabase/migrations/20260813000005_delta.sql" in r3.stdout, r3.stdout
    assert "git mv supabase/migrations/20260813000005_alpha.sql" not in r3.stdout, r3.stdout
    assert "Repeat until ONE file remains" in r3.stdout, r3.stdout
    # ⛔ THE GUIDANCE MUST MATCH THE OPERAND LIST. Three files at one prefix need
    # TWO renames, so a "run exactly ONE line per prefix" instruction leaves a
    # live duplicate — the next `db push` aborts on it and the gate is red again
    # (#1235). Pin the arity statement to the operands actually printed, and pin
    # that the two-file-only `(b)` choice is NOT offered here.
    a_lines = [
        ln for ln in r3.stdout.splitlines() if ln.strip().startswith("(a) git mv ")
    ]
    assert len(a_lines) == 2, r3.stdout
    assert "run EVERY (a) line" in r3.stdout, r3.stdout
    assert "run exactly ONE line per prefix" not in r3.stdout, r3.stdout
    assert "(b) git mv " not in r3.stdout, r3.stdout
    # ... and the sibling "which file to KEEP" warning uses the same arity rule.
    assert "every (a) line, or (b) instead" in r3.stdout, r3.stdout
    # The placeholder is a bare token (no shell metacharacters) and carries a
    # GLOBAL index, so two operands can never be given the same prefix by
    # substituting "the" token: each needs its own free numeric prefix.
    assert "NEXTFREE1_" in r3.stdout, r3.stdout
    # The gate cannot see which file's DDL prod already has; keeping the wrong one
    # re-runs the applied DDL and silently never applies the other (#1001, round-8).
    assert "Which file to KEEP is a prod question" in r3.stdout, r3.stdout


def test_note_covers_the_duplicate_branch_when_nothing_is_repo_ahead():
    """The `$DUP_LOCAL_VERSIONS` disjunct of the note's guard is load-bearing.

    Two index-only files at ONE prefix whose version IS recorded in prod: no
    BLOCKING member (index-only is warn class), no repo-ahead — only the
    duplicate term can fire, and that is a red rename exit. A mutant with the
    # term deleted stays green, because every other duplicate fixture uses
    # CREATE TABLE and so sets BLOCKING too.
    """
    r = _run_fixture(
        {
            "20260813000005_a.sql": "CREATE INDEX IF NOT EXISTS i1 ON public.t (a);\n",
            "20260813000005_b.sql": "CREATE INDEX IF NOT EXISTS i2 ON public.t (b);\n",
        },
        ["0001", "20260813000005"],
    )
    assert r.returncode == 1, r.stdout
    assert "duplicate" in r.stdout.lower(), r.stdout
    # The repo-ahead DIAGNOSIS is deliberately absent here: the version IS
    # recorded and there is no repo-ahead to explain, so "bookkeeping drift
    # produces exactly this report" would be false (round-4 review finding).
    assert "note: 'repo-ahead' means prod has no" not in r.stdout, r.stdout
    # What must survive is the OWNER bound — and it is the ONLY thing pinning the
    # `$DUP_LOCAL_VERSIONS` disjunct of that guard, since BLOCKING is empty here.
    assert "OWNER-ONLY" in r.stdout, r.stdout
    assert "#2240" in r.stdout, r.stdout


def test_mixed_out_of_order_and_safe_are_partitioned():
    """A blocking set can hold both kinds; the out-of-order marker must name
    only the older version and the apply remediation only the newer one."""
    r = _run_fixture(
        {
            "20260917000001_old.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n",
            "20260919000001_new.sql": "CREATE TABLE IF NOT EXISTS public.b (id text);\n",
        },
        ["20260918000001"],
    )
    assert r.returncode == 1, r.stdout
    lines = r.stdout.splitlines()
    ooo_i = next(i for i, ln in enumerate(lines) if "OUT OF ORDER" in ln)
    safe_i = next(i for i, ln in enumerate(lines) if "PENDING AFTER" in ln)
    assert ooo_i < safe_i, r.stdout  # out-of-order section precedes the safe one
    ooo_block = "\n".join(lines[ooo_i:safe_i])
    safe_block = "\n".join(lines[safe_i:])
    assert "20260917000001" in ooo_block and "20260919000001" not in ooo_block, ooo_block
    assert "20260919000001" in safe_block and "20260917000001" not in safe_block, safe_block
    # The apply step is only correct AFTER the out-of-order versions are
    # resolved, so it is printed as one ordered remediation — never as a
    # standalone "apply migrations first".
    assert "Remediation (in this order)" in r.stdout, r.stdout
    assert "apply migrations first" not in r.stdout, r.stdout
    assert "1. resolve each OUT OF ORDER version" in r.stdout
    assert "2. then dispatch the deploy" in r.stdout


def test_warn_only_set_named_when_blocking():
    """The BLOCKING list is a FILTERED subset — the index-only members it
    excludes are still pushed by --include-all, so they must be named."""
    r = _run_fixture(
        {
            "20260813000003_idx.sql": "CREATE INDEX IF NOT EXISTS idx_x ON public.t (a);\n",
            "20260813000004_claim.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n",
        },
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert "warn-only" in r.stdout.lower(), r.stdout
    assert "20260813000003" in r.stdout, "the excluded warn-class member must be named"
    assert "FILTERED" in r.stdout, r.stdout
    assert "1 of 2 repo-ahead pending" in r.stdout, r.stdout
    assert "--include-all" in r.stdout


def _run_with_supabase_stub(
    files: dict[str, str], versions: list[str]
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the gate with a fake `supabase` FIRST on PATH; return (result, marker
    path). The marker is written iff the gate executed `supabase`."""
    fakebin = FIXTURES / "fakebin"
    fakebin.mkdir(parents=True, exist_ok=True)
    marker = FIXTURES / "supabase-invoked.marker"
    if marker.exists():
        marker.unlink()
    stub_supabase = fakebin / "supabase"
    stub_supabase.write_text(f"#!/usr/bin/env bash\n: > '{marker}'\nexit 0\n")
    stub_supabase.chmod(0o755)
    mig = _write_fixture_migrations(list(files))
    for name, body in files.items():
        (mig / name).write_text(body)
    env = _seam_env(mig, _stub_curl(versions), path_prefix=fakebin)
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT
    )
    return r, marker


def test_in_order_report_never_executes_supabase():
    """The ordinary pending branch prints TWO command-shaped remediations
    (`supabase db push --linked --include-all` and `supabase migration repair
    --linked --status applied <version>`); an unescaped backtick on either line
    would run prod DDL while merely printing it (#4278's defect class).
    `test_report_never_executes_supabase` only covers the OUT OF ORDER branch,
    which is the reason this fixture is added rather than widened (#9613 review
    finding).

    What the marker proves, exactly, measured on mutants (#9613 review, round 4):

    * The marker is only reachable for command text carrying no bash
      metacharacters. The OUT OF ORDER branch's `supabase db push --include-all`
      is that shape: backticked, the stub runs and the marker appears — which is
      why `test_report_never_executes_supabase` is mutation-provable.
    * The `db push --linked --include-all` line in THIS branch is that shape too
      (measured: backticked, the stub runs and the marker IS written — a trailing
      parenthetical after the closing backtick does not make the substitution
      inert). So the marker is a real guard for this line as well.
    * The REPAIR line is the one the marker cannot prove: `<version>` is parsed as
      a redirect with a missing target, so a backticked mutant makes bash report a
      syntax error inside the command substitution, the `echo` still runs and
      prints a line with a hole, and the exit status is unchanged — nothing
      executes, no marker appears. That line is pinned instead by the LITERAL
      suffix assertion below (with ` <version>`); with the looser `... applied`
      pin the mutant stayed green.
    """
    r, marker = _run_with_supabase_stub(
        {"20260813000004_claim.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n"},
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert not marker.exists(), "the report EXECUTED supabase instead of printing it"
    # Both command lines really are present in this branch, or the guard is vacuous.
    assert "supabase db push --linked --include-all" in r.stdout, r.stdout
    # Literal suffix on purpose: `... applied` alone is still a substring of a
    # backticked mutant (the `<version>` is what vanishes), so a looser pin
    # leaves that regression uncovered.
    assert "migration repair --linked --status applied <version>" in r.stdout, r.stdout


def test_report_never_executes_supabase():
    """Guard: an unescaped backtick in the remediation text ran
    `supabase db push --include-all` while merely printing it (it also injected
    the CLI's error JSON into the remediation). That defect existed only in an
    INTERMEDIATE revision of this change — the pre-#4278 script never had it —
    so against `origin/main` this test fails on its stdout assertion (the base
    text is `--linked --include-all`), not on execution. It is mutation-provable
    on execution by reintroducing the backticks."""
    r, marker = _run_with_supabase_stub(
        {
            "20260917000001_old.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n",
            "20260919000001_new.sql": "CREATE TABLE IF NOT EXISTS public.b (id text);\n",
        },
        ["20260918000001"],
    )
    assert r.returncode == 1, r.stdout
    assert not marker.exists(), "the report EXECUTED supabase instead of printing it"
    assert "supabase db push --include-all" in r.stdout, r.stdout


def test_duplicate_branch_flags_out_of_order_after_rename():
    """A duplicate prefix does not excuse the ordering check: after the rename
    the deploy still applies the whole repo-pending set, so a repo-ahead version
    older than prod's newest applied must still be flagged (#4278)."""
    r = _run_fixture(
        {
            "20260813000005_a.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n",
            "20260813000005_b.sql": "CREATE TABLE IF NOT EXISTS public.b (id text);\n",
            "20260917000001_old.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n",
        },
        ["20260918000001"],
    )
    assert r.returncode == 1, r.stdout
    assert "duplicate" in r.stdout.lower(), r.stdout
    ooo = [ln for ln in r.stdout.splitlines() if "OUT OF ORDER after the rename" in ln]
    assert ooo, r.stdout
    assert "20260917000001" in ooo[0], ooo[0]
    assert "apply via supabase-deploy dispatch" in r.stdout, r.stdout
    # The repair instruction on THIS branch carries its own inline owner bound
    # (the alert wrapper keeps only the report's last 6000 bytes), pinned
    # literally — `#2240` alone is also supplied by the statement above it.
    assert "— OWNER ACTION, a lane must not run prod DDL" in r.stdout, r.stdout


def test_fresh_project_with_no_applied_migrations_is_not_out_of_order():
    """A valid empty remote set means nothing is applied yet, so every
    repo-ahead version is newer and none is out of order — and the gate still
    exits 1 (drift), not 2."""
    r = _run_fixture(
        {"20260813000004_claim.sql": "CREATE TABLE IF NOT EXISTS public.c (id text);\n"},
        [],
    )
    assert r.returncode == 1, r.stdout
    assert "nothing applied yet" in r.stdout, r.stdout
    assert "OUT OF ORDER" not in r.stdout, r.stdout
    assert "apply migrations first" in r.stdout, r.stdout


def test_remote_max_is_the_greatest_applied_version():
    """The out-of-order reference is the GREATEST applied version, not the first
    or last line the API happened to return."""
    r = _run_fixture(
        {"20260917000001_old.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n"},
        ["0001", "20260918000001", "20260813000004"],
    )
    assert r.returncode == 1, r.stdout
    ooo = [ln for ln in r.stdout.splitlines() if "OUT OF ORDER" in ln]
    assert ooo, r.stdout
    assert "20260918000001" in ooo[0], ooo[0]


def test_comparator_is_lexical_matching_cli_filename_order():
    """The ordering reference follows the CLI's filename order (string compare),
    not numeric order. With variable-width prefixes they differ: an applied
    `999` and a pending `1000` — the CLI orders `1000` first, so applying `1000`
    on top of the already-applied `999` IS out of order. Numeric comparison
    would wrongly call it safe."""
    r = _run_fixture(
        {"1000_late_named.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n"},
        ["999"],
    )
    assert r.returncode == 1, r.stdout
    assert "OUT OF ORDER" in r.stdout, r.stdout
    assert "999" in r.stdout, r.stdout


def test_duplicate_branch_never_executes_supabase():
    """Guard for a PRE-EXISTING escaped-backtick site (the duplicate-prefix
    heading) — mutation-provable by unescaping it, though it passes as-is on the
    pre-#4278 script. It must never shell out either."""
    r, marker = _run_with_supabase_stub(
        {
            "20260813000005_a.sql": "CREATE TABLE IF NOT EXISTS public.a (id text);\n",
            "20260813000005_b.sql": "CREATE TABLE IF NOT EXISTS public.b (id text);\n",
        },
        ["0001"],
    )
    assert r.returncode == 1, r.stdout
    assert "duplicate" in r.stdout.lower(), r.stdout
    assert not marker.exists(), "the report EXECUTED supabase instead of printing it"
    assert "supabase db push" in r.stdout, r.stdout


def test_clean_run_has_no_remediation_markers():
    """Negative control: a clean run must not print any block remediation.

    The repo-ahead note is in the marker list because it is a RED diagnosis, so
    it belongs only on a run that prescribes an action. What the marker pins,
    precisely: the NO-GUARD state (a note printed unconditionally) — a clean run
    has no repo-ahead, so the `$REPO_AHEAD`-only mutant from round 1 is caught by
    `test_warn_only_run_is_green_and_carries_no_red_diagnosis_note` instead
    (round-3 review measured both mutants; neither test covers the other).
    """
    r = _run({}, ["0001_base.sql", "20260813000004_claim.sql"], ["0001", "20260813000004"])
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    for marker in (
        "OUT OF ORDER",
        "BLOCKING",
        "PENDING AFTER",
        "include-all",
        "note: 'repo-ahead' means prod has no",
        "OWNER-ONLY",
    ):
        assert marker not in r.stdout, f"{marker!r} leaked into a clean run: {r.stdout}"


def test_warn_only_run_is_green_and_carries_no_red_diagnosis_note():
    """A warn-only (index-only) repo-ahead run EXITS 0 — so it must not carry the
    "repo-ahead proves no ROW, not missing objects" note, which diagnoses a
    blocked report. Measured leak before the round-2 fix: the note printed
    immediately above "OK (no blocking drift)"."""
    r = _run_fixture(
        {"20260901000001_idx.sql": "CREATE INDEX IF NOT EXISTS idx_x ON public.t (a);\n"},
        ["0001"],
    )
    assert r.returncode == 0, r.stdout
    assert "OK (no blocking drift)" in r.stdout, r.stdout
    assert "warn-only" in r.stdout.lower(), r.stdout
    assert "note: 'repo-ahead' means prod has no" not in r.stdout, r.stdout
    assert "OWNER-ONLY" not in r.stdout, r.stdout


# ── #6136 regression: early-exit pipeline consumers under `set -euo pipefail` ──
#
# The gate runs under `set -euo pipefail` (line 32). Where a pipeline's LAST
# member exits at its first match (`head -n1`, `grep -q`), the UPSTREAM writer
# takes SIGPIPE, so the PIPELINE's status becomes 141 rather than 0/1. That has
# two distinct consequences, and BOTH are invisible to a small fixture, because
# the writer only takes SIGPIPE once its output exceeds the pipe buffer:
#
#   * statement level -> `set -e` ABORTS the script (141, often with no output)
#   * condition level -> `if`/`elif` read a MATCH as a NON-match
#
# The tests below make both deterministic by exceeding the pipe buffer. The
# block-class one is the important one: as a condition, a lost match silently
# downgrades a pending `CREATE TABLE` to warn-only and the gate exits 0 with the
# migration unapplied — FAIL-OPEN.
#
# A body comfortably over the pipe capacity is used rather than a
# timing-dependent race, so these are deterministic on a loaded host and under
# both BSD and GNU coreutils. The `ls | head -n1` site at `:126` is guarded with
# `|| true`: it needs a listing larger than the buffer to be reachable, while the
# test that observed the CI red stages a handful of tiny migrations — orders of
# magnitude below that, so it cannot reach the site. (The boundary between 2
# files and 2600 is measured; the exact listing sizes are not load-bearing and
# are deliberately not quoted here.) So the specific trigger of that red is
# UNIDENTIFIED; what these tests establish is the defect CLASS, and that these
# paths now fail loudly pre-fix and pass post-fix. The `:126` site itself is
# pinned separately by
# `test_many_matching_migrations_still_report_the_verdict`, which makes its
# listing exceed the buffer via deeply-nested paths.
#
# False-negative floor, stated because it bounds what these can promise: every
# fixture must EXCEED the pipe capacity, so on a host whose default capacity were
# raised above ~243 KiB these would silently stop failing pre-fix (they would
# still pass post-fix). The sizes below give ~4x headroom over the 64 KiB default.

_PIPE_BUSTING_LINES = 5000  # measured 243 KiB (block) / 268 KiB (index-only)


def _run_staged(mig: Path, stub: Path) -> subprocess.CompletedProcess:
    """Run the gate against fixtures the caller has already staged."""
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=_seam_env(mig, stub),
        cwd=REPO_ROOT,
    )


def _stage_one(name: str, body: str) -> Path:
    """Stage a single migration with explicit content and return its dir."""
    mig = _write_fixture_migrations([name])
    (mig / name).write_text(body)
    return mig


def test_large_block_class_body_with_one_index_line_still_blocks():
    """A block-class migration padded past the pipe buffer must still BLOCK.

    Regression for #6136. The block-class `if` condition is a pipeline ending in
    `grep -qiE`; on a body larger than the pipe buffer that `grep` exits at its
    first match, SIGPIPEs the upstream `sed`, and `pipefail` turns the pipeline's
    status into 141 — so the condition reads a MATCH as NO MATCH. Control then
    falls to the index `elif`, which matches the one `CREATE INDEX` line, and a
    pending `CREATE TABLE` is reported warn-only: the gate exits 0 (FAIL-OPEN).
    """
    body = "\n".join(
        f"CREATE TABLE IF NOT EXISTS public.t_{i} (a int);"
        for i in range(_PIPE_BUSTING_LINES)
    ) + "\nCREATE INDEX IF NOT EXISTS idx_z ON public.t_0 (a);\n"
    assert len(body) > 65536, f"fixture must exceed the pipe buffer, got {len(body)}"

    mig = _stage_one("20260813000009_big.sql", body)
    r = _run_staged(mig, _stub_curl(["0001"]))

    assert r.returncode == 1, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert "BLOCKING" in r.stdout, r.stdout[-2000:]
    assert "20260813000009" in r.stdout, r.stdout[-2000:]


def test_large_index_only_body_warns_rather_than_blocks():
    """The mirror case: an index-only migration must warn, not block.

    Regression for #6136. The index `elif` has the same early-exit consumer, so
    on a body past the pipe buffer it too read a match as no-match and the
    fail-closed `else` reported an index-only migration as BLOCKING — a spurious
    block, and the opposite error from the test above.
    """
    body = "\n".join(
        f"CREATE INDEX IF NOT EXISTS idx_{i} ON public.t_0 (a);"
        for i in range(_PIPE_BUSTING_LINES)
    ) + "\n"
    assert len(body) > 65536, f"fixture must exceed the pipe buffer, got {len(body)}"

    mig = _stage_one("20260813000010_idx.sql", body)
    r = _run_staged(mig, _stub_curl(["0001"]))

    assert r.returncode == 0, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert "warn-only" in r.stdout, r.stdout[-2000:]
    assert "BLOCKING" not in r.stdout, r.stdout[-2000:]


def test_large_api_error_body_still_reports_exit_2():
    """An over-buffer API error body must report the documented exit 2.

    Regression for #6136. The error path piped the Management API body through
    `head -c 400`; a body larger than the pipe buffer SIGPIPE'd `printf`, so
    `set -e` ended the script at 141 instead of the documented 2
    (could-not-determine) that callers rely on to fail closed.
    """
    big = FIXTURES / "big-api-error-body.txt"
    big.write_text("x" * 262144 + "\n")
    stub = FIXTURES / "stub-curl-big-error.sh"
    # body first, then the http_code line the script reads last
    stub.write_text(f"#!/usr/bin/env bash\ncat '{big}'\necho 500\n")
    stub.chmod(0o755)

    mig = _write_fixture_migrations(["0001_base.sql"])
    r = _run_staged(mig, stub)

    assert r.returncode == 2, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert "500" in r.stderr, r.stderr[-2000:]


def test_many_matching_migrations_still_report_the_verdict():
    """A version with more matching files than the pipe buffer must still
    produce its verdict, not a SIGPIPE abort.

    Pins the `:126` `ls … | head -n1` site, the one #7567 hunk that shipped
    without a failing-on-revert test. With no guard, a listing larger than the
    pipe buffer kills the still-writing `ls` with SIGPIPE, `pipefail` makes the
    pipeline 141, and `set -e` ABORTS the script before it can print a verdict.
    #7567 added `|| true` to that assignment, binding the pipeline to exit 0
    before `set -e` can see the 141; against the pre-#7567 script this test
    fails with exactly `(141, '', '')`.

    It does NOT identify the trigger of the 2026-10-06 main-red (#6136): the
    fixture that observed that red stages a handful of tiny migrations, far
    below the buffer (see the block comment above), so this site cannot reach
    it. What this pins is the site's own behaviour once its listing exceeds the
    buffer — the defect CLASS, not the reported incident.

    The fixture directory is nested deep so each `ls` line is long and ~150
    files suffice. Like its siblings this relies on the host's default pipe
    capacity: a capacity raised above the fixture's ~115 KiB listing would stop
    it failing pre-fix (it would still pass post-fix).
    """
    mig = FIXTURES / "migrations"
    for _ in range(6):
        mig = mig / ("nested" * 20)
    mig.mkdir(parents=True, exist_ok=True)
    files = {
        f"20260813000009_{i:03d}.sql":
            "CREATE TABLE IF NOT EXISTS public.a (id text);\n"
        for i in range(150)
    }
    for name, body in files.items():
        (mig / name).write_text(body)
    listing = sum(len(str(mig / n)) + 1 for n in files)
    assert listing > 65536, f"listing must exceed the pipe buffer, got {listing}"

    r = _run_staged(mig, _stub_curl(["0001"]))

    assert r.returncode == 1, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert "20260813000009" in r.stdout, r.stdout[-2000:]
