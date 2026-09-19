"""#4179 — the retention/deletion promise, enforced at the test layer.

Canonical document: ``docs/retention-and-deletion.md``.

This module is the anti-scatter falsifier for the one promise: the 7-day
restore window is a single authority, the frontend copy is bound to it, the
backup-lock bound stays below it, and no promise-bearing file states a
retention/deletion number without either linking the canonical doc or being one
of the named implementation constants.

Imports of the modules under change live *inside* the test bodies so a
pre-implementation run fails on the assertion/attribute, not at collection.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL_DOC = REPO / "docs" / "retention-and-deletion.md"
CANONICAL_DOC_REL = "docs/retention-and-deletion.md"
CANONICAL_DOC_URL = (
    "https://github.com/daniel-ospina/tortoise/blob/main/docs/retention-and-deletion.md"
)


# ── 1. the three paths agree on one 7-day window ────────────────────────────

def test_restore_window_constants_agree():
    """All three deletion paths read one window. Must be RED before #4179.

    RED today: the team default is 24h and the user-account constant does not
    exist. GREEN after: every path derives from ``retention.RESTORE_WINDOW``.
    """
    from tortoise import retention
    from tortoise.backup_sweep import _GRAPH_PURGE_GRACE_DAYS
    from tortoise.hosted_api import (
        TEAM_DELETE_GRACE_HOURS,
        USER_ACCOUNT_DELETE_GRACE_HOURS,
    )

    assert retention.RESTORE_WINDOW_DAYS == 7
    assert retention.RESTORE_WINDOW_HOURS == 168
    # unit-normalized: the graph path is days, the account paths are hours.
    assert _GRAPH_PURGE_GRACE_DAYS * 24 == retention.RESTORE_WINDOW_HOURS
    assert TEAM_DELETE_GRACE_HOURS == retention.RESTORE_WINDOW_HOURS
    # Documentation pin only — there is no user-account deletion code path yet
    # (privacy §16: no self-service deletion). This records the promised window
    # for the support request; it is not a behaviour assertion.
    assert USER_ACCOUNT_DELETE_GRACE_HOURS == retention.RESTORE_WINDOW_HOURS


def test_lock_bound_stays_below_restore_window():
    """The R2 bucket-lock bound must stay strictly below the purge window, so
    a purged graph's artifacts are never inside the lock window."""
    from tortoise import retention
    from tortoise.backup_config import LOCK_DAYS_MAX

    assert LOCK_DAYS_MAX < retention.RESTORE_WINDOW_DAYS


# ── 2. the frontend window copy is bound to the server authority ────────────

def test_frontend_trash_grace_matches_authority():
    """``graphs.js`` cannot import Python, so the dashboard's copy could drift.
    Parse it and bind it to the authority."""
    from tortoise import retention

    js = (REPO / "website" / "apps" / "dashboard" / "src" / "graphs.js").read_text(
        encoding="utf-8"
    )
    m = re.search(r"export const TRASH_GRACE_DAYS\s*=\s*(\d+)", js)
    assert m, "graphs.js must declare `export const TRASH_GRACE_DAYS = <n>`"
    assert int(m.group(1)) == retention.RESTORE_WINDOW_DAYS


# ── 3. the privacy page states the path-qualified horizon ───────────────────
# Ships with the owner-gated wording commit (privacy.html + dpa.html). If that
# commit is dropped, this test is dropped with it and the suite stays green.

def test_privacy_page_states_backup_horizon_without_contradiction():
    page = (REPO / "website" / "privacy.html").read_text(encoding="utf-8")
    # The old unbounded claim must be gone.
    assert "may retain data for a limited additional period after deletion" not in page
    # The horizon must be named, and the canonical doc linked.
    assert re.search(r"(four weeks|4 weeks|28 days)", page, re.I), (
        "privacy §6 must state the backup horizon"
    )
    assert CANONICAL_DOC_URL in page, "privacy page must link the canonical doc"


# ── 4. the anti-scatter scan ────────────────────────────────────────────────
# Recursive over the declared promise-bearing roots, with a tight
# deletion/retention-window pattern. Historical point-in-time records are
# excluded with a stated reason; live promise-bearing files are either linked
# (checked below) or on the explicit allowlist. A NEW hit fails the test.

SCAN_ROOTS = (
    ("docs", "*.md"),
    ("website", "*.html"),
    ("website/apps/dashboard/src", "*"),
    ("tortoise", "*.py"),
    ("supabase/migrations", "*.sql"),
)

# Point-in-time records — they state the window as it was; the canonical doc
# supersedes them. Excluded by prefix, with a reason.
EXCLUDED_PREFIXES = {
    "docs/drafts/": "historical draft (point-in-time)",
    "docs/epics/": "epic record (point-in-time)",
    "docs/plans/": "plan record (point-in-time)",
    "docs/prototypes/": "prototype record (point-in-time)",
    "docs/research/": "research record (point-in-time)",
    "docs/runbook/": "runbook record (point-in-time)",
    "docs/scoping/": "scoping record (point-in-time)",
    "docs/product-success-eval.md": "product eval metric — not a deletion window",
}

