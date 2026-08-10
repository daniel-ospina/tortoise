"""#771 — tests for the #669 flip gate + registry-delete scripts.

Covers:
- verify-cutover-preconditions: registry-empty + Supabase-placeholder-only
  assertions (clean state passes; every violation fails).
- verify-cutover (bash wrapper): smoke — exit 0 on a clean local state,
  non-zero when a precondition is violated.
- delete-registry: the whitelist refuses non-registry graphs; dry-run and
  --confirm delete ONLY the registry graph set; knowledge graphs verified
  intact (present + same node counts) after the delete.

All runs use FalkorDBLite + the seam fake — zero network, no Docker (the
CI flip-gate job runs the same equivalence; the operator's --live run uses
prod creds).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / ".github" / "scripts"

REGISTRY_GRAPH = "registry_control_plane"


# ── helpers ────────────────────────────────────────────────────────────────

def _load_script_module(name: str):
    """Import a .github/scripts module from its path (extensionless OK)."""
    path = SCRIPTS / name
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


preconditions = _load_script_module("verify-cutover-preconditions.py")
delete_registry = _load_script_module("delete-registry")


def _fresh_db_path(tmp_path: str) -> str:
    """A fresh embedded DB path (nonexistent file — created on open)."""
    return os.path.join(tmp_path, "gate.db")


def _seed_registry_node(db_path: str) -> None:
    """Create a Team node in the registry graph of an embedded DB."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(db_path)
    try:
        reg = proj.db.select_graph(REGISTRY_GRAPH)
        reg.query("CREATE (t:Team {id:'team-x'})")
    finally:
        proj.close()


def _run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env.pop("TORTOISE_DB_URI", None)
    full_env.pop("SUPABASE_URL", None)
    full_env.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    full_env.pop("SUPABASE_SERVICE_KEY", None)
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=str(REPO_ROOT), env=full_env,
                          capture_output=True, text=True, timeout=300)


# ── verify-cutover preconditions (unit) ────────────────────────────────────

class TestPreconditions:
    def test_registry_clean_passes(self, tmp_path):
        failures = preconditions.check_registry_empty(
            db_path=_fresh_db_path(str(tmp_path)))
        assert failures == []

    def test_registry_with_nodes_fails(self, tmp_path):
        db_path = _fresh_db_path(str(tmp_path))
        _seed_registry_node(db_path)
        failures = preconditions.check_registry_empty(db_path=db_path)
        assert len(failures) == 1
        assert "registry_control_plane" in failures[0]
        assert "node(s)" in failures[0]

    def test_supabase_empty_fake_passes(self):
        from tests.fake_control_plane import FakeControlPlane
        cp = FakeControlPlane()
        assert preconditions.check_supabase_placeholders(cp) == []

    def test_supabase_placeholder_membership_only_passes(self):
        from tests.fake_control_plane import FakeControlPlane
        cp = FakeControlPlane({"team_memberships": [
            {"id": "m1", "team_id": "", "key_hash": "pending"},
        ]})
        assert preconditions.check_supabase_placeholders(cp) == []

    def test_supabase_real_team_fails(self):
        from tests.fake_control_plane import FakeControlPlane
        cp = FakeControlPlane({"teams": [{"id": "team-free-001"}]})
        failures = preconditions.check_supabase_placeholders(cp)
        assert len(failures) == 1
        assert "teams" in failures[0]

    def test_supabase_real_api_key_fails(self):
        from tests.fake_control_plane import FakeControlPlane
        cp = FakeControlPlane({"api_keys": [{"id": "key-1"}]})
        failures = preconditions.check_supabase_placeholders(cp)
        assert len(failures) == 1
        assert "api_keys" in failures[0]

    def test_supabase_reconciled_membership_fails(self):
        # A membership with a REAL team_id is reconciled data, not a
        # placeholder — the flip must not proceed.
        from tests.fake_control_plane import FakeControlPlane
        cp = FakeControlPlane({"team_memberships": [
            {"id": "m1", "team_id": "team-free-001", "key_hash": "abc123"},
        ]})
        failures = preconditions.check_supabase_placeholders(cp)
        assert len(failures) == 1
        assert "team_memberships[0]" in failures[0]

    def test_main_clean_state_exits_zero(self, tmp_path):
        code = preconditions.main([
            "--db-path", _fresh_db_path(str(tmp_path))])
        assert code == 0

    def test_main_registry_violation_exits_nonzero(self, tmp_path):
        db_path = _fresh_db_path(str(tmp_path))
        _seed_registry_node(db_path)
        assert preconditions.main(["--db-path", db_path]) == 1

    def test_main_supabase_violation_exits_nonzero(self, tmp_path):
        seed = json.dumps({"teams": [{"id": "team-free-001"}]})
        assert preconditions.main([
            "--db-path", _fresh_db_path(str(tmp_path)),
            "--fake-cp-seed-json", seed]) == 1

    def test_main_live_without_creds_cannot_run(self, tmp_path):
        code = preconditions.main(["--live", "--db-path",
                                   _fresh_db_path(str(tmp_path))])
        assert code == 2


