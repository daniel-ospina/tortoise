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


# ── 1. the implemented deletion paths agree; the user path is a doc pin ─────

def test_restore_window_constants_agree():
    """The two IMPLEMENTED deletion paths (graph, team) read one window. The
    user-account constant is asserted separately as a DOCUMENTATION PIN — it
    has no consumer path, so this test must not claim a third wired path.

    RED before #4179: the team default was 24h and the user-account constant
    did not exist. GREEN after: every realised path derives from
    ``retention.RESTORE_WINDOW``.
    """
    from tortoise import retention
    from tortoise.backup_sweep import _GRAPH_PURGE_GRACE_DAYS
    from tortoise.hosted_api import TEAM_DELETE_GRACE_HOURS

    assert retention.RESTORE_WINDOW_DAYS == 7
    assert retention.RESTORE_WINDOW_HOURS == 168
    # unit-normalized: the graph path is days, the team path is hours.
    assert _GRAPH_PURGE_GRACE_DAYS * 24 == retention.RESTORE_WINDOW_HOURS
    assert TEAM_DELETE_GRACE_HOURS == retention.RESTORE_WINDOW_HOURS


def test_user_account_window_is_a_documentation_pin():
    """The user-account path has no deletion code today (privacy §16: no
    self-service deletion). ``USER_ACCOUNT_DELETE_GRACE_HOURS`` records the
    promised support/email window and is NOT read by any production module —
    this pins the value and the fact that it is a promise, not behaviour.

    Wiring it is a real feature (missing Supabase auth-admin deletion); if a
    consumer appears, replace this pin with a behaviour assertion.
    """
    from tortoise import retention
    from tortoise.hosted_api import USER_ACCOUNT_DELETE_GRACE_HOURS

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
    Parse it and bind it to the authority; ``main.jsx`` renders the window and
    is allowlisted in the scatter scan on the claim that it derives the number,
    so bind it here too — a bare literal cannot hide behind the allowlist."""
    from tortoise import retention

    src = REPO / "website" / "apps" / "dashboard" / "src"
    js = (src / "graphs.js").read_text(encoding="utf-8")
    m = re.search(r"export const TRASH_GRACE_DAYS\s*=\s*(\d+)", js)
    assert m, "graphs.js must declare `export const TRASH_GRACE_DAYS = <n>`"
    assert int(m.group(1)) == retention.RESTORE_WINDOW_DAYS

    main = (src / "main.jsx").read_text(encoding="utf-8")
    assert "TRASH_GRACE_DAYS" in main, (
        "main.jsx must interpolate TRASH_GRACE_DAYS, not restate the window"
    )
    # Comments may narrate the window; rendered code must not hardcode it.
    code = re.sub(r"/\*.*?\*/", " ", main, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    n = retention.RESTORE_WINDOW_DAYS
    bare = re.findall(rf"\b{n}\s*-?\s*d(?:ays?)?\b", code, re.I)
    assert not bare, f"main.jsx hardcodes the restore window: {bare}"


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
    ("config", "*"),
)

# Single config surfaces outside the roots above (the env template states the
# window as "grace (7d)" — a compact form the scan must see).
SCAN_FILES = (".env.example", "fly.toml")

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
    # #2881: the durability authority. It must state the VENDOR's snapshot
    # retention (7-day) and deletion (14-day) windows as verified facts, and its
    # own loss windows — a different axis from the deletion promise. It links
    # docs/retention-and-deletion.md in prose.
    "docs/durability-posture.md": "durability authority — states vendor snapshot retention/loss windows; links the canonical doc",
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
    "website/apps/dashboard/src/graphs.test.js": "derives its expectation from TRASH_GRACE_DAYS (graphs.js)",
    "website/apps/dashboard/src/graphsBackupColumnTripwire.test.js": "'retained' in a backup-list assertion — not a window",
    "docs/scoping-432-subscriptions-claim-lifecycle.md": "event-store (30-day) retention — a different axis, not a deletion promise",
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
    "docs/durability-posture.md",
    "docs/00_index.md",
    "tortoise/hosted_backup.py",
    "website/apps/dashboard/src/graphs.js",
    "website/apps/dashboard/src/main.jsx",
}

# Small modules that ARE the named-constant definitions — exempt as a whole
# (every line in them is the authority).
_NAMED_CONSTANT_MODULES = {
    "tortoise/retention.py",
    "tortoise/backup_config.py",
}

# Large modules that merely USE the constants — exempt ONLY on lines that name
# a constant or link the canonical doc. A whole-file exemption on a ~24k-line
# module hid a new independent literal (#4179 review).
_NAMED_CONSTANT_USER_FILES = {
    "tortoise/backup_sweep.py",
    "tortoise/hosted_api.py",
    "tortoise/sdk.py",
    "tortoise/supabase_control.py",
}

# The canonical constant names. A claim line in a user file is exempt when it
# names one of these (the number is then traceable to the sole authority).
_NAMED_CONSTANT_RE = re.compile(
    r"\b(?:RESTORE_WINDOW_(?:DAYS|HOURS)|_GRAPH_PURGE_GRACE_DAYS"
    r"|_TRASH_GRACE_DAYS|TEAM_DELETE_GRACE_HOURS|USER_ACCOUNT_DELETE_GRACE_HOURS"
    r"|LOCK_DAYS_MAX|retention_(?:hourly|daily|weekly))\b"
)


# A deletion/restore/backup/retention WINDOW NOUN. The window branches require
# one nearby, so an unrelated "our sprint cadence is 4 weeks", a "trial lasts
# 28 days", a 24h display threshold, or a 30-day decay half-life cannot fail
# the gate (#4179 review). "retain" only counts as "retained for" (a bare
# "retained key"/"retained-pool" is not a window), and "recover" excludes the
# hyphenated "recovery-velocity".
_WINDOW_NOUN = (
    r"(?:restor\w*|recover\w*(?![\w-])|trash|grace|purg\w*|"
    r"retention(?![\w-])|retain\w*\s+for|deleted|deletion|delet\w*\s+for|"
    r"eras\w*|permanent\w*)"
)
# The window literals — long, numeric, and compact forms. Compact forms
# (7d / 7-day) are included because `.env.example` states the window as
# "grace (7d)"; "24 hours" and "30 days" are covered because both appear in
# promise-bearing copy (#4179 review).
_WINDOW_RE = (
    r"(?:\b7\s*-?\s*d(?:ays?)?\b"
    r"|\b168\s*-?\s*h(?:ours?|rs?|r)?\b"
    r"|\b24\s*-?\s*h(?:ours?|rs?|r)?\b"
    r"|\b28\s*-?\s*days?\b"
    r"|\b30\s*-?\s*days?\b"
    r"|\bfour\s+weeks?\b"
    r"|\b4\s*-?\s*weeks?\b)"
)
_CLAIM_RE = re.compile(
    r"(?i)(?:"
    rf"{_WINDOW_NOUN}[^\n]{{0,45}}{_WINDOW_RE}"
    rf"|{_WINDOW_RE}[^\n]{{0,45}}{_WINDOW_NOUN}"
    r"|limited additional period"
    r"|(?:\bbackups?\b|backup copies)[^\n]{0,30}(?:erased|kept|retained|horizon|limited)"
    r"|(?:erased|permanently erased)[^\n]{0,30}\bbackups?\b"
    r")"
)


def _scanned_files():
    for rel in SCAN_FILES:
        path = REPO / rel
        if path.is_file():
            yield path
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
        if rel == CANONICAL_DOC_REL or rel in _NAMED_CONSTANT_MODULES:
            continue
        if _is_excluded(rel) or rel in SCATTER_ALLOWLIST:
            continue
        line_scoped = rel in _NAMED_CONSTANT_USER_FILES
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if not _CLAIM_RE.search(line):
                continue
            if CANONICAL_DOC_REL in line or CANONICAL_DOC_URL in line:
                continue  # the claim links the authority
            if line_scoped and _NAMED_CONSTANT_RE.search(line):
                continue  # the claim names a canonical constant
            offenders.append(f"{rel}:{i}: {line.strip()[:110]}")
    assert not offenders, (
        "retention/deletion claim outside the canonical doc and the named "
        "constants — link " + CANONICAL_DOC_REL + " or add a reasoned "
        "allowlist entry:\n" + "\n".join(offenders)
    )


def test_claim_regex_covers_new_forms_and_rejects_unrelated_numbers():
    """#4179 review: the scan must catch the compact/numeric forms the plan
    promised (30 days / 24 hours / 7d) AND must not fire on an unrelated
    number that merely sits near a soft context word."""
    for claim in (
        "deleted data is retained for 30 days",
        "backups are retained for 24 hours",
        "the trash grace is 7d",
        "restore it for 7 days",
    ):
        assert _CLAIM_RE.search(claim), f"scan misses a real claim: {claim!r}"
    for unrelated in (
        "our sprint cadence is 4 weeks and the trial lasts 28 days",
        "returns a locale date past 24h — the retained-pool case",
        "the fix is not to delete it but to shorten the 30-day half-life",
        "BACKUP_KEY_PREVIOUS=   # RETAINED key during a rotation overlap",
    ):
        assert not _CLAIM_RE.search(unrelated), (
            f"scan false-positives on unrelated copy: {unrelated!r}"
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
