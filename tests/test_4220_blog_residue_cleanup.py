"""Tests for the #4220 blog-residue cleanup script.

``graph-scripts/4220_blog_residue_cleanup.py`` DELETES production rows, so the
guards are the thing under test: a wrong prefix, or a statement missing the
``created_by`` guard, would delete editorial content (the owner's
``hello-tortoise-first-post`` draft sits in the same table). These tests drive
the real module against a RECORDING fake runner — no network, no credentials,
no production access.

The module lives under ``graph-scripts/`` (a dash in the path), so it is loaded
by path — same pattern as ``tests/test_2199_decide_calibration.py``.
"""
from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "graph-scripts" / "4220_blog_residue_cleanup.py"
_spec = _ilu.spec_from_file_location("blog_residue_cleanup_4220", str(_SCRIPT_PATH))
cleanup = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(cleanup)


ROW = {
    "slug": "meta-contract-1234",
    "status": "draft",
    "created_by": "blog-e2e",
    "created_at": "2026-09-19T00:00:00Z",
}


class Recorder:
    """Fake SQL runner: records every statement, never touches a network."""

    def __init__(self, rows: list[dict] | None = None, remaining: int = 0):
        self.queries: list[str] = []
        self._rows = [ROW] if rows is None else rows
        self._remaining = remaining

    def __call__(self, query: str) -> list[dict]:
        self.queries.append(query)
        if query.startswith("SELECT count"):
            return [{"remaining": self._remaining}]
        if query.startswith("DELETE"):
            return [{"slug": r["slug"]} for r in self._rows]
        return self._rows

    def of_kind(self, kind: str) -> list[str]:
        return [q for q in self.queries if q.startswith(kind)]


# ── Guard A: only the known test prefixes ──────────────────────────────────

def test_unknown_prefix_is_refused():
    """An arbitrary prefix must be refused — this is not a general delete tool."""
    with pytest.raises(cleanup.OpError, match="refused"):
        cleanup.resolve_prefixes("hello-tortoise")


def test_both_expands_to_the_known_test_prefixes():
    assert cleanup.resolve_prefixes(cleanup.BOTH) == cleanup.ALLOWED_PREFIXES


@pytest.mark.parametrize("prefix", cleanup.ALLOWED_PREFIXES)
def test_named_prefix_resolves_to_itself(prefix):
    assert cleanup.resolve_prefixes(prefix) == (prefix,)


# ── Guard B: created_by, inseparable from the prefix ───────────────────────

@pytest.mark.parametrize("prefix", cleanup.ALLOWED_PREFIXES)
def test_every_statement_carries_the_created_by_guard(prefix):
    """A prefix-only statement would delete rows the E2E agent never created."""
    prefixes = cleanup.resolve_prefixes(prefix)
    for sql in (
        cleanup.enumerate_sql(prefixes),
        cleanup.count_sql(prefixes),
        cleanup.delete_sql(prefixes),
    ):
        assert "created_by = 'blog-e2e'" in sql, sql
        assert f"slug LIKE '{prefix}%'" in sql, sql


def test_scope_is_identical_across_enumerate_count_and_delete():
    """The verification query must scope EXACTLY as the delete does.

    If count/enumerate scoped differently, the post-delete verification could
    pass while rows the DELETE targeted are still there — the check would
    measure something other than what was deleted.
    """
    prefixes = cleanup.resolve_prefixes(cleanup.BOTH)
    where = cleanup.where_clause(prefixes)
    for sql in (
        cleanup.enumerate_sql(prefixes),
        cleanup.count_sql(prefixes),
        cleanup.delete_sql(prefixes),
    ):
        assert where in sql, sql


# ── Guard B is PINNED: `--agent` is not a knob ─────────────────────────────

def test_agent_flag_accepts_only_the_e2e_agent():
    """P2 (#4316): `--agent` cannot be pointed at any other created_by value.

    The docstring and the runbook both claim every statement is AND-ed with
    ``created_by = 'blog-e2e'``. A free-text flag falsified that claim: passing
    another agent name (or a human author) would target rows the E2E agent never
    created. argparse refuses it with exit code 2 — falsifiable: a plain
    ``default=`` flag (the pre-fix shape) accepts the value and this raises
    nothing.
    """
    assert cleanup._parse(["--prefix", "both"]).agent == cleanup.DEFAULT_AGENT
    assert cleanup._parse(["--prefix", "both", "--agent", cleanup.DEFAULT_AGENT]).agent == cleanup.DEFAULT_AGENT
    with pytest.raises(SystemExit) as ei:
        cleanup._parse(["--prefix", "both", "--agent", "hello-tortoise"])
    assert ei.value.code == 2


def test_unknown_agent_is_refused_before_any_sql():
    """The refusal happens at parse time — no statement is ever issued."""
    rec = Recorder()
    with pytest.raises(SystemExit) as ei:
        cleanup.main(["--prefix", "both", "--agent", "hello-tortoise", "--execute"], runner=rec)
    assert ei.value.code == 2
    assert rec.queries == [], f"SQL ran for a refused --agent value: {rec.queries}"


# ── Dry-run is genuinely non-destructive ───────────────────────────────────

def test_dry_run_issues_no_delete():
    rec = Recorder()
    rc = cleanup.main(["--prefix", "both"], runner=rec)
    assert rc == 0
    assert rec.of_kind("DELETE") == [], "dry-run executed a DELETE"
    assert len(rec.of_kind("SELECT")) == 1, "dry-run should only enumerate"


def test_dry_run_with_zero_matches_is_a_clean_no_op():
    rec = Recorder(rows=[])
    rc = cleanup.main(["--prefix", "meta-contract-"], runner=rec)
    assert rc == 0
    assert rec.of_kind("DELETE") == []


# ── Execute: deletes, then verifies ────────────────────────────────────────

def test_execute_deletes_and_verifies_zero_remaining():
    rec = Recorder(remaining=0)
    rc = cleanup.main(["--prefix", "both", "--execute"], runner=rec)

    assert rc == 0
    assert len(rec.of_kind("DELETE")) == 1
    # The delete is followed by a count — verification is part of the run.
    assert rec.of_kind("SELECT count") == [cleanup.count_sql(cleanup.resolve_prefixes("both"))]


def test_execute_reports_failure_when_rows_remain():
    """The falsifiable half: a delete that did not take must NOT report success."""
    rec = Recorder(remaining=1)
    rc = cleanup.main(["--prefix", "both", "--execute"], runner=rec)
    assert rc == 1


# ── Fail-closed: no credential is never "nothing to do" ────────────────────

def test_missing_credential_is_fail_closed(monkeypatch):
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
    rec_called: list[str] = []
    rc = cleanup.main(["--prefix", "both", "--via", "api"],
                      runner=lambda q: rec_called.append(q) or [])
    assert rc == 2
    assert rec_called == [], "SQL ran despite a missing credential"


# ── The pre-fix slug shape is still matched (the pile to clean) ────────────

def test_legacy_randomised_meta_slugs_are_in_scope():
    """The accumulated pile used `meta-contract-<random n>`; only a PREFIX match
    (not the new exact slug) reaches those rows."""
    sql = cleanup.delete_sql(cleanup.resolve_prefixes("meta-contract-"))
    assert "slug LIKE 'meta-contract-%'" in sql
