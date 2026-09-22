"""Tests for the shared GitHub ontology mapper (#1725 Slice 0 / #1714).

Pure unit tests — no DB, no SDK. Pins the SINGLE eventId/eventKind
vocabulary (the #1155 normalization), the statement prop contract
(P2-6: never ``github_state``), monotonic version ids (P1-2), and
gh-CLI/REST shape equivalence.

#1844 (object-only): the statement tests below pin the mapper helper that
is now DORMANT — ``issue_to_statements`` is reserved for #1843 (issue
impact analysis) and is no longer called by the default ingest path. The
unit tests stay as the contract for the future analyzer.
"""
from __future__ import annotations

import pytest  # noqa: F401

from tortoise import github_map as gm

# ── fixtures ───────────────────────────────────────────────────────

REST_ISSUE = {
    "number": 42,
    "title": "Fix login bug",
    "state": "open",
    "created_at": "2026-07-10T10:00:00Z",
    "updated_at": "2026-07-19T12:00:00Z",
    "closed_at": None,
    "html_url": "https://github.com/test/repo/issues/42",
    "body": "The bug is in the parser.",
    "labels": [{"name": "complexity:micro"}],
    "assignees": [{"login": "bob"}],
    "user": {"login": "alice"},
}

GHCLI_ISSUE = {
    "number": 42,
    "title": "Fix login bug",
    "state": "open",
    "createdAt": "2026-07-10T10:00:00Z",
    "updatedAt": "2026-07-19T12:00:00Z",
    "closedAt": None,
    "url": "https://github.com/test/repo/issues/42",
    "body": "The bug is in the parser.",
    "labels": [{"name": "complexity:micro"}],
    "assignees": [{"login": "bob"}],
    "author": {"login": "alice"},
}


# ── eventId/eventKind vocabulary ───────────────────────────────────

def test_creation_event_id_and_kind():
    """First ingest → `-created` event, kind carries the fetched state."""
    ev = gm.issue_to_event(REST_ISSUE, "test/repo")
    assert ev["eventId"] == "github-issue-test/repo-42-created"
    assert ev["eventKind"] == "github.issue.open"
    assert ev["subject"] == "issue:test/repo#42"
    assert ev["object"] == "test/repo#42"
    assert ev["sourceKind"] == "github_issue"


def test_creation_event_closed_kind():
    """First ingest of an ALREADY-CLOSED issue → `-created` ONLY, with the
    closed kind (pinned at test_github_connector:30-31)."""
    closed = dict(REST_ISSUE, state="closed", closed_at="2026-07-19T12:00:00Z")
    ev = gm.issue_to_event(closed, "test/repo")
    assert ev["eventId"] == "github-issue-test/repo-42-created"
    assert ev["eventKind"] == "github.issue.closed"
    assert ev["endedAt"] == "2026-07-19T12:00:00Z"


def test_transition_events_mint_distinct_ids():
    """open→closed mints `-closed`; closed→open mints `-reopened`."""
    closed = dict(REST_ISSUE, state="closed", closed_at="2026-07-19T12:00:00Z")
    ev = gm.issue_to_event(closed, "test/repo", previous_state="open")
    assert ev["eventId"] == "github-issue-test/repo-42-closed"
    assert ev["eventKind"] == "github.issue.closed"
    assert ev["endedAt"] == "2026-07-19T12:00:00Z"

    reopened = dict(REST_ISSUE, state="open", closed_at=None)
    ev = gm.issue_to_event(reopened, "test/repo", previous_state="closed")
    assert ev["eventId"] == "github-issue-test/repo-42-reopened"
    assert ev["eventKind"] == "github.issue.reopened"
    assert ev["endedAt"] is None


def test_no_transition_event_on_same_state():
    assert gm.issue_to_event(REST_ISSUE, "test/repo", previous_state="open") is None
    closed = dict(REST_ISSUE, state="closed", closed_at="2026-07-19T12:00:00Z")
    assert gm.issue_to_event(closed, "test/repo", previous_state="closed") is None


def test_event_id_never_emits_observation_or_github_state():
    """The mapper NEVER emits the removed `observation` kind or github_state."""
    for issue in (REST_ISSUE, GHCLI_ISSUE):
        ev = gm.issue_to_event(issue, "test/repo")
        assert ev["eventKind"].startswith("github.issue.")
        assert "github_state" not in ev


