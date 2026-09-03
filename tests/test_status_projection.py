"""Tests for the Object status projection (#1350, decisions 1a/2a/3a):

- CommitPayload supersessions field (optional, additive — old payloads valid)
- SDK _commit_session_v2 surfaces + passes supersessions through
- ObjectSuperseded event → projection fold → Object.status='superseded'
  (+ rebuild replay, clobber guard, chain idempotence)
- Work-item fold: GitHub/Linear lifecycle events → in_progress/completed
- Read side: object results carry status/superseded_by; recall_state filters
"""
from __future__ import annotations

import json  # noqa: F401
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100


def _fresh_sdk() -> TortoiseSDK:
    return TortoiseSDK(os.path.join(tempfile.mkdtemp(), "t.db"))


# ── Payload contract (Step 1) ─────────────────────────────────────────

class TestPayloadContract:
    def test_supersessions_optional_and_additive(self):
        from tortoise.commit_schema import (  # noqa: I001
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
        from tortoise.commit_schema import SupersessionRecord  # noqa: I001
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
        import hashlib  # noqa: I001
        import json  # noqa: F811
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

    def test_rebuild_all_restores_object_superseded_fold(self, tmp_path):
        """#2164 pass-1b rebuild parity: a journaled ObjectSuperseded (C2
        kwargs shape — id + name + supersedes_by riding the JSONL envelope) is
        REPLAYED by rebuild_all, not just folded live by apply().

        Pre-fix the rebuild pass-1b elif chain (projection/__init__.py ~:1351)
        dispatched ObjectSuperseded in apply() but had NO rebuild branch — the
        journaled event silently fell through and the Object reverted to
        status='live' on JSONL wipe+rebuild. (The older
        test_rebuild_replays_object_superseded is MISLABELED: it calls
        proj.apply() directly and never exercises rebuild_all — this test
        journals real events and replays them.)

        Rebuild-only shape note (M1): the fold target must EXIST at replay, so
        ObjectRegistered is journaled FIRST. That is an EventAPI-style
        journaled producer (ObjectRegistered IS journaled there). SDK
        capture-created Objects are NOT journaled as ObjectRegistered
        (pre-existing — sdk.py _create_entity journals EventRecorded for
        Events only) — capture folds stay live-graph-only until the separate
        OD2 journaling issue lands; this test journals ObjectRegistered
        directly to make the replay coherent.
        """
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t2164a.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy")
            # hosted_api §6b resolves the canonical id from the graph before
            # emitting — mirror that here.
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-A"}).result_set
            oid = rows[0][0]
            # Journal the fold-target registration FIRST — rebuild replays in
            # journal order, so the Object must exist when ObjectSuperseded
            # folds (a MATCH with no rows is a silent no-op).
            sdk._emit_event("ObjectRegistered", id=oid, name="strategy-A",
                            objectKind="core:strategy")
            # C2 kwargs shape: extra kwargs ride the JSONL envelope
            # (event.update(extra)) so supersededBy survives the replay.
            sdk._emit_event("ObjectSuperseded", id=oid, name="strategy-A",
                            supersedes_by="strategy-B")
            sdk._get_proj().rebuild_all(str(events))
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": "strategy-A"}).result_set
            assert rows, "journaled ObjectRegistered must recreate the target"
            assert rows[0][0] == "superseded", "status reverted to live on replay"
            assert rows[0][1] == "strategy-B", "supersededBy lost on replay"
        finally:
            sdk.close()

    def test_rebuild_all_legacy_6b_id_only_shape_supersedes(self, tmp_path):
        """#2164 legacy §6b id-only shape: hosted_api §6b currently passes the
        payload dict POSITIONALLY to _emit_event (GraphEvent-store only) with
        just id= as the JSONL kwarg — name/supersedes_by never reach the
        journal, so a rebuild replay folds with supersededBy='' (the
        documented interim until producers move to the C2 kwargs shape). The
        status flip must still survive rebuild_all."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t2164b.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.create_entity("object", "strategy-C",
                              objectKind="core:strategy")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-C"}).result_set
            oid = rows[0][0]
            sdk._emit_event("ObjectRegistered", id=oid, name="strategy-C",
                            objectKind="core:strategy")
            # Verbatim §6b emission shape: positional payload (GraphEvent
            # store) + id kwarg (JSONL). The JSONL line carries type+id only.
            sdk._emit_event(
                "ObjectSuperseded",
                {"id": oid, "name": "strategy-C",
                 "supersedes_by": "strategy-D", "evidence": ""},
                id=oid,
            )
            sdk._get_proj().rebuild_all(str(events))
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": "strategy-C"}).result_set
            assert rows and rows[0][0] == "superseded", \
                "id-only ObjectSuperseded must still flip status on replay"
            assert rows[0][1] == "", "legacy shape must fold supersededBy=''"
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

    def test_fold_returns_match_count(self):
        """#2164 Task 2: _fold_object_superseded returns the MATCHED-ROW count
        (additive fold-miss signal).

        Pre-change the fold returned None and its Cypher was a bare MATCH…SET
        with no RETURN — a fold that matched 0 rows (missing Object, stale id)
        was indistinguishable from a successful fold. Now: 0 = no Object
        matched (the fold missed), 1 = the Object was folded.
        """
        sdk = _fresh_sdk()
        try:
            fold = sdk._get_proj()._fold_object_superseded
            # (1) fold on a MISSING Object id → 0 (fold-miss signal)
            assert fold({"id": "no-such-object", "name": "",
                          "supersedes_by": "strategy-B"}) == 0
            # (1b) empty id AND empty name → 0 (early return, never None)
            assert fold({"id": "", "name": "",
                          "supersedes_by": "strategy-B"}) == 0
            # (2) fold on an EXISTING Object (create + resolve canonical id)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy")
            oid = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-A"}).result_set[0][0]
            assert fold({"id": oid, "name": "strategy-A",
                          "supersedes_by": "strategy-B"}) == 1
            # (3) re-fold (already superseded, same successor) → 1 and the
            #     stored values are unchanged (idempotency preserved)
            assert fold({"id": oid, "name": "strategy-A",
                          "supersedes_by": "strategy-B"}) == 1
            # (3b) name-branch also returns the match count (legacy no-id
            #     shape — regression guard for the name-branch RETURN)
            assert fold({"id": "", "name": "strategy-A",
                          "supersedes_by": "strategy-B"}) == 1
            assert fold({"id": "", "name": "no-such-object",
                          "supersedes_by": "strategy-B"}) == 0
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status, o.supersededBy",
                params={"id": oid}).result_set
            assert rows and rows[0][0] == "superseded"
            assert rows[0][1] == "strategy-B", \
                "re-fold must not clobber the successor"
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

    def test_connector_fold_does_not_clobber_superseded(self):
        """M3-P1 (#2164 Task 11): the connector work-item fold must NOT
        reset a superseded Object back to in_progress/completed.

        A dual-tracked Object (connector work item AND conversationally
        superseded via the capture fold) must keep status='superseded' when
        a LATER connector lifecycle event lands — recall_state's default view
        excludes superseded/deprecated/archived/retracted, so an unguarded
        SET would silently resurrect the Object into the default view.
        BOTH families are driven here against the same superseded Object:
        github.issue.closed (completed family) then
        github.issue.reopened (in_progress family) — each would have
        clobbered status='superseded' pre-fix.
        """
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "TASK-9", objectKind="dev:issue")
            # #2164 capture fold: supersede the work item (repo convention —
            # the projection fold invoked by the commit path).
            sdk._get_proj()._fold_object_superseded({
                "name": "TASK-9", "supersedes_by": "TASK-9-v2"})
            # completed family first — pre-fix this SET clobbers → completed.
            sdk.create_event("closed", "github.issue.closed", object="TASK-9")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "TASK-9"}).result_set
            assert rows and rows[0][0] == "superseded", (
                "github.issue.closed clobbered superseded status -> "
                f"{rows[0][0] if rows else 'MISSING'}")
            # in_progress family — pre-fix this SET clobbers → in_progress.
            sdk.create_event("reopened", "github.issue.reopened",
                             object="TASK-9")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "TASK-9"}).result_set
            assert rows and rows[0][0] == "superseded", (
                "github.issue.reopened clobbered superseded status -> "
                f"{rows[0][0] if rows else 'MISSING'}")
        finally:
            sdk.close()

    def test_connector_fold_still_works_on_live_object(self):
        """Regression for the M3-P1 guard: the WHERE (status IS NULL OR
        status <> 'superseded') must only skip SUPERSEDED Objects — a live
        Object's connector fold keeps working (#1725 reopen → in_progress
        family, the one kind not explicitly covered by the pre-existing
        live-Object tests above)."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the live bug", objectKind="dev:issue")
            sdk.create_event("reopened", "github.issue.reopened",
                             object="the live bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "the live bug"}).result_set
            assert rows and rows[0][0] == "in_progress", \
                "live-Object reopen fold regressed under the guard"
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
