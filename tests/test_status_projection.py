"""Tests for the Object status projection (#1350, decisions 1a/2a/3a):

- CommitPayload supersessions field (optional, additive — old payloads valid)
- SDK _commit_session_v2 surfaces + passes supersessions through
- ObjectSuperseded event → projection fold → Object.status='superseded'
  (+ rebuild replay, clobber guard, chain idempotence)
- Work-item fold: GitHub/Linear lifecycle events → in_progress/completed
- Read side: object results carry status/superseded_by; recall_state filters
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK  # noqa: E402


def _fresh_sdk() -> TortoiseSDK:
    return TortoiseSDK(os.path.join(tempfile.mkdtemp(), "t.db"))


# ── Payload contract (Step 1) ─────────────────────────────────────────

class TestPayloadContract:
    def test_supersessions_optional_and_additive(self):
        from tortoise.commit_schema import (
            validate_payload_dict, compute_client_commit_id)
        base = {
            "schema_version": "1", "session_id": "s1",
            "client_commit_id": "", "captured_at": "2026-08-18T00:00:00+00:00",
            "extractor": {"version": "v", "mode": "byok", "calibration_version": "v2"},
            "points": [],
            "telemetry": {
                "extractor": {"version": "v", "mode": "byok"},
                "model": {"provider": "byok", "id": "user-model"},
                "counts": {},
            },
        }
        base["client_commit_id"] = compute_client_commit_id(
            base["session_id"], [], [], [], "", "", [], [])
        l1, _ = validate_payload_dict(base)
        assert l1.ok, "old-shape payload (no supersessions) must still validate"
        base["supersessions"] = [
            {"superseded": "obj-a", "supersedes_by": "strategy-B",
             "evidence": "entity lifecycle supersedes"}]
        base["client_commit_id"] = compute_client_commit_id(
            base["session_id"], [], [], [], "", "", [],
            base["supersessions"])
        l1b, _ = validate_payload_dict(base)
        assert l1b.ok, "new-shape payload with supersessions must validate"

    def test_supersession_record_requires_fields(self):
        from tortoise.commit_schema import SupersessionRecord
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SupersessionRecord(superseded="", supersedes_by="b")
        with pytest.raises(ValidationError):
            SupersessionRecord(superseded="a", supersedes_by="")
        r = SupersessionRecord(superseded="a", supersedes_by="b")
        assert r.evidence == ""

    def test_canonical_includes_supersessions(self):
        from tortoise.commit_schema import canonical_payload
        c1 = canonical_payload("s", [], [], [], "sum", "arc", [],
                               [{"superseded": "a", "supersedes_by": "b"}])
        c2 = canonical_payload("s", [], [], [], "sum", "arc", [], [])
        assert c1 != c2, "a supersession change must change the commit id"

    def test_old_client_commit_id_still_validates(self):
        """#1350 compat guard: a pre-#1350 client computed its id over a
        canonical WITHOUT the supersessions key — the new recompute must
        omit the key when empty so old commits still validate (no 422)."""
        import hashlib
        import json
        from tortoise.commit_schema import validate_payload_dict, canonical_payload
        # the pre-#1350 canonical (keys before supersessions existed)
        pre_change = json.dumps({
            "session_id": "s1", "summary": "sum", "story_arc": "arc",
            "points": [], "entities": [], "operators": [], "events": [],
        }, sort_keys=True, separators=(",", ":"))
        old_id = hashlib.sha256(pre_change.encode()).hexdigest()
        payload = {
            "schema_version": "1", "session_id": "s1",
            "client_commit_id": old_id,
            "captured_at": "2026-08-18T00:00:00+00:00",
            "extractor": {"version": "v", "mode": "byok",
                           "calibration_version": "v2"},
            "summary": "sum", "story_arc": "arc", "points": [],
            "telemetry": {"extractor": {"version": "v", "mode": "byok"},
                           "model": {"provider": "byok", "id": "user-model"},
                           "counts": {}},
        }
        l1, _ = validate_payload_dict(payload)
        assert l1.ok, "pre-#1350 commit id must still validate"
        # and the empty-canonical truly omits the key (byte-identical)
        c = canonical_payload("s1", [], [], [], "sum", "arc", [], [])
        assert '"supersessions"' not in c


# ── Projection fold (Steps 4-6) ──────────────────────────────────────

class TestProjectionFold:
    def test_object_superseded_fold(self):
        """The projection-owned fold (what the commit path invokes after
        emitting ObjectSuperseded) sets status + supersededBy."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            sdk._get_proj()._fold_object_superseded({
                "id": "", "name": "strategy-A", "supersedes_by": "strategy-B"})
            proj = sdk._get_proj()
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": "strategy-A"}).result_set
            assert rows and rows[0][0] == "superseded"
            assert rows[0][1] == "strategy-B"
        finally:
            sdk.close()

    def test_rebuild_replays_object_superseded(self):
        """FalkorProjection.apply folds an ObjectSuperseded event — the
        rebuild replay path reproduces status='superseded'."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            # the journaled event shape (id + name + supersedes_by)
            sdk._get_proj().apply({
                "type": "ObjectSuperseded",
                "id": "", "name": "strategy-A",
                "supersedes_by": "strategy-B"})
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "strategy-A"}).result_set
            assert rows and rows[0][0] == "superseded"
        finally:
            sdk.close()

    def test_clobber_guard_second_mention_keeps_superseded(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            sdk._get_proj()._fold_object_superseded({
                "name": "strategy-A", "supersedes_by": "B"})
            # a re-mention (ObjectRegistered) must NOT reset status
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "strategy-A"}).result_set
            assert rows[0][0] == "superseded", "clobber guard failed"
        finally:
            sdk.close()


# ── Work-item fold (Step 7) ──────────────────────────────────────────

class TestWorkItemFold:
    def test_linear_card_completed(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "TASK-42", objectKind="dev:issue")
            sdk.create_event("card done", "pm:cardCompleted",
                             object="TASK-42", endedAt="2026-08-18")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "TASK-42"}).result_set
            assert rows and rows[0][0] == "completed"
        finally:
            sdk.close()

    def test_github_issue_open_and_close(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the bug", objectKind="dev:issue")
            sdk.create_event("opened", "github.issue.open", object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "the bug"}).result_set
            assert rows[0][0] == "in_progress"
            sdk.create_event("closed", "github.issue.closed", object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "the bug"}).result_set
            assert rows[0][0] == "completed"
        finally:
            sdk.close()


# ── Read side (Steps 8-9) ────────────────────────────────────────────

class TestReadSide:
    def test_object_result_carries_status(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the strategy", objectKind="core:strategy")
            hits = sdk.tortoise_fts_query("the strategy", kind="core:strategy",
                                          entity_type="object", limit=5)
            assert hits, "seeded object must be retrievable (kind-scoped)"
            assert hits[0]["status"] == "live"
        finally:
            sdk.close()

    def test_recall_state_filters_superseded_object(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "old-strategy", objectKind="core:strategy")
            sdk.create_entity("object", "new-strategy", objectKind="core:strategy")
            sdk._get_proj()._fold_object_superseded({
                "name": "old-strategy", "supersedes_by": "new-strategy"})
            default = [o["content"] for o in sdk.recall_state(
                kind="core:strategy", limit=10, object_centric=True)
                if o.get("entity_type") == "object"]
            assert "old-strategy" not in default, "superseded object must be filtered"
            with_all = [o["content"] for o in sdk.recall_state(
                kind="core:strategy", limit=10, object_centric=True,
                include_superseded=True)
                if o.get("entity_type") == "object"]
            assert "old-strategy" in with_all, "include_superseded must bring it back"
        finally:
            sdk.close()
