"""#1999 (W3) hosted seed endpoint — docker-lane integration tests.

POST /v1/onboarding/seed — the interactive ontology-precise seed:
exactly two Subjects (Organization/organization + User/naturalPerson linked
memberOf), collision detection (never silent merge of distinct identities),
person→naturalPerson normalization, never-invented identity (email-derived
person name requires confirmation), fork-aware completion (self = two
Subjects + decide + connected; build defers decide to catalog-presented;
compact = seed-lite org anchor + connected).

Runs in the docker lane (TORTOISE_DB_URI) — real FalkorDB graph assertions
(Subject kinds, memberOf edge, onboards edge/org_subject_id, step-edge
created signals). URI-less runs (tier-2 embedded legs, carve-out) SKIP at
module level — mirror of test_onboarding_state_split.py's guard.
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip("docker-lane onboarding seed tests require TORTOISE_DB_URI "
                "(tier-2 embedded legs skip)", allow_module_level=True)

from fastapi.testclient import TestClient

from tortoise.hosted_api import _make_sdk, app
from tortoise.onboarding import state as onboarding_state


def _registered():
    """TestClient + a freshly registered team (registry lane) + email."""
    tc = TestClient(app)
    tc.__enter__()
    email = f"w3s-{uuid.uuid4().hex[:10]}@example.com"
    r = tc.post("/v1/register", json={"email": email,
                                      "password": "password123"})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
    return tc, r.json()["team_id"], email


def _proj(team_id):
    return _make_sdk(namespace=team_id)._get_proj()


def _subjects(team_id):
    """All Subject rows in the tenant graph: {name, id, subjectKind, props}."""
    rows = _proj(team_id).query(
        "MATCH (s:Subject) RETURN s.name, s.id, properties(s)").result_set
    return {r[0]: r[2] for r in rows}


def _member_of_edges(team_id):
    rows = _proj(team_id).query(
        "MATCH (a:Subject)-[:memberOf]->(b:Subject) "
        "RETURN a.name, b.name").result_set
    return set((r[0], r[1]) for r in rows)


def _completed(team_id):
    return set(onboarding_state.completed_steps(_proj(team_id), team_id))


def _seed(tc, **body):
    r = tc.post("/v1/onboarding/seed", json=body or {})
    assert r.status_code == 200, r.text
    return r.json()


class TestSeedEndpoint:
    def test_email_derived_person_requires_confirmation_no_writes(self):
        """Never-invented identity: person name derived from email is a
        PROPOSAL — the seed must ask before filing (zero graph writes)."""
        tc, team_id, email = _registered()
        try:
            res = _seed(tc)
            assert res["status"] == "needs_confirmation"
            gaps = {g["field"] for g in res["gaps"]}
            assert "person_name" in gaps
            person_gap = next(g for g in res["gaps"]
                              if g["field"] == "person_name")
            assert person_gap["source"] == "email-derived"
            from tortoise.onboarding.seed import derive_display_name_from_email
            assert person_gap["derived"] == \
                derive_display_name_from_email(email)
            assert _subjects(team_id) == {}          # no writes at all
            assert "first-points-filed" not in _completed(team_id)
        finally:
            tc.__exit__(None, None, None)

    def test_org_name_falls_back_to_teams_name(self):
        """Hosted anchor data: org display name ← teams.name (never
        invented); explicit person_name files the seed."""
        tc, team_id, _email = _registered()
        try:
            org_name = f"w3org{uuid.uuid4().hex[:8]}"
            # rename the team so teams.name ≠ email slug? teams.name IS the
            # email slug post-register — assert the seed USES it when no
            # org_name is given.
            team_node = _make_sdk(namespace="registry")._get_registry().query(
                "MATCH (t:Team {id: $id}) SET t.name = $name RETURN t.name",
                params={"id": team_id, "name": org_name}).result_set
            assert team_node[0][0] == org_name
            res = _seed(tc, person_name="Alex Johnson")
            assert res["status"] == "seeded", res
            assert res["org_name"] == org_name
            subs = _subjects(team_id)
            assert subs[org_name]["subjectKind"] == "organization"
            assert subs[org_name]["org_id"] == team_id
        finally:
            tc.__exit__(None, None, None)

    def test_seed_files_two_subjects_member_of(self):
        """DE2E-4: exactly 2 Subjects (organization + naturalPerson) linked
        memberOf; org_subject_id + onboards edge; first-points-filed step
        edge; status stays active on the self fork (decide pending)."""
        tc, team_id, _email = _registered()
        try:
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            res = _seed(tc, org_name="Acme Labs", person_name="Alex Johnson")
            assert res["status"] == "seeded", res
            assert res["steps"]["first-points-filed"]["created"] is True
            subs = _subjects(team_id)
            assert set(subs) == {"Acme Labs", "Alex Johnson"}
            assert subs["Acme Labs"]["subjectKind"] == "organization"
            assert subs["Acme Labs"]["org_id"] == team_id
            assert subs["Alex Johnson"]["subjectKind"] == "naturalPerson"
            # the person anchor is NEVER identity-tagged as the org (the
            # org_id ref belongs to the org anchor only)
            assert subs["Alex Johnson"].get("org_id") != team_id
            assert _member_of_edges(team_id) == {("Alex Johnson", "Acme Labs")}
            node = onboarding_state.read_onboarding_node(_proj(team_id), team_id)
            assert node["org_subject_id"] == subs["Acme Labs"]["id"]
            rows = _proj(team_id).query(
                "MATCH (n:OnboardingState {org_id: $oid})-[:onboards]->"
                "(s:Subject) RETURN s.id", oid=team_id).result_set
            assert rows[0][0] == subs["Acme Labs"]["id"]
            assert "first-points-filed" in _completed(team_id)
            assert node["status"] == "active"  # self fork: decide still needed
            assert res["onboarding"]["onboarding_complete"] is False
            assert res["next"] == "decide-completed"
        finally:
            tc.__exit__(None, None, None)

    def test_seed_replay_idempotent(self):
        """Replay: canonical ids stable, memberOf/onboards not duplicated,
        first-points-filed step edge no-op (created False)."""
        tc, team_id, _email = _registered()
        try:
            r1 = _seed(tc, org_name="Acme", person_name="Alex")
            r2 = _seed(tc, org_name="Acme", person_name="Alex")
            assert r2["status"] == "seeded"
            assert r2["org_subject"]["id"] == r1["org_subject"]["id"]
            assert r2["user_subject"]["id"] == r1["user_subject"]["id"]
            assert r2["steps"]["first-points-filed"]["created"] is False
            assert len(_subjects(team_id)) == 2
            assert len(_member_of_edges(team_id)) == 1
        finally:
            tc.__exit__(None, None, None)

    def test_org_collision_never_silent_merge(self):
        """Same-name org Subject exists for a DIFFERENT org_id → collision
        surfaced, zero writes, distinct identity untouched."""
        tc, team_id, _email = _registered()
        try:
            name = f"Acme{uuid.uuid4().hex[:6]}"
            _proj(team_id).query(
                "CREATE (:Subject {id: $sid, name: $name, "
                "subjectKind: 'organization', org_id: 'team-other'})",
                sid=f"sub-{uuid.uuid4().hex[:10]}", name=name)
            res = _seed(tc, org_name=name, person_name="Alex")
            assert res["status"] == "collision"
            assert res["collisions"][0]["kind"] == "organization"
            assert res["collisions"][0]["existing_id"].startswith("sub-")
            assert "Alex" not in _subjects(team_id)
            assert _subjects(team_id)[name]["org_id"] == "team-other"  # untouched
            assert "first-points-filed" not in _completed(team_id)
        finally:
            tc.__exit__(None, None, None)

    def test_person_collision_never_silent_merge(self):
        """Same-name person Subject with a DIFFERENT user_id → collision."""
        tc, team_id, _email = _registered()
        try:
            _proj(team_id).query(
                "CREATE (:Subject {id: $sid, name: 'Alex', "
                "subjectKind: 'naturalPerson', user_id: 'user-other'})",
                sid=f"sub-{uuid.uuid4().hex[:10]}")
            res = _seed(tc, org_name="Acme", person_name="Alex")
            assert res["status"] == "collision"
            assert res["collisions"][0]["kind"] == "naturalPerson"
            assert _subjects(team_id)["Alex"]["user_id"] == "user-other"
            assert "Acme" not in _subjects(team_id)
        finally:
            tc.__exit__(None, None, None)

    def test_ref_less_same_name_person_collision(self):
        """A same-name person with NO identity refs is unprovable → collision
        (never silently claim a legacy node)."""
        tc, team_id, _email = _registered()
        try:
            _proj(team_id).query(
                "CREATE (:Subject {id: $sid, name: 'Alex', "
                "subjectKind: 'naturalPerson'})",
                sid=f"sub-{uuid.uuid4().hex[:10]}")
            res = _seed(tc, org_name="Acme", person_name="Alex")
            assert res["status"] == "collision"
        finally:
            tc.__exit__(None, None, None)

    def test_legacy_person_normalized_on_match(self):
        """DM-3 normalization: an existing 'person'-kind Subject that IS ours
        (auth-email match, no user ref) is reused AND normalized to
        naturalPerson on MATCH (never validate-block)."""
        tc, team_id, email = _registered()
        try:
            _proj(team_id).query(
                "CREATE (:Subject {id: $sid, name: 'Alex Johnson', "
                "subjectKind: 'person', email: $email})",
                sid=f"sub-{uuid.uuid4().hex[:10]}", email=email)
            res = _seed(tc, org_name="Acme", person_name="Alex Johnson")
            assert res["status"] == "seeded", res
            assert res["person_kind_normalized"] is True
            assert _subjects(team_id)["Alex Johnson"]["subjectKind"] == \
                "naturalPerson"
        finally:
            tc.__exit__(None, None, None)

    def test_never_object_or_statement(self):
        """B1 regression: anchors are Subjects ONLY — no Object/Statement
        node exists for the anchor names after a seed."""
        tc, team_id, _email = _registered()
        try:
            _seed(tc, org_name="Acme", person_name="Alex")
            for label in ("Object",):
                rows = _proj(team_id).query(
                    f"MATCH (o:{label} {{name: 'Acme'}}) RETURN count(o)"
                ).result_set
                assert rows[0][0] == 0, f"anchor filed as {label}"
            rows = _proj(team_id).query(
                "MATCH (s:Subject) RETURN count(s)").result_set
            assert rows[0][0] == 2
        finally:
            tc.__exit__(None, None, None)


class TestSeedJourney:
    def _self_fork_setup(self):
        tc, team_id, _email = _registered()
        tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
        tc.post("/v1/onboarding/state/checkpoint",
                json={"step": "harness-connected"})
        return tc, team_id

    def test_self_fork_completes_only_with_decide(self):
        """Completion is fork-aware: two Subjects + connected WITHOUT decide
        → status active; decide-completed → complete."""
        tc, _team_id = self._self_fork_setup()
        try:
            res = _seed(tc, org_name="Acme", person_name="Alex")
            assert res["onboarding"]["status"] == "active"
            assert res["onboarding"]["onboarding_complete"] is False
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "decide-completed"})
            assert r.json()["onboarding"]["status"] == "complete"
            assert r.json()["onboarding"]["onboarding_complete"] is True
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"step": "decide-completed"})
            assert r2.json()["noop_steps"] == ["decide-completed"]
        finally:
            tc.__exit__(None, None, None)

    def test_llm503_decide_attempted_failed_distinct_from_dismissed(self):
        """LLM-503 → last_decide_attempt 'failed' recorded (retry
        reachable); a later success clears it; 'failed' never un-completes
        a completed decide; dismissal alone never completes."""
        tc, _team_id = self._self_fork_setup()
        try:
            _seed(tc, org_name="Acme", person_name="Alex")
            # 503 attempt → failed recorded, still active
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"last_decide_attempt": "failed"})
            assert r.json()["onboarding"]["last_decide_attempt"] == "failed"
            assert r.json()["onboarding"]["status"] == "active"
            # retry succeeds → decide-completed → complete; attempt cleared
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"step": "decide-completed"})
            assert r2.json()["onboarding"]["status"] == "complete"
            assert r2.json()["onboarding"]["last_decide_attempt"] is None
            # a later 503 must NOT regress the completed decide
            r3 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"last_decide_attempt": "failed"})
            assert r3.json()["onboarding"]["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def test_dismissal_alone_never_completes(self):
        tc, _team_id = self._self_fork_setup()
        try:
            _seed(tc, org_name="Acme", person_name="Alex")
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"last_decide_attempt": "dismissed"})
            assert r.json()["onboarding"]["last_decide_attempt"] == "dismissed"
            assert r.json()["onboarding"]["status"] == "active"
            assert r.json()["onboarding"]["onboarding_complete"] is False
        finally:
            tc.__exit__(None, None, None)

    def test_build_fork_defers_decide_to_catalog(self):
        """Build fork: two Subjects + connected are NOT complete; the
        catalog-presented checkpoint completes WITHOUT any decide."""
        tc, _team_id, _email = _registered()
        try:
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "build"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            res = _seed(tc, org_name="Acme", person_name="Alex")
            assert res["onboarding"]["status"] == "active"
            assert res["next"] == "catalog-presented"
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "decide-completed"})
            # decide alone can NEVER complete a build fork
            assert r.json()["onboarding"]["status"] == "active"
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"step": "catalog-presented"})
            assert r2.json()["onboarding"]["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def _compact_team(self):
        """A registered team whose OnboardingState node is compact (raw
        fixture write — compact is server-owned; the docker lane cannot mint
        a compact org through register/provision, so the test constructs the
        node state directly, mirroring W5's pre-init fixtures)."""
        tc, team_id, email = _registered()
        _proj(team_id).query(
            "MATCH (n:OnboardingState {org_id: $oid}) SET n.compact = true",
            oid=team_id)
        return tc, team_id, email

    def test_compact_seed_lite_org_anchor_only(self):
        """Compact org (product-literate second org): seed-lite files the
        org-anchor Subject only (no person ask); completes on
        first-points-filed + connected — no decide, no person Subject."""
        from tortoise.hosted_api import _run_onboarding_seed
        tc, team_id, _email = self._compact_team()
        try:
            res = _run_onboarding_seed(team_id, org_name="Second Org")
            assert res["status"] == "seeded", res
            assert res["user_subject"] is None
            assert res["onboarding"]["compact"] is True
            subs = _subjects(team_id)
            assert set(subs) == {"Second Org"}
            assert subs["Second Org"]["subjectKind"] == "organization"
            node = onboarding_state.read_onboarding_node(
                _proj(team_id), team_id)
            assert node["org_subject_id"] == subs["Second Org"]["id"]
            # compact gate: first-points-filed + connected → complete
            from tortoise.hosted_api import _maybe_apply_completion
            onboarding_state.write_completed_step(
                _proj(team_id), team_id, "harness-connected")
            _maybe_apply_completion(team_id)
            node = onboarding_state.read_onboarding_node(
                _proj(team_id), team_id)
            assert node["status"] == "complete"
            assert node.get("last_decide_attempt") is None
        finally:
            tc.__exit__(None, None, None)

    def test_compact_seed_runs_without_person_confirmation(self):
        """compact never blocks on person confirmation — seed-lite files the
        org anchor immediately even with no person data at all."""
        from tortoise.hosted_api import _run_onboarding_seed
        tc, team_id, _email = self._compact_team()
        try:
            res = _run_onboarding_seed(team_id)  # compact + no names
            assert res["status"] == "seeded", res
            assert res["user_subject"] is None
        finally:
            tc.__exit__(None, None, None)

    def test_non_compact_missing_email_person_gap_ask(self):
        """No email to derive from (and not compact) → the person-name gap
        is a plain ask (never a placeholder, zero writes)."""
        tc, team_id, _email = _registered()
        try:
            # strip the team email so no derivation is possible
            _make_sdk(namespace="registry")._get_registry().query(
                "MATCH (t:Team {id: $id}) REMOVE t.email",
                params={"id": team_id})
            res = tc.post("/v1/onboarding/seed", json={}).json()
            assert res["status"] == "needs_confirmation"
            person_gap = next(g for g in res["gaps"]
                              if g["field"] == "person_name")
            assert person_gap["source"] == "ask"
            assert _subjects(team_id) == {}
        finally:
            tc.__exit__(None, None, None)