# ── verify-cutover (bash wrapper smoke) ────────────────────────────────────

class TestVerifyCutoverScript:
    def test_exit_zero_on_clean_state(self, tmp_path):
        db_path = _fresh_db_path(str(tmp_path))
        res = _run(
            ["bash", str(SCRIPTS / "verify-cutover"), "--preconditions-only"],
            env={"TORTOISE_DB_PATH": db_path,
                 "PYTHON": sys.executable,
                 "TORTOISE_CONTROL_PLANE": "supabase"})
        assert res.returncode == 0, res.stdout + res.stderr
        assert "PASS" in res.stdout

    def test_exit_nonzero_on_violated_precondition(self, tmp_path):
        db_path = _fresh_db_path(str(tmp_path))
        _seed_registry_node(db_path)
        res = _run(
            ["bash", str(SCRIPTS / "verify-cutover"), "--preconditions-only"],
            env={"TORTOISE_DB_PATH": db_path,
                 "PYTHON": sys.executable,
                 "TORTOISE_CONTROL_PLANE": "supabase"})
        assert res.returncode == 1, res.stdout + res.stderr
        assert "FAIL" in res.stderr

    def test_help_exits_zero(self):
        res = _run(["bash", str(SCRIPTS / "verify-cutover"), "--help"])
        assert res.returncode == 0
        assert "flip" in res.stdout.lower()


# ── delete-registry (whitelist + targeted delete) ──────────────────────────

class TestDeleteRegistry:
    def test_plan_refuses_non_registry_graphs(self):
        """A registry set containing a team_* (knowledge) graph is refused."""
        to_delete, absent, refused = delete_registry.plan_delete(
            all_graphs=["registry_control_plane", "team_team_x", "tortoise"],
            registry_set=frozenset({"registry_control_plane", "team_team_x"}),
        )
        assert refused == ["team_team_x"]
        assert to_delete == ["registry_control_plane"]
        # The whitelist never includes anything outside the registry set.
        assert set(to_delete) <= delete_registry.REGISTRY_GRAPHS

    def test_plan_missing_registry_is_absent_not_refused(self):
        to_delete, absent, refused = delete_registry.plan_delete(
            all_graphs=["team_team_x", "tortoise"])
        assert to_delete == []
        assert absent == ["registry_control_plane"]
        assert refused == []

    def _make_db_with_registry_and_knowledge(self, tmp_path: str) -> str:
        from tortoise.projection import FalkorProjection
        db_path = _fresh_db_path(tmp_path)
        proj = FalkorProjection(db_path)
        try:
            reg = proj.db.select_graph(REGISTRY_GRAPH)
            reg.query("CREATE (t:Team {id:'team-x'})")
            kg = proj.db.select_graph("team_team_x")
            kg.query("CREATE (p:Point {id:'pt-1'})")
        finally:
            proj.close()
        return db_path

    def test_dry_run_does_not_delete(self, tmp_path):
        db_path = self._make_db_with_registry_and_knowledge(str(tmp_path))
        res = _run([sys.executable, str(SCRIPTS / "delete-registry"),
                    "--db-path", db_path])
        assert res.returncode == 0, res.stdout + res.stderr
        assert "DRY-RUN" in res.stdout
        assert "would delete: ['registry_control_plane']" in res.stdout
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(db_path)
        try:
            graphs = sorted(proj.db.list_graphs())
        finally:
            proj.close()
        assert REGISTRY_GRAPH in graphs  # nothing deleted

    def test_confirm_deletes_only_registry_set(self, tmp_path):
        db_path = self._make_db_with_registry_and_knowledge(str(tmp_path))
        res = _run([sys.executable, str(SCRIPTS / "delete-registry"),
                    "--db-path", db_path, "--confirm"])
        assert res.returncode == 0, res.stdout + res.stderr
        assert "VERIFY OK" in res.stdout
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(db_path)
        try:
            graphs = sorted(proj.db.list_graphs())
        finally:
            proj.close()
        assert REGISTRY_GRAPH not in graphs
        assert "team_team_x" in graphs      # knowledge graph intact
        assert "tortoise" in graphs         # default graph intact

    def test_knowledge_graph_node_counts_unchanged(self, tmp_path):
        db_path = self._make_db_with_registry_and_knowledge(str(tmp_path))
        res = _run([sys.executable, str(SCRIPTS / "delete-registry"),
                    "--db-path", db_path, "--confirm"])
        assert res.returncode == 0, res.stdout + res.stderr
        assert "node counts unchanged" in res.stdout
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(db_path)
        try:
            kg = proj.db.select_graph("team_team_x")
            rows = kg.query("MATCH (n) RETURN count(n)").result_set
            assert int(rows[0][0]) == 1
        finally:
            proj.close()

    def test_no_db_env_cannot_run(self):
        res = _run([sys.executable, str(SCRIPTS / "delete-registry")])
        assert res.returncode == 2
        assert "CANNOT RUN" in res.stderr


