"""E2E-5-D — backup → restore (public endpoints, hermetic memory storage).

Reconstructed case (#303; E2E-5-D appears nowhere in the surviving repo —
reconstructed from its journey position between billing and export/delete).
The full journey through the REAL HTTP surface: Pro tenant (hermetic webhook
bump; fixture pricing enables daily_backups) → POST /backups → mutate the
live graph → /backups/restore(confirm=true) → original content restored.
Enabled by the #303 TORTOISE_BACKUP_STORAGE=memory seam + TORTOISE_PRICING_PATH.

Negatives: free tenant → 402 gate; restore without confirm → 400; restore of
an unknown/cross-team key → 400. Skips in remote mode (memory storage is a
local-only seam).
"""
from __future__ import annotations  # noqa: I001

import uuid

import pytest

from conftest import bump_team_tier, is_remote_mode, skip_unless_hosted_e2e

skip_unless_hosted_e2e()

pytestmark = pytest.mark.skipif(
    is_remote_mode(),
    reason="E2E-5-D needs the local TORTOISE_BACKUP_STORAGE=memory seam",
)


def _seed_points(api, h, contents):
    ids = []
    for c in contents:
        r = api.post("/v1/points", headers=h, data={"content": c, "kind": "statement"})
        assert r.status == 200, r.text()
        ids.append(r.json()["id"])
    return ids


def test_backup_restore_round_trip(api, tenant_factory):
    """Positive: backup → mutate → restore → pre-mutation content is back and
    post-mutation content is gone (graph swap actually happened)."""
    t = tenant_factory("backup")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "pro")

    original = [f"backup-seed-{i}-{uuid.uuid4().hex[:4]}" for i in range(3)]
    _seed_points(api, h, original)

    r = api.post("/backups", headers=h)
    assert r.status == 201, f"backup create: {r.status} {r.text()}"
    manifest = r.json()
    assert manifest["backup_id"] and manifest["node_count"] >= 3, manifest

    r = api.get("/backups", headers=h)
    assert r.status == 200 and len(r.json()["backups"]) == 1, r.text()

    # Mutate the live graph AFTER the backup.
    r = api.post("/v1/points", headers=h,
                 data={"content": "post-backup mutation", "kind": "statement"})
    assert r.status == 200, r.text()

    backup_key = f"backups/{manifest['backup_id']}/dump.enc"
    r = api.post("/backups/restore", headers=h,
                 data={"backup_key": backup_key, "confirm": True})
    assert r.status == 200, f"restore: {r.status} {r.text()}"

    r = api.get("/v1/points", headers=h)
    assert r.status == 200
    contents = {p["content"] for p in r.json()["points"]}
    for c in original:
        assert c in contents, f"restored graph lost {c!r}"
    assert "post-backup mutation" not in contents, \
        "restore must replace the live graph (post-backup write must vanish)"


def test_backup_free_tier_402(api, tenant_factory):
    t = tenant_factory("backup-free")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/backups", headers=h)
    assert r.status == 402, f"free-tier backup must 402, got {r.status}: {r.text()}"
    r = api.post("/backups/restore", headers=h,
                 data={"backup_key": "backups/x/dump.enc", "confirm": True})
    assert r.status == 402, f"free-tier restore must 402, got {r.status}"


def test_restore_requires_confirm_400(api, tenant_factory):
    t = tenant_factory("backup-confirm")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "pro")
    r = api.post("/backups/restore", headers=h,
                 data={"backup_key": "backups/x/dump.enc"})  # confirm defaults False
    assert r.status == 400, f"restore without confirm must 400, got {r.status}"
    assert "confirm" in r.text()


def test_restore_unknown_key_400(api, tenant_factory):
    t = tenant_factory("backup-unknown")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "pro")
    r = api.post("/backups/restore", headers=h,
                 data={"backup_key": f"backups/{t['team_id']}/nope/dump.enc",
                       "confirm": True})
    assert r.status == 400, f"unknown backup key must 400, got {r.status}: {r.text()}"