# ── gh-CLI vs REST shape equivalence ───────────────────────────────

def test_ghcli_and_rest_shapes_map_identically():
    """Field-name/casing adaptation is pinned: both producers emit
    byte-identical eventIds/kinds/subjects."""
    a = gm.issue_to_event(REST_ISSUE, "test/repo")
    b = gm.issue_to_event(GHCLI_ISSUE, "test/repo")
    assert a == b
    oa = gm.issue_to_object(REST_ISSUE, "test/repo")
    ob = gm.issue_to_object(GHCLI_ISSUE, "test/repo")
    assert oa == ob
    sa, _ = gm.issue_to_subjects(REST_ISSUE)
    sb, _ = gm.issue_to_subjects(GHCLI_ISSUE)
    assert sa == sb


# ── Subjects ───────────────────────────────────────────────────────

def test_issue_to_subjects_dedupes_author_and_assignees():
    subjects, about_ids = gm.issue_to_subjects(REST_ISSUE)
    assert [s["id"] for s in subjects] == ["github-user:alice", "github-user:bob"]
    assert [s["subject_kind"] for s in subjects] == ["naturalPerson"] * 2
    assert about_ids == ["github-user:alice", "github-user:bob"]


def test_issue_to_subjects_null_author_ok():
    issue = dict(REST_ISSUE, user=None, assignees=[])
    subjects, about_ids = gm.issue_to_subjects(issue)
    assert subjects == []
    assert about_ids == []


# ── Statements (DORMANT — reserved for #1843) ─────────────────────
# #1844 object-only: the default ingest path no longer writes statement
# Points; ``issue_to_statements`` is kept as the dormant basis for #1843
# (issue impact analysis). These unit tests pin that helper's contract
# (id scheme, props contract, versioning) for the future analyzer.

def test_statement_external_id_unique_per_issue():
    s1 = gm.issue_to_statements(REST_ISSUE, "test/repo", version=1)
    other = dict(REST_ISSUE, number=99, title="Other")
    s2 = gm.issue_to_statements(other, "test/repo", version=1)
    assert s1[0]["props"]["externalId"] == "github:issue:test/repo#42"
    assert s2[0]["props"]["externalId"] == "github:issue:test/repo#99"
    assert s1[0]["props"]["externalId"] != s2[0]["props"]["externalId"]


def test_statement_props_contract_only():
    """P2-6: statement props = {externalId, extractedFrom, source,
    github_repo, github_number, github_url} ONLY — github_state IS NULL
    (never emitted)."""
    rec = gm.issue_to_statements(REST_ISSUE, "test/repo", version=1)[0]
    assert set(rec["props"].keys()) == set(gm.STATEMENT_PROP_KEYS)
    assert "github_state" not in rec["props"]
    assert "observation" not in rec["pointKind"]
    assert rec["pointKind"] == "statement"
    assert rec["props"]["github_repo"] == "test/repo"
    assert rec["props"]["github_number"] == 42
    assert rec["props"]["github_url"] == "https://github.com/test/repo/issues/42"
    assert rec["props"]["extractedFrom"] == "https://github.com/test/repo/issues/42"


def test_statement_version_suffix_monotonic():
    """P1-2: version suffix changes the id; a revert to prior content mints
    v+1 — never reuses the superseded v1 id."""
    rec_v1 = gm.issue_to_statements(REST_ISSUE, "test/repo", version=1)[0]
    edited = dict(REST_ISSUE, body="The bug is in the lexer now.")
    rec_v2 = gm.issue_to_statements(edited, "test/repo", version=2)[0]
    # revert to v1 content → v3 (NEW id, never the superseded v1 id)
    rec_v3 = gm.issue_to_statements(REST_ISSUE, "test/repo", version=3)[0]
    assert rec_v1["id"] != rec_v2["id"] != rec_v3["id"]
    assert rec_v1["props"]["externalId"] == rec_v2["props"]["externalId"] \
        == rec_v3["props"]["externalId"]
    assert rec_v1["id"].endswith("_1")
    assert rec_v2["id"].endswith("_2")
    assert rec_v3["id"].endswith("_3")
    # v3 content hash prefix equals v1's (reverted content) but id differs
    assert rec_v1["id"].split("_")[-2] == rec_v3["id"].split("_")[-2]
    assert rec_v1["id"] != rec_v3["id"]


