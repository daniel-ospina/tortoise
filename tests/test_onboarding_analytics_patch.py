"""Copy-attribution beacon tests (epic #529, issue #967 — plan T5/T6/T7).

The welcome page fires PATCH /v1/onboarding/state with the displayed key and
a {harness, section} body; the handler must emit `artifact_copied` for
enum-valid pairs (#235 schema verbatim), ignore invalid values, and NEVER
persist harness/section into onboarding state.

DB-free by construction: the state writer and team-email reader are
monkeypatched (they are the registry-SDK legs); the assertions target the
pop-before-merge contract and the analytics capture — exactly the surfaces
this epic changed.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest
from fastapi.testclient import TestClient

from tortoise import hosted_api
from tortoise.hosted_api import app, get_current_team

TEAM = {"team_id": "test-team-529", "tier": "free", "key_id": "k1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with auth overridden and analytics captured to tmp JSONL.

    Capture mechanics pinned per plan T5 (pattern: test_mcp_telemetry.py):
    fallback path redirected + Supabase env removed so exactly one local
    JSONL line is written per emitted event.
    """
    monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics_fallback.jsonl"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    # DB-free: capture what reaches the state writer (the pop contract).
    captured_kwargs = {}

    def fake_update(team_id, **fields):
        captured_kwargs.update(fields)
        captured_kwargs["__team_id__"] = team_id
        return dict(hosted_api.DEFAULT_ONBOARDING_STATE)

    monkeypatch.setattr(hosted_api, "_update_onboarding_state", fake_update)
    monkeypatch.setattr(hosted_api, "_team_email", lambda team_id: None)
    app.dependency_overrides[get_current_team] = lambda: TEAM
    with TestClient(app) as c:
        c._captured_kwargs = captured_kwargs
        c._jsonl = tmp_path / "analytics_fallback.jsonl"
        yield c
    app.dependency_overrides.clear()


def _events(client):
    if not client._jsonl.exists():
        return []
    return [json.loads(line) for line in client._jsonl.read_text().splitlines() if line.strip()]


def test_patch_harness_section_emits_event(client):
    """T5: valid pair → exactly one artifact_copied with exactly those props."""
    resp = client.patch("/v1/onboarding/state",
                        json={"harness": "cursor", "section": "config"})
    assert resp.status_code == 200
    events = _events(client)
    assert len(events) == 1, f"expected exactly one event, got {events}"
    ev = events[0]
    assert ev["event_name"] == "artifact_copied"
    assert ev["properties"] == {"harness": "cursor", "section": "config"}
    assert ev["team_id"] == "test-team-529"
    # State pollution guard: harness/section popped before the merge.
    assert "harness" not in client._captured_kwargs
    assert "section" not in client._captured_kwargs
    body = resp.json()
    assert "harness" not in body["onboarding"]
    assert "section" not in body["onboarding"]


def test_patch_section_both_is_valid(client):
    """T5 (enum member): section 'both' is part of #235's schema."""
    resp = client.patch("/v1/onboarding/state",
                        json={"harness": "cursor", "section": "both"})
    assert resp.status_code == 200
    events = _events(client)
    assert len(events) == 1
    assert events[0]["properties"] == {"harness": "cursor", "section": "both"}


def test_patch_section_setup_is_valid(client):
    """T5 (enum member): welcome page one-click setup prompt attribution."""
    resp = client.patch("/v1/onboarding/state",
                        json={"harness": "pi", "section": "setup"})
    assert resp.status_code == 200
    events = _events(client)
    assert len(events) == 1
    assert events[0]["properties"] == {"harness": "pi", "section": "setup"}


@pytest.mark.parametrize("payload", [
    {"harness": "vim", "section": "config"},      # invalid harness
    {"harness": "cursor", "section": "bogus"},    # invalid section
    {"harness": "claude-v1", "section": "config"},  # no versioned suffixes (align cycle-3)
])
def test_patch_invalid_harness_or_section_ignored(client, payload):
    """T6: invalid enum values → 200, no event, no state change."""
    resp = client.patch("/v1/onboarding/state", json=payload)
    assert resp.status_code == 200
    assert _events(client) == []
    # Nothing but the (empty) merge reached the state writer.
    state_kwargs = {k: v for k, v in client._captured_kwargs.items()
                    if k != "__team_id__"}
    assert state_kwargs == {}


def test_patch_legit_state_fields_still_merge_alongside_beacon(client):
    """Beacon fields and real state fields can arrive together without leaking."""
    resp = client.patch("/v1/onboarding/state",
                        json={"prompt_pasted": True, "harness": "pi", "section": "prompt"})
    assert resp.status_code == 200
    assert client._captured_kwargs.get("prompt_pasted") is True
    assert "harness" not in client._captured_kwargs
    events = _events(client)
    assert len(events) == 1
    assert events[0]["properties"] == {"harness": "pi", "section": "prompt"}


def test_patch_without_auth_still_401():
    """T7 regression: the repaired beacon must auth — no silent 200 for anon."""
    with TestClient(app) as c:
        resp = c.patch("/v1/onboarding/state",
                       json={"harness": "cursor", "section": "config"})
    assert resp.status_code == 401
