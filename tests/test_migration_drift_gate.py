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