def test_statement_id_deterministic():
    a = gm.statement_id("test/repo", 42, "content", 1)
    b = gm.statement_id("test/repo", 42, "content", 1)
    assert a == b == "pt_gh_test/repo_42_" + \
        __import__("hashlib").sha256(b"content").hexdigest()[:12] + "_1"


def test_statement_truncates_body():
    issue = dict(REST_ISSUE, body="x" * 10000)
    rec = gm.issue_to_statements(issue, "test/repo", version=1)[0]
    assert len(rec["content"]) <= gm._MAX_CONTENT_CHARS


def test_statements_skip_titleless():
    assert gm.issue_to_statements(dict(REST_ISSUE, title=""), "test/repo") == []


# ── Lifecycle diff ─────────────────────────────────────────────────

def test_diff_lifecycle_open_closed_reopened():
    open_issue = dict(REST_ISSUE, state="open")
    closed_issue = dict(REST_ISSUE, state="closed", closed_at="2026-07-19T12:00:00Z")
    # first ingest → no transitions (creation handled separately)
    assert gm.diff_lifecycle(None, open_issue) == []
    # open → closed
    assert gm.diff_lifecycle(open_issue, closed_issue) == ["closed"]
    # closed → reopened
    assert gm.diff_lifecycle(closed_issue, open_issue) == ["reopened"]
    # unchanged states
    assert gm.diff_lifecycle(open_issue, open_issue) == []
    assert gm.diff_lifecycle(closed_issue, closed_issue) == []


# ── Object ─────────────────────────────────────────────────────────

def test_issue_to_object():
    obj = gm.issue_to_object(REST_ISSUE, "test/repo")
    assert obj["type"] == "ObjectRegistered"
    assert obj["id"] == "github-issue-test/repo-42"
    assert obj["name"] == "test/repo#42"
    assert obj["object_kind"] == "pm:issue"
    assert obj["title"] == "Fix login bug"
    assert obj["complexity"] == "standard"  # labels not routed by mapper


def test_issue_to_object_routing_passthrough():
    obj = gm.issue_to_object(REST_ISSUE, "test/repo",
                             routed_team="epistemic-team", routed_role="ops",
                             routed_product="acme", complexity="complex", ux_rating="medium")
    assert obj["routed_team"] == "epistemic-team"
    assert obj["routed_role"] == "ops"
    assert obj["routed_product"] == "acme"
    assert obj["complexity"] == "complex"
    assert obj["ux_rating"] == "medium"


def test_issue_to_object_skips_titleless():
    assert gm.issue_to_object(dict(REST_ISSUE, title=""), "test/repo") is None


# ── PR ─────────────────────────────────────────────────────────────

def test_pr_to_event_open():
    ev = gm.pr_to_event({
        "number": 7, "title": "Add auth module", "state": "open",
        "createdAt": "2026-07-15T08:00:00Z", "closedAt": "",
        "mergedAt": "", "url": "...",
    }, "test/repo")
    assert ev["eventId"] == "github-pr-test/repo-7"
    assert ev["eventKind"] == "github.pr.open"
    assert ev["endedAt"] is None
    assert ev["sourceKind"] == "github_pr"


def test_pr_to_event_merged_and_closed():
    merged = gm.pr_to_event({
        "number": 7, "title": "Add auth module", "state": "closed",
        "createdAt": "2026-07-15T08:00:00Z",
        "closedAt": "2026-07-16T10:00:00Z", "mergedAt": "2026-07-16T10:00:00Z",
        "url": "...",
    }, "test/repo")
    assert merged["eventKind"] == "github.pr.merged"
    assert merged["endedAt"] == "2026-07-16T10:00:00Z"

    unmerged = gm.pr_to_event({
        "number": 8, "title": "Rejected idea", "state": "closed",
        "createdAt": "2026-07-15T08:00:00Z",
        "closedAt": "2026-07-16T10:00:00Z", "mergedAt": None,
        "url": "...",
    }, "test/repo")
    assert unmerged["eventKind"] == "github.pr.closed"