# Files that legitimately state a window. Built by running the scan and
# auditing every hit; a NEW hit fails the test, which is the point.
SCATTER_ALLOWLIST: dict[str, str] = {
    "docs/infra-runbook.md": "links the canonical doc",
    "docs/ops/registry-backup-dr.md": "links the canonical doc",
    "docs/registry-graph-schema.md": "links the canonical doc",
    "docs/scoping-2304-delete-semantics.md": "graph-delete semantics record — links the canonical doc",
    "docs/scoping-2313-per-graph-backups.md": "backup scoping record — links the canonical doc",
    "tortoise/hosted_backup.py": "backup internals — links the canonical doc",
    "website/apps/dashboard/src/graphs.js": "frontend window constant — links the canonical doc",
    "website/apps/dashboard/src/main.jsx": "renders TRASH_GRACE_DAYS — links the canonical doc",
    "website/dpa.html": "owner-gated wording commit — links the canonical doc",
    "website/privacy.html": "owner-gated wording commit — links the canonical doc",
    # unrelated numbers (not deletion/retention windows)
    "website/apps/dashboard/src/graphs.test.js": "derives its expectation from graphs.js",
    "website/apps/dashboard/src/graphsBackupColumnTripwire.test.js": "'retained' in a backup-list assertion — not a window",
    "supabase/migrations/20260906000001_graphs_deleted_at.sql": "additive schema-history migration comment",
}

# Files in the canonical-doc link gate (main commit). privacy.html/dpa.html are
# covered by the owner-gated privacy test above, not here.
LINKED_FILES = {
    "docs/infra-runbook.md",
    "docs/ops/registry-backup-dr.md",
    "docs/registry-graph-schema.md",
    "docs/scoping-2304-delete-semantics.md",
    "docs/scoping-2313-per-graph-backups.md",
    "docs/data-safety.md",
    "docs/00_index.md",
    "tortoise/hosted_backup.py",
    "website/apps/dashboard/src/graphs.js",
    "website/apps/dashboard/src/main.jsx",
}

# A tight deletion/retention-WINDOW statement — not any occurrence of "7 days".
_CLAIM_RE = re.compile(
    r"(?i)(?:"
    r"(?:recover|restore|trash|grace|purge|eras|delet|permanently|retention|retain)"
    r"[^\n]{0,45}\b7\b[^\n]{0,20}\bdays?\b"
    r"|\b7\b[^\n]{0,20}\bdays?\b[^\n]{0,45}"
    r"(?:recover|restore|trash|grace|purge|eras|delet|permanently|retention|retain)"
    r"|\b(?:four weeks|4 weeks|28 days|168 hours)\b"
    r"|\b24\s*h(?:ours?)?\b[^\n]{0,25}\bgrace\b"
    r"|\bgrace\b[^\n]{0,25}\b24\s*h(?:ours?)?\b"
    r"|limited additional period"
    r"|(?:backups?|backup copies)[^\n]{0,30}(?:erased|kept|retained|horizon|four weeks|limited)"
    r"|(?:erased|permanently erased)[^\n]{0,30}backups?"
    r")"
)

# Code files whose only hits are the named implementation constants — the
# canonical doc names them, so they are exempt by design (category ii).
_NAMED_CONSTANT_FILES = {
    "tortoise/retention.py",
    "tortoise/backup_sweep.py",
    "tortoise/backup_config.py",
    "tortoise/hosted_api.py",
    "tortoise/sdk.py",
    "tortoise/supabase_control.py",
}


def _scanned_files():
    for root, pattern in SCAN_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in base.rglob(pattern):
            if path.is_file() and "/node_modules/" not in path.as_posix():
                yield path


def _is_excluded(rel: str) -> bool:
    return any(rel.startswith(p) for p in EXCLUDED_PREFIXES)


def test_scatter_scan_reaches_nested_files_and_frontend():
    """Set-containment of sentinels — a narrowed glob must fail here even if
    the claim scan would otherwise pass vacuously."""
    scanned = {p.relative_to(REPO).as_posix() for p in _scanned_files()}
    for sentinel in (
        "docs/ops/registry-backup-dr.md",
        "docs/research/2026-09-06-backup-dr-best-practices.md",
        "website/apps/dashboard/src/graphs.js",
        "tortoise/hosted_backup.py",
        "docs/event-catalog.md",
    ):
        assert sentinel in scanned, f"scan root is too narrow — missing {sentinel}"


def test_surveyed_files_link_to_canonical_doc():
    """The live, promise-bearing files must name the canonical doc (or be a
    named implementation constant)."""
    missing = []
    for rel in sorted(LINKED_FILES):
        path = REPO / rel
        assert path.is_file(), f"{rel} is missing"
        text = path.read_text(encoding="utf-8", errors="ignore")
        if CANONICAL_DOC_REL not in text and CANONICAL_DOC_URL not in text:
            missing.append(rel)
    assert not missing, (
        "promise-bearing files must link " + CANONICAL_DOC_REL + ": "
        + ", ".join(missing)
    )


def test_no_unlinked_retention_claims():
    """No promise-bearing file states a retention/deletion number without
    either linking the canonical doc or being a named implementation constant."""
    offenders: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(REPO).as_posix()
        if rel == CANONICAL_DOC_REL or rel in _NAMED_CONSTANT_FILES:
            continue
        if _is_excluded(rel) or rel in SCATTER_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if _CLAIM_RE.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()[:110]}")
    assert not offenders, (
        "retention/deletion claim outside the canonical doc and the named "
        "constants — link " + CANONICAL_DOC_REL + " or add a reasoned "
        "allowlist entry:\n" + "\n".join(offenders)
    )


def test_allowlist_entries_exist():
    """A stale allowlist entry (renamed/removed file) must not rot silently."""
    missing = [rel for rel in SCATTER_ALLOWLIST if not (REPO / rel).is_file()]
    assert not missing, f"allowlist entries no longer exist: {missing}"


# ── 5. the canonical doc exists and names the authority ─────────────────────

def test_canonical_doc_exists_and_names_the_constants():
    assert CANONICAL_DOC.is_file(), f"{CANONICAL_DOC_REL} must exist"
    text = CANONICAL_DOC.read_text(encoding="utf-8")
    assert "RESTORE_WINDOW_DAYS" in text
    assert "_GRAPH_PURGE_GRACE_DAYS" in text
    assert "retention_hourly" in text
    assert "restore window" in text.lower()
