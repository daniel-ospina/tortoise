"""#388: connector Source materialization — projection choke point, idempotency,
mining regression, provenance chain, EP neutrality, list_sources regression.

Embedded real-path suite (no Docker) — applies GitHub/Linear/Slack-shaped
EventRecorded dicts through the same `proj.apply` → `_upsert_event` choke point
the connectors use, then asserts graph shape via raw Cypher + SDK consumers.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

FRESH = "2024-01-01T00:00:00+00:00"

GH_URL = "https://github.com/test/repo/issues/42"


@contextmanager
def fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_connsrc_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def _count(proj, label: str, **props) -> int:
    """Count nodes with a label + optional property constraints."""
    if props:
        where = " AND ".join(f"n.{k} = ${k}" for k in props)
        r = proj.g.query(
            f"MATCH (n:{label}) WHERE {where} RETURN count(n)",
            params={**props},
        )
    else:
        r = proj.g.query(f"MATCH (n:{label}) RETURN count(n)")
    return int(r.result_set[0][0])


def _edge_count(proj, a_label: str, a_prop: str, a_val: str,
                rel: str, b_label: str, b_prop: str, b_val: str) -> int:
    r = proj.g.query(
        f"MATCH (a:{a_label} {{{a_prop}: $av}})-[:{rel}]->"
        f"(b:{b_label} {{{b_prop}: $bv}}) RETURN count(*)",
        params={"av": a_val, "bv": b_val},
    )
    return int(r.result_set[0][0])


# ── Connector-shaped EventRecorded builders ─────────────────────────

def gh_issue_event(**over) -> dict:
    ev = {
        "type": "EventRecorded",
        "eventId": "github-issue-test/repo-42",
        "eventKind": "github.issue.open",
        "subject": "issue:test/repo#42",
        "object": "Fix login bug",
        "startedAt": "2026-07-10T10:00:00Z",
        "endedAt": None,
        "source": "github:test/repo",
        "sourceUrl": GH_URL,
        "sourceKind": "github_issue",
        "participants": [],
    }
    ev.update(over)
    return ev


def gh_pr_event(**over) -> dict:
    ev = gh_issue_event(
        eventId="github-pr-test/repo-7",
        eventKind="github.pr.open",
        subject="pr:test/repo#7",
        sourceUrl="https://github.com/test/repo/pull/7",
        sourceKind="github_pr",
    )
    ev.update(over)
    return ev


def linear_issue_event(**over) -> dict:
    ev = {
        "type": "EventRecorded",
        "eventId": "linear-issue-TEAM-42",
        "eventKind": "linear.issue.in_progress",
        "subject": "issue:linear:TEAM-42",
        "object": "Fix login flow",
        "startedAt": "2026-07-15T10:00:00Z",
        "endedAt": None,
        "source": "linear:TEAM",
        "sourceUrl": "https://linear.app/test/issue/TEAM-42",
        "sourceKind": "linear_card",
        "participants": [],
    }
    ev.update(over)
    return ev


def linear_cycle_event(**over) -> dict:
    ev = {
        "type": "EventRecorded",
        "eventId": "linear-cycle-ENG-5",
        "eventKind": "linear.cycle.active",
        "subject": "cycle:linear:ENG#5",
        "object": "Sprint 5",
        "startedAt": "2026-07-01T00:00:00Z",
        "endedAt": None,
        "source": "linear:ENG",
        "sourceUrl": "linear:ENG",  # container-level fallback (no web URL)
        "sourceKind": "linear_cycle",
        "participants": [],
    }
    ev.update(over)
    return ev


def slack_event(**over) -> dict:
    ev = {
        "type": "EventRecorded",
        "eventId": "slack-msg-C01-1690000000-123456",
        "eventKind": "slack.message",
        "subject": "slack:C01:U01",
        "object": "Hello world",
        "startedAt": "2023-07-22T14:26:40.123456+00:00",
        "endedAt": None,
        "source": "slack:C01",
        "sourceUrl": "https://ws.slack.com/archives/C01/p1690000000.123456",
        "sourceKind": "slack_message",
        "participants": ["U01"],
    }
    ev.update(over)
    return ev


# ═══════════════════════════════════════════════════════════════════════
# Choke-point materialization
# ═══════════════════════════════════════════════════════════════════════

class TestChokePointMaterialization:
    def test_github_issue_event_creates_one_source_with_references(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_issue_event())
            assert _count(proj, "Source") == 1
            rows = proj.g.query("MATCH (s:Source) RETURN s.url, s.sourceKind").result_set
            assert rows[0][0] == GH_URL
            assert rows[0][1] == "github_issue"
            assert _edge_count(proj, "Source", "url", GH_URL,
                               "references", "Event", "eventId",
                               "github-issue-test/repo-42") == 1

    def test_github_pr_event_materializes_with_pr_kind(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_pr_event())
            rows = proj.g.query("MATCH (s:Source) RETURN s.sourceKind").result_set
            assert rows[0][0] == "github_pr"  # NOT github_issue (#388 fix)

    def test_linear_issue_event_materializes(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(linear_issue_event())
            rows = proj.g.query(
                "MATCH (s:Source) RETURN s.url, s.sourceKind"
            ).result_set
            assert rows[0][0] == "https://linear.app/test/issue/TEAM-42"
            assert rows[0][1] == "linear_card"

    def test_linear_cycle_event_materializes_with_fallback_key(self):
        """Cycles have no web URL — the Source keys on the container-level
        `linear:{team_key}` string and carries sourceKind linear_cycle."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(linear_cycle_event())
            rows = proj.g.query(
                "MATCH (s:Source) RETURN s.url, s.sourceKind"
            ).result_set
            assert rows[0][0] == "linear:ENG"
            assert rows[0][1] == "linear_cycle"

    def test_slack_event_with_permalink_materializes(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(slack_event())
            rows = proj.g.query(
                "MATCH (s:Source) RETURN s.url, s.sourceKind"
            ).result_set
            assert rows[0][0] == "https://ws.slack.com/archives/C01/p1690000000.123456"
            assert rows[0][1] == "slack_message"

    def test_slack_event_without_permalink_falls_back_to_channel(self):
        """Permalink failure → no sourceUrl → Source keys on `slack:{channel}`."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(slack_event(sourceUrl=None))  # explicit None → absent
            rows = proj.g.query("MATCH (s:Source) RETURN s.url").result_set
            assert rows[0][0] == "slack:C01"
            assert _count(proj, "Source") == 1

    def test_sourceUrl_alone_gate_fires(self):
        """An explicit sourceUrl materializes even with an unregistered kind
        (the linear_cycle path relies on this leg)."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_issue_event(sourceKind="mystery_kind"))
            assert _count(proj, "Source") == 1
            rows = proj.g.query("MATCH (s:Source) RETURN s.sourceKind").result_set
            assert rows[0][0] == "mystery_kind"

    def test_event_without_source_metadata_creates_no_source(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply({
                "type": "EventRecorded",
                "eventId": "ev-x",
                "eventKind": "deployment",
                "subject": "alice",
                "startedAt": "2026-01-01T00:00:00Z",
            })
            assert _count(proj, "Source") == 0

    def test_mining_shaped_event_creates_no_source(self):
        """Regression: mining.py emits `source` WITHOUT sourceKind/sourceUrl —
        the gate must NOT fire on bare `source` (spurious non-URL Sources)."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply({
                "type": "EventRecorded",
                "eventId": "meeting-abc",
                "eventKind": "meeting",
                "subject": "transcript:abc",
                "object": "summary line",
                "startedAt": "2026-01-01T00:00:00Z",
                "source": "conversation:abc",
                "participants": [],
            })
            assert _count(proj, "Source") == 0


# ═══════════════════════════════════════════════════════════════════════
# Idempotency (AC2) — no churn on re-poll, #398 kind-authoritative contract
# ═══════════════════════════════════════════════════════════════════════

class TestIdempotency:
    def test_reapply_same_event_no_churn(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_issue_event())
            proj.apply(gh_issue_event())
            assert _count(proj, "Source") == 1
            assert _count(proj, "Event") == 1
            assert _edge_count(proj, "Source", "url", GH_URL,
                               "references", "Event", "eventId",
                               "github-issue-test/repo-42") == 1
            # no version bump on re-materialization (unlike _upsert_source)
            rows = proj.g.query("MATCH (s:Source) RETURN s.version").result_set
            assert rows[0][0] is None

    def test_reapply_over_pre_existing_document_stub_keeps_kind(self):
        """#398 never-overwrite contract: a pre-existing `_link_source` stub
        (sourceKind 'document') must NOT be re-stamped by connector
        materialization — kind is set on CREATE only."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj._link_source("pt-stub", GH_URL)  # kind 'document'
            proj.apply(gh_issue_event())
            rows = proj.g.query("MATCH (s:Source) RETURN s.sourceKind").result_set
            assert rows[0][0] == "document"

    def test_reapply_over_tiered_source_keeps_kind_and_tier(self):
        """A tiered pre-existing Source keeps BOTH its kind and tier."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj._link_source("pt-stub", GH_URL)
            proj.g.query(
                "MATCH (s:Source {url: $u}) SET s.credibilityTier = 'T3'",
                params={"u": GH_URL},
            )
            proj.apply(gh_issue_event())
            rows = proj.g.query(
                "MATCH (s:Source) RETURN s.sourceKind, s.credibilityTier"
            ).result_set
            assert rows[0][0] == "document"
            assert rows[0][1] == "T3"


# ═══════════════════════════════════════════════════════════════════════
# GitHub entity path — Source → Object reference via explicit sourceObjectId
# ═══════════════════════════════════════════════════════════════════════

class TestEntityPathObjectReference:
    def test_entity_path_wires_object_reference(self):
        """The github entity path emits a second Event (`{entity_id}-created`)
        carrying sourceObjectId → (Source)-[:references]->(Object {id}).
        Both Event nodes + both references edges must exist (two-Events-per-
        issue shape is pre-existing — do not enshrine a one-Event invariant)."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            # Object first (ingest() order: ObjectRegistered → Event → ...)
            proj.apply({
                "type": "ObjectRegistered",
                "id": "github-issue-test/repo-42",
                "name": "test/repo#42",
                "object_kind": "pm:issue",
                "title": "Fix login bug",
                "url": GH_URL,
                "createdAt": "2026-07-10T10:00:00Z",
            })
            # poll-shaped event + entity-path event (both fire in ingest())
            proj.apply(gh_issue_event())
            proj.apply(gh_issue_event(
                eventId="github-issue-test/repo-42-created",
                eventKind="pm:cardCreated",
                object="github-issue-test/repo-42",
                sourceObjectId="github-issue-test/repo-42",
            ))
            # both Event nodes exist
            assert _count(proj, "Event") == 2
            # Source references BOTH events
            assert _edge_count(proj, "Source", "url", GH_URL,
                               "references", "Event", "eventId",
                               "github-issue-test/repo-42") == 1
            assert _edge_count(proj, "Source", "url", GH_URL,
                               "references", "Event", "eventId",
                               "github-issue-test/repo-42-created") == 1
            # Source references the persisted Object by id (NOT by event.object)
            assert _edge_count(proj, "Source", "url", GH_URL,
                               "references", "Object", "id",
                               "github-issue-test/repo-42") == 1

    def test_event_object_never_used_as_object_key(self):
        """AC gate: event.object (a TITLE on poll paths) must never produce a
        Source→Object references edge."""
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_issue_event())  # object = "Fix login bug", no sourceObjectId
            r = proj.g.query("MATCH (:Source)-[:references]->(:Object) RETURN count(*)").result_set
            assert r[0][0] == 0


# ═══════════════════════════════════════════════════════════════════════
# Provenance chain (P4 unblock) — AC5
# ═══════════════════════════════════════════════════════════════════════

class TestProvenanceChain:
    def test_get_provenance_chain_returns_connector_source(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_issue_event())
            p = sdk.create_point("statement", "claim from issue 42",
                                 extractedFrom=GH_URL)
            chain = sdk.get_provenance_chain(p["id"])
            assert len(chain) == 1
            src = chain[0]["source"]
            assert src["url"] == GH_URL
            assert src["sourceKind"] == "github_issue"
            assert chain[0]["labels"] == ["Event"]


# ═══════════════════════════════════════════════════════════════════════
# EP neutrality (AC6) — connector kinds are neutral → no weight change
# ═══════════════════════════════════════════════════════════════════════

class TestEPNeutrality:
    def test_connector_sources_do_not_change_point_weights(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            # Control: a T4-tiered point (alpha = 1.1 at decay 1.0)
            p_tiered = sdk.create_point("statement", "tiered claim",
                                        extractedFrom="https://t4.example")
            proj.g.query(
                "MATCH (s:Source {url:$u}) SET s.credibilityTier = 'T4', "
                "s.sourceDate = $d, s.ingestedAt = $d",
                params={"u": "https://t4.example", "d": FRESH},
            )
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_before = sdk.get_point(p_tiered["id"]).get("ep_alpha")
            assert alpha_before == pytest.approx(1.1, rel=1e-9)

            # Now materialize connector sources (all kinds neutral)
            for ev in (gh_issue_event(), gh_pr_event(), linear_issue_event(),
                       linear_cycle_event(), slack_event()):
                proj.apply(ev)
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_after = sdk.get_point(p_tiered["id"]).get("ep_alpha")
            assert alpha_after == pytest.approx(alpha_before, rel=1e-9)

            # A point extracted from a neutral connector Source inherits
            # nothing — ep_alpha stays UNSET (None), exactly like any untiered
            # source: github_issue resolves to a None tier (SOURCE_KIND_DEFAULTS
            # explicit neutral), so the connector Source adds zero EP weight.
            p_conn = sdk.create_point("statement", "connector-backed claim",
                                      extractedFrom=GH_URL)
            p_plain = sdk.create_point("statement", "plain untiered claim",
                                       extractedFrom="https://plain.example")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert sdk.get_point(p_conn["id"]).get("ep_alpha") is None
            assert sdk.get_point(p_plain["id"]).get("ep_alpha") is None


# ═══════════════════════════════════════════════════════════════════════
# list_sources regression (AC4)
# ═══════════════════════════════════════════════════════════════════════

class TestListSources:
    def test_connector_sources_listed_with_kind_and_zero_points(self):
        with fresh_sdk() as sdk:
            proj = sdk._get_proj()
            proj.apply(gh_issue_event())
            proj.apply(gh_pr_event())
            proj.apply(linear_cycle_event())
            sdk.create_source("https://doc.example", "document")
            sources = {s["url"]: s for s in sdk.list_sources()}
            assert sources[GH_URL]["sourceKind"] == "github_issue"
            assert sources[GH_URL]["points"] == 0
            assert sources["https://github.com/test/repo/pull/7"]["sourceKind"] == "github_pr"
            assert sources["linear:ENG"]["sourceKind"] == "linear_cycle"
            assert sources["https://doc.example"]["sourceKind"] == "document"