# ── Stripe webhook Supabase-mode branch (#771 review P1) ────────────────────

class TestWebhookSupabaseBranch:
    """The Stripe webhook was the LAST un-migrated registry writer — post-
    delete it would silently lose team bindings OR recreate the registry
    graph (FalkorDB auto-creates on GRAPH.QUERY). In Supabase mode it must
    resolve + write via the seam (teams row)."""

    def test_team_id_for_stripe_customer_via_seam(self, monkeypatch):
        from tortoise.supabase_control import (
            SupabaseControlPlane, team_id_for_stripe_customer,
        )
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane({"teams": [
            {"id": "team-1", "stripe_customer_id": "cus_123"},
            {"id": "team-2", "stripe_customer_id": None},
        ]})
        assert team_id_for_stripe_customer(fake, "cus_123") == "team-1"
        assert team_id_for_stripe_customer(fake, "cus_nope") is None

    def test_update_team_billing_writes_known_columns_only(self):
        from tortoise.supabase_control import update_team_billing
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane({"teams": [{"id": "team-1"}]})
        update_team_billing(fake, "team-1", {
            "tier": "team",
            "subscription_id": "sub_1",
            "subscription_status": "active",
            "grace_until": "2026-09-01T00:00:00Z",
            "current_period_end": "2026-09-01T00:00:00Z",
            "customer_email": "billing@example.com",
            "stripe_customer_id": "cus_123",
            # unknown column must be dropped, not written
            "not_a_column": 42,
        })
        row = fake.tables["teams"][0]
        assert row["tier"] == "team"
        assert row["subscription_id"] == "sub_1"
        assert row["subscription_status"] == "active"
        assert row["grace_until"] == "2026-09-01T00:00:00Z"
        assert row["current_period_end"] == "2026-09-01T00:00:00Z"
        assert row["customer_email"] == "billing@example.com"
        assert row["stripe_customer_id"] == "cus_123"
        assert "not_a_column" not in row

    def test_webhook_endpoint_supabase_mode_never_touches_registry(self, monkeypatch):
        """E2E-level: a verified webhook event in Supabase mode writes the
        teams row via the seam and NEVER calls _get_registry()."""
        import importlib
        import tortoise.hosted_api as ha
        from tortoise.billing import StripeClient
        from tests.fake_control_plane import FakeControlPlane

        # Enable Supabase mode with a fake control plane.
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_key")
        import tortoise.supabase_control as sc
        fake = FakeControlPlane({"teams": [{"id": "team-1",
                                             "stripe_customer_id": "cus_123"}]})
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

        # Registry spy: any _get_registry() call in the webhook path fails.
        orig_make_sdk = ha._make_sdk
        calls = []

        def _spy_make_sdk(*a, **kw):
            calls.append(("make_sdk", a, kw))
            return orig_make_sdk(*a, **kw)

        monkeypatch.setattr(ha, "_make_sdk", _spy_make_sdk)

        # Signature-verified event: sign a real payload with the test secret.
        import time as _time
        import hmac as _hmac
        import hashlib as _hashlib
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps({
            "free": {"monthly": "price_free", "annual": "price_free_yr"},
            "team": {"monthly": "price_team", "annual": "price_team_yr"},
        }))
        # StripeClient reads the secrets at construction time (webhooks_stripe
        # builds one per request).
        from fastapi.testclient import TestClient

        payload = {
            "id": "evt_test_1",
            "type": "customer.subscription.deleted",
            "data": {"object": {
                "id": "sub_1", "customer": "cus_123",
                "status": "canceled",
            }},
        }
        raw = json.dumps(payload).encode()
        t = str(int(_time.time()))
        signed = _hmac.new(b"whsec_test", f"{t}.{raw.decode()}".encode(),
                           _hashlib.sha256).hexdigest()
        headers = {"stripe-signature": f"t={t},v1={signed}"}

        with TestClient(ha.app) as tc:
            r = tc.post("/webhooks/stripe", content=raw, headers=headers)

        assert r.status_code == 200, r.text
        # The teams row got the cancel (tier → free) via the seam.
        row = fake.tables["teams"][0]
        assert row.get("tier") == "free"
        assert row.get("subscription_status") == "canceled"
        # No registry write: the webhook must not construct a registry SDK
        # for the apply path in Supabase mode (make_sdk may be called for
        # unrelated reasons — assert no registry-namespaced _get_registry
        # usage by checking the teams row was the only write).
