"""Agent-ops rules-with-why pack — extraction + ontology integration tests
(issue #1933, epic #1891; test-design #1898 surface 11).

Covers (offline MockModel — no network):

- Happy-path mining: a rules-with-why transcript mints the rule Object
  (objectKind agent-ops:rule), the rationale Point (committed as statement
  per FIX P), the groundedIn IMPL edge (rationale → ruleRevised Event via
  the operator node), and the session linkage (extractedFrom → Source,
  Session CONTAINS) — through the real /v1/sessions/commit path.
- No-reasoning variant (E2E-4 negative a): rationale NOT minted + the
  ruleLifecycle chain-completeness warning fires (chain enforcement warn).
- R7: ontology.memory_granularity reaches the value brief (compile_value_brief
  / build_master_list carry the agent-ops granularity text).
- Enforcement-resolution forward contract (#1934/E2E-5): the manifest's
  extraction.enforcement.kinds {rule: retry} resolves via
  PackManifest.enforcement_for("rule") == "retry" (the WIRING is #1934's).
- Pack validation: the manifest validates clean in the shared catalog.

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")

import pytest  # noqa: I001
from fastapi.testclient import TestClient

from tortoise import extractor_v2 as v2
from tortoise.hosted_api import app, get_current_team
from tortoise.sdk import TortoiseSDK

# ── Test constants ───────────────────────────────────────────────────────────

TEST_TEAM_ID = f"team-{uuid.uuid4().hex[:8]}"
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    "tier": "free",
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": 1000,
}

RULE_TEXT = "destructive actions require a verbal token acknowledgement"
RATIONALE_TEXT = (
    "a prior incident of unacknowledged destructive action caused rollback "
    "loss, so the rule exists"
)
REVISED_RULE_TEXT = (
    "destructive actions require a verbal token acknowledgement before any "
    "destructive action"
)

FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),  # tests/
    "fixtures", "expansion-epic",
)


def _fixture(name: str) -> list[dict]:
    """Fixture .txt → conversation EDUs (role: content)."""
    text = open(os.path.join(FIXTURES_DIR, name), encoding="utf-8").read()
    return [
        {"role": "assistant", "content": line.strip()}
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class MockModel:
    """Deterministic adapter with the complete(system=, user=) interface.

    Exactly 3 responses for a single-chunk session: S1 (story string),
    S2 (embed list JSON), S4 (same embed list — no-op merge).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        self.calls.append((system, user))
        if self._i >= len(self._responses):
            raise AssertionError("MockModel exhausted")
        resp = self._responses[self._i]
        self._i += 1
        return resp


def _embed(rule_kind: str = "agent-ops:rule", *, with_rationale: bool = True,
           with_event: bool = True) -> dict:
    """The mock S2/S4 embed list for the rules-with-why fixtures."""
    embed: dict = {
        "entities": [
            {"name": RULE_TEXT, "kind": rule_kind, "lifecycle": "created",
             "supersedes": None, "note": None},
        ],
        "events": [],
        "points": [],
        "operators": [],
        "chain_notes": [],
        "link_before_create": [],
    }
    if with_event:
        embed["events"].append(
            {"content": "the verbal-token rule was adopted",
             "eventKind": "agent-ops:ruleRevised", "about_entities": [RULE_TEXT]})
    if with_rationale:
        embed["points"].append(
            {"content": RATIONALE_TEXT, "pointKind": "agent-ops:rationale",
             "about_entities": [RULE_TEXT]})
    if with_rationale and with_event:
        embed["operators"].append(
            {"src": RATIONALE_TEXT, "dst": "the verbal-token rule was adopted",
             "op_type": "IMPL"})
    return embed


def _patch_tortoise_sdk_init(db_path: str):
    """Make TortoiseSDK use a temp db_path when constructed without one."""
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init
    ha_mod._FALLBACK_KEEPALIVE.clear()


