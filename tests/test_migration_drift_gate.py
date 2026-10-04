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
# predates this change) the behaviour tests below fail. Re-run it by writing
# that revision to the script path and running this file — do NOT use `HEAD`,
# which already contains the fix. `test_clean_run_has_no_remediation_markers`
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
    # The refused action is withheld, not merely labelled.
    assert "apply migrations first" not in r.stdout, r.stdout
    assert "gh workflow run supabase-deploy.yml" not in r.stdout, r.stdout


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
    """Negative control: a clean run must not print any block remediation."""
    r = _run({}, ["0001_base.sql", "20260813000004_claim.sql"], ["0001", "20260813000004"])
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    for marker in ("OUT OF ORDER", "BLOCKING", "PENDING AFTER", "include-all"):
        assert marker not in r.stdout, f"{marker!r} leaked into a clean run: {r.stdout}"