@pytest.fixture
def client():
    """TestClient with auth override + a temp FalkorDBLite DB (the
    test_commit_endpoint pattern; on the docker lane the redirect flips
    the patched SDK's path= construction to the server)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, db_path
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


def _team_sdk(db_path: str) -> TortoiseSDK:
    return TortoiseSDK(db_path, namespace=TEST_TEAM_ID)


# ── Pack validation + ontology (surface 11: ontology validation) ────────────

class TestPackValidation:
    def test_agent_ops_manifest_valid_in_catalog(self):
        """The agent-ops manifest validates clean in the shared catalog —
        schema + cross-pack refs (relations/chains/kindDefs/extraction)."""
        from tortoise.pack_registry import PackRegistry, default_packs_dir
        reg = PackRegistry(default_packs_dir())
        reg.load_all()
        assert "agent-ops" in reg.packs
        assert not reg.errors.get("agent-ops"), reg.errors

    def test_agent_ops_manifest_shape(self):
        """The declared ontology shape: rule ⊂ (nearMiss) standard, rationale
        point kind, ruleRevised event kind, groundedIn extractable IMPL,
        ruleLifecycle warn chain."""
        from tortoise.pack_registry import PackRegistry, default_packs_dir
        reg = PackRegistry(default_packs_dir())
        reg.load_all()
        p = reg.packs["agent-ops"]
        assert p.object_kinds == ["rule"]
        assert p.point_kinds == ["rationale"]
        assert p.event_kinds == ["ruleRevised"]
        grounded = [r for r in p.relations if r.get("predicate") == "groundedIn"]
        assert grounded and grounded[0]["mechanism"] == "IMPL"
        assert grounded[0]["extractable"] is True
        assert grounded[0]["fromKind"] == "agent-ops:rule"
        assert grounded[0]["toKind"] == "agent-ops:rationale"
        chain = p.chains[0]
        assert chain["id"] == "ruleLifecycle"
        assert chain["steps"] == ["rule", "rationale", "ruleRevised"]
        assert chain["enforcement"] == "warn"
        # kindDefs: rule confusable with core standard (the #1934 near-miss)
        assert "standard" in p.kind_defs["rule"].get("nearMisses", [])

    def test_enforcement_rule_retry_resolution(self):
        """E2E-5 forward contract: extraction.enforcement.kinds {rule: retry}
        resolves via the registry seam (the retry WIRING is #1934's)."""
        from tortoise.pack_registry import PackRegistry, default_packs_dir
        reg = PackRegistry(default_packs_dir())
        reg.load_all()
        assert reg.packs["agent-ops"].enforcement_for("rule") == "retry"
        # chain enforcement reads the manifest warn
        from tortoise.domain_loader import domain_chain_spec
        spec = domain_chain_spec("agent-ops")
        assert spec["ruleLifecycle"]["enforcement"] == "warn"
        assert spec["ruleLifecycle"]["steps"] == ["rule", "rationale", "ruleRevised"]


# ── R7: memory_granularity reaches the value brief ─────────────────────────

class TestMemoryGranularity:
    def test_r7_granularity_reaches_value_brief(self):
        """R7: ontology.memory_granularity (under ontology:, the shipped-pack
        placement) reaches the value brief — compile_value_brief carries the
        agent-ops durable/ephemeral text (the surface-11 failure mode
        'memory_granularity not in prompts')."""
        from tortoise.value_extractor import compile_value_brief
        brief = compile_value_brief()
        g = brief.get("memory_granularity", {}).get("agent-ops", "")
        assert g, "agent-ops memory_granularity missing from the value brief"
        assert "Durable" in g and "rule text" in g
        assert "Ephemeral" in g

    def test_granularity_rides_the_v2_master_list(self):
        """build_master_list mirrors the granularity (the S1/S2/S4 prompt
        source)."""
        master = v2.build_master_list()
        g = master["memory_granularity"].get("agent-ops", "")
        assert g and "rule text" in g

    def test_agent_ops_kinds_in_v2_master_list(self):
        """PACK_NS: the agent-ops kinds ride the v2 master list's pack_kinds
        (the S2/S4 prompt vocabulary)."""
        master = v2.build_master_list()
        assert "agent-ops:rule" in master["pack_kinds"]


# ── E2E-4 happy path + negative (a) — extraction integration ────────────────

class TestRulesWithWhyExtraction:
    def test_happy_path_mines_rule_rationale_impl(self, client):
        """E2E-4 happy path (surface 11): mining a rules-with-why transcript
        mints the rule Object + rationale Point + groundedIn IMPL edge linked
        to the session, through the real commit path."""
        tc, db_path = client
        conv = _fixture("rules_with_why.txt")
        sid = f"sess_{uuid.uuid4().hex[:8]}"
        model = MockModel(
            ["the ops rotation adopted the verbal-token rule with its rationale",
             json.dumps(_embed()), json.dumps(_embed())])
        out = v2.extract_session_v2(model, conv, session_id=sid)
        assert out["errors"] == [], out["errors"]
        # happy path: NO ruleLifecycle completeness warning
        assert out.get("chain_completeness", {}).get("notes") == [], \
            out.get("chain_completeness")
        assert not any("ruleLifecycle" in w for w in out["warnings"]), out["warnings"]
        assert model._i == 3  # S1 → S2 → S4, nothing extra (determinism guard)

        payload = out["payload"]
        r = tc.post("/v1/sessions/commit", json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is False

        g = _team_sdk(db_path)._get_proj().g
        # rule Object minted with the pack kind (namespaced — entity lane)
        rows = g.query(
            "MATCH (o:Object {name:$n}) RETURN o.objectKind",
            params={"n": RULE_TEXT}).result_set
        assert rows and rows[0][0] == "agent-ops:rule", rows
        # rationale Point minted (statement after the FIX P repair), about the rule
        rows = g.query(
            "MATCH (p:Point {content:$c}) RETURN p.pointKind",
            params={"c": RATIONALE_TEXT}).result_set
        assert rows and rows[0][0] == "statement", rows
        n = g.query(
            "MATCH (p:Point {content:$c})-[:aboutObject]->(o:Object {name:$n}) "
            "RETURN count(o)",
            params={"c": RATIONALE_TEXT, "n": RULE_TEXT}).result_set[0][0]
        assert n >= 1
        # groundedIn IMPL edge: the operator node carries IMPL idx0 → rationale
        # and idx1 → the ruleRevised Event (the extraction's materialization of
        # the groundedIn relation, mechanism IMPL)
        rows = g.query(
            "MATCH (op:Point {is_operator:true, op_type:'IMPL'})"
            "-[:IMPL {idx:0}]->(p:Point {content:$c}) "
            "MATCH (op)-[:IMPL {idx:1}]->(e:Event) RETURN e.eventKind",
            params={"c": RATIONALE_TEXT}).result_set
        assert rows and rows[0][0] == "ruleRevised", rows
        # ruleRevised Event minted (bare kind — FIX A stripping)
        rows = g.query(
            "MATCH (e:Event {eventKind:'ruleRevised'}) "
            "RETURN e.eventKind, e.content", {}).result_set
        assert rows, "ruleRevised Event missing"
        # session linkage: extractedFrom → session Source + Session CONTAINS
        n = g.query(
            "MATCH (p:Point {content:$c})-[:extractedFrom]->"
            "(s:Source {url:'session.md'}) RETURN count(s)",
            params={"c": RATIONALE_TEXT}).result_set[0][0]
        assert n >= 1
        n = g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point {content:$c}) "
            "RETURN count(p)",
            params={"sid": sid, "c": RATIONALE_TEXT}).result_set[0][0]
        assert n >= 1

    def test_no_reasoning_variant_warns_chain_incomplete(self, client):
        """E2E-4 negative (a): the no-reasoning variant mints the rule but NO
        rationale — the ruleLifecycle chain-completeness warning fires
        (chain enforcement warn) and the rationale Point is NOT in the graph."""
        tc, db_path = client
        conv = _fixture("rules_no_reasoning.txt")
        sid = f"sess_{uuid.uuid4().hex[:8]}"
        model = MockModel(
            ["the ops rotation adopted the verbal-token rule",
             json.dumps(_embed(with_rationale=False, with_event=False)),
             json.dumps(_embed(with_rationale=False, with_event=False))])
        out = v2.extract_session_v2(model, conv, session_id=sid)
        assert out["errors"] == [], out["errors"]
        notes = out.get("chain_completeness", {}).get("notes", [])
        assert notes, "chain-completeness note expected for the no-reasoning variant"
        assert notes[0]["chain"] == "ruleLifecycle"
        assert notes[0]["action"] == "warned"
        assert "rationale" in notes[0]["finding"]
        assert any("ruleLifecycle" in w and "incomplete" in w
                   for w in out["warnings"]), out["warnings"]
        # the rationale content is never minted
        payload = out["payload"]
        assert all(RATIONALE_TEXT not in p.get("content", "")
                   for p in payload.get("points", [])), payload["points"]
        r = tc.post("/v1/sessions/commit", json=payload)
        assert r.status_code == 200, r.text
        g = _team_sdk(db_path)._get_proj().g
        n = g.query(
            "MATCH (p:Point {content:$c}) RETURN count(p)",
            params={"c": RATIONALE_TEXT}).result_set[0][0]
        assert n == 0, "rationale Point must NOT be minted (no reasoning stated)"

    def test_fixtures_present(self):
        """The three E2E fixtures exist with content (near_miss.txt is the
        #1934 forward contract — not consumed by this slice)."""
        for name in ("rules_with_why.txt", "rules_no_reasoning.txt",
                     "near_miss.txt"):
            path = os.path.join(FIXTURES_DIR, name)
            assert os.path.exists(path), f"missing fixture {name}"
            text = open(path, encoding="utf-8").read()
            assert len(text.strip()) > 0
