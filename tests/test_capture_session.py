"""SDK capture_session tests (#312 delta 4 + delta 5 speaker tagging, #822).

#822: LLM extraction is the default (and only) capture extraction — the regex
loop was removed as a product path. #3892 (owner ruling 2026-09-18) changed
what a MISSING key means: the capture itself is unconditional — the session's
turns are always STORED (and are keylessly searchable) — and the key gates
ONLY the LLM extraction into memory points (receipt ``extraction_mode``
``"no-provider"``). These tests run against the offline MockModel extractor
(TORTOISE_SESSION_LLM_MOCK=1 seam) so no provider key or network is needed.
"""
import json
import os
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tests._provider_env import pin_provider_routing_env, provider_routing_env_names
from tests._verdict import verdict_client
from tortoise import hosted_api as _ha
from tortoise.hosted_api import app, get_current_org
from tortoise.sdk import TortoiseSDK

# #2242 concurrency test docker-lane guard — mirrors
# tests/test_commit_supersession_parity.py:56-71 (constant + helper only, no
# module-level pytestmark). Single-statement CAS atomicity is server-mode only
# (metering.py doctrine) → the race test skips on URI-less lanes.
_SUPPORTED = {"docker", "redis", "rediss"}


def _server_uri_set() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    scheme = uri.split("://", 1)[0]
    return scheme in _SUPPORTED and bool(urlparse(uri).hostname)




# ── #3662: the SDK transmission path requires explicit consent ────────────
# `_post_commit` is the in-repo CLIENT-side transmission primitive for
# session-derived content; #3615's predicate did not reach it, so
# `commit_session` uploaded with no opt-in. The gate now runs twice: first here
# (before any extraction is spent), then at `_post_commit` itself (the
# bypass-proof chokepoint, pinned in tests/test_capture_consent.py).

def _valid_v2_payload() -> dict:
    """A Layer-1 payload the gate ACCEPTS.

    Both tests below need a mocked extraction that would genuinely reach
    `_post_commit`. With an empty ``{}`` (or a payload the Layer-1 gate
    rejects) the SDK appends an error, returns ``ok=False`` BEFORE the POST, and
    the refusal test's ``posted == []`` would hold whether or not the consent
    gate existed — the gate would then be pinned only by ``extracted == []``.

    The shape mirrors ``tests/test_commit_schema.py``'s proven fixture: an
    EMPTY ``client_commit_id`` finalized with the schema's own
    ``compute_client_commit_id``, and a ``provenance_refs`` entry — the gate
    resolves ``source_ref`` against the emitted sources / provenance refs.
    """
    from tortoise.commit_schema import compute_client_commit_id

    raw = {
        "schema_version": "1",
        "session_id": "sess_3662_consent",
        "client_commit_id": "",
        "captured_at": "2026-08-11T10:00:00Z",
        "extractor": {"version": "value@1.0.0", "mode": "byok",
                      "calibration_version": "v3"},
        "summary": "consent-gate fixture",
        "story_arc": "",
        "provenance_refs": [{"path": "session.md", "spans": ["0-10"]}],
        "sources": [],
        "entities": [],
        "points": [{
            "id": "pt_" + "3662" * 16,
            "content": "an unconsented transmission must not leave the machine",
            "pointKind": "statement",
            "reason": "NEW",
            "confidence": 0.5,
            "c_cal": 0.5,
            "about_entities": [],
            "source_ref": "session.md",
            "quote": "",
            "status": "live",
        }],
        "operators": [],
        "telemetry": {
            "extractor": {"version": "value@1.0.0", "mode": "byok",
                          "calibration_version": "v3"},
            "model": {"provider": "anthropic", "id": "claude-3-7",
                      "cfg_hash": "h1"},
            "counts": {"kept": 1, "candidate": 1, "segment": 1, "window": 1,
                       "empty_windows": 0},
            "keep_ratio": 1.0,
            "dedup_hits": 0,
            "frontier_calls": 0,
            "llm_cost_usd": 0.0,
            "extraction_ms": 1,
            "retry_count": 0,
            "last_error_code": None,
            "confidence_histogram": [0] * 10,
        },
    }
    raw["client_commit_id"] = compute_client_commit_id(
        raw["session_id"], raw["points"], raw["entities"], raw["operators"],
        raw["summary"], raw["story_arc"], raw.get("events", []),
        raw.get("supersessions", []))
    return raw


def _extract_with_payload(payload: dict, extracted: list):
    """A stand-in for ``extract_session_v2`` that records the call."""
    def _fake(model, conversation, **kw):
        extracted.append(conversation)
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "chain_notes": [], "link_before_create": [],
                "warnings": [], "story_arc": "", "search": {},
                "stats": {}, "errors": []}
    return _fake






def test_capture_session_module_isolates_real_home(tmp_path):
    """#3662 review (P2): pin the HOME isolation the refusal test depends on.

    ``test_commit_session_refuses_without_consent_before_extraction`` reaches
    ``record_capture_declined()``, which resolves and CREATES
    ``$HOME/.tortoise/capture-consent-notice``. Without the module-wide HOME
    isolation (see ``_isolated_home``) that path is the developer's real
    ``$HOME`` and the durable #3615 notice leaks onto their machine. Assert the
    resolved location, not the notice text.
    """
    from tortoise.capture_consent import capture_notice_path

    assert os.environ.get("HOME") == str(tmp_path), (
        "tests/test_capture_session.py must isolate HOME (see _isolated_home)")
    assert capture_notice_path() == tmp_path / ".tortoise" / (
        "capture-consent-notice")


# Legacy predicate name for negative-direction tests (#281). Kept as a
# constant so no edge-syntax literal appears in source (Task 5 sweep requires
# zero hits) — same pattern as tests/test_ranking.py.
_LEGACY_INSTANTIATES = "INSTANTIATES"


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Install the offline MockModel session extractor (#822) — the M2 LLM
    pipeline runs with zero network regardless of ambient provider keys
    (the dev shell has real OPENROUTER/DEEPSEEK keys). Any test that needs
    the keyless path clears the seam AND the provider keys itself."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture(autouse=True)
def _pinned_provider_routing_env(monkeypatch):
    """#3699: remove the AMBIENT provider-routing env for every test in this
    module, from the canonical registries (`tests/_provider_env.py`).

    The per-test ``monkeypatch.delenv`` lists are hand-lists and had drifted
    from the gates: an ambient ``TORTOISE_EXTRACTOR_PROVIDER=openrouter`` or
    ``VENICE_API_KEY`` decided the five #1530 gate/failover tests above,
    turning the developer's shell into the test result. Clearing the derived
    name set HERE means a routing test's outcome is set by the test's own
    environment — a test that needs a provider still sets one explicitly, and
    its ``monkeypatch`` write wins over this teardown-undone removal.

    ``TORTOISE_SESSION_LLM_MOCK`` is NOT in the set (see the module docstring):
    the sibling ``llm_extraction_provider`` fixture above owns that seam.
    """
    pin_provider_routing_env(monkeypatch)


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """#3662 review (P2): never let this module write into the developer's real
    ``$HOME``.

    The unconsented-refusal test below drives
    ``capture_consent.capture_declined_reason()`` → ``record_capture_declined()``,
    which resolves ``Path.home()/.tortoise/capture-consent-notice`` and CREATES
    it — so a normal pytest run left the durable #3615 migration notice in the
    real ``$HOME``, and the next interactive ``tortoise`` command printed it.
    Mirrors ``test_capture_consent.py::_isolated`` for the HOME half only: the
    suite-wide ``_capture_consent_default_on`` grant stays in force here (unlike
    that file's decline matrix), so the consented control test is unaffected.
    """
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]






def _money_payload(content: str, *, quote: str = "",
                   pid: str = "pt_2817_money") -> dict:
    """One v2 payload point carrying ``content``; ``quote`` is the E3 excerpt."""
    return {
        "entities": [],
        "events": [],
        "points": [{
            "id": pid,
            "content": content,
            "pointKind": "statement",
            "reason": "NEW",
            "confidence": 0.5,
            "c_cal": 0.5,
            "about_entities": [],
            "source_ref": "session.md",
            "quote": quote,
            "status": "draft",
        }],
        "operators": [],
    }


def _read_money_row(sdk, pid: str) -> list:
    return list(sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN n.amountMinor, n.currency, n.minorUnitExponent, "
        "n.verbatim, n.asOf",
        params={"id": pid},
    ).result_set[0])










# ── #2817 review F1/F2: the money abstention CHANNEL at the capture seam ───
#: A point whose claim carries a bare number and no currency anchor. Every one
#: of these drops as ``no_currency_anchor`` — the design's named abstention
#: (§6/T3) — which the seam MUST report.
_UNANCHORED_CLAIMS = (
    "the tranche released 800 last week",
    "the policy covers all 800 accounts",
    "800 people were hired",
)


def _money_channel(*contents: str) -> dict:
    """The v2 payload for ``contents`` — one point per claim."""
    return {
        "entities": [],
        "events": [],
        "points": [{
            "id": f"pt_2817_f1_{i}",
            "content": content,
            "pointKind": "statement",
            "reason": "NEW",
            "confidence": 0.5,
            "c_cal": 0.5,
            "about_entities": [],
            "source_ref": "session.md",
            "quote": content[:200],
            "status": "draft",
        } for i, content in enumerate(contents)],
        "operators": [],
    }


def _money_warnings(res: dict) -> list[str]:
    return [w for w in res.get("warnings") or []
            if "value-normalizer" in w]












def _passthrough_payload(content: str) -> dict:
    """The v2 payload shape the #2813/#2949 tests drive — one point carrying
    all four E3 passthrough fields."""
    return {
        "entities": [],
        "events": [],
        "points": [{
            "id": "pt_2949_passthrough",
            "content": content,
            "pointKind": "statement",
            "reason": "NEW",
            "confidence": 0.5,
            "c_cal": 0.5,
            "about_entities": [],
            "source_ref": "session.md",
            "quote": "We decided to ship serve --http first.",
            "status": "draft",
            "search_keys": ["auth", "dead-end"],
            "source_turn_id": "turn-2813",
            "when": "2026-08-01",
        }],
        "operators": [],
    }


def _install_fake_extract(monkeypatch, payload: dict) -> None:
    import tortoise.extractor_v2 as ev2

    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "chain_notes": [], "link_before_create": [],
                "warnings": [], "story_arc": "", "search": {},
                "stats": {}, "errors": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)


def _read_passthrough_props(sdk, pid: str) -> list:
    return list(sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN n.quote, n.when, n.search_keys, n.source_turn_id",
        params={"id": pid},
    ).result_set[0])








def test_capture_passthrough_read_clause_covers_whitelist():
    """#2949 (review F4): the dedup-hit read-back field list is DERIVED from
    the single ordered declaration, so it cannot silently omit a newly
    whitelisted E3 field — the pre-fix defect, where the create path stored by
    whitelist membership while a hand-written inline RETURN omitted the field
    (the #2813 class on the dedup path).

    MUTATION THAT REDS THIS TEST: hand-write the read-back (drop a field from
    ``_capture_passthrough_read_fields``) — the coverage assertion fails."""
    from tortoise.sdk import (
        _CAPTURE_PASSTHROUGH_ORDER,
        _CAPTURE_PASSTHROUGH_PROPS,
        _capture_passthrough_read_fields,
    )
    clause = _capture_passthrough_read_fields()
    missing = sorted(
        k for k in _CAPTURE_PASSTHROUGH_PROPS if f"n.{k}" not in clause)
    assert missing == [], (
        f"read-back {clause!r} omits whitelisted props {missing!r}")
    assert clause.count("n.") == len(_CAPTURE_PASSTHROUGH_PROPS), clause
    assert set(_CAPTURE_PASSTHROUGH_ORDER) == set(_CAPTURE_PASSTHROUGH_PROPS)
    assert len(_CAPTURE_PASSTHROUGH_ORDER) == len(_CAPTURE_PASSTHROUGH_PROPS)


def test_capture_passthrough_read_helper_reads_every_whitelisted_prop(sdk):
    """#2949 (review F4) behavioral arm: the shared read-back helper returns
    EVERY whitelisted field the node holds, through the SAME generated RETURN
    clause. A runtime drop (a field missing from the derivation) REDs here.
    ``search_keys`` is read back in its stored flat-string form."""
    from tortoise.sdk import _CAPTURE_PASSTHROUGH_PROPS
    # #5007: the probe writes EVERY whitelisted field, so the "node holds"
    # set stays equal to the whitelist — a newly whitelisted OPTIONAL field
    # (span_start/span_end are absent on a spanless point) would otherwise
    # make this assertion vacuous rather than red.
    pid = sdk.create_point(
        "statement", "read helper probe", quote="q-2949",
        when="2026-01-01", search_keys=["a", "b"],
        source_turn_id="turn-2949",
        span_start=0, span_end=5)["id"]
    stored = sdk._read_capture_passthrough_props(sdk._get_proj(), pid)
    assert set(stored) == set(_CAPTURE_PASSTHROUGH_PROPS), stored
    assert stored == {
        "quote": "q-2949",
        "when": "2026-01-01",
        "search_keys": "a b",
        "source_turn_id": "turn-2949",
        "span_start": 0,
        "span_end": 5,
    }, stored





    
def test_capture_promotes_a_multi_labelled_endpoint_operator_once(
        tmp_path, monkeypatch):
    """#7853 review P2-1: the per-label sweep must journal ONE promotion per
    operator.

    A multi-labelled endpoint (``:Point:Object`` — the shape ``sdk.py``'s
    entity write and ``mcp_server`` model) matches the ``Point`` AND the
    ``Object`` sweep, so an un-deduped row list promotes the SAME operator
    twice and journals ``OperatorPromoted`` twice for one id — breaking the
    invariant ``compute_confidence``'s write-back asserts (the journal set
    equals the committed set, #395).
    """
    from tortoise.log import EventLog
    from tortoise.sdk import _apply_capture_ingest_ep

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db_path=str(tmp_path / "multi.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        proj = sdk._get_proj()
        # ONE endpoint carrying BOTH labels, wired to a draft IMPL operator.
        proj.g.query(
            "CREATE (n:Point:Object {id:'multi-1', name:'m', "
            "status:'draft'})")
        proj.g.query(
            "CREATE (o:Point {id:'op-multi-1', is_operator:true, "
            "op_type:'IMPL', status:'draft'})")
        proj.g.query(
            "MATCH (o:Point {id:'op-multi-1'}), (c {id:'multi-1'}) "
            "CREATE (o)-[:IMPL {idx:0}]->(c) "
            "CREATE (c)-[:INPUT {idx:0}]->(o)")
        # The promotion happens before the ingest EP pass; stub the pass so
        # the test measures the SWEEP, not EP cost.
        monkeypatch.setattr(sdk, "dream", lambda **kw: {})
        # #1088 signature: ``promotion_ids`` is the minted-claim set this call
        # may promote. The operator below is reached through the CLAIM join
        # (it was not created by this capture), so ``operator_ids`` stays
        # empty and the row can only arrive via the per-label sweep — which
        # is exactly what the dedup assertion measures.
        _apply_capture_ingest_ep(sdk, ["multi-1"],
                                 promotion_ids=["multi-1"], operator_ids=[])
    finally:
        sdk.close()

    promos = [e for e in EventLog(events / "events.jsonl").read_all()
              if e.get("type") == "OperatorPromoted"]
    assert [e["id"] for e in promos] == ["op-multi-1"], (
        f"exactly one OperatorPromoted for the operator, got {promos}")






















































# ── E2E-6: Semantic connections (Event→Object) ──────────────────────────
#
# ONTOLOGY v3.2 §3.2 (issue #214) removed the INSTANTIATES predicate from
# the valid-predicate vocabulary; the canonical Event→Object connection is
# the aboutObject edge (#281 re-scope). These tests assert the canonical
# aboutObject edge produced by the capture surface (capture_session →
# create_about_edge / create_event) and guard that no INSTANTIATES edge is
# created anywhere in the graph these tests touch. The session-indexer's
# legacy INSTANTIATES producer (_connect_issue_objects, sdk.py) is still
# pending #281's swap on origin/main; its compliance with #214 is #281's
# test scope, not asserted here.




def test_create_event_wires_about_object_by_name(sdk):
    """E2E-6 companion: create_event(aboutObject=<name>) — the wiring the
    session-capture flow uses once #281 lands — resolves an existing Object
    by name and produces the canonical aboutObject edge."""
    sdk.create_object("license-research", "skill")
    ev = sdk.create_event("session-s1", "sessionCaptured",
                          aboutObject="license-research")
    proj = sdk._get_proj()
    conn = proj.g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object) "
        "RETURN o.name, o.objectKind",
        params={"eid": ev["eventId"]},
    ).result_set
    assert conn == [["license-research", "skill"]], f"name-resolved aboutObject miss: {conn}"


# ── #1194: relation-stage flood cap — the pre-write estimate stays a true ceiling


class _PermissiveRelationModel:
    """Relation model returning EVERY ordered pair (i→j) — the O(n²) flood a
    permissive real model can emit. MockModel's cue-word sparsity hides this in
    the other tests; a real model has no prompt-enforced cap (extractor.py
    _RelationStage.run returns the raw relations list)."""

    id = "perm-relations"

    def complete(self, *, system, user):
        pts = json.loads(user)["points"]
        n = len(pts)
        return json.dumps({"relations": [
            {"op_type": "IMPL", "src": i, "dst": j}
            for i in range(n) for j in range(n) if i != j
        ]})


class _CountingAPI:
    """Duck-typed EventAPI recording point/operator counts (extractor only
    calls add_point/add_operator in transcript mode)."""

    def __init__(self):
        self.points = 0
        self.operators = 0
        self.ids = []

    def add_point(self, content, provenance, **fields):
        self.points += 1
        self.ids.append(f"p{self.points}")
        return self.ids[-1]

    def add_operator(self, op_type, args, provenance):
        self.operators += 1


def test_session_relation_flood_capped_estimate_is_true_ceiling():
    """#1194: a permissive relation model emitting O(n²) relations must not
    write more operator nodes than the pre-write estimate counted. The
    extractor dedupes + clamps operators ≤ points, so actual node writes
    (points + operators) stay ≤ estimate (2 × sentences) — the 402 flood
    gate the estimate feeds remains a true upper bound on node writes.

    10 points → the permissive model emits 90 relations; without the cap that
    is 90 IMPL operator nodes (non-episodic Points counted by the quota) vs
    an estimate of 2 × 10 = 20 — a silent gate bypass. With the cap, operators
    are held at ≤ points and the estimate holds."""
    from tortoise.extractor import LLMExtractor, MockModel
    from tortoise.sdk import _session_llm_transcript

    conversation = [
        {"role": "user", "content": f"sentence number {i} about the topic."}
        for i in range(10)
    ]
    transcript, est = _session_llm_transcript(conversation)
    assert est == 20, f"expected 2×10 sentences estimate, got {est}"

    ex = LLMExtractor(MockModel("mock-point"), _PermissiveRelationModel())
    api = _CountingAPI()
    ex.run(transcript, "src-test", api)

    assert api.points == 10, api.points
    # the O(n²) flood (90 relations) is clamped to the estimate's ceiling
    assert api.operators <= api.points, (
        f"operator flood escaped the cap: {api.operators} > {api.points} points")
    assert api.points + api.operators <= est, (
        f"estimate {est} < actual nodes {api.points + api.operators}")


# ── #1350: v2 capture (the default since this wiring) ─────────────────









# ── #1530 P2: provider routing through the capture path (E2E-8) ─────────────

_V2_EMBED_JSON = (
    "{\"entities\": [{\"name\": \"the strategy\", "
    "\"kind\": \"core:strategy\", \"lifecycle\": \"created\", "
    "\"supersedes\": null, \"note\": null}], "
    "\"events\": [{\"content\": \"we decided on the new strategy\", "
    "\"eventKind\": \"core:decision\", \"about_entities\": [\"the strategy\"]}], "
    "\"points\": [{\"content\": \"the new strategy is durable\", "
    "\"pointKind\": \"statement\", \"about_entities\": [\"the strategy\"]}], "
    "\"operators\": [], \"chain_notes\": [], \"link_before_create\": []}")


class _FakeLLMResp:
    """Deterministic offline LLM response, keyed on the system prompt like
    _V2SessionMock (S1 → narrative, S2/S4 → the embed JSON)."""
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}],
                "usage": {}}


def _install_fake_provider(monkeypatch, requests_log):
    """Monkeypatch requests.Session.post with the _V2SessionMock response
    logic so the real adapters (OpenRouterModel / DeepSeekDirectModel) run
    fully offline; every request URL is appended to ``requests_log``."""
    import requests as _requests

    def _fake_post(self_or_url, url=None, **kwargs):
        requests_log.append(url)
        system = ((kwargs.get("json") or {}).get("messages") or [{}])[0].get("content", "")
        if "STORY SUMMARIZER" in system:
            content = "The session revealed a new strategy."
        else:
            content = _V2_EMBED_JSON
        return _FakeLLMResp(content)

    # Epic #1647 (PR #1684): the adapters call self._session.post
    # (requests.Session) — patching requests.post never intercepted, so
    # these tests made REAL network calls and 401'd/flaked without keys.
    monkeypatch.setattr(_requests.Session, "post", _fake_post)


def test_no_provider_keys_fixture_scrubs_every_ambient_key(
        no_provider_keys, monkeypatch):
    """#3695 regression pin: the hermeticity fixture must scrub EVERY key the
    gate stack reads, DERIVED rather than re-listed.

    Both drift directions are pinned, because the gate stack reads TWO
    registries that have already drifted apart once:

    * v2-side — ``model_adapters._PROVIDER_KEY_ENV`` carries VENICE_API_KEY
      (#1549), which re-opened the v2 inner gate on six tests green in CI and
      red on a venice-configured box (#3695/#5477).
    * lane-side — ``hosted_api._llm_provider_keys()`` (from
      ``ingest._PROVIDERS``) carries GEMINI_API_KEY, which builds an M2
      gemini extractor and so suppresses the ValueError these same tests
      expect (#3699).

    ANTHROPIC/MOONSHOT/ZAI are deliberately NOT scrubbed and need not be: no
    module under ``tortoise/`` reads them, and ``test_provider_availability``
    pins that a foreign key cannot open the gate.

    The pin is non-vacuous: the ambient keys that caused each class of red are
    shown to REALLY open their gate below, so a fixture that scrubbed nothing
    could not pass."""
    from tortoise.hosted_api import _llm_provider_keys
    from tortoise.model_adapters import (
        _PROVIDER_KEY_ENV,
        resolve_extractor_provider,
    )

    scrubbed = set(no_provider_keys)
    assert set(_PROVIDER_KEY_ENV.values()) <= scrubbed, \
        "a provider key the v2 resolver honours escaped the fixture scrub"
    assert set(_llm_provider_keys()) <= scrubbed, \
        "a lane-gate key escaped the fixture scrub"
    assert "VENICE_API_KEY" in scrubbed, \
        "the #1549 venice key is the one that reddened six tests (#3695)"
    assert "GEMINI_API_KEY" in scrubbed, \
        "the lane-side key that reddened the same tests (#3699)"
    assert resolve_extractor_provider() == (None, None)
    # non-vacuous, both directions: each ambient key alone opens its own gate
    monkeypatch.setenv("VENICE_API_KEY", "ambient-venice")
    assert resolve_extractor_provider() == ("venice", ["venice"])
    monkeypatch.delenv("VENICE_API_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-gemini")
    from tortoise.sdk import _session_llm_provider
    assert _session_llm_provider() == "gemini"










def test_capture_session_failover_meta_flags(sdk, monkeypatch, no_provider_keys):
    """The (extracted, meta) contract surfaces failover_used + errors through
    _extract_session_v2 (P1 consumes meta — shared contract, D8).

    Hermetic provider env via ``no_provider_keys`` (#3695/#5477): the two fake
    keys are the whole pool, so the primary is deepseek-direct."""
    import requests as _requests

    from tortoise.model_adapters import DeepSeekDirectModel, _reset_failover_cooldown

    _reset_failover_cooldown()
    requests_log = []
    _install_fake_provider(monkeypatch, requests_log)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    calls = {"ds": 0}
    orig = DeepSeekDirectModel.complete

    def _flaky_ds_complete(self, *, system, user, max_tokens: int | None = None):
        calls["ds"] += 1
        if calls["ds"] == 1:
            raise _requests.ConnectionError("simulated DS collapse (#1350)")
        return orig(self, system=system, user=user)

    monkeypatch.setattr(DeepSeekDirectModel, "complete", _flaky_ds_complete)

    extracted, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "we decided"}],
        session_id="s-meta", now="2026-08-20T00:00:00Z")
    assert meta["provider"] == "deepseek-direct"
    assert meta["route"] == "openrouter"
    assert meta["failover_used"] is True
    assert isinstance(meta["errors"], list)
    assert isinstance(meta["warnings"], list)
    assert len(extracted) >= 1


def test_capture_session_fatal_4xx_no_failover(sdk, monkeypatch, no_provider_keys):
    """E2E-8 fatal-4xx negative: the primary raises 401 → the extraction
    fails closed with NO fallback attempt (fatal is never failed over).

    Hermetic provider env via ``no_provider_keys`` (#3695/#5477) — the 401
    must be attributed to deepseek-direct, the primary this test configured."""
    import requests as _requests

    from tortoise.model_adapters import DeepSeekDirectModel, _reset_failover_cooldown

    _reset_failover_cooldown()
    requests_log = []
    _install_fake_provider(monkeypatch, requests_log)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    err = _requests.HTTPError("HTTP 401")
    err.response = type("R", (), {"status_code": 401})()

    def _fatal_ds_complete(self, *, system, user, max_tokens: int | None = None):
        raise err

    monkeypatch.setattr(DeepSeekDirectModel, "complete", _fatal_ds_complete)

    _, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "we decided"}],
        session_id="s-fatal", now="2026-08-20T00:00:00Z")
    assert meta["failover_used"] is False, "fatal 4xx must never fail over"
    assert meta["route"] == "deepseek-direct"
    assert meta["errors"], "the fatal 401 must be recorded in meta errors"
    # no OpenRouter call ever happened
    assert not any("openrouter.ai" in u for u in requests_log)


# ── #3699: the provider-routing env is PINNED, not inherited ──────────────
# The five tests above failed on a developer shell exporting
# TORTOISE_EXTRACTOR_PROVIDER / VENICE_API_KEY (measured: `5 failed` with
# `TORTOISE_EXTRACTOR_PROVIDER=openrouter VENICE_API_KEY=vz-ambient`), because
# their hand-listed delenv pair did not cover what
# model_adapters.resolve_extractor_provider() reads. The module-wide
# `_pinned_provider_routing_env` fixture closes that; these pin the closure.

def test_provider_routing_env_is_pinned_for_this_module():
    """The pin is in force for every test here, so an ambient routing value
    cannot decide a gate result. Reds on a hostile shell WITHOUT the fixture
    (the #3699 false-red class) — that is the point of the assertion."""
    residual = [n for n in provider_routing_env_names() if n in os.environ]
    assert not residual, (
        f"ambient provider-routing env leaked into this module: {residual} — "
        f"the #3699 pin (_pinned_provider_routing_env) must neutralize these")


def test_provider_routing_pin_covers_every_gate_input():
    """Drift guard: the pin is DERIVED, so it covers both routing gates'
    key sets — including VENICE_API_KEY, which the hosted availability set
    does not carry and the hand-lists therefore missed."""
    from tortoise.hosted_api import _LLM_PROVIDER_KEYS
    from tortoise.model_adapters import _PROVIDER_KEY_ENV

    pinned = set(provider_routing_env_names())
    assert set(_LLM_PROVIDER_KEYS) <= pinned, (
        "a hosted provider key env is not pinned — the broad availability "
        "gate could be opened by the ambient shell")
    assert set(_PROVIDER_KEY_ENV.values()) <= pinned, (
        "a v2 adapter key env is not pinned — resolve_extractor_provider() "
        "could route on the ambient shell")
    assert "TORTOISE_EXTRACTOR_PROVIDER" in pinned, (
        "the primary/fallback pool selector is not pinned — an ambient "
        "TORTOISE_EXTRACTOR_PROVIDER flips the primary (the measured #3699 red)")


def test_provider_routing_pin_is_load_bearing_against_a_hostile_shell(monkeypatch):
    """Non-vacuous proof that the pin — not luck about the shell — is what
    keeps the routing gate honest. Reproduces the measured #3699 mechanism:
    with the hostile ambient values in place `resolve_extractor_provider()`
    decides on THEM; after the pin it reports no routing-usable provider, which
    is what lets `test_capture_session_gate_match_openai_key_alone_rejected`
    observe the DEEPSEEK_API_KEY failure it is actually asserting on."""
    from tortoise.model_adapters import resolve_extractor_provider

    # the hostile ambient shell (measured reproduction)
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    monkeypatch.setenv("VENICE_API_KEY", "vz-ambient")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError):
        resolve_extractor_provider()  # ambient value decides — the false red

    pin_provider_routing_env(monkeypatch)
    assert resolve_extractor_provider() == (None, None), (
        "after the pin the gate must see no routing-usable provider")


# ── P1 (#1529) fail-closed capture ────────────────────────────────────────
# Extraction errors surface (ok=False + errors on the response, mode="error"),
# extraction_mode is truthful ("llm:<route>" / "llm" on success, "empty" /
# "error" never claim success), an empty/blank conversation never ok=True and
# commits nothing, and E3's source_turn_id is never clobbered. Built on the
# P2 (#1530) live (extracted, meta) contract — meta carries errors/warnings/
# mode; P1 must not re-shape it (additive only).


class _FailingSessionExtractor:
    """Duck-typed M2 extractor whose run() raises — the dead-key / mid-run
    failure P1 must surface, not swallow (E2E-8 dead-key negative)."""
    version = "failing@0"

    def run(self, transcript, source_id, api):
        raise RuntimeError("provider returned 500")


class _PartialFailingSessionExtractor:
    """Emits ONE point then raises — the partial-emission case: extracted>0
    must never be read as success (ok is the signal)."""
    version = "partial@0"

    def run(self, transcript, source_id, api):
        api.add_point("decision: ship serve first", {"source": source_id})
        raise RuntimeError("provider rate limited mid-run")


class _EmptyOutputExtractor:
    """'Succeeds' but emits no points — the last silent extracted:0 window."""
    version = "empty-out@0"

    def run(self, transcript, source_id, api):
        pass


def _v2_out(payload=None, errors=(), warnings=()):
    """Shape-complete extractor_v2.extract_session_v2 output for seams."""
    return {"session_id": "sess_p1", "story_arc": "", "embed_list": {},
            "search": {"mode": "embedded", "degraded": True},
            "payload": payload, "chain_notes": [], "link_before_create": [],
            "supersessions": [], "warnings": list(warnings),
            "minted_kinds": [], "stats": {}, "errors": list(errors)}


# ── v2 branch (DEFAULT — the issue's "_extract_session_v2 discards
#    out[errors]" checklist item) ────────────────────────────────────────

def test_extract_session_v2_consults_out_errors(sdk, monkeypatch):
    """P1: the v2 wrapper must surface extractor_v2 out['errors'] — a dead
    key yields meta mode='error' + errors, never a silent extracted:0 (E2E-8)."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(errors=["RuntimeError: provider returned 500"]))
    extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert extracted == []
    assert any("provider returned 500" in e for e in meta["errors"])
    assert meta["warnings"] == [], "failure carries errors, never warnings"


def test_extract_session_v2_surfaces_warnings_and_zero_points(sdk, monkeypatch):
    """P1 (D6): completed-but-empty v2 output (payload None, no errors) is
    an additive warning, not a silent 0 and not a fake failure."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out())
    extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "v2"
    assert extracted == []
    assert any("no points" in w for w in meta["warnings"])
    assert meta["errors"] == []


def test_extract_session_v2_passthroughs_source_turn_id(sdk, monkeypatch):
    """E3 passthrough (v2 carrier): payload points carrying E3's fields
    (source_turn_id, search_keys, when, quote — E3 #1535 landed them on the
    v2 payload) flow through `props` unchanged (whitelisted)."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": "p-v2-1", "content": "we decided X",
                           "pointKind": "statement", "source_turn_id": "t0",
                           "search_keys": ["decide x", "x decision"],
                           "when": "2026-08-20", "quote": "we decided X"}],
               "operators": [], "events": [], "supersessions": [],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    extracted, _meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert extracted, "payload point must land"
    props = extracted[0].get("props", {})
    assert props.get("source_turn_id") == "t0", extracted
    assert props.get("search_keys") == ["decide x", "x decision"], extracted
    assert props.get("when") == "2026-08-20", extracted
    assert props.get("quote") == "we decided X", extracted
    # whitelist discipline: internal payload fields must NOT leak into props
    assert not any(k in props for k in ("id", "content", "pointKind",
                                        "about_entities")), props


def test_extract_session_v2_counts_point_write_skips(sdk, monkeypatch):
    """P1: the v2 point-write loop's silent `except: pass` becomes counted —
    a point that fails to write surfaces as an additive warning + error, never
    an invisible partial write."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": "p-ok", "content": "we decided X",
                           "pointKind": "statement"},
                          {"id": "", "content": "no id -> skipped"},
                          {"id": "p-boom", "content": "we decided Y",
                           "pointKind": "statement"}],
               "operators": [], "events": [], "supersessions": [],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _real_create_point = sdk.create_point

    def _boom_point(*args, **kwargs):
        if (args and args[1] == "we decided Y") or kwargs.get("content") == "we decided Y":
            raise RuntimeError("point write failed")
        return _real_create_point(*args, **kwargs)

    monkeypatch.setattr(sdk, "create_point", _boom_point)
    extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert extracted, "the successful point is reported"
    assert any("failed to write" in w or "skipped" in w for w in meta["warnings"]), meta
    assert meta["mode"] == "error", "a failed point write is a capture failure"


# ── #2552: payload operators with EVENT endpoints land at capture ──────


def test_extract_session_v2_wires_operator_to_event_endpoint(sdk, monkeypatch):
    """#2552: a payload operator whose endpoint is an extracted EVENT must
    reach the graph. Pre-fix the v2 capture seam created payload events under
    fresh ULID ids (dropping the content-addressed ``ev_<sha>`` id the payload
    and its operators reference) — create_operator's existence check then
    failed and EVERY event-endpoint operator was silently skipped (the
    apply_payload_operators "operator write skipped (inputs missing?)" log).
    The events loop now passes the payload id through the sanctioned
    ``_server_id`` channel (hosted §7 commit parity), so the operator edge
    lands on the node the payload names."""
    import tortoise.extractor_v2 as ev2
    from tortoise.extractor_v2 import _content_id

    ev_content = "the skew-tolerant lease grace period shipped to every region"
    eid = _content_id("ev", ev_content)  # exactly what execute_embed computes
    assert eid.startswith("ev_")
    pid = "pt-2552-ev-op"
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": pid,
                           "content": "clock skew between regions can make "
                                       "lease expiry unsafe",
                           "pointKind": "statement"}],
               "events": [{"id": eid, "content": ev_content,
                           "eventKind": "core:occurrence"}],
               "operators": [{"src": pid, "dst": eid, "op_type": "IMPL",
                              "direction": "unidirectional"}],
               "supersessions": [], "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert not meta["errors"], meta
    assert not [w for w in meta["warnings"] if "event" in w.lower()], meta
    proj = sdk._get_proj()
    # 1) the Event node exists under its content-addressed payload id.
    ev_rows = proj.g.query(
        "MATCH (e:Event {id:$eid}) RETURN e.eventId",
        params={"eid": eid}).result_set
    assert ev_rows and ev_rows[0][0] == eid, ev_rows
    # 2) the IMPL operator node is wired onto that Event endpoint (the
    #    pre-fix behavior dropped this operator: the ev_ node did not exist).
    op_rows = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'})"
        "-[:IMPL]->(e:Event {id:$eid}) RETURN count(o)",
        params={"eid": eid}).result_set
    assert op_rows and op_rows[0][0] == 1, op_rows


def test_extract_session_v2_blank_event_id_falls_back_to_content_addressing(
        sdk, monkeypatch):
    """#2552 P2 (review r1): a payload event with a blank/missing id must
    still be created under its content-derived ``ev_<sha>`` id — never a
    fresh ULID — or every operator referencing the content-addressed
    ``ev_<sha>`` endpoint drops again at commit (ULID would silently reopen
    the exact hole the content-addressed fix closed)."""
    import tortoise.extractor_v2 as ev2
    from tortoise.extractor_v2 import _content_id

    ev_content = "the fallback lease event carried no payload id at all"
    eid = _content_id("ev", ev_content)
    pid = "pt-2552-ev-blank"
    payload = {"session_id": "sess_p2", "story_arc": "", "entities": [],
               "points": [{"id": pid, "content": "lease expiry needs the "
                            "grace period", "pointKind": "statement"}],
               # id is BLANK — the seam must content-address it itself.
               "events": [{"id": "", "content": ev_content,
                           "eventKind": "core:occurrence"}],
               "operators": [{"src": pid, "dst": eid,
                              "op_type": "IMPL",
                              "direction": "unidirectional"}],
               "supersessions": [], "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p2", "2026-08-20T00:00:00+00:00")
    assert not meta["errors"], meta
    proj = sdk._get_proj()
    # the Event exists under the content-derived id (not a fresh ULID), and
    # the operator referencing it is wired — not dropped.
    ev_rows = proj.g.query(
        "MATCH (e:Event {id:$eid}) RETURN e.eventId",
        params={"eid": eid}).result_set
    assert ev_rows and ev_rows[0][0] == eid, ev_rows
    op_rows = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'})"
        "-[:IMPL]->(e:Event {id:$eid}) RETURN count(o)",
        params={"eid": eid}).result_set
    assert op_rows and op_rows[0][0] == 1, op_rows

# ── #2164 Task 3: capture applies payload supersessions (shared
#    apply_supersessions helper — entity-level records fold Object.status
#    via ObjectSuperseded + the projection fold; pt_ records CORRECTS) ────


def test_extract_session_v2_folds_entity_supersession(sdk, monkeypatch):
    """#2164 (Task 3): a payload supersession record (entity ref) is APPLIED
    by capture — the superseded Object's status flips to 'superseded' with
    supersededBy set, via the ObjectSuperseded event + projection fold.
    Pre-fix capture IGNORED payload supersessions (Object A stayed 'live')."""
    import tortoise.extractor_v2 as ev2
    sdk.create_entity("object", "approach-A", objectKind="core:strategy")
    payload = {"session_id": "sess_p1", "story_arc": "",
               "entities": [{"name": "approach-B", "kind": "core:strategy"}],
               "points": [], "operators": [], "events": [],
               "supersessions": [{"superseded": "approach-A",
                                   "supersedes_by": "approach-B",
                                   "evidence": "entity lifecycle supersedes"}],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    rows = sdk._get_proj().g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
        params={"n": "approach-A"}).result_set
    assert rows and rows[0][0] == "superseded", rows
    assert rows[0][1] == "approach-B", rows
    assert meta["mode"] == "v2"
    assert meta["errors"] == [], meta


def test_extract_session_v2_supersession_meta_warnings(sdk, monkeypatch):
    """#2164 (Task 3): supersession records that cannot apply are additive
    meta WARNINGS — never errors and never a capture failure. An unresolved
    entity ref (no Object matches) and a dangling successor (supersedes_by
    absent from the payload entities and the graph) both surface by ref."""
    import tortoise.extractor_v2 as ev2

    # (a) unresolved superseded ref — no Object matches "ghost-A"
    payload = {"session_id": "sess_p1", "story_arc": "",
               "entities": [{"name": "approach-B", "kind": "core:strategy"}],
               "points": [], "operators": [], "events": [],
               "supersessions": [{"superseded": "ghost-A",
                                   "supersedes_by": "approach-B",
                                   "evidence": "lifecycle"}],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["errors"] == [], "unresolved ref is a warning, never an error"
    assert any("ghost-A" in w for w in meta["warnings"]), meta["warnings"]

    # (b) dangling successor — "ghost-B" is in neither the payload entities
    #     nor the graph. approach-A RESOLVES, but folding it would reference
    #     an invisible successor (recall_state excludes superseded Objects),
    #     so the record is skipped and the Object stays live.
    sdk.create_entity("object", "approach-A", objectKind="core:strategy")
    payload2 = {"session_id": "sess_p2", "story_arc": "",
                "entities": [], "points": [], "operators": [], "events": [],
                "supersessions": [{"superseded": "approach-A",
                                    "supersedes_by": "ghost-B",
                                    "evidence": "lifecycle"}],
                "client_commit_id": "ccid2"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload2))
    _extracted, meta2 = sdk._extract_session_v2(
        CONV, "sess_p2", "2026-08-20T00:00:00+00:00")
    assert meta2["errors"] == [], "dangling successor is a warning, never an error"
    assert any("ghost-B" in w for w in meta2["warnings"]), meta2["warnings"]
    rows = sdk._get_proj().g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status",
        params={"n": "approach-A"}).result_set
    assert rows and rows[0][0] == "live", \
        (f"fold must never fire for a dangling successor: {rows!r}")


def test_extract_session_v2_over_cap_supersession_batch_warns_bounded(
        sdk, monkeypatch):
    """#5654: CAPTURE applies the raw extractor batch with NO Layer-1 gate
    (#2243 proposes a Layer-1 cap on the commit API's ``supersessions`` list —
    PR #5648, still open at this base — which would not cover the capture
    path anyway), so an over-cap batch reaches
    ``commit_ops.apply_supersessions``, whose fail-open loop emitted ONE warn
    PER RECORD. The per-record emission is
    now bounded (the first N, then ONE summary carrying the total) — an
    emission bound only: the batch below places records that MUST still be
    folded AFTER the warn budget is exhausted, so the loop is proven not to
    have become a write bound."""
    import tortoise.extractor_v2 as ev2

    # Hard-coded, deliberately NOT imported from commit_ops: an expected
    # value taken from the thing under test asserts nothing (#5654 brief).
    BOUND = 20
    OVER_CAP = 25

    # 25 records whose successor resolves nowhere — each warns exactly once
    # (the entity lane's dangling-successor skip) and is skipped fail-open.
    malformed = [
        {"superseded": f"ghost-ref-{i}",
         "supersedes_by": f"ghost-successor-{i}",
         "evidence": "malformed batch record"}
        for i in range(OVER_CAP)
    ]
    # 3 records that MUST still fold. The fold-order pre-pass keeps payload
    # order here (no chain edges among them), so these sort AFTER the
    # malformed block — they are only reached once the warn budget is spent.
    applied_pairs = []
    for k in range(3):
        target, successor = f"bulk-target-{k}", f"bulk-successor-{k}"
        sdk.create_entity("object", target, objectKind="core:strategy")
        sdk.create_entity("object", successor, objectKind="core:strategy")
        applied_pairs.append((target, successor))

    supersessions = malformed + [
        {"superseded": t, "supersedes_by": s, "evidence": "must still fold"}
        for t, s in applied_pairs
    ]
    payload = {"session_id": "sess_5654", "story_arc": "",
               "entities": [], "points": [], "operators": [], "events": [],
               "supersessions": supersessions,
               "client_commit_id": "ccid5654"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_5654", "2026-08-20T00:00:00+00:00")

    assert meta["errors"] == [], meta
    # (1) BOUNDED emission: one warn per malformed record would be 25; the
    # bound emits BOUND of them and adds exactly ONE summary warn.
    sup_warns = [w for w in meta["warnings"] if "supersession" in w]
    assert len(sup_warns) == BOUND + 1, (
        f"expected {BOUND} per-record warns + 1 summary, got "
        f"{len(sup_warns)}: {sup_warns!r}")
    assert len(sup_warns) < OVER_CAP, (
        "emission must stay below one-warn-per-record")
    # (2) the summary carries the batch size and the true warning total, so
    # the amplification is still observable even when it is not emitted.
    summary = sup_warns[-1]
    assert "supersession batch of" in summary, summary
    assert f"{len(supersessions)} record(s)" in summary, summary
    assert f"{OVER_CAP} per-record warning(s) raised" in summary, summary

    # (3) NO DATA LOSS: every foldable record still landed. These three are
    # the last records in fold order, i.e. processed after suppression — a
    # write bound would have dropped them.
    proj = sdk._get_proj()
    for target, successor in applied_pairs:
        rows = proj.g.query(
            "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
            params={"n": target}).result_set
        assert rows and rows[0][0] == "superseded", (target, rows)
        assert rows[0][1] == successor, (target, rows)


# ── #2164 Task 4: pt_ capture routing + terminal guard + unresolved-ref
#    meta warnings (indicators 3 + 4) — end-to-end through
#    _extract_session_v2 (each test fails if the helper's pt_ branch were
#    removed; the records would never reach supersede()/CORRECTS) ────────

def _capture_pt_id(content: str) -> str:
    """Content-addressed capture point id (extractor_v2._content_id parity:
    pt_<sha256[:62]> — E5 #1537 emits supersession refs in this format)."""
    from tortoise.ids import content_hash
    return f"pt_{content_hash(content)[:62]}"


def test_extract_session_v2_routes_pt_supersession_to_supersede(sdk, monkeypatch):
    """#2164 (Task 4): a pt_ supersession record on the capture payload
    routes to the shared helper's CORRECTS branch — (new)-[:CORRECTS]->(old)
    with the old point terminal (status='superseded' + outdated=True), meta
    mode 'v2', no errors. The successor point rides payload.points (written
    by the points loop) exactly as extractor_v2 emits it (E5 #1537); the
    OLD point is a prior session's live statement point."""
    import tortoise.extractor_v2 as ev2

    old = _capture_pt_id("the gym moved from 6pm to 5pm")
    new = _capture_pt_id("the gym session is now at 5pm")
    sdk.create_point("statement", "the gym moved from 6pm to 5pm",
                     id=old, status="live")
    payload = {"session_id": "sess_p1", "story_arc": "",
               "entities": [], "events": [], "operators": [],
               "points": [{"id": new, "content": "the gym session is now at 5pm",
                            "pointKind": "statement"}],
               "supersessions": [{"superseded": old, "supersedes_by": new,
                                   "evidence": "fact-value contradiction"}],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
        "RETURN o.status, o.outdated",
        params={"new": new, "old": old}).result_set
    assert rows, "CORRECTS edge missing — pt_ record never reached supersede()"
    status, outdated = rows[0]
    assert status == "superseded", rows
    assert outdated is True, rows
    assert meta["mode"] == "v2"
    assert meta["errors"] == [], meta


def test_extract_session_v2_terminal_old_is_silent_noop(sdk, monkeypatch):
    """#2164 (Task 4): re-ingesting a pt_ supersession record whose OLD
    point is ALREADY terminal is an idempotent silent no-op — no raise, no
    NEW meta warning naming the ref (the helper's terminal probe pre-empts
    supersede_point's raise — sdk.supersede_point rejects terminal olds),
    and no duplicate CORRECTS edge."""
    import tortoise.extractor_v2 as ev2

    old = _capture_pt_id("the gym moved from 6pm to 5pm")
    new = _capture_pt_id("the gym session is now at 5pm")
    sdk.create_point("statement", "the gym moved from 6pm to 5pm",
                     id=old, status="live")
    sdk.create_point("statement", "the gym session is now at 5pm",
                     id=new, status="draft")
    sdk.supersede(old, new)  # first application — old now terminal
    payload = {"session_id": "sess_p1", "story_arc": "",
               "entities": [], "events": [], "operators": [],
               "points": [],
               "supersessions": [{"superseded": old, "supersedes_by": new,
                                   "evidence": "fact-value contradiction"}],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["errors"] == [], meta
    assert not any(old in w for w in meta["warnings"]), meta["warnings"]
    n = sdk._get_proj().g.query(
        "MATCH (n:Point)-[:CORRECTS]->(o:Point {id:$old}) RETURN count(n)",
        params={"old": old}).result_set[0][0]
    assert n == 1, f"re-ingest must be idempotent: {n} CORRECTS edges"


def test_extract_session_v2_draft_old_point_supersedeable(sdk, monkeypatch):
    """#2164 (Task 4): a capture-DRAFT old point (the points loop writes
    status='draft') CAN be superseded by a later session's E5 record — the
    supersede guard rejects only retracted/superseded/archived, so draft is
    not terminal. Pins that a session-N draft is correctable by session-N+1."""
    import tortoise.extractor_v2 as ev2

    old = _capture_pt_id("deploy target is serve --http")
    new = _capture_pt_id("deploy target is serve --https")
    sdk.create_point("statement", "deploy target is serve --http",
                     id=old, status="draft")
    payload = {"session_id": "sess_p1", "story_arc": "",
               "entities": [], "events": [], "operators": [],
               "points": [{"id": new, "content": "deploy target is serve --https",
                            "pointKind": "statement"}],
               "supersessions": [{"superseded": old, "supersedes_by": new,
                                   "evidence": "fact-value contradiction"}],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
        "RETURN o.status",
        params={"new": new, "old": old}).result_set
    assert rows, ("draft old point must be supersedeable — CORRECTS edge "
                  "missing")
    assert rows[0][0] == "superseded", rows
    assert meta["errors"] == [], meta


def test_extract_session_v2_pt_ref_meta_warnings(sdk, monkeypatch):
    """#2164 (Task 4, indicator 4): the pt_ branch's fail-open skips surface
    as meta WARNINGS with the specific cause — never an error, never a
    silent drop. (a) unresolved pt_ ref (old point absent → 'not found');
    (b) dangling successor (new point absent → supersede()'s No point guard
    is caught + warned, and the old point is NOT terminalized — a CORRECTS
    edge to a nonexistent point would be dangling)."""
    import tortoise.extractor_v2 as ev2

    # (a) unresolved OLD ref — the successor IS written by the points loop
    ghost_old = _capture_pt_id("ghost old point never captured")
    new = _capture_pt_id("the successor that does exist")
    payload = {"session_id": "sess_p1", "story_arc": "",
               "entities": [], "events": [], "operators": [],
               "points": [{"id": new, "content": "the successor that does exist",
                            "pointKind": "statement"}],
               "supersessions": [{"superseded": ghost_old,
                                   "supersedes_by": new,
                                   "evidence": "lifecycle"}],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["errors"] == [], \
        "unresolved pt_ ref is a warning, never an error"
    assert any(ghost_old in w and "not found" in w
               for w in meta["warnings"]), meta["warnings"]
    n = sdk._get_proj().g.query(
        "MATCH (:Point)-[:CORRECTS]->() RETURN count(*)").result_set[0][0]
    assert n == 0, f"no CORRECTS may fire for an unresolved ref: {n}"

    # (b) dangling successor — the OLD point exists + is live, the new does
    #     not. The helper probes both endpoints but supersede()'s own
    #     missing-new guard raises BEFORE any mutation — the raise is caught
    #     and warned; the old point must stay live (never terminalized onto
    #     a phantom).
    old = _capture_pt_id("the point that gets corrected")
    ghost_new = _capture_pt_id("ghost successor never written")
    sdk.create_point("statement", "the point that gets corrected",
                     id=old, status="live")
    payload2 = {"session_id": "sess_p2", "story_arc": "",
                "entities": [], "events": [], "operators": [],
                "points": [],
                "supersessions": [{"superseded": old,
                                    "supersedes_by": ghost_new,
                                    "evidence": "lifecycle"}],
                "client_commit_id": "ccid2"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload2))
    _extracted, meta2 = sdk._extract_session_v2(
        CONV, "sess_p2", "2026-08-20T00:00:00+00:00")
    assert meta2["errors"] == [], \
        "dangling pt_ successor is a warning, never an error"
    assert any(ghost_new in w and "failed" in w
               for w in meta2["warnings"]), meta2["warnings"]
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$old}) RETURN o.status",
        params={"old": old}).result_set
    assert rows and rows[0][0] == "live", \
        ("old point must stay live — a CORRECTS edge to a nonexistent "
         f"successor would be dangling: {rows!r}")


def test_apply_supersessions_legacy_idless_object_is_refused_not_folded(sdk):
    """#3590: an id-less Object is UNADDRESSABLE — the supersession is
    REFUSED (recorded + warned), never folded and never journaled.

    #2164 review (P2-1) once made this path fold: it SYNTHESIZED the canonical
    name-derived id (``obj-<sha26(name)>``) so the journal carried "a real,
    auditable id" for a raw-Cypher-created node. #3590 deleted that derivation
    (``docs/architecture/identity-decision.md`` — identity is the MINTED id,
    never a function of the name), so there is no id to address or journal and
    ``commit_ops.apply_supersessions`` refuses the record instead of inventing
    one. This pin replaces the pre-#3590 pin
    ``test_apply_supersessions_legacy_idless_object_journaled_and_folded``,
    which asserted the synthesized-id fold and was left stale by that change
    (it went RED on CI jobs `test-slow (e)` / `ci-introduced-failure` at
    ``9e18023da01f``).

    ⛔ Why the refusal, and not a journal-less fold: #3590's recorded scope —
    *"No migration / no backfill … Nothing to preserve — start fresh"* —
    licenses dropping the pre-#3590 record, but a fold that cannot be
    JOURNALED is not representable at all: writing ``status``/``supersededBy``
    with no ``ObjectSuperseded`` line would put live state on the graph that a
    wipe+rebuild cannot restore — the M2 provenance gap that opened #2164 in
    the first place. So the refusal is the coherent choice, and what this pin
    forbids is the SILENT version of it.
    """
    from tortoise.commit_ops import apply_supersessions
    from tortoise.entity_identity import clear_non_folded_entries, non_folded_entries

    # process-level record: clear it so the assertion below cannot pass on a
    # neighbouring test's entry (non-vacuity).
    clear_non_folded_entries()
    proj = sdk._get_proj()
    # successor must be visible (the payload-entities equivalent)
    sdk.create_entity("object", "successor-B", objectKind="core:strategy")
    # raw legacy Object — NO id property at all
    proj.g.query("CREATE (o:Object {name:'legacy-X', status:'live'})")
    # premise asserted BEFORE the conclusion: the fixture really is id-less,
    # so a refusal-free pass cannot be mistaken for a fold.
    rows = proj.g.query(
        "MATCH (o:Object {name:'legacy-X'}) RETURN o.id, o.status",
    ).result_set
    assert rows and rows[0][0] is None, f"fixture must be id-less: {rows!r}"

    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "legacy-X", "supersedes_by": "successor-B",
          "evidence": "legacy lifecycle"}],
        session_id="sess_legacy", warn=warns.append)
    assert applied == 0, \
        "an id-less target has no id to address — the record is refused"
    assert _entity_fold_state(proj, "legacy-X") == ("live", None), \
        ("the target must be left untouched: a fold with no journal line is "
         "not restorable by a rebuild")
    assert _object_superseded_events(proj) == 0, \
        "nothing may be journaled — least of all under a synthesized id"
    assert len(warns) == 1 and "id-less" in warns[0] \
        and "#3590" in warns[0], warns
    # The RECORD is the non-folded entry (a refusal is an assertion, not a log
    # line) — shape and counted carrier both named.
    entries = [e for e in non_folded_entries() if e.name == "legacy-X"]
    assert [(e.label, e.shape, e.candidate_ids) for e in entries] == [
        ("Object", "unaddressable-name", ("<id-less>",))], entries


def test_apply_supersessions_legacy_idless_object_writes_no_jsonl_line(tmp_path):
    """#3590 (JSONL half of #2164's M2 gap): the id-less refusal writes NO
    journal line — and the log is proven LIVE in the same test.

    #2164 review (P2-1, JSONL half) pinned the synthesized-id emission into the
    JSONL rebuild log (#548). With the synthesis gone there is no id to emit,
    so the contract inverts: an id-less target leaves NO ``ObjectSuperseded``
    line. A bare "the log contains none" assertion is vacuous — an SDK built
    without ``event_log_path``, a closed log, or a broken emit would satisfy it
    — so the same log is bracket-proven by a CANONICAL-id supersession:
    nothing for the id-less target, then exactly one line for the canonical
    one, with its own id/name/supersedes_by.

    This pin replaces ``test_apply_supersessions_legacy_idless_object_reaches_jsonl``
    (the pre-#3590 JSONL exerciser named in ``docs/event-catalog.md``), which
    asserted the synthesized id and was left stale by #3590.
    """
    from _node_ids import node_id

    from tortoise.commit_ops import apply_supersessions

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "tlegacy.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", "successor-C", objectKind="core:strategy")
        # raw legacy Object — NO id property at all
        proj.g.query("CREATE (o:Object {name:'legacy-Y', status:'live'})")

        def _superseded_lines():
            log = (events / "events.jsonl").read_text(
                encoding="utf-8").splitlines()
            return [json.loads(ln) for ln in log
                    if '"type": "ObjectSuperseded"' in ln]

        applied = apply_supersessions(
            proj, sdk,
            [{"superseded": "legacy-Y", "supersedes_by": "successor-C",
              "evidence": "legacy lifecycle"}],
            session_id="sess_legacy2")
        assert applied == 0, "an id-less target is refused, not folded"
        assert _superseded_lines() == [], (
            "an id-less target has no id to journal — no line may be written")

        # ── positive control: the SAME log, a canonical-id target ──────────
        sdk.create_entity("object", "canonical-Z", objectKind="core:strategy")
        applied2 = apply_supersessions(
            proj, sdk,
            [{"superseded": "canonical-Z", "supersedes_by": "successor-C",
              "evidence": "canonical lifecycle"}],
            session_id="sess_canon")
        assert applied2 == 1, "a canonical-id target must still fold"
        lines = _superseded_lines()
        assert len(lines) == 1, f"exactly one line, for the canonical target: {lines}"
        assert lines[0]["id"] == node_id(proj, "Object", "canonical-Z"), lines
        assert lines[0]["name"] == "canonical-Z", lines
        assert lines[0]["supersedes_by"] == "successor-C", lines
    finally:
        sdk.close()


def test_apply_supersessions_prefers_id_match_over_same_string_name(sdk):
    """#2164 review (P2-2): when a supersession ref matches an Object BY ID
    and ALSO a DIFFERENT Object BY NAME (a legacy no-id Object whose name
    happens to equal another Object's id), the selection must deterministi-
    cally prefer the id-match row. Pre-fix rows[0] was backend-order-depen-
    dent — the OR probe returned both rows and FalkorDB's unordered result
    picked either, so the fold target (and the status flip) was a coin flip."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "successor-Z", objectKind="core:strategy")
    # canonical Object B — its id is the ref
    sdk.create_entity("object", "approach-B", objectKind="core:strategy")
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "approach-B"}).result_set
    oid = rows[0][0]
    # legacy no-id Object whose NAME equals B's id → both probe clauses hit
    proj.g.query(
        "CREATE (o:Object {name:$n, status:'live'})", params={"n": oid})
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": oid, "supersedes_by": "successor-Z",
          "evidence": "id-vs-name ambiguity"}],
        session_id="sess_p22")
    assert applied == 1, "the id-match row must win deterministically"
    # B (matched by id) is the fold target — superseded
    b = proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status, o.supersededBy",
        params={"id": oid}).result_set
    assert b and b[0][0] == "superseded", f"id-match Object not folded: {b!r}"
    assert b[0][1] == "successor-Z", b
    # the same-string NAME match must NOT be the one folded
    n = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status",
        params={"n": oid}).result_set
    assert n and n[0][0] == "live", \
        f"name-match Object wrongly folded instead of the id match: {n!r}"


# ── #2164 Task 7: indicator 5 (helper-routed) — the shared helper's OWN
#    terminal rules for OUT-OF-BAND records (journal replay, hosted §6b
#    reconcile re-runs, direct calls). Real capture can never reach these
#    branches: S3's terminal exclusion kills record formation at the SOURCE.
#    ⚠️ The pin that said so (`test_chain_pin_and_no_double_fold` in
#    `tests/test_capture_session_supersession_e2e.py`) was deleted WHOLE by
#    #4282 — the file drove `TortoiseSDK.capture_session`, one of the 26 dead
#    public methods — so "re-capture is byte-unchanged" is now an unpinned
#    claim, not a fact this tree can check. The tests below pin the helper's
#    defense-in-depth for records that arrive WITHOUT a capture: same
#    successor → silent dedup; divergent successor → warn + keep-first (never
#    blind-overwrite). ──────

def _entity_fold_state(proj, name: str) -> tuple | None:
    """(status, supersededBy) of one Object — None when absent."""
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
        params={"n": name}).result_set
    return tuple(rows[0]) if rows else None


def _object_superseded_events(proj) -> int:
    """ObjectSuperseded events journaled to the :GraphEvent store."""
    rows = proj.g.query(
        "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) RETURN count(e)",
    ).result_set
    return int(rows[0][0])


def test_apply_supersessions_same_payload_idempotent(sdk):
    """#2164 (Task 7, indicator 5 — helper-routed idempotency): calling the
    shared helper with the SAME supersession record twice — records arriving
    OUT-OF-BAND (journal replay, hosted §6b reconcile re-runs, direct calls)
    — is idempotent at the helper's OWN layer. First call folds Object A
    (status='superseded', supersededBy='successor-B', applied=1, ONE
    ObjectSuperseded event); the SECOND call is a SILENT no-op (applied=0 —
    the same-successor terminal branch: A's supersededBy already equals the
    record's successor): no second fold, no second journal line, NO new
    warning. This branch cannot fire through real capture (S3 excludes
    terminal Objects, so record formation dies at the source). The source-side
    pin for that (`test_chain_pin_and_no_double_fold`) went with
    `tests/test_capture_session_supersession_e2e.py` when #4282 deleted the SDK
    capture door; this test pins the helper's own defense-in-depth for the
    out-of-band path, which is the half that survives.

    Absorbents (the full dedup stack, capture→commit): (1) reconcile-merge
    delta-0 — a re-reconcile whose payload has no diff produces no
    supersession records; (2) the S3 terminal probe — a folded Object stops
    resolving, so record formation dies at the source; (3) the helper's
    terminal rules — pt_ records hit the terminal probe, entity records hit
    this same-successor silent no-op; (4) the live-path CAS fold
    (_fold_object_superseded: a terminal re-apply returns (0, 1) — the
    first-fold stamps are never re-SET; the LOSING concurrent line stays
    journaled and warns "lost a concurrent race"); (5) benign duplicate
    journals — a replay re-emits the same event and the projection fold
    converges. Hosted capture writes NO CommitRecord, so there is no
    client_commit_id dedup between capture and commit (the commit-id key is
    a commit-endpoint absorbent only); dedup between capture and re-capture
    rides absorbents (1)-(5), never a commit-id key."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "approach-A", objectKind="core:strategy")
    sdk.create_entity("object", "successor-B", objectKind="core:strategy")
    record = [{"superseded": "approach-A", "supersedes_by": "successor-B",
               "evidence": "entity lifecycle supersedes"}]
    warns: list[str] = []

    applied = apply_supersessions(proj, sdk, record, session_id="sess_idem",
                                  warn=warns.append)
    assert applied == 1, "first application must fold"
    assert warns == [], f"fold is clean — no warnings: {warns}"
    state = _entity_fold_state(proj, "approach-A")
    assert state == ("superseded", "successor-B"), state
    assert _object_superseded_events(proj) == 1, "exactly one journal line"

    # Re-apply the SAME record — same successor already folded → silent dedup
    applied2 = apply_supersessions(proj, sdk, record, session_id="sess_idem",
                                   warn=warns.append)
    assert applied2 == 0, \
        "same-successor re-apply must be a silent dedup no-op, not a re-fold"
    assert warns == [], f"dedup must be silent — got a warning: {warns}"
    assert _entity_fold_state(proj, "approach-A") == ("superseded",
                                                       "successor-B")
    assert _object_superseded_events(proj) == 1, \
        "no second ObjectSuperseded journal for a re-applied record"


def test_apply_supersessions_divergent_successor_keeps_first(sdk):
    """#2164 (Task 7, helper-routed conflict): a supersession record whose
    successor DIVERGES from an already-folded one (Object A already
    superseded by B; a later out-of-band record claims A→C) is REJECTED
    keep-first — A stays supersededBy='successor-B', the C claim is never
    folded in (applied=0, no second journal, successor-C stays live), and
    the rejection is a LOUD warning naming the ref, the kept successor, and
    the rejected one. hosted §6b migrated onto the helper in #2193 — this
    is now the ONE discipline; the helper-routed keep-first is the one
    consumer discipline
    that never blind-overwrites; a capture CAN trip it — the extractor's
    S3 search_graph calls tortoise_fts_query(entity_type='object',
    include_terminal=True), which keeps terminal Objects visible to that
    PRIOR/resolution leg (#3301 widened the default exclusion on the four
    search legs; this leg opts back in), so overlapping capture re-derives a
    supersession against a target session 1 already folded — this
    keep-first branch is the idempotency mechanism for that path."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "approach-A", objectKind="core:strategy")
    sdk.create_entity("object", "successor-B", objectKind="core:strategy")
    sdk.create_entity("object", "successor-C", objectKind="core:strategy")
    first = [{"superseded": "approach-A", "supersedes_by": "successor-B",
              "evidence": "lifecycle"}]
    conflict = [{"superseded": "approach-A", "supersedes_by": "successor-C",
                 "evidence": "lifecycle"}]
    warns: list[str] = []

    applied = apply_supersessions(proj, sdk, first, session_id="sess_keep1",
                                  warn=warns.append)
    assert applied == 1, "first fold must apply"
    assert warns == [], f"first fold is clean: {warns}"

    # Divergent successor → keep-first: A stays supersededBy B
    applied2 = apply_supersessions(proj, sdk, conflict,
                                   session_id="sess_keep2",
                                   warn=warns.append)
    assert applied2 == 0, \
        "divergent successor must be rejected, never blind-folded"
    state = _entity_fold_state(proj, "approach-A")
    assert state == ("superseded", "successor-B"), \
        f"keep-first violated — A's fold was overwritten: {state!r}"
    # the warning names the conflict: the ref, the KEPT successor, the
    # rejected one, and the keep-first rule (assert substrings, per the
    # actual warn text in commit_ops.apply_supersessions)
    joined = " | ".join(warns)
    assert "approach-A" in joined, f"warning must name the ref: {joined}"
    assert "successor-B" in joined, \
        f"warning must name the kept successor: {joined}"
    assert "successor-C" in joined, \
        f"warning must name the rejected successor: {joined}"
    assert "keep-first" in joined, f"warning must cite keep-first: {joined}"
    # nothing folded for C: no second journal, successor-C stays live
    assert _object_superseded_events(proj) == 1, \
        "rejected conflict must not journal a second event"
    c_state = _entity_fold_state(proj, "successor-C")
    assert c_state is not None and (c_state[0] or "live") == "live", c_state
    assert c_state[1] is None, f"successor-C must never be folded: {c_state}"


def test_apply_supersessions_long_successor_dedup_and_legacy_prefix(sdk):
    """#5370 (indicators 3+4): the fold stores the FULL successor name, and
    the dedup/keep-first compare accepts EITHER the full name (rows folded
    after the fix) or its 200-char prefix (rows folded before it).

      * a same-successor re-ingest on the NEW (full) stored form is a SILENT
        dedup — no spurious divergence;
      * a LEGACY row (the old 200-char prefix) re-ingested with the full name
        is still an idempotent dedup (applied=0, stored value kept) — never a
        keep-first "conflict" — with the loud unverified-identity warning the
        #2164 round-2 review added;
      * a genuinely DIVERGENT successor is still keep-first (applied=0, stored
        value untouched, loud conflict warning).
    """
    from tortoise.commit_ops import apply_supersessions

    long_name = "gh-issue-title-" + ("y" * 240)
    other_name = "other-title-" + ("z" * 240)
    assert len(long_name) > 200 and len(other_name) > 200
    proj = sdk._get_proj()
    sdk.create_entity("object", "long-target")
    sdk.create_entity("object", long_name)
    sdk.create_entity("object", other_name)
    record = [{"superseded": "long-target", "supersedes_by": long_name,
               "evidence": "#5370"}]
    warns: list[str] = []

    applied = apply_supersessions(proj, sdk, record, session_id="s5370_a",
                                  warn=warns.append)
    assert applied == 1 and warns == [], (applied, warns)
    assert _entity_fold_state(proj, "long-target") == (
        "superseded", long_name), _entity_fold_state(proj, "long-target")

    # (1) NEW full form: same-successor re-ingest → SILENT dedup.
    applied2 = apply_supersessions(proj, sdk, record, session_id="s5370_b",
                                   warn=warns.append)
    assert applied2 == 0, "same successor must dedup, not re-fold"
    assert warns == [], f"full-form dedup must be silent: {warns}"
    assert _object_superseded_events(proj) == 1

    # (2) LEGACY row (pre-#5370 200-char prefix): a full-name re-ingest still
    #     dedups — never a keep-first conflict — and says so loudly.
    proj.g.query("MATCH (o:Object {name:'long-target'}) "
                 "SET o.supersededBy=$p", params={"p": long_name[:200]})
    applied3 = apply_supersessions(proj, sdk, record, session_id="s5370_c",
                                   warn=warns.append)
    assert applied3 == 0, "legacy prefix must dedup, not re-fold"
    assert len(warns) == 1, warns
    # Round 3: the warning deliberately does NOT claim the stored value is a
    # "legacy fold" — the same arithmetic is reached by a POST-#5370 row whose
    # successor name is exactly 200 chars (a genuine full name). Pin the
    # neutral wording, not a legacy claim.
    assert "identity beyond that prefix is not verified" in warns[0], warns
    assert "legacy" not in warns[0], \
        f"the warning must not blame a legacy fold: {warns}"
    assert "conflict with" not in warns[0], \
        f"legacy dedup must not read as a divergence: {warns}"
    assert _entity_fold_state(proj, "long-target") == (
        "superseded", long_name[:200])
    assert _object_superseded_events(proj) == 1

    # (3) genuinely DIVERGENT successor → keep-first + loud conflict warning.
    divergent = [{"superseded": "long-target", "supersedes_by": other_name,
                  "evidence": "#5370"}]
    applied4 = apply_supersessions(proj, sdk, divergent, session_id="s5370_d",
                                   warn=warns.append)
    assert applied4 == 0, "divergent successor must never blind-overwrite"
    assert _entity_fold_state(proj, "long-target") == (
        "superseded", long_name[:200])
    assert "keep-first" in warns[-1], warns
    assert _object_superseded_events(proj) == 1


def test_apply_supersessions_chain_converges_both_orders(sdk):
    """#2249 (O3): a same-payload chain (approach-A → approach-B →
    approach-C) must converge to the IDENTICAL end state whether emitted in
    fold order [A→B, B→C] or reverse order [B→C, A→B]. End state is
    PAYLOAD-LITERAL: A.supersededBy='approach-B' (NOT 'approach-C' — each
    event folds its own target; rebuild replays per-event), B.supersededBy=
    'approach-C', C live. Pre-fix the reverse arm gate-skipped A→B (its
    successor B was terminalized by B→C earlier in the payload) leaving A
    LIVE with its A→B fold unjournaled — the order-sensitive divergence.
    Both arms run on fresh objects (per-arm prefixes) and must produce
    byte-identical fold states + applied counts + journal sets."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, records: list[dict]) -> list[str]:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_chain_" + prefix,
                                      warn=warns.append)
        assert applied == 2, \
            f"[{prefix}] both folds must land: {warns}"
        assert warns == [], \
            f"[{prefix}] no skip warnings on a converging chain: {warns}"
        return warns

    fold_records = [
        {"superseded": "fold-A", "supersedes_by": "fold-B",
         "evidence": "b replaces a"},
        {"superseded": "fold-B", "supersedes_by": "fold-C",
         "evidence": "c replaces b"},
    ]
    reverse_records = [
        {"superseded": "rev-B", "supersedes_by": "rev-C",
         "evidence": "c replaces b"},
        {"superseded": "rev-A", "supersedes_by": "rev-B",
         "evidence": "b replaces a"},
    ]
    _run("fold", fold_records)
    _run("rev", reverse_records)
    for prefix in ("fold", "rev"):
        a = _entity_fold_state(proj, prefix + "-A")
        b = _entity_fold_state(proj, prefix + "-B")
        c = _entity_fold_state(proj, prefix + "-C")
        assert a == ("superseded", prefix + "-B"), \
            f"[{prefix}] payload-literal A.supersededBy=B: {a}"
        assert b == ("superseded", prefix + "-C"), \
            f"[{prefix}] B.supersededBy=C: {b}"
        assert c is not None and (c[0] or "live") == "live" \
            and c[1] is None, f"[{prefix}] C must stay live: {c}"
    # journal parity: both arms journaled exactly one ObjectSuperseded per
    # fold (fold-A/rev-A then fold-B/rev-B — no skip line for either).
    assert _object_superseded_events(proj) == 4, \
        "2 folds × 2 arms — one journal line per fold"
    # journal seq order follows FOLD order in the reverse arm (A→B emitted
    # BEFORE B→C) — fold-order emission, not payload order.
    rows = proj.g.query(
        "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) "
        "RETURN e.payload ORDER BY e.seq",
    ).result_set
    names = [json.loads(r[0])["name"] for r in rows]
    assert names == ["fold-A", "fold-B", "rev-A", "rev-B"], names


def test_apply_supersessions_chain_three_link_reverse(sdk):
    """#2249: three-link reverse emission [C→D, B→C, A→B] must land all
    three folds (applied=3), payload-literal: A.sb=B, B.sb=C, C.sb=D, D
    live. PRE-FIX trace (payload order): C→D folds C (C.sb=D); B→C's
    successor C is now terminal → visible-successor gate skips (B stays
    live); A→B folds onto the still-live B (A.sb=B) → applied=2 with A
    folded, B live, C.sb=D — the chain head isn't the loser here, the
    MIDDLE link B→C is (B never folds). POST-FIX the dependency sort folds
    [A→B, B→C, C→D] and all three land."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    for n in ("t3-A", "t3-B", "t3-C", "t3-D"):
        sdk.create_entity("object", n, objectKind="core:strategy")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "t3-C", "supersedes_by": "t3-D",
          "evidence": "d replaces c"},
         {"superseded": "t3-B", "supersedes_by": "t3-C",
          "evidence": "c replaces b"},
         {"superseded": "t3-A", "supersedes_by": "t3-B",
          "evidence": "b replaces a"}],
        session_id="sess_t3", warn=warns.append)
    assert applied == 3, f"all three folds must land: {warns}"
    assert warns == [], f"no skip warnings: {warns}"
    assert _entity_fold_state(proj, "t3-A") == ("superseded", "t3-B")
    assert _entity_fold_state(proj, "t3-B") == ("superseded", "t3-C")
    assert _entity_fold_state(proj, "t3-C") == ("superseded", "t3-D")
    d = _entity_fold_state(proj, "t3-D")
    assert d is not None and (d[0] or "live") == "live" and d[1] is None, d


def test_apply_supersessions_chain_mixed_id_name_forms(sdk):
    """#2249: chains are naturally MIXED-FORM — the extractor emits the
    superseded side as the graph ID when the object pre-exists
    (extractor_v2.py:5836) while supersedes_by is always the NAME
    (commit_schema.py:776). So the chain records are X.supersedes_by = name
    'mx-B' (id-form-free) and Y.superseded = B's canonical id
    obj-<sha26('mx-B')>. Reverse emission must STILL converge. This test
    forces the fold-order pre-pass to resolve BOTH sides against the graph
    (id-form ref + name-form successor) — raw string comparison would miss
    the edge (name != id string)."""
    from _node_ids import node_id

    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "mx-A", objectKind="core:strategy")
    sdk.create_entity("object", "mx-B", objectKind="core:strategy")
    sdk.create_entity("object", "mx-C", objectKind="core:strategy")
    b_id = node_id(proj, "Object", "mx-B")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": b_id, "supersedes_by": "mx-C",
          "evidence": "c replaces b (id-form ref)"},
         {"superseded": "mx-A", "supersedes_by": "mx-B",
          "evidence": "b replaces a (name-form successor)"}],
        session_id="sess_mx", warn=warns.append)
    assert applied == 2, f"mixed-form chain must converge: {warns}"
    assert warns == [], warns
    assert _entity_fold_state(proj, "mx-A") == ("superseded", "mx-B")
    assert _entity_fold_state(proj, "mx-B") == ("superseded", "mx-C")


def test_apply_supersessions_chain_legacy_noncanonical_mid_node(sdk):
    """#2249: a chain whose middle node carries a NON-canonical id (legacy
    raw-Cypher write with an explicit pre-canonical id — the
    test_status_projection.py:399 registration-id class) must still
    converge. This discriminates PROBE-based fold-order resolution from
    canonical-id MATH (obj-<sha26(name)>): math would fail to see the edge
    (actual id != computed id) and the reverse-order divergence would
    survive. The id-less inverse is pinned separately (legacy id-less
    mid-node record stays skipped — today's semantics)."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "lc-A", objectKind="core:strategy")
    sdk.create_entity("object", "lc-C", objectKind="core:strategy")
    # raw legacy mid-node with an explicit NON-canonical id
    proj.g.query(
        "CREATE (o:Object {id:'github-issue-2249-lc-B', "
        "name:'lc-B', status:'live'})")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "github-issue-2249-lc-B", "supersedes_by": "lc-C",
          "evidence": "c replaces b"},
         {"superseded": "lc-A", "supersedes_by": "lc-B",
          "evidence": "b replaces a"}],
        session_id="sess_lc", warn=warns.append)
    assert applied == 2, \
        f"chain through a non-canonical id must converge: {warns}"
    assert warns == [], warns
    assert _entity_fold_state(proj, "lc-A") == ("superseded", "lc-B")
    assert _entity_fold_state(proj, "lc-B") == ("superseded", "lc-C")
    rows = proj.g.query(
        "MATCH (o:Object {name:'lc-B'}) RETURN o.supersededBy",
    ).result_set
    assert rows and rows[0][0] == "lc-C", rows


def test_apply_supersessions_inpayload_identical_to_guard_h(sdk):
    """#2249 regression pin (the gate IS the discriminator): when B is
    ALREADY terminal BEFORE the payload (folded by an earlier commit —
    guard-(h) semantics) and the payload REDUNDANTLY re-asserts [B→C, A→B],
    A must STAY LIVE (applied=0): A→B sorts first but its fold-time gate
    sees B's PRE-payload terminal status (nothing folded yet) → skip. The
    sort must never turn a pre-payload-terminal successor into a fold."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "gh-A", objectKind="core:strategy")
    sdk.create_entity("object", "gh-B", objectKind="core:strategy")
    sdk.create_entity("object", "gh-C", objectKind="core:strategy")
    # prior commit folds B→C (B terminal pre-payload)
    prior = apply_supersessions(
        proj, sdk,
        [{"superseded": "gh-B", "supersedes_by": "gh-C",
          "evidence": "prior commit"}],
        session_id="sess_gh1")
    assert prior == 1
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "gh-B", "supersedes_by": "gh-C",
          "evidence": "redundant dedup"},
         {"superseded": "gh-A", "supersedes_by": "gh-B",
          "evidence": "records a onto pre-terminal b"}],
        session_id="sess_gh2", warn=warns.append)
    assert applied == 0, \
        f"A must stay live (gate is the discriminator): {warns}"
    a = _entity_fold_state(proj, "gh-A")
    assert a is not None and (a[0] or "live") == "live" and a[1] is None, a
    assert _entity_fold_state(proj, "gh-B") == ("superseded", "gh-C")
    assert _object_superseded_events(proj) == 1, \
        "no second journal — B→C deduped silently, A→B skipped"


def test_apply_supersessions_cycle_deterministic(sdk):
    """#2249 regression pin: a same-payload cycle [A→B, B→A] has no total
    fold order → payload order wins (deterministic): the first-emitted
    claim folds, the second gate-skips (its successor is terminal) with the
    keep-first warn, NO exception, applied=1. Both emission orders
    asserted. Pre-fix behavior, pinned."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, records: list[dict]) -> None:
        for n in ("-A", "-B"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_cyc_" + prefix,
                                      warn=warns.append)
        assert applied == 1, f"[{prefix}] first-emitted claim folds: {warns}"
        assert any("keep-first" in w or "no visible successor" in w
                   for w in warns), f"[{prefix}] second claim warns: {warns}"
    _run("c1", [
        {"superseded": "c1-A", "supersedes_by": "c1-B", "evidence": ""},
        {"superseded": "c1-B", "supersedes_by": "c1-A", "evidence": ""},
    ])
    _run("c2", [
        {"superseded": "c2-B", "supersedes_by": "c2-A", "evidence": ""},
        {"superseded": "c2-A", "supersedes_by": "c2-B", "evidence": ""},
    ])
    assert _entity_fold_state(proj, "c1-A") == ("superseded", "c1-B")
    c1b = _entity_fold_state(proj, "c1-B")
    assert c1b is not None and (c1b[0] or "live") == "live", c1b
    assert _entity_fold_state(proj, "c2-B") == ("superseded", "c2-A")
    c2a = _entity_fold_state(proj, "c2-A")
    assert c2a is not None and (c2a[0] or "live") == "live", c2a


def test_apply_supersessions_same_ref_stability(sdk):
    """#2249 regression pin: same-ref divergent claims in ONE payload
    [A→B, A→C] have no dependency edge (disjoint objects) → STABLE payload
    order keeps first-emitted-wins (A.sb=B); reversed [A→C, A→B] → A.sb=C.
    The sort must not destabilize keep-first within one payload."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, winner: str) -> None:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        records = [{"superseded": prefix + "-A", "supersedes_by": prefix + "-B",
                    "evidence": ""},
                   {"superseded": prefix + "-A", "supersedes_by": prefix + "-C",
                    "evidence": ""}]
        if winner == "C":
            records.reverse()
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_sr_" + prefix,
                                      warn=warns.append)
        assert applied == 1, f"[{prefix}] first-emitted claim folds: {warns}"
        assert _entity_fold_state(proj, prefix + "-A") == (
            "superseded", prefix + "-" + winner), \
            f"[{prefix}] first-emitted claim must win"
    _run("sr1", "B")
    _run("sr2", "C")


def test_apply_supersessions_cycle_chain_entangled_payload_order(sdk):
    """#2249 regression pin (plan-review P2, round 2): a cycle entangled with a
    chain in ONE payload has no total order — the whole block falls back to
    PAYLOAD order (deterministic, emission-literal — pre-fix parity). Two
    permutations, both asserting applied=2 with A.sb=B, B.sb=C, each
    exercising a DIFFERENT skip branch on the cyclic record B→A:
    e1 [A→B, B→A, B→C]: A→B folds A; B→A gate-skips (its successor A is
    recall-excluded — no visible successor warn, B is still live at that
    point); B→C folds B.
    e2 [A→B, B→C, B→A]: A→B folds A; B→C folds B; B→A keep-first-warns
    (its ref B is now terminal with a DIVERGENT stored fold C).
    A future Kahn/leftover change that hoists B→C ahead of A→B (terminalizing
    B first) would silently drop A's fold — these pins lock the fallback.
    (Round-1 e2 emission [B→C, B→A, A→B] was WRONG — payload-order fallback
    folds B→C first and the visible-successor gate keeps A live: that arm
    asserted an unreachable applied=2. Permutations are emission-literal and
    only orders with A→B first can land two folds.)"""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, records: list[dict]) -> None:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_ecc_" + prefix,
                                      warn=warns.append)
        assert applied == 2, \
            f"[{prefix}] A→B and B→C must fold (payload-order fallback): {warns}"
        assert _entity_fold_state(proj, prefix + "-A") == (
            "superseded", prefix + "-B")
        assert _entity_fold_state(proj, prefix + "-B") == (
            "superseded", prefix + "-C")
    _run("e1", [
        {"superseded": "e1-A", "supersedes_by": "e1-B", "evidence": ""},
        {"superseded": "e1-B", "supersedes_by": "e1-A", "evidence": ""},
        {"superseded": "e1-B", "supersedes_by": "e1-C", "evidence": ""},
    ])
    _run("e2", [
        {"superseded": "e2-A", "supersedes_by": "e2-B", "evidence": ""},
        {"superseded": "e2-B", "supersedes_by": "e2-C", "evidence": ""},
        {"superseded": "e2-B", "supersedes_by": "e2-A", "evidence": ""},
    ])


def test_apply_supersessions_cycle_predecessor_hoisted(sdk):
    """#2249 (plan-review round 3 / second-model P2-1): a record whose fold
    FEEDS a cycle (its successor is a cycle member) is hoisted AHEAD of the
    cycle block by the Kahn pass — deterministic, monotonic, garbage-in-only
    (contradictory cyclic input; hoisting can only ADD folds — the hoisted
    record folds onto the cycle member while it is still live, and never
    removes a fold from the cycle block itself). Payload [A→B, B→A, C→A]:
    C→A folds onto the live A first (applied), then the A↔B cycle block
    folds A→B (B→A gate-skips). End state C.sb=A, A.sb=B, B live, applied=2.
    PRE-FIX (payload order): A→B folds A → B→A and C→A both gate-skip
    (successor A terminal) → applied=1, C stays live. This arm is RED
    pre-fix (discriminates the hoist) — it is NOT a regression pin."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    for n in ("h3-A", "h3-B", "h3-C"):
        sdk.create_entity("object", n, objectKind="core:strategy")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "h3-A", "supersedes_by": "h3-B", "evidence": ""},
         {"superseded": "h3-B", "supersedes_by": "h3-A", "evidence": ""},
         {"superseded": "h3-C", "supersedes_by": "h3-A", "evidence": ""}],
        session_id="sess_h3", warn=warns.append)
    assert applied == 2, \
        f"C→A must fold onto the live cycle member before the cycle block: {warns}"
    assert _entity_fold_state(proj, "h3-C") == ("superseded", "h3-A")
    assert _entity_fold_state(proj, "h3-A") == ("superseded", "h3-B")
    b = _entity_fold_state(proj, "h3-B")
    assert b is not None and (b[0] or "live") == "live", b


def test_apply_supersessions_chain_idless_target_is_refused(sdk):
    """#3590 (re-derives #2249 P2-2): in a chain whose HEAD is id-less, only
    the addressable link folds — the id-less link is REFUSED, not folded.

    #2249 pinned the pre-#3590 behaviour: an id-less target folded by NAME via
    the loop's fallback, so [C→D, mid→C] landed BOTH folds (applied=2,
    mid.sb=C, C.sb=D). #3590 deleted the name fallback (identity is the minted
    id), so ``mid→C`` is refused while the addressable ``C→D`` still folds —
    applied=1. This pin replaces ``test_apply_supersessions_chain_idless_target_converges``,
    which asserted the name fallback and was left stale by that change (RED on
    CI jobs `test-slow (e)` / `ci-introduced-failure` at ``9e18023da01f``).

    ⛔ The point of pinning it in a CHAIN is the ordering guarantee #2249 was
    about: the pre-pass still sorts the id-less record normally (it is a needer
    of its successor), so the refusal is a MAIN-LOOP decision, not an accident
    of payload order. What it must not do is take C's fold down with it.
    """
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "it-C", objectKind="core:strategy")
    sdk.create_entity("object", "it-D", objectKind="core:strategy")
    # raw legacy id-less mid node (NO id property at all)
    proj.g.query("CREATE (o:Object {name:'it-mid', status:'live'})")
    rows = proj.g.query(
        "MATCH (o:Object {name:'it-mid'}) RETURN o.id",
    ).result_set
    assert rows and rows[0][0] is None, f"fixture must be id-less: {rows!r}"
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "it-C", "supersedes_by": "it-D",
          "evidence": "d replaces c"},
         {"superseded": "it-mid", "supersedes_by": "it-C",
          "evidence": "c replaces mid (id-less head)"}],
        session_id="sess_it", warn=warns.append)
    assert applied == 1, \
        f"only the addressable link folds; the id-less one is refused: {warns}"
    assert len(warns) == 1 and "it-mid" in warns[0] and "#3590" in warns[0], \
        warns
    # the refusal is LOCAL: the id-less head is untouched...
    assert _entity_fold_state(proj, "it-mid") == ("live", None), \
        _entity_fold_state(proj, "it-mid")
    # ...and the addressable link in the same chain still lands.
    assert _entity_fold_state(proj, "it-C") == ("superseded", "it-D")


def test_apply_supersessions_selfref_dupname_keep_first_stable(sdk):
    """#2249 regression pin (code-review P2-1, second-model): when a
    record's successor name resolves back onto its OWN target via a
    duplicate-name carrier (self-referential mixed id/name claim against a
    dup-named distinct carrier), the pre-pass must NOT create an edge from
    it — the fold-time gate excludes the target itself by construction, so
    such an edge is spurious and would hoist the self-referential claim
    ahead of a divergent first-emitted sibling, FLIPPING the keep-first
    winner. Payload [B→C, Y] where B (id-B) shares name 'plan-X' with a
    duplicate B' (id-B-dup): B→C (first-emitted) must win — B.sb='plan-C',
    Y keep-first-warns, applied=1. (Green pre-fix: no sort. Green with the
    guard. RED on an unguarded pre-pass: Y would fold B.sb='plan-X' first.)
    See test_apply_supersessions_same_ref_stability for the plain same-ref
    stability contract this extends."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-C", objectKind="core:strategy")
    # duplicate-named carriers of 'plan-X' with explicit ids (legacy/raw)
    proj.g.query(
        "CREATE (o:Object {id:'sf-id-B', name:'plan-X', status:'live'})")
    proj.g.query(
        "CREATE (o:Object {id:'sf-id-B-dup', name:'plan-X', status:'live'})")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "sf-id-B", "supersedes_by": "plan-C",
          "evidence": "b replaced by c (first-emitted)"},
         {"superseded": "sf-id-B", "supersedes_by": "plan-X",
          "evidence": "self-referential claim onto own name"}],
        session_id="sess_sf", warn=warns.append)
    assert applied == 1, f"first-emitted claim wins: {warns}"
    b = _entity_fold_state(proj, "plan-X")
    assert b is not None, b
    # B and B' share the name — the fold landed on ONE carrier by id (sf-id-B);
    # assert via the id-carrying probe that the FIRST-EMITTED successor won.
    rows = proj.g.query(
        "MATCH (o:Object {id:'sf-id-B'}) RETURN o.status, o.supersededBy",
    ).result_set
    assert rows and rows[0][0] == "superseded" and rows[0][1] == "plan-C", rows
    assert any("keep-first" in w for w in warns), \
        f"divergent second claim must keep-first warn: {warns}"


def test_apply_supersessions_cycle_and_entangled_reverse_emission_literal(sdk):
    """#2249 determinism pins (code-review P2): cycle/entangled payloads in
    REVERSE emissions stay emission-LITERAL and deterministic (payload-order
    fallback — no exception, applied counts pinned). Arms:
    (a) 3-cycle [C→A, B→C, A→B] reverse (payload-order fallback): C→A folds
    C; B→C gate-skips (successor C already terminal); A→B folds A (B still
    live) → applied=2, C.sb=A, A.sb=B, B LIVE (B→C is the skipped link —
    emission-literal, not the "chain" a human might expect).
    (b) entangled [B→C, B→A, A→B]: B→C folds B (applied), B→A keep-first-
    warns, A→B gate-skips → applied=1, B.sb=C, A live.
    (c) entangled [B→A, B→C, A→B]: B→A folds B (applied), B→C keep-first-
    warns, A→B gate-skips → applied=1, B.sb=A, A live.
    Each asserts the exact pre-fix-parity outcome so a future Kahn/leftover
    change cannot silently shift an unpinned arm."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _seed(prefix: str) -> None:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")

    # (a) pure 3-cycle, reverse emission
    _seed("cy3")
    warns_a: list[str] = []
    applied_a = apply_supersessions(
        proj, sdk,
        [{"superseded": "cy3-C", "supersedes_by": "cy3-A", "evidence": ""},
         {"superseded": "cy3-B", "supersedes_by": "cy3-C", "evidence": ""},
         {"superseded": "cy3-A", "supersedes_by": "cy3-B", "evidence": ""}],
        session_id="sess_cy3", warn=warns_a.append)
    assert applied_a == 2, f"3-cycle folds first two emitted: {warns_a}"
    assert any("keep-first" in w or "no visible successor" in w
               for w in warns_a), warns_a
    c = _entity_fold_state(proj, "cy3-C")
    assert c == ("superseded", "cy3-A"), c
    assert _entity_fold_state(proj, "cy3-A") == ("superseded", "cy3-B")
    b_live = _entity_fold_state(proj, "cy3-B")
    assert b_live is not None and (b_live[0] or "live") == "live", b_live

    # (b) entangled reverse [B→C, B→A, A→B] — A stays live (emission-literal)
    _seed("re1")
    warns_b: list[str] = []
    applied_b = apply_supersessions(
        proj, sdk,
        [{"superseded": "re1-B", "supersedes_by": "re1-C", "evidence": ""},
         {"superseded": "re1-B", "supersedes_by": "re1-A", "evidence": ""},
         {"superseded": "re1-A", "supersedes_by": "re1-B", "evidence": ""}],
        session_id="sess_re1", warn=warns_b.append)
    assert applied_b == 1, f"payload-order fallback: {warns_b}"
    b = _entity_fold_state(proj, "re1-B")
    assert b == ("superseded", "re1-C"), b
    a_b = _entity_fold_state(proj, "re1-A")
    assert a_b is not None and (a_b[0] or "live") == "live", a_b

    # (c) entangled [B→A, B→C, A→B] — garbage claim wins the middle (literal)
    _seed("re2")
    warns_c: list[str] = []
    applied_c = apply_supersessions(
        proj, sdk,
        [{"superseded": "re2-B", "supersedes_by": "re2-A", "evidence": ""},
         {"superseded": "re2-B", "supersedes_by": "re2-C", "evidence": ""},
         {"superseded": "re2-A", "supersedes_by": "re2-B", "evidence": ""}],
        session_id="sess_re2", warn=warns_c.append)
    assert applied_c == 1, f"payload-order fallback: {warns_c}"
    b2 = _entity_fold_state(proj, "re2-B")
    assert b2 == ("superseded", "re2-A"), b2
    a_c = _entity_fold_state(proj, "re2-A")
    assert a_c is not None and (a_c[0] or "live") == "live", a_c


def test_apply_supersessions_self_supersession_skipped(sdk):
    """#2164 review (P1 — ISSUE A): a SELF-supersession record —
    superseded == supersedes_by (the same ref string) — must be SKIPPED
    with a warning (applied=0) and leave the Object untouched. Pre-fix the
    entity lane applied it with applied=1: the LIVE Object folded to
    status='superseded', supersededBy=<itself> — permanently removing it
    from recall_state's default view — and a durable self-referential
    ObjectSuperseded was journaled. Every sibling path guards this (the
    replaced eval inline loop's old_id == new_id → continue;
    supersede_point raises on old==new; the producer-side id-match
    short-circuits before its kind filter) — this consumer-side guard is
    the defense-in-depth sink, placed before the pt_/entity dispatch so it
    guards BOTH lanes (the pt_ self-ref previously tripped supersede()'s
    raise into a spurious "failed" warning)."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "plan-X", "supersedes_by": "plan-X",
          "evidence": "self lifecycle"}],
        session_id="sess_self", warn=warns.append)
    assert applied == 0, "self-supersession must never apply"
    assert any("self-supersession" in w for w in warns), warns
    state = _entity_fold_state(proj, "plan-X")
    assert state == ("live", None), \
        f"self-supersession folded plan-X onto itself: {state!r}"
    assert _object_superseded_events(proj) == 0, \
        "no ObjectSuperseded may be journaled for a self-supersession"


def test_apply_supersessions_pt_self_supersession_skipped(sdk):
    """#2164 review (P1 — ISSUE A, pt_ lane): the same self-supersession
    guard fires for point records — a pt_ ref superseding ITSELF is a
    meaningless record, skipped with the same warning (pre-fix it fell
    through to sdk.supersede() whose old==new guard raised, surfacing as a
    spurious "point supersede ... failed" warning with no better signal)."""
    from tortoise.commit_ops import apply_supersessions
    from tortoise.commit_schema import point_content_id

    proj = sdk._get_proj()
    pid = point_content_id("self-supersession pt content")
    sdk.create_point("statement", "self-supersession pt content", id=pid,
                     status="live")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": pid, "supersedes_by": pid, "evidence": "pt"}],
        session_id="sess_self_pt", warn=warns.append)
    assert applied == 0
    assert any("self-supersession" in w for w in warns), warns
    assert not any("failed" in w for w in warns), \
        f"pt_ self-ref must not surface as a supersede() failure: {warns}"
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.status",
        params={"id": pid}).result_set
    assert rows and rows[0][0] == "live", \
        "the self-referencing point must stay live"


def test_apply_supersessions_mixed_id_name_self_alias_skipped(sdk):
    """#2164 review (P1 advisory): the string-equality self-guard only
    catches ref == supersedes_by on the SAME string — a MIXED id/name
    self-reference (superseded = the Object's canonical id,
    supersedes_by = that same Object's name) must ALSO be skipped. The
    successor visibility probe resolves the successor's id; when it equals
    the ref-side resolution's id, both sides are the SAME Object → folding
    would set status='superseded', supersededBy=<its own name> and remove
    the live Object from recall_state's default view (ISSUE A harm class).
    Id equality is unambiguous; distinct-id duplicate names are a LEGIT
    supersession and must NOT be skipped."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    # canonical id minted by create_entity
    obj_id = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "plan-X"}).result_set[0][0]
    assert obj_id, "create_entity must mint the canonical id"
    warns: list[str] = []
    # mixed alias: superseded = canonical id, supersedes_by = same Object's name
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": obj_id, "supersedes_by": "plan-X",
          "evidence": "self lifecycle (id/name alias)"}],
        session_id="sess_alias", warn=warns.append)
    assert applied == 0, "id/name-aliased self-supersession must never apply"
    assert any("self-supersession" in w for w in warns), warns
    state = _entity_fold_state(proj, "plan-X")
    assert state == ("live", None), \
        f"mixed alias folded plan-X onto itself: {state!r}"


def test_apply_supersessions_dup_name_successor_not_self(sdk):
    """#2164 review (P1 advisory, inverse + round-2 ISSUE 4): duplicate-
    named Objects (distinct canonical ids) do NOT make a supersession
    ambiguous — the fold stores only the successor DISPLAY string (never a
    node ref), so when a DISTINCT same-named successor exists, folding is
    deterministic and correct. Target A (named plan-X) superseded by
    "plan-X" where B (also named plan-X, live) exists → A IS superseded by
    B-in-effect: applied=1, A folded, B untouched. Self-alias fires ONLY
    when EVERY candidate is the target itself (no distinct successor) —
    covered by test_apply_supersessions_mixed_id_name_self_alias_skipped.
    Blind LIMIT 1 (pre-round-2) that picked A as its own successor would
    have been the self-fold; the alias scan makes the distinct successor
    visible."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    # object A named plan-X
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    id_a = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "plan-X"}).result_set[0][0]
    # object B ALSO named plan-X — force a second, distinct-id node directly
    # (create_entity would dedup by name; the raw write models a legacy/corrupt
    # duplicate-name state the helper must tolerate)
    proj.g.query(
        "CREATE (o:Object {id:$id, name:$n, kind:$k, status:'live'})",
        params={"id": "obj-planx-dup-b", "n": "plan-X",
                "k": "core:strategy"})
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": id_a, "supersedes_by": "plan-X",
          "evidence": "A replaced by duplicate-named B"}],
        session_id="sess_dup", warn=warns.append)
    assert applied == 1, "A IS superseded — a distinct successor exists"
    assert not any("self-supersession" in w for w in warns), warns
    rows = proj.g.query(
        "MATCH (o:Object {name:'plan-X'}) RETURN o.id, o.status, "
        "o.supersededBy").result_set
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    assert by_id[id_a] == ("superseded", "plan-X"), \
        f"A must fold to superseded by plan-X: {by_id}"
    assert by_id["obj-planx-dup-b"] == ("live", None), \
        f"B (the distinct successor) must stay live: {by_id}"


def test_apply_supersessions_dead_dup_candidate_skipped(sdk):
    """#2164 round-3 review: the fold-through decision needs a LIVE
    distinct successor. A (live, named plan-X) superseded by "plan-X" where
    the ONLY other plan-X carrier (B) is already TERMINAL (superseded) →
    folding A would leave NO visible Object under that name (A now dead, B
    already dead — recall_state excludes terminal Objects): the exact
    dangling-successor harm the successor-visibility probe prevents. Must
    warn + skip (applied=0, A stays live)."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    id_a = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "plan-X"}).result_set[0][0]
    # second plan-X carrier, already superseded (terminal)
    proj.g.query(
        "CREATE (o:Object {id:$id, name:$n, kind:$k, status:'superseded', "
        "supersededBy:'plan-Y'})",
        params={"id": "obj-planx-dead", "n": "plan-X",
                "k": "core:strategy"})
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": id_a, "supersedes_by": "plan-X",
          "evidence": "A replaced by the dead duplicate"}],
        session_id="sess_dead", warn=warns.append)
    assert applied == 0, "no visible successor — fold must not apply"
    assert any("no visible successor" in w for w in warns), warns
    rows = proj.g.query(
        "MATCH (o:Object {name:'plan-X'}) RETURN o.id, o.status").result_set
    by_id = {r[0]: r[1] for r in rows}
    assert by_id[id_a] == "live", \
        f"A must stay live — its only successor carrier is dead: {by_id}"
    assert by_id["obj-planx-dead"] == "superseded", by_id


def test_apply_supersessions_deprecated_carrier_skipped(sdk):
    """#2164 round-4 review (P1-1): the visible-successor gate must use
    recall_state's OBJECT exclusion set — a DEPRECATED successor is recall-
    INVISIBLE (enters the FTS pool but never the state view) even though
    'deprecated' is not in the point-terminal vocabulary set. A (live,
    named plan-X) superseded by "plan-X" where the only other carrier (B)
    is deprecated → no visible successor → warn + skip (applied=0)."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    id_a = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "plan-X"}).result_set[0][0]
    proj.g.query(
        "CREATE (o:Object {id:$id, name:$n, kind:$k, status:'deprecated'})",
        params={"id": "obj-planx-dep", "n": "plan-X",
                "k": "core:strategy"})
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": id_a, "supersedes_by": "plan-X",
          "evidence": "A replaced by the deprecated duplicate"}],
        session_id="sess_dep", warn=warns.append)
    assert applied == 0, "deprecated carrier is recall-invisible — no fold"
    assert any("no visible successor" in w for w in warns), warns
    rows = proj.g.query(
        "MATCH (o:Object {name:'plan-X'}) RETURN o.id, o.status").result_set
    by_id = {r[0]: r[1] for r in rows}
    assert by_id[id_a] == "live", \
        f"A must stay live — its only other carrier is deprecated: {by_id}"


def test_apply_supersessions_idless_live_carrier_skipped(sdk):
    """#2164 round-4 review (P1-2): an id-less Object is recall-INVISIBLE
    regardless of status (retrieval keys on o.id — name query, kind scan,
    and recall_state all exclude id-less rows). A (canonical, live, named
    plan-X) superseded by "plan-X" where the only other carrier is a raw
    id-less LIVE Object → no VISIBLE successor → warn + skip (applied=0),
    not a fold onto an unseen carrier."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    id_a = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "plan-X"}).result_set[0][0]
    # raw legacy id-less carrier — no id property, status live
    proj.g.query(
        "CREATE (o:Object {name:$n, kind:$k, status:'live'})",
        params={"n": "plan-X", "k": "core:strategy"})
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": id_a, "supersedes_by": "plan-X",
          "evidence": "A replaced by the id-less duplicate"}],
        session_id="sess_idless", warn=warns.append)
    assert applied == 0, "id-less carrier is recall-invisible — no fold"
    assert any("no visible successor" in w for w in warns), warns
    rows = proj.g.query(
        "MATCH (o:Object {name:'plan-X'}) RETURN o.id, o.status").result_set
    by_id = {r[0]: r[1] for r in rows}
    assert by_id[id_a] == "live", \
        f"A must stay live — its only other carrier is id-less: {by_id}"


def test_apply_supersessions_outdated_carrier_folds(sdk):
    """#2164 round-4 review (P2-1, inverse): 'outdated' IS visible in
    recall_state's default OBJECT view (it is in the point-terminal
    vocabulary, NOT the object exclusion tuple). A (live, named plan-X)
    superseded by "plan-X" where the only other carrier is outdated → a
    VISIBLE successor exists → fold proceeds (applied=1)."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "plan-X", objectKind="core:strategy")
    id_a = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id",
        params={"n": "plan-X"}).result_set[0][0]
    proj.g.query(
        "CREATE (o:Object {id:$id, name:$n, kind:$k, status:'outdated'})",
        params={"id": "obj-planx-old", "n": "plan-X",
                "k": "core:strategy"})
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": id_a, "supersedes_by": "plan-X",
          "evidence": "A replaced by the outdated duplicate"}],
        session_id="sess_old", warn=warns.append)
    assert applied == 1, "outdated carrier IS visible — fold applies"
    assert not any("no visible successor" in w for w in warns), warns
    rows = proj.g.query(
        "MATCH (o:Object {name:'plan-X'}) RETURN o.id, o.status").result_set
    by_id = {r[0]: r[1] for r in rows}
    assert by_id[id_a] == "superseded", by_id
    assert by_id["obj-planx-old"] == "outdated", \
        "the outdated carrier must be untouched"


# ── M2 branch (behind TORTOISE_SESSION_EXTRACTOR=m2) — seam tests call the
#    method directly (no env var needed) ─────────────────────────────────

def test_extract_session_llm_failure_is_structured_not_raised(sdk, monkeypatch):
    """P1: M2 LLM failure returns meta mode='error' with the exception class
    preserved (P2 needs TypeName to classify fatal 4xx) — never raises."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _FailingSessionExtractor())
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert extracted == []
    assert any("RuntimeError" in e and "500" in e for e in meta["errors"])


def test_extract_session_llm_partial_emission_reports_points(sdk, monkeypatch):
    """P1: a run that emitted points then failed reports them (extracted>0
    with ok=False — the caller's ok flag is the success signal)."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _PartialFailingSessionExtractor())
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert len(extracted) >= 1, "partial points must still be reported"
    assert any("RuntimeError" in e for e in meta["errors"])


def test_extract_session_llm_fold_failure_is_structured(sdk, monkeypatch):
    """P1: a failure AFTER run() (fold) stays inside the fail-closed surface
    — no raw exception after partial writes. Partial emitter documents the
    orphan window (projection writes points during run(); a fold failure
    leaves unowned statement nodes — accepted and visible, not silent)."""
    import tortoise.projection as proj

    class _BoomFoldCaller:
        version = "boom-fold@0"

        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _BoomFoldCaller())
    monkeypatch.setattr(proj, "fold",
                        lambda events: (_ for _ in ()).throw(RuntimeError("fold blew up")))
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert extracted == []
    assert any("fold blew up" in e for e in meta["errors"])


def test_extract_session_llm_wiring_failure_is_structured(sdk, monkeypatch):
    """P1: a mid-CONTAINS-wiring failure stays inside the fail-closed surface
    and the response never silently diverges from the graph: points wired
    before the raise are reported; graph-side orphan state is pinned.
    NOTE: the wiring query MATCHes the Session — pre-create it (the seam
    bypasses capture_session, which is what creates the Session)."""
    sdk._get_proj().g.query("MERGE (s:Session {id:'sess_p1'})")

    class _EmitThenBoomWiring:
        version = "wiring-boom@0"

        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})
            api.add_point("decision: deploy second", {"source": source_id})

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmitThenBoomWiring())
    proj = sdk._get_proj()
    _raw = proj.g._g
    _real_query = _raw.query  # _GuardedGraph.query is a read-only slot method
    calls = {"n": 0}

    def _boom_on_second(query, **params):
        if "CONTAINS" in query:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("falkordb transient write failure")
        return _real_query(query, **params)

    monkeypatch.setattr(_raw, "query", _boom_on_second)
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert any("transient write failure" in e for e in meta["errors"])
    assert len(extracted) == 1, "the point wired before the failure stays reported"
    proj = sdk._get_proj()
    stmts = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind IS NULL RETURN count(p)").result_set
    assert stmts[0][0] == 2, "both emitted points exist in the graph (orphan pinned)"
    wired_n = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IS NULL RETURN count(p)",
        params={"sid": "sess_p1"}).result_set
    assert wired_n[0][0] == 1, "exactly one point is CONTAINS-wired (the reported one)"


def test_extract_session_llm_empty_guard_is_self_consistent(sdk):
    """P1 (D2): the internal empty-transcript guard (defense-in-depth) must
    report an error entry — a regression to errors=[] would make capture
    compute ok=True on the empty path (the 'lying extraction_mode' bug)."""
    for conv in ([], [{"role": "user", "content": "ok"}]):
        extracted, meta = sdk._extract_session_llm(
            conv, "sess_p1", "2026-08-20T00:00:00+00:00")
        assert meta["mode"] == "empty", meta
        assert extracted == []
        assert any("empty" in e.lower() for e in meta["errors"]), meta
        assert meta["warnings"] == []


def test_extract_session_llm_zero_output_warns(sdk, monkeypatch):
    """P1: completed-but-empty M2 extraction is an additive warning, not a
    silent 0 and not a fake failure."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "llm"
    assert extracted == []
    assert any("no points" in w for w in meta["warnings"])
    assert meta["errors"] == []


def test_extract_session_llm_passthroughs_source_turn_id(sdk, monkeypatch):
    """E3 passthrough (M2 carrier): source_turn_id injected via
    add_point(**fields) — the carrier E3's projection will use — flows
    through `props` unchanged. (A synthetic PointUpdated event does NOT
    fold; the test uses the real carrier.)"""
    from tortoise.api import EventAPI

    class _StampingAPI(EventAPI):
        def add_point(self, content, provenance, **fields):
            fields["source_turn_id"] = "session_x_t0"
            return super().add_point(content, provenance, **fields)

    monkeypatch.setattr("tortoise.api.EventAPI", _StampingAPI)
    extracted, _meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert extracted, "CONV must extract points"
    assert any(p.get("props", {}).get("source_turn_id") == "session_x_t0"
               for p in extracted), extracted


# #3778: the M2 lane's `props` describes the NODE, never the folded statement
# dict. The seam's write is the shared EventAPI → projection door, so a field
# that belongs to a FOLDED canonical (written by an earlier occurrence) is a
# value the node does not hold. Echoing the folded dict advertised it anyway —
# the #2813 symptom on the M2 lane, in both directions.
#
# #8006 UPDATE: `search_keys` no longer belongs in that "does not hold" class.
# The shared projection writer now applies the R2 D3 flatten rule on the
# payload (`projection/entities.py::flatten_search_keys_prop`, called from
# `_upsert_point_props`), so this door stores the space-joined STRING exactly
# as `create_point` does. The divergence the test below still guards is
# REPRESENTATION: the payload says `["alpha", "beta"]`, the node says
# `"alpha beta"`, so a payload echo advertises the list — the same #2813
# symptom, one layer down.

class _E3FieldsExtractor:
    """Emits ONE point with the four E3 fields (plus E4 spans) passed as
    TOP-LEVEL ``add_point(**fields)`` — the shape `_extract_session_llm`
    reports straight off the folded statement dict."""
    version = "m2-e3fields@0"

    def run(self, transcript, source_id, api):
        from tortoise.api import provenance

        api.add_point(
            "decision: ship serve first",
            provenance(source_id, [0, 10], quote="q nested",
                       extracted_by="probe@0"),
            quote="q top", when="2026-08-01", source_turn_id="turn-7",
            search_keys=["alpha", "beta"], span_start=0, span_end=10)


def test_extract_session_llm_reports_stored_props_not_the_folded_payload(
        sdk, monkeypatch, force_sparse_tfidf):
    """#3778 (create path): the response's ``props`` equals the resolved
    NODE's stored props — not the folded statement dict's. The probe-verified
    divergence: a payload echo advertised a field whose STORED form differs
    from the payload's — the #2813 symptom ("the reply looked correct while
    the node stored nothing") on the M2 lane.

    #8006 UPDATE: the E3 ``search_keys`` list IS now stored on this door — the
    shared projection writer flattens it to the graph's space-joined string
    (``flatten_search_keys_prop``). So the pinned divergence for that field is
    REPRESENTATION, not presence: the payload says ``["alpha", "beta"]`` and
    the node says ``"alpha beta"``. A payload echo would advertise the LIST.

    MUTATION THAT REDS THIS TEST: restore the payload echo
    (``props = {k: v for k, v in p.items() if k in
    _CAPTURE_PASSTHROUGH_PROPS}``) — ``search_keys`` reappears in the reply as
    ``["alpha", "beta"]`` instead of the stored ``"alpha beta"``."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _E3FieldsExtractor())
    proj = sdk._get_proj()
    proj.g.query("MERGE (s:Session {id:'sess_3778'})")
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_3778", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "llm" and len(extracted) == 1, (meta, extracted)
    entry = extracted[0]
    quote, when, sk, tid = _read_passthrough_props(sdk, entry["id"])
    assert (quote, when, tid) == ("q top", "2026-08-01", "turn-7")
    # #8006: the node HOLDS search_keys, in its canonical flattened form.
    assert sk == "alpha beta"
    # The reply is exactly what the graph holds, field for field (the same
    # read-back helper the v2 dedup-hit lane uses — one contract, both lanes).
    assert entry["props"] == sdk._read_capture_passthrough_props(
        proj, entry["id"]), entry["props"]
    assert entry["props"] == {
        "quote": "q top", "when": "2026-08-01",
        "source_turn_id": "turn-7", "span_start": 0, "span_end": 10,
        "search_keys": "alpha beta",
    }, entry["props"]
    # …and NOT the payload's list spelling (the representation trap above).
    assert entry["props"]["search_keys"] != ["alpha", "beta"], entry["props"]


def test_extract_session_llm_fold_reports_the_canonicals_stored_props(
        sdk, monkeypatch, force_sparse_tfidf):
    """#3778 (in-capture fold path): the duplicate is DETACH DELETEd and the
    entry reports the CANONICAL id, so its ``props`` must be the canonical's
    STORED values — the duplicate's payload (a different quote/when/
    source_turn_id) describes a node that no longer exists.

    MUTATION THAT REDS THIS TEST: restore the payload echo — the folded
    entry advertises ``q second``/``turn-2`` (the detached duplicate's) while
    its id names the node holding ``q first``/``turn-1``."""

    class _DuplicateExtractor:
        version = "m2-dup@0"

        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first",
                          {"source": source_id},
                          quote="q first", when="2026-08-01",
                          source_turn_id="turn-1")
            api.add_point("decision: ship serve first",
                          {"source": source_id},
                          quote="q second", when="2026-08-02",
                          source_turn_id="turn-2")

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _DuplicateExtractor())
    proj = sdk._get_proj()
    proj.g.query("MERGE (s:Session {id:'sess_3778_fold'})")
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_3778_fold", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "llm" and len(extracted) == 2, (meta, extracted)
    first, folded = extracted
    assert first["id"] == folded["id"], extracted
    assert folded["dedup"] == "content_hash_hit", folded
    # ONE node, so both entries must describe the SAME stored state.
    assert folded["props"] == first["props"] == {
        "quote": "q first", "when": "2026-08-01",
        "source_turn_id": "turn-1",
    }, extracted
    assert folded["props"] == sdk._read_capture_passthrough_props(
        proj, folded["id"]), folded["props"]


def test_extract_session_llm_in_tree_lane_advertises_nothing_unstored(
        sdk, force_sparse_tfidf):
    """#3778 parity pin (real in-tree M2 extractor, offline MockModel): the
    shipped extractor emits ``quote`` NESTED in provenance and never passes
    search_keys/source_turn_id, so every entry's ``props`` is ``{}`` because
    the NODES hold none — the read-back must not fabricate presence, and this
    lane's receipt is unchanged by the #3778 fix."""
    proj = sdk._get_proj()
    proj.g.query("MERGE (s:Session {id:'sess_3778_intree'})")
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_3778_intree", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "llm" and extracted, (meta, extracted)
    for entry in extracted:
        assert entry["props"] == {}, entry
        stored = sdk._read_capture_passthrough_props(proj, entry["id"])
        assert stored == {}, (entry["id"], stored)
        quote, when, sk, tid = _read_passthrough_props(sdk, entry["id"])
        assert (quote, when, sk, tid) == (None, None, None, None), entry


# ── capture_session fail-closed assembly (both branches) ──────────────────































# ── #1532 P4: capture-path parity (window / role / quota / MITIGATES / id) ──

def test_capture_turn_window_truncates_content(sdk):
    from tortoise.sdk import (
        _CAPTURE_TRUNCATION_SENTINEL,
        _capture_truncation_marker,
        _capture_turn_window,
    )
    conv = [{"role": "user", "content": "x" * 6000}]
    out = _capture_turn_window(conv)
    assert len(out[0]["content"]) == 5000
    # #4897: the cut is VISIBLE — the body is the first 5,000-char-worth of
    # prefix plus a marker carrying the true length, never a bare `[:5000]`.
    assert out[0]["content"].endswith(_capture_truncation_marker(6000))
    assert out[0]["content"] != "x" * 5000
    assert _CAPTURE_TRUNCATION_SENTINEL in out[0]["content"]
    assert len(conv[0]["content"]) == 6000, "input list is never mutated"


def test_capture_turn_window_preserves_short_and_absent(sdk):
    from tortoise.sdk import _capture_turn_window
    conv = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": None},
            {"role": "user"}]                      # content key absent
    out = _capture_turn_window(conv)
    assert out[0]["content"] == "hi"
    assert out[1]["content"] == ""                  # None -> "" (store-loop parity)
    assert out[2]["content"] == ""


def test_capture_turn_window_idempotent_when_pre_truncated(sdk):
    """#1532 D1: running the window over an already-windowed conversation is
    a no-op — the SDK loop's clip and the extraction call can both apply
    it without double-truncating."""
    from tortoise.sdk import _capture_turn_window
    conv = [{"role": "user", "content": "y" * 5000}]
    out = _capture_turn_window(conv)
    assert out[0]["content"] == "y" * 5000


def test_shared_extraction_window_blanks_a_clipped_turn_with_nothing_to_say():
    """#6246: the #4897 marker-only invariant is enforced at ONE SHARED point.

    The invariant — "a turn with no extractable content of its own yields no
    claims" — used to live only inside ``_session_llm_transcript`` (the m2
    lane). The DEFAULT lane is ``_extract_session_v2`` ->
    ``extractor_v2._edus_from_conversation``, which keeps any turn with truthy
    content, so a clipped turn whose readable body was a lone ``X`` was
    submitted as a v2 EDU while the m2 lane dropped it: the SAME conversation
    was clean on one lane and not on the other.

    Pinned at the INPUT UNIT both lanes share — no DB, no provider, no LLM.
    Mutation: drop the ``_extractable_sentences`` check in
    ``_capture_extraction_window`` and the two blank turns reappear as EDUs
    while the m2 lane still drops them.
    """
    from tortoise.extractor_v2 import _edus_from_conversation
    from tortoise.sdk import (
        _capture_extraction_window,
        _capture_turn_window,
        _redact_turn_contents,
        _session_llm_transcript,
    )

    mixed = [
        {"role": "user",
         "content": "I think the auth dead-end is the top issue."},
        # 10,000 chars flattening to lone dots — no >=3-char sentence
        {"role": "user", "content": "  .  " * 2000},
        # 6,001 chars whose readable body is the single char "X"
        {"role": "user", "content": "X" + " " * 6000},
    ]

    # The STORED view (what the turn Points hold) is clipped, NOT blanked —
    # only the extraction input is blanked.
    stored = _capture_turn_window(mixed)
    assert stored[1]["content"].strip() and stored[2]["content"].strip()

    extract = _capture_extraction_window(mixed)
    assert len(extract) == len(mixed), "the turn list must not be renumbered"
    assert [t["content"] for t in extract] == [mixed[0]["content"], "", ""]

    # LANE 1 — the m2 transcript and the Source summary.
    transcript, _est = _session_llm_transcript(extract)
    assert transcript == "User: I think the auth dead-end is the top issue."

    # LANE 2 — the default v2 EDU list. Same input, same verdict.
    edus = _edus_from_conversation(_redact_turn_contents(extract)[0])
    assert [e["index"] for e in edus] == [0], (
        f"a clipped turn with nothing to say produced a v2 EDU: {edus}")
    assert all(e["text"].strip() for e in edus)

    # NARROW BY CONSTRUCTION: an ordinary unmarked short turn is untouched —
    # this must not become "drop every sentence-less turn from v2".
    short = _capture_extraction_window([{"role": "user", "content": "ok"}])
    assert short[0]["content"] == "ok"
    # A clipped turn that DOES have a sentence keeps its content verbatim.
    kept = _capture_extraction_window(
        [{"role": "user", "content": "a real sentence here. " + " pad" * 1500}])
    assert kept[0]["content"].startswith("a real sentence here. ")




# ── #4897: a cut turn is MARKED, never silently shortened ──────────────────
#
# The READER's contract, stated as a literal rather than imported: this is what
# a consumer of a stored turn matches on, and the assertion must be able to fail
# on a tree where the writer emits no marker — importing the SDK sentinel here
# would turn a missing marker into a collection error instead of a failed
# assertion. `test_the_test_sentinel_literal_matches_the_sdk_contract` pins it
# to the SDK so this copy cannot drift.
_TRUNCATION_SENTINEL = "…[truncated:"


def test_the_test_sentinel_literal_matches_the_sdk_contract():
    from tortoise.sdk import _CAPTURE_TRUNCATION_SENTINEL
    assert _TRUNCATION_SENTINEL == _CAPTURE_TRUNCATION_SENTINEL


def test_clip_capture_turn_content_marks_only_an_actual_cut():
    """A turn that FITS is complete and must never be marked; only a real cut
    marks, and the marker carries the true length (#4897)."""
    from tortoise.sdk import _CAPTURE_TURN_CAP, _clip_capture_turn_content
    cap = _CAPTURE_TURN_CAP
    for n in (0, 1, cap - 1, cap):
        content = "a" * n
        assert _clip_capture_turn_content(content) == content, (
            f"a {n}-char turn fits the cap and must be returned verbatim")
        assert _TRUNCATION_SENTINEL not in _clip_capture_turn_content(content), (
            "only an ACTUAL cut is truncation — cap is complete, not cut")

    over = cap + 1
    clipped = _clip_capture_turn_content("a" * over)
    assert len(clipped) == cap, "a clipped turn fills the window exactly"
    assert clipped.startswith("a" * 10)
    assert clipped.endswith(
        f" {_TRUNCATION_SENTINEL} original length {over} chars]")
    assert f"original length {over}" in clipped

    # STABLE under re-application: the server re-applies the window to the
    # stored body, and the clip reserved the marker inside the cap, so the
    # second application is a true no-op (this is what keeps the marker alive).
    assert _clip_capture_turn_content(clipped) == clipped


def test_clip_capture_turn_content_cuts_mid_word_without_splitting_a_character():
    from tortoise.sdk import _CAPTURE_TURN_CAP, _clip_capture_turn_content
    cap = _CAPTURE_TURN_CAP
    # The cut lands MID-WORD: the marker is what makes that visible.
    content = "design " * 1000                       # 7000 chars
    clipped = _clip_capture_turn_content(content)
    assert _TRUNCATION_SENTINEL in clipped
    assert f"original length {len(content)}" in clipped
    body = clipped[: clipped.index(_TRUNCATION_SENTINEL) - 1]
    assert content.startswith(body), "the body must be a prefix of the turn"

    # A multi-byte character straddling the cut: code-point slicing cannot
    # produce a lone surrogate, and the count is CODE POINTS (matching the
    # server's len()), so the recorded length is the true character count.
    emoji = "\U0001f600" * 6000
    clipped = _clip_capture_turn_content(emoji)
    assert len(clipped) == cap
    assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in clipped), \
        "a cut must never split a character into a lone surrogate"
    assert _TRUNCATION_SENTINEL in clipped
    assert f"original length {len(emoji)}" in clipped






def test_an_expanding_redaction_keeps_stored_and_extraction_markers_in_parity():
    """#721 parity must hold WITH a credential — the case the marker is for.

    The stored node and the extraction transcript are built from the SAME window
    and the SAME scrub, so their markers must report the SAME true length. The
    pre-fix re-clip gave the NODE the scrubbed length while the transcript still
    carried the window's true length, so the two disagreed exactly when the
    marker mattered. The existing parity test used NO credential, which made its
    claim vacuously true.
    """
    from tortoise.sdk import (
        _capture_turn_texts,
        _capture_turn_window,
        _redact_turn_contents,
        _session_llm_transcript,
    )
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    content = "y" * 4900 + " " + key + " tail " + "z" * 2000
    windowed = _capture_turn_window([{"role": "user", "content": content}])
    # The extraction leg builds its transcript from the SCRUBBED window —
    # ``_extract_session_llm`` and ``_materialize_session_source`` both do this.
    redacted, _counts = _redact_turn_contents(windowed)
    transcript, _est = _session_llm_transcript(redacted)
    stored = _capture_turn_texts(windowed)[0]
    assert stored == "[user] " + redacted[0]["content"], (
        "the stored turn and the scrubbed window the transcript is built from "
        "must be the same text")
    assert f"original length {len(content)}" in transcript
    assert f"original length {len(content)}" in stored






def test_the_marker_survives_the_servers_cap_reapplication():
    """(#4897 review) ``_capture_turn_texts`` is REDACTION-ONLY: it must NOT
    re-apply the cap to the stored body.

    ⛔ The cap has ONE owner (``_capture_turn_window``), so re-windowing a
    marked turn is a no-op and a redaction that EXPANDS the body past the cap
    must survive intact. A re-clip at the stored-text sink would take
    ``content[:cap]`` of the scrubbed text and destroy the marker the window
    appended at the cut.

    (The earlier version of this test asserted the same property with a fixture
    exactly AT the cap, where any re-clip is a no-op — it could not fail. The
    fixture now expands, so the assertion binds.)
    """
    from tortoise.sdk import (
        _CAPTURE_TRUNCATION_SENTINEL,
        _CAPTURE_TURN_CAP,
        _capture_turn_texts,
        _capture_turn_window,
    )
    # 400 x 21 chars (4 + 16 + 1) = 8,400, clipped to the cap; the 20-char AWS keys inside
    # the stored window each expand to a 28-char marker, pushing it past the cap.
    content = ("AKIA" + "ABCDEFGHIJKLMNOP" + " ") * 400
    once = _capture_turn_window([{"role": "user", "content": content}])
    assert len(once[0]["content"]) == _CAPTURE_TURN_CAP, "the window owns the cap"
    assert _CAPTURE_TRUNCATION_SENTINEL in once[0]["content"]
    twice = _capture_turn_window([dict(t) for t in once])
    assert twice == once, "a re-applied window must not change the marked turn"
    stored = _capture_turn_texts(once)[0]
    assert stored != "[user] " + once[0]["content"], (
        "the fixture must actually EXPAND on scrubbing, or nothing is bound")
    assert _CAPTURE_TRUNCATION_SENTINEL in stored, (
        "the stored-text sink is redaction-only and must not re-clip — a re-clip "
        "here truncates the scrubbed body and loses the marker")
    assert stored.endswith(
        f" {_CAPTURE_TRUNCATION_SENTINEL} original length {len(content)} chars]"), \
        "the marker must survive the stored-text re-application"


def test_normalize_turn_role_matches_sdk_loop(sdk):
    from tortoise.sdk import _normalize_turn_role
    assert _normalize_turn_role("user") == "user"
    assert _normalize_turn_role(None) == "unknown"
    assert _normalize_turn_role(123) == "123"
    assert _normalize_turn_role({"a": 1}) == "{'a': 1}"
    assert _normalize_turn_role(False) == "False"   # falsy non-string, not swallowed




def test_extraction_estimate_v2_shape(sdk):
    from tortoise.sdk import _session_extraction_estimate
    conv = [{"role": "user", "content": "one. two. three."}]  # 3 sentences
    m2 = _session_extraction_estimate(conv, extractor="m2")
    v2 = _session_extraction_estimate(conv, extractor="v2")
    assert m2 == 6          # 3 sentences * 2 (points + operators)
    assert v2 == 9          # 3 * 3 (points + operators + entities/events)
    assert v2 > m2


def test_extraction_estimate_default_is_v2(sdk, monkeypatch):
    monkeypatch.delenv("TORTOISE_EXTRACTOR", raising=False)
    from tortoise.sdk import _session_extraction_estimate
    conv = [{"role": "user", "content": "one. two."}]
    assert _session_extraction_estimate(conv) == 6  # v2 default (2 sentences * 3)


def test_extraction_estimate_legacy_alias(sdk, monkeypatch):
    """#1532 D4: the deprecated-compat name resolves to the same v2-aware
    estimator — pre-migration callers keep working."""
    from tortoise.sdk import _session_extraction_estimate, _session_llm_extraction_estimate
    conv = [{"role": "user", "content": "one. two. three."}]
    assert _session_llm_extraction_estimate(conv) == \
        _session_extraction_estimate(conv)


def test_extraction_estimate_is_taken_over_the_stored_window(sdk):
    """M5 (#4897 round 2): the estimate must window its OWN input.

    ``_session_llm_transcript`` no longer clips (the true total is decided once,
    on pre-redaction text), so ``_session_extraction_estimate`` windows before
    calling it. Without that compensating window a raw caller's estimate counts
    sentences the extractor never receives — this fixture makes the raw and
    windowed sentence counts differ, so removing the window reds it.
    """
    from tortoise.sdk import (
        _capture_turn_window,
        _session_extraction_estimate,
        _session_llm_transcript,
    )
    content = "one. " + "x" * 4900 + " extra." * 120
    raw = [{"role": "user", "content": content}]
    assert len(content) > 5000
    windowed = _capture_turn_window(raw)
    assert _session_llm_transcript(windowed)[1] < _session_llm_transcript(raw)[1], (
        "this fixture must discriminate: the tail sentences are past the cut")
    assert _session_extraction_estimate(raw) == \
        _session_extraction_estimate(windowed), (
        "the raw estimate counted sentences the stored window never receives")


def test_capture_turn_texts_preserves_the_windowing_bound():
    """M4 (#4897 round 5): the stored-text sink REQUIRES a windowed conversation.

    ``_capture_turn_texts_with_redactions`` no longer windows and no longer
    clips: it is REDACTION-ONLY, and ``_capture_turn_window`` is the sole owner
    of the cap (#4897 round 5 — clipping here is the two-jobs defect that five
    review rounds could not patch away).

    The round-2 version of this test asserted that a RAW caller of this sink was
    bounded. That is a guarantee the code deliberately no longer has, so it is
    narrowed here to what IS true: the entry-point window bounds a raw over-cap
    conversation, MARKED, and the sink preserves that bound and its TRUE marker
    for a caller that follows the contract (production always passes
    ``windowed``). The raw-caller contract is pinned separately by
    ``test_the_redactor_is_redaction_only_and_never_clips``.
    """
    from tortoise.sdk import (
        _CAPTURE_TRUNCATION_SENTINEL,
        _CAPTURE_TURN_CAP,
        _capture_turn_texts_with_redactions,
        _capture_turn_window,
    )
    content = "x" * (_CAPTURE_TURN_CAP + 1000)
    windowed = _capture_turn_window([{"role": "user", "content": content}])
    texts, _counts = _capture_turn_texts_with_redactions(windowed)
    body = texts[0][len("[user] "):]
    assert len(body) <= _CAPTURE_TURN_CAP, (
        "the stored-text sink re-clipped, or the window failed to bound the "
        "raw over-cap turn")
    assert _CAPTURE_TRUNCATION_SENTINEL in body, (
        "a raw over-cap turn was stored with NO marker")
    assert f"original length {len(content)} chars]" in body, (
        "the marker must carry the RAW turn's true pre-redaction length")


def test_a_caller_supplied_marker_cannot_defeat_the_cap():
    """#4897 round-3 P1 / round 5: a marker-shaped tail is NOT proof of a clip.

    The round-3 guard tried to fix this by deciding the clip on the marker-FREE
    body inside the redactor — which is what forced the redactor to do two jobs.
    Round 5 removes the tension structurally: the WINDOW owns the cap and tests
    the TOTAL length (markers never exempt a body), and the redactor does not
    clip at all. So a client appending a marker to an unbounded body still
    cannot store it whole (reproduced: a 2,000,000-char body), and a bare-
    sentinel tail produces a marker with the length actually seen — never a
    marker with NO total and never the caller's claim.
    """
    from tortoise.sdk import (
        _CAPTURE_TRUNCATION_SENTINEL,
        _CAPTURE_TURN_CAP,
        _capture_turn_texts_with_redactions,
        _capture_turn_window,
    )
    body_text = "A" * 2_000_000
    for tail, label in (
        (f"{_CAPTURE_TRUNCATION_SENTINEL} original length 1 chars]",
         "lying total"),
        (_CAPTURE_TRUNCATION_SENTINEL, "bare sentinel, no total"),
    ):
        content = body_text + tail
        windowed = _capture_turn_window([{"role": "user", "content": content}])
        texts, _counts = _capture_turn_texts_with_redactions(windowed)
        body = texts[0][len("[user] "):]
        assert len(body) <= _CAPTURE_TURN_CAP, (
            f"{label}: a caller-supplied marker defeated the cap — got "
            f"{len(body)} chars")
        assert _CAPTURE_TRUNCATION_SENTINEL in body, label
        assert f"original length {len(content)} chars]" in body, (
            f"{label}: the marker must carry the length actually seen, not the "
            "caller's claim (or no total at all)")
        assert "original length 1 chars]" not in body, (
            f"{label}: the caller's claim survived as the recorded total")
    # Control: the bound is the same one the no-marker raw caller already got.
    control, _c = _capture_turn_texts_with_redactions(
        _capture_turn_window([{"role": "user", "content": body_text}]))
    assert len(control[0][len("[user] "):]) <= _CAPTURE_TURN_CAP


def test_extract_session_llm_windows_a_raw_over_cap_conversation(
        sdk, monkeypatch):
    """#4897 round-2 P3: the M2 seam windows its own input.

    The transcript contract now requires the WINDOWED conversation; a direct
    caller must not feed the extractor and the blank-gate an unclipped body.
    Production already passes ``windowed`` (a no-op), so this pins the raw
    caller's path.
    """
    from tortoise import sdk as sdk_mod
    from tortoise.sdk import _CAPTURE_TRUNCATION_SENTINEL, _CAPTURE_TURN_CAP
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    seen: list = []
    real = sdk_mod._session_llm_transcript

    def spy(conversation):
        seen.append([t["content"] for t in conversation])
        return real(conversation)

    monkeypatch.setattr(sdk_mod, "_session_llm_transcript", spy)
    content = "x" * (_CAPTURE_TURN_CAP + 3000)
    sdk._extract_session_llm(
        [{"role": "user", "content": content}], "sess_p3_win",
        "2026-08-20T00:00:00+00:00")
    assert seen, "the transcript builder was never called"
    assert all(len(c) <= _CAPTURE_TURN_CAP for c in seen[0]), (
        "the M2 extractor received an unclipped body from a raw caller")
    # ⛔ The GATE and the EXTRACTOR build the transcript SEPARATELY now (#4897 review round 12):
    # the gate needs the marker STRIPPED (a blank past-cap turn must read as blank), the extractor
    # needs it KEPT (it is the evidence of what was cut). So the marker is no longer on the FIRST
    # call — asserting `seen[0]` made this test bind the gate's call instead of the extractor's.
    assert any(_CAPTURE_TRUNCATION_SENTINEL in c[0] for c in seen if c), (
        "the extractor must see the cut marked — the marker is the evidence of what was removed")
    assert all(len(c) <= _CAPTURE_TURN_CAP for call in seen for c in call), (
        "EVERY builder call must receive a clipped body, gate included")


def test_extract_session_llm_refuses_a_marker_only_conversation(sdk, monkeypatch):
    """#4897 round-13 P2: the M2 seam's OWN empty guard reads the STRIPPED window.

    Round 12 routed this defence-in-depth guard through ``_capture_gate_window``
    but pinned only the transcript's clipping, so reverting the gate left the
    whole pinned selection green — mutation (d) SURVIVED the round-13 review.
    Reverting it is NOT a no-op: read against the MARKED window, a conversation
    whose entire content is a marker lookalike is non-blank, the guard passes,
    and the v1 extractor turns the marker into a Point whose whole content is
    synthetic.

    ⛔ ROUND 14 NOTE: this test no longer isolates the gate. Round 14 added "a
    turn with no content of its own yields no claims" to
    ``_session_llm_transcript``, which refuses these exact lookalikes on its own
    — so reverting the gate now leaves THIS test green (re-measured: it does).
    The gate's binding pin is
    ``test_the_gate_strips_a_lookalike_with_trailing_whitespace``, whose
    trailing space defeats that skip. This test is kept because it pins the
    END-TO-END behaviour of the seam, which is what a reader cares about.

    The extractor is monkeypatched to one that yields NO points, and THAT is
    what makes this pin selective: a gate that fails to fire lands in
    ``mode='llm'`` with a 'no points' warning, while a gate that fires returns
    ``mode='empty'``. Asserting only ``extracted == []`` would pass either way.
    """
    from tortoise.sdk import _capture_truncation_marker
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    for lookalike in (_capture_truncation_marker(5001),
                      _capture_truncation_marker(10 ** 9)):
        extracted, meta = sdk._extract_session_llm(
            [{"role": "user", "content": lookalike}],
            "sess_r13", "2026-08-20T00:00:00+00:00")
        assert meta["mode"] == "empty", (lookalike, meta)
        assert extracted == []
        assert any("empty" in e.lower() for e in meta["errors"]), meta

    # The strip takes only the MARKER: a turn with real content past the cap still
    # extracts, so the guard cannot be satisfied by refusing everything long.
    _extracted, admitted = sdk._extract_session_llm(
        [{"role": "user", "content": "x" * 6000}],
        "sess_r13b", "2026-08-20T00:00:00+00:00")
    assert admitted["mode"] == "llm", admitted


def test_the_gate_strips_a_lookalike_with_trailing_whitespace(sdk, monkeypatch):
    """#4897 round-13 P3: a marker lookalike followed by a space is still one.

    ``_split_truncation_marker`` recognises the marker only as the EXACT tail, so
    ``marker + " "`` escaped the gate's strip and was admitted as a non-blank
    turn holding nothing but marker text — the docstring's fail-closed claim was
    one character wide. Right-stripping before the split closes it, and only in
    the fail-closed direction.

    ⛔ THIS IS THE TEST THAT BINDS THE ``_extract_session_llm`` GATE (#4897 review
    round 14). The trailing space defeats round 14's "a turn with no content
    yields no claims" skip in ``_session_llm_transcript``, because
    ``_split_truncation_marker`` recognises a marker only as the EXACT tail — so
    this is the one input where the gate's strip, and nothing else, decides the
    verdict. Verified: reverting the gate reddens this test.
    """
    from tortoise.sdk import _capture_truncation_marker
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    for suffix in (" ", "\n", "\t  "):
        extracted, meta = sdk._extract_session_llm(
            [{"role": "user", "content": _capture_truncation_marker(5001) + suffix}],
            "sess_r13c", "2026-08-20T00:00:00+00:00")
        assert meta["mode"] == "empty", (suffix, meta)
        assert extracted == []








def test_the_default_v2_lane_cannot_mint_a_claim_from_a_marker_only_turn():
    """#4897 round 18 P2: the sibling lane must not disagree with the transcript.

    The rounds 14-16 skip lives in ``_session_llm_transcript`` — the m2 lane and
    the Source summary. The DEFAULT lane is ``_extract_session_v2``, which hands
    the raw turns to ``extractor_v2._edus_from_conversation``, and that builder
    keeps any turn with truthy content: a clipped turn whose real body is a lone
    ``X`` was fed to the model as ``"X …[truncated: …]"``, i.e. a turn with
    nothing to say whose only sentence is this module's own marker. Pinned here
    as an EDU-LEVEL invariant (no DB, no provider, no LLM): the marker-only turn
    yields no edu, the real turn still does, and turn INDICES are unshifted.

    ⛔ NARROW BY CONSTRUCTION — an UNMARKED short turn (``"ok"``) is untouched,
    and a marked turn that still has a sentence keeps its marker. The fix must
    not silently become "drop every short turn from v2"; that would be a
    different, unreviewed change to the default extraction lane.
    """
    from tortoise.extractor_v2 import _edus_from_conversation
    from tortoise.sdk import (
        _blank_unextractable_marked_turns,
        _capture_truncation_marker,
    )

    marker = _capture_truncation_marker(6001)
    conversation = [
        {"role": "user", "content": "a real sentence here."},
        {"role": "assistant", "content": "X" + marker},
        {"role": "user", "content": "another real sentence."},
    ]
    blanked = _blank_unextractable_marked_turns(conversation)

    # Length preserved: the edu builder derives each turn's index from its
    # POSITION, and `_resolve_source_turn` anchors quotes to those indices.
    assert len(blanked) == len(conversation)
    assert blanked[0]["content"] == conversation[0]["content"]
    assert blanked[1]["content"] == "", "the marker-only turn is still content"
    assert blanked[2]["content"] == conversation[2]["content"]
    assert conversation[1]["content"] == "X" + marker, "input mutated in place"

    edus = _edus_from_conversation(blanked)
    assert [e["index"] for e in edus] == [0, 2], (
        f"the marker-only turn produced an edu, or indices shifted: {edus}")
    assert all(marker not in e["text"] for e in edus), (
        f"the marker reached the v2 model as its own turn: {edus}")

    # Narrowness — the two ways this fix could over-reach.
    short = _blank_unextractable_marked_turns([{"role": "user", "content": "ok"}])
    assert short[0]["content"] == "ok", "an unmarked short turn was dropped"
    kept = _blank_unextractable_marked_turns(
        [{"role": "user", "content": "a real sentence. " + marker}])
    assert kept[0]["content"].endswith(marker), (
        "a marked turn WITH content lost its marker")

    # Idempotent: a blanked turn carries no marker, so a second pass is a no-op.
    assert _blank_unextractable_marked_turns(blanked) == blanked


def test_extract_session_v2_blanks_a_marker_only_turn_before_the_pipeline(
        sdk, monkeypatch):
    """#4897 round 18 P2 — the WIRING, not just the helper.

    The helper test above would still pass if nobody called it. This drives the
    DEFAULT lane's real entry point and captures the conversation the v2
    pipeline actually receives, so deleting the call site reds this test.
    ``extract_session_v2`` is replaced (the same seam the session-date tests
    use), so no provider key, network or DB is involved; the autouse mock seam
    satisfies the lane's inner provider gate.
    """
    import tortoise.extractor_v2 as ev2
    from tortoise.sdk import _capture_truncation_marker

    marker = _capture_truncation_marker(6001)
    seen: list = []

    def _fake_extract(model, conversation, **kw):
        seen.append(conversation)
        return {"payload": None, "minted_kinds": [], "supersessions": [],
                "chain_notes": [], "link_before_create": [],
                "warnings": [], "story_arc": "", "search": {},
                "stats": {}, "errors": ["no payload produced"]}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)
    sdk._extract_session_v2(
        [{"role": "user", "content": "a real sentence here."},
         {"role": "assistant", "content": "   X" + marker}],
        session_id="s-marker-lane", now="2026-08-20T00:00:00Z")

    assert len(seen) == 1, "the v2 pipeline was not reached"
    received = seen[0]
    assert len(received) == 2, "the turn list was renumbered, not blanked"
    assert received[0]["content"] == "a real sentence here."
    assert received[1]["content"] == "", (
        f"a marker-only turn reached the v2 pipeline: {received[1]!r}")


def test_the_clippers_blank_set_is_explicit():
    """#4897 review round 15, P3: blankness is an explicit code-point set.

    ``_clip_capture_turn_content`` used ``not content.strip()`` while the shipped
    TS clipper used ``!content.trim()``, and the two disagree on exactly five code
    points — U+001C-U+001F and U+0085 (Python-stripped, JS-kept) and U+FEFF
    (JS-stripped, Python-kept). The Python clipper now uses
    ``_CAPTURE_BLANK_CHARS``, the union, so no input one side called blank becomes
    non-blank on the other. This pins the Python side natively (so a revert to
    ``.strip()`` reds even where Node is absent); the TS half is pinned to the
    same set by the cross-language parity case in ``tests/test_pi_capture_hooks.py``.
    """
    from tortoise.sdk import (
        _CAPTURE_BLANK_CHARS,
        _CAPTURE_NONBLANK_RE,
        _CAPTURE_TRUNCATION_SENTINEL,
        _CAPTURE_TURN_CAP,
        _clip_capture_turn_content,
    )
    # Both directions of the divergence `str.strip()` got wrong.
    for cp in ("\u001c", "\u001d", "\u001e", "\u001f", "\u0085", "\ufeff"):
        assert cp in _CAPTURE_BLANK_CHARS, hex(ord(cp))
        assert not _CAPTURE_NONBLANK_RE.search(cp * 10), hex(ord(cp))
        out = _clip_capture_turn_content(cp * (_CAPTURE_TURN_CAP + 1))
        assert _CAPTURE_TRUNCATION_SENTINEL not in out, (
            f"a blank retention must not be marked: {out[-60:]!r}")
    # And a NON-blank whitespace-lookalike is still marked.
    assert _CAPTURE_NONBLANK_RE.search("\u200b" * 10)
    out = _clip_capture_turn_content("\u200b" * (_CAPTURE_TURN_CAP + 1))
    assert _CAPTURE_TRUNCATION_SENTINEL in out








# ═══════════════════════════════════════════════════════════════════════════
# #1727 Slice 2 (Task 11) — server-enforced consent + Session.harness +
# idempotency + per-harness receipts (hosted POST /v1/sessions).
#
# The SDK-level tests above exercise capture_session directly (no consent
# gate — the gate is hosted-only). These tests drive the HOSTED endpoint with
# a TestClient against a temp embedded DB (the test_hosted_api pattern: SDK
# init patched so registry + team graphs share one temp DB).
# ═══════════════════════════════════════════════════════════════════════════


_CONSENT_TEAM = {
    "org_id": "team-1727-consent", "tier": "free", "key_id": "k-1727",
    # C5 #2114: C2 owner class (legacy tt_ key) — scope-less key_id dicts
    # 403 the capture gates otherwise.
    "legacy_full_access": True, "max_points": 100000,
    # #4010: the resolved-limits contract carries EVERY resource — sessions is
    # unlimited (explicit None), and a MISSING key is fail-closed.
    "max_sessions": None,
}


def _provision_team(org_id: str) -> None:
    """Create the registry Team node (onboarding state lives on it)."""
    _ha._make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": org_id, "st": "{}"},
    )


@pytest.fixture()
def consent_client(tmp_path, monkeypatch):
    """Hosted TestClient for the session-recording/harness/receipt surface.

    The team starts DEFAULT-ON (session_recording defaults to True via the
    read-time merge — #1927, no consent gate; the fixture does not seed the
    flag). Tests that need the off-switch seed session_recording=False via
    _update_onboarding_state. The Team node is PROVISIONED first — the
    registry state writer is a MATCH...SET (silent no-op without the node),
    mirroring the production provision path.
    TORTOISE_SESSION_LLM_MOCK=1 satisfies the provider gate.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
    # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
    # __init__ → deterministic anchor close → clear overrides at exit.
    # Supersedes the local _patch_sdk_to_temp/_close_keepalive_anchors pair
    # (this fixture was already #1950-canonical — pin + close present; the
    # helper is the single source of truth now).
    with patched_tortoise_sdk(str(tmp_path / "c.db")):
        app.dependency_overrides[get_current_org] = lambda: dict(_CONSENT_TEAM)
        _provision_team(_CONSENT_TEAM["org_id"])
        with verdict_client(app) as tc:
            yield tc


def _opt_in(org_id: str = _CONSENT_TEAM["org_id"], enabled: bool = True):
    _ha._update_onboarding_state(org_id, session_recording=enabled)
    # #1950: self-verify — read back through the same registry path the
    # consent gate uses. A silently-no-op seed would surface as a confusing
    # 403 downstream; fail loud HERE with the actual persisted state.
    readback = _ha._get_onboarding_state(org_id)
    assert readback.get("session_recording") is enabled, (
        f"consent seed not visible to gate read (team={org_id}): {readback}"
    )
    return readback


def _state(org_id: str = _CONSENT_TEAM["org_id"]) -> dict:
    return _ha._get_onboarding_state(org_id)


def _graph(org_id: str = _CONSENT_TEAM["org_id"]):
    return _ha._make_sdk(namespace=org_id)._get_proj()


def _session_count(org_id: str = _CONSENT_TEAM["org_id"]) -> int:
    rows = _graph(org_id).g.query(
        "MATCH (s:Session) RETURN count(s)").result_set
    return int(rows[0][0])


_CONV = [{"role": "user", "content": "we decided to ship the memory capture slice"},
         {"role": "assistant", "content": "agree — the consent gate is the P0"},
         {"role": "user", "content": "ok"}]

_CONV_B = [{"role": "user", "content": "we should open the mobile beta to external testers"},
           {"role": "assistant", "content": "agreed — crash-free sessions are the gate"},
           {"role": "user", "content": "ok"}]


def test_fresh_team_captures_by_default(consent_client):
    """#1927: session_recording defaults to TRUE — a fresh team (never
    toggled) POSTs a session with NO gate and gets a 200 + Session write.
    The read-time default merge provides the flag; no opt-in required."""
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "claude"})
    assert r.status_code == 200, r.text
    assert _session_count() == 1, "default-on team must capture"


def test_hosted_capture_anchors_its_extractedfrom_links(consent_client):
    """#8501 — the hosted lane records the version each captured Point was read
    from, exactly as the SDK lane does (#1727/#4014 byte-parity).

    FAILS BEFORE: the session `:Source` was still the empty-hash stub while
    extraction created the Points, so every `extractedFrom` link carried a NULL
    `sourceVersion` (the hosted half of the same ordering defect) — and a
    revised transcript could never mark those Points stale.
    """
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "claude"})
    assert r.status_code == 200, r.text

    rows = _graph().g.query(
        "MATCH (p:Point)-[r:extractedFrom]->(s:Source) "
        "RETURN p.id, r.sourceVersion, s.contentHash").result_set
    assert rows, "the hosted capture produced no extractedFrom link — guard"
    unanchored = [row for row in rows if not row[1]]
    assert not unanchored, (
        f"hosted capture left Points unanchored (#8501): {unanchored!r}")
    for pid, recorded, current in rows:
        assert current and recorded == current, (
            f"hosted {pid} recorded {recorded!r}, read from {current!r}")


def test_off_switch_stops_capture_409(consent_client):
    """#1927: the off-switch (session_recording=False) STOPS ingestion with
    a clear 409 (NOT the old 403 consent error) — no Session write, no
    receipt, per-harness last-error recorded so the dashboard row shows why."""
    _opt_in(enabled=False)
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "claude"})
    assert r.status_code == 409, r.text
    assert "disabled" in r.json()["detail"]
    assert _session_count() == 0, "off-switch must not write a Session"
    st = _state()
    assert st.get("session_capture_last_error_claude"), \
        "409 must record the per-harness last error"
    assert st.get("session_capture_receipt_claude") is None, \
        "409 must not record a receipt"


def test_opted_200_and_harness_persisted(consent_client):
    """Task 11: opted team → 200; Session.harness persisted graph-side."""
    _opt_in()
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "codex"})
    assert r.status_code == 200, r.text
    rows = _graph().g.query(
        "MATCH (s:Session) RETURN s.id, s.harness").result_set
    assert len(rows) == 1
    assert rows[0][1] == "codex", f"harness not persisted: {rows}"


def test_harness_none_never_erases_stored_value(consent_client):
    """Task 11 (set-only-when-present): a re-POST without harness must NEVER
    erase a stored harness value."""
    _opt_in()
    r1 = consent_client.post("/v1/sessions",
                             json={"conversation": _CONV, "harness": "pi",
                                   "session_id": "s-harness-keep"})
    assert r1.status_code == 200, r1.text
    r2 = consent_client.post("/v1/sessions",
                             json={"conversation": _CONV,
                                   "session_id": "s-harness-keep"})
    assert r2.status_code == 200, r2.text
    rows = _graph().g.query(
        "MATCH (s:Session {id:'s-harness-keep'}) RETURN s.harness").result_set
    assert rows[0][0] == "pi", f"harness erased by harness-less re-POST: {rows}"


def test_invalid_harness_422_opted_team(consent_client):
    """Task 11 (P2): invalid harness on an OPTED team → 422, no write. The
    invalid value must be visible (never a silent drop or a 200)."""
    _opt_in()
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "vim"})
    assert r.status_code == 422, r.text
    assert "harness" in json.dumps(r.json()["detail"]).lower()
    assert _session_count() == 0, "invalid harness must not write"


def test_repost_same_session_id_zero_new(consent_client):
    """Task 11 (T2-P2c): re-POST of the same session_id mints ZERO new
    nodes for Session + turn Points, and extraction is skipped (mode
    'replayed') — the M2/v2 extracted points are not deterministically keyed,
    so they are not in scope."""
    _opt_in()
    payload = {"conversation": _CONV, "session_id": "s-idem-1727",
               "harness": "claude"}
    r1 = consent_client.post("/v1/sessions", json=payload)
    assert r1.status_code == 200, r1.text
    g = _graph()
    s1 = _session_count()
    t1 = g.g.query("MATCH (t:Point {pointKind:'event'}) RETURN count(t)"
                   ).result_set[0][0]
    ev1 = g.g.query("MATCH (e:Event {eventKind:'sessionCaptured'}) "
                    "RETURN count(e)").result_set[0][0]
    r2 = consent_client.post("/v1/sessions", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["extraction_mode"] == "replayed", r2.json()
    assert r2.json()["extracted"] == 0, r2.json()
    assert _session_count() == s1, "Session count changed on re-POST"
    t2 = g.g.query("MATCH (t:Point {pointKind:'event'}) RETURN count(t)"
                   ).result_set[0][0]
    assert t2 == t1, f"turn Points grew on re-POST: {t1} -> {t2}"
    ev2 = g.g.query("MATCH (e:Event {eventKind:'sessionCaptured'}) "
                    "RETURN count(e)").result_set[0][0]
    assert ev2 == ev1, f"sessionCaptured Events grew on re-POST: {ev1} -> {ev2}"
    # Exactly one Session node — convergence (T1-P3).
    assert _session_count() == 1


def test_receipt_requires_durable_data(consent_client):
    """Task 11 (T1-P12): the receipt is set ONLY on a 2xx — the data is
    durable at that point. A receipt-PATCH failure must never report a
    receipt; the retry with the same session_id converges to ONE Session."""
    _opt_in()
    calls = {"receipt_writes": 0}

    real_update = _ha._update_onboarding_state

    def failing_update(org_id, **fields):
        if any(k.startswith("session_capture_receipt") for k in fields):
            calls["receipt_writes"] += 1
            raise RuntimeError("simulated receipt PATCH failure")
        return real_update(org_id, **fields)

    import tortoise.hosted_api as ha_mod
    ha_mod._update_onboarding_state = failing_update
    try:
        r1 = consent_client.post("/v1/sessions",
                                 json={"conversation": _CONV,
                                       "session_id": "s-receipt-1727"})
        # The capture itself 200s (the mutation happened); the receipt write
        # failed — the response still carries the session.
        assert r1.status_code == 200, r1.text
        assert calls["receipt_writes"] == 1
    finally:
        ha_mod._update_onboarding_state = real_update
    # Retry with the same session_id → converges to exactly one Session.
    r2 = consent_client.post("/v1/sessions",
                             json={"conversation": _CONV,
                                   "session_id": "s-receipt-1727"})
    assert r2.status_code == 200, r2.text
    assert _session_count() == 1, "receipt-failure retry must converge"
    assert _state().get("session_capture_receipt_claude") is None
    assert _state().get("session_capture_receipt") is not None, \
        "bare receipt written on the converged 2xx (harness-less retry)"


def test_receipt_2xx_only_and_last_error_lifecycle(consent_client):
    """Task 11 (T1-P12 + cycle-4 P1-2): receipt set ONLY on 2xx; per-harness
    last-error set on non-2xx and CLEARED on 2xx.

    #4188: the non-2xx trigger is the empty-conversation 422 — the old
    no-provider trigger is now a 2xx (the capture is STORED and only
    extraction is skipped)."""
    _opt_in()
    # non-2xx: empty conversation → 422 → last_error set, no receipt
    r = consent_client.post("/v1/sessions",
                            json={"conversation": [], "harness": "claude"})
    assert r.status_code == 422, r.text
    st = _state()
    assert st.get("session_capture_last_error_claude"), \
        "non-2xx must set session_capture_last_error_claude"
    assert st.get("session_capture_receipt_claude") is None, \
        "no receipt on a non-2xx"
    # 2xx: a real conversation → receipt set, last_error cleared
    r2 = consent_client.post("/v1/sessions",
                             json={"conversation": _CONV, "harness": "claude"})
    assert r2.status_code == 200, r2.text
    st2 = _state()
    assert st2.get("session_capture_receipt_claude"), \
        "2xx must set the per-harness receipt"
    assert st2.get("session_capture_last_error_claude") is None, \
        "2xx must clear the per-harness last error"


def test_off_switch_keeps_existing_sessions(consent_client):
    """#1927: after the off-switch (session_recording=False), POST → 409
    while EXISTING Sessions stay untouched — and the blocked POST uses a
    FRESH session_id so an errant write would surface (count grows)."""
    _opt_in()
    r1 = consent_client.post("/v1/sessions",
                             json={"conversation": _CONV,
                                   "session_id": "s-decline-1727"})
    assert r1.status_code == 200, r1.text
    assert _session_count() == 1
    # off-switch (toggle-off writes the same key)
    _opt_in(enabled=False)
    r2 = consent_client.post("/v1/sessions",
                             json={"conversation": _CONV,
                                   "session_id": "s-off-1927-fresh"})
    assert r2.status_code == 409, r2.text
    assert "disabled" in r2.json()["detail"]
    # existing Sessions untouched AND no new node from the blocked POST
    assert _session_count() == 1, \
        "off-switch must never remove already-captured sessions nor write"


def test_off_switch_409_first_before_provider_gate(
        consent_client, no_provider_keys):
    """#1927 (review P2): the 409 opt-out check is FIRST in the gate stack —
    a disabled team with NO provider key gets 409, never the stored keyless
    capture path. ``no_provider_keys`` (#3695) is the whole gate key set, so
    the 409 cannot be satisfied by an ambient provider key."""
    _opt_in(enabled=False)
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "claude"})
    assert r.status_code == 409, r.text
    assert "disabled" in r.json()["detail"]


def test_off_switch_409_first_before_quota_gate(consent_client):
    """#1927 (review P2): a disabled team OVER quota gets 409, not the
    quota 402 — the opt-out check precedes the points-estimate gate."""
    _opt_in(enabled=False)
    # push the estimate astronomically high so any quota gate would 402
    orig = _ha._session_extraction_estimate
    _ha._session_extraction_estimate = lambda w: 10**9
    try:
        r = consent_client.post("/v1/sessions",
                                json={"conversation": _CONV, "harness": "claude"})
    finally:
        _ha._session_extraction_estimate = orig
    assert r.status_code == 409, r.text
    assert "disabled" in r.json()["detail"]


def test_legacy_true_team_captures_200(consent_client):
    """#1927: a team with session_recording=True (the previous consent flag
    — now the default) captures fine; the flag still reads as the off-switch."""
    _opt_in(enabled=True)  # same flag a legacy team would carry
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV})
    assert r.status_code == 200, r.text
    assert _session_count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# #1727 Slice 2 (Task 12) — session → entity linking (aboutObject).
# ═══════════════════════════════════════════════════════════════════════════

def _object(proj, oid: str, name: str):
    proj.apply({"type": "ObjectRegistered", "id": oid, "name": name,
                "object_kind": "pm:issue", "title": name})


def _link_edges(proj, label: str, sid: str) -> set:
    rows = proj.g.query(
        f"MATCH (s:{label} {{id:$sid}})-[:aboutObject]->(o:Object) "
        "RETURN o.id", params={"sid": sid}).result_set
    return {r[0] for r in rows}


def test_session_links_full_url_form(consent_client):
    """Task 12: github.com/{org}/{repo}/issues/{n} in the conversation links
    the Session + the matching turn Point via aboutObject; counters tracked."""
    from tortoise.session_link import extract_refs
    _opt_in()
    proj = _graph()
    _object(proj, "github-issue-test/repo-42", "test/repo#42")
    conv = [{"role": "user",
             "content": "we should fix github.com/test/repo/issues/42 first"}]
    r = consent_client.post("/v1/sessions",
                            json={"conversation": conv, "harness": "claude",
                                  "session_id": "s-link-url"})
    assert r.status_code == 200, r.text
    # Session: all-matches (1 target). The turn Point that mentioned it
    # carries the first-match link.
    assert _link_edges(proj, "Session", "s-link-url") == {"github-issue-test/repo-42"}
    rows = proj.g.query(
        "MATCH (s:Session {id:'s-link-url'}) "
        "RETURN s.entity_links_attempted, s.entity_links_created").result_set
    assert rows[0][0] >= 1, f"attempted not tracked: {rows}"
    assert rows[0][1] >= 1, f"created not tracked: {rows}"
    # The trigger regex itself is pinned (unit-level).
    refs = extract_refs("see github.com/acme/web/issues/7 now")
    assert refs == [{"org": "acme", "repo": "web", "num": "7", "form": "url"}]


def test_session_links_repo_num_and_bare_num_forms(consent_client):
    """Task 12: {repo}#{n} (name-suffix) and bare #n (guarded) forms link;
    the bare-#n false-positive guard rejects C#42 / v#42 / dir/42."""
    from tortoise.session_link import extract_refs
    _opt_in()
    proj = _graph()
    _object(proj, "github-issue-acme/tortoise-12", "acme/tortoise#12")
    _object(proj, "github-issue-acme/other-7", "acme/other#7")
    conv = [{"role": "assistant",
             "content": "tortoise#12 is the blocker; other#7 too"}]
    r = consent_client.post("/v1/sessions",
                            json={"conversation": conv, "harness": "claude",
                                  "session_id": "s-link-repo"})
    assert r.status_code == 200, r.text
    assert _link_edges(proj, "Session", "s-link-repo") == {
        "github-issue-acme/tortoise-12", "github-issue-acme/other-7"}
    # Guard: the bare-#n false-positive guard rejects #n preceded by alnum
    # or slash — `docs/#42` never matches, and inside `C#42` the bare form
    # does NOT fire (only the {repo}#{n} form, which is legitimately
    # ambiguous for single-letter repos — resolution still no-ops without a
    # matching Object).
    assert extract_refs("docs/#42") == []
    assert all(r["form"] != "bare_num" for r in extract_refs("C#42"))
    assert extract_refs("C#42 is a language") == [
        {"repo": "C", "num": "42", "form": "repo_num"}]
    # Guarded bare #n (whitespace/start-of-line preceded) matches.
    refs = extract_refs("fix #42 now")
    assert any(r["form"] == "bare_num" and r["num"] == "42" for r in refs)


def test_session_links_cross_org_ambiguous_no_link(consent_client):
    """Review PR #1827: an org-ambiguous ref (#n / {repo}#{n}) links ONLY
    when EXACTLY ONE Object matches — the same bare #42 or tortoise#12 can
    exist in every org, so multiple matches are an honest no-match (never a
    fabricated aboutObject edge)."""
    _opt_in()
    proj = _graph()
    _object(proj, "github-issue-acme/tortoise-12", "acme/tortoise#12")
    _object(proj, "github-issue-other/tortoise-12", "other/tortoise#12")
    conv = [{"role": "user",
             "content": "tortoise#12 is in two orgs — see also #12"}]
    r = consent_client.post("/v1/sessions",
                            json={"conversation": conv, "harness": "claude",
                                  "session_id": "s-link-ambig"})
    assert r.status_code == 200, r.text
    assert _link_edges(proj, "Session", "s-link-ambig") == set(), \
        "ambiguous suffix must not fabricate aboutObject edges"


def test_session_links_first_match_per_point_all_for_session(consent_client):
    """Task 12: the SESSION links all matches; each turn POINT links only its
    FIRST match (pinned trigger rule)."""
    _opt_in()
    proj = _graph()
    _object(proj, "github-issue-test/repo-1", "test/repo#1")
    _object(proj, "github-issue-test/repo-2", "test/repo#2")
    conv = [{"role": "user",
             "content": "test/repo#1 and test/repo#2 are both in flight"}]
    r = consent_client.post("/v1/sessions",
                            json={"conversation": conv, "harness": "claude",
                                  "session_id": "s-link-first"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert _link_edges(proj, "Session", sid) == {
        "github-issue-test/repo-1", "github-issue-test/repo-2"}
    # The single turn point links only the FIRST ref (#1).
    turn_edges = _link_edges(proj, "Point", f"{sid}_t0")
    assert turn_edges == {"github-issue-test/repo-1"}, turn_edges


def test_session_links_no_match_honest(consent_client):
    """Task 12: no references in the conversation ⇒ no links, no counters,
    no error — honest no-match (nothing fabricated)."""
    _opt_in()
    proj = _graph()
    conv = [{"role": "user", "content": "plain chit-chat with no refs"}]
    r = consent_client.post("/v1/sessions",
                            json={"conversation": conv, "harness": "claude",
                                  "session_id": "s-link-none"})
    assert r.status_code == 200, r.text
    assert _link_edges(proj, "Session", "s-link-none") == set()
    rows = proj.g.query(
        "MATCH (s:Session {id:'s-link-none'}) "
        "RETURN s.entity_links_attempted, s.entity_links_created").result_set
    assert rows[0][0] is None and rows[0][1] is None, \
        f"counters must stay unset on no-match: {rows}"


def test_session_links_resolve_after_index(consent_client):
    """Task 12 (T1-P15): a session captured BEFORE the entity existed carries
    no link; the index-completion re-link pass resolves it (resolve-to-current
    by stable Object id)."""
    _opt_in()
    proj = _graph()
    conv = [{"role": "user",
             "content": "ship github.com/test/repo/issues/99 this week"}]
    r = consent_client.post("/v1/sessions",
                            json={"conversation": conv, "harness": "claude",
                                  "session_id": "s-link-late"})
    assert r.status_code == 200, r.text
    assert _link_edges(proj, "Session", "s-link-late") == set(), \
        "no entity yet — honest no-match at capture time"
    # Index lands → entity materializes → re-link resolves.
    _object(proj, "github-issue-test/repo-99", "test/repo#99")
    _ha._relink_sessions_after_index(_CONSENT_TEAM["org_id"])
    assert _link_edges(proj, "Session", "s-link-late") == \
        {"github-issue-test/repo-99"}, "re-link on index completion must resolve"
    rows = proj.g.query(
        "MATCH (s:Session {id:'s-link-late'}) "
        "RETURN s.entity_links_attempted, s.entity_links_created").result_set
    assert rows[0][1] >= 1, f"counters updated after re-link: {rows}"


# ═══════════════════════════════════════════════════════════════════════════
# #1727 Slice 2 (Task 13) — tortoise_session_capture MCP tool.
# ═══════════════════════════════════════════════════════════════════════════

def _mcp_team_context(tmp_path, monkeypatch, *, org_id="team-1727-mcp",
                       seed_recording: bool = True):
    """Set the MCP auth ContextVars (hosted-tenant shape) + provision the
    team, so the tool's hosted pipeline runs against the temp DB.
    ``seed_recording`` controls whether the session_recording flag is
    explicitly seeded (True = the default for the consent-era tests; False =
    provision-only so the test exercises the read-time default-ON merge —
    #1927)."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        from tortoise.mcp_auth import _current_org_id, _current_org_limits, _transport_mode
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        # #2127 wave 2: shared helper — the old enter/exit plain-clear (no
        # pin, no anchor close) is the #1950 clear-without-close gap this
        # wave fixes; the helper adds the pin + close-then-clear at enter and
        # the deterministic close at exit. The MCP ContextVar set/reset stays
        # fixture-owned INSIDE the helper (they are per-call tokens, not SDK
        # state).
        with patched_tortoise_sdk(str(tmp_path / "mcp.db")):
            _provision_team(org_id)
            if seed_recording:
                _ha._update_onboarding_state(org_id, session_recording=True)
            tok_t = _current_org_id.set(org_id)
            tok_l = _current_org_limits.set(
                {"org_id": org_id, "tier": "free", "max_points": 100000,
                 "max_sessions": None})
            tok_m = _transport_mode.set("http")
            try:
                yield org_id
            finally:
                _current_org_id.reset(tok_t)
                _current_org_limits.reset(tok_l)
                _transport_mode.reset(tok_m)

    return _ctx()


def test_session_capture_tool_registered_and_invokeable(tmp_path, monkeypatch):
    """Task 13: the tool is registered in the registry AND invokeable with
    TORTOISE_SESSION_LLM_MOCK=1 — a real capture through the MCP surface
    (Session + receipt)."""
    from tortoise.mcp_server import tortoise_session_capture
    with _mcp_team_context(tmp_path, monkeypatch):
        result = tortoise_session_capture(
            conversation=_CONV, harness="claude", session_id="s-mcp-1727")
        st = _ha._get_onboarding_state("team-1727-mcp")
    assert result.get("session_id") == "s-mcp-1727", result
    # W5 (#2104): the memory_write_v1 envelope ALWAYS carries an error key
    # (None on success) — assert the null value, not key absence.
    assert not result.get("error"), result
    assert result.get("turns") == len(_CONV)
    assert st.get("session_capture_receipt_claude"), \
        "MCP capture must set the per-harness receipt"


def test_session_capture_tool_fresh_team_captures(tmp_path, monkeypatch):
    """#1927: the MCP tool has NO consent gate — a fresh (default-on,
    provision-only — NO explicit flag seed) team captures through the MCP
    surface (Session + receipt), proving the read-time default merge."""
    from tortoise.mcp_server import tortoise_session_capture
    with _mcp_team_context(tmp_path, monkeypatch, seed_recording=False):
        result = tortoise_session_capture(
            conversation=_CONV, harness="claude", session_id="s-mcp-1927-default")
        st = _ha._get_onboarding_state("team-1727-mcp")
    assert st.get("session_recording") is True, "read-time default must be ON"
    assert result.get("session_id") == "s-mcp-1927-default", result
    # W5 (#2104): memory_write_v1 envelope always carries error (None on
    # success) — assert the null value, not key absence.
    assert not result.get("error"), result
    assert result.get("turns") == len(_CONV)
    assert st.get("session_capture_receipt_claude"), \
        "MCP capture must set the per-harness receipt"


def test_session_capture_tool_off_switch_409(tmp_path, monkeypatch):
    """Task 13 + #1927: the MCP tool carries the SAME off-switch as the REST
    path — a team with recording disabled gets the clear 409-style error
    (stops ingestion), never a silent capture or the old 403."""
    from tortoise.mcp_auth import _current_org_id, _current_org_limits
    from tortoise.mcp_server import tortoise_session_capture
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # #2127 wave 2: shared helper (same pin + deterministic-close upgrade
    # as _mcp_team_context — the old inline restore was plain-clear).
    with patched_tortoise_sdk(str(tmp_path / "mcp-opt.db")):
        _provision_team("team-1727-mcp-opt")
        _ha._update_onboarding_state("team-1727-mcp-opt",
                                     session_recording=False)
        tok_t = _current_org_id.set("team-1727-mcp-opt")
        tok_l = _current_org_limits.set(
            {"org_id": "team-1727-mcp-opt", "tier": "free",
             "max_points": 100000, "max_sessions": None})
        try:
            result = tortoise_session_capture(conversation=_CONV, harness="pi")
            st = _ha._get_onboarding_state("team-1727-mcp-opt")
        finally:
            _current_org_id.reset(tok_t)
            _current_org_limits.reset(tok_l)
    assert result.get("status") == 409, result
    assert "disabled" in result.get("error", ""), result
    assert st.get("session_capture_last_error_pi"), \
        "off-switch MCP attempt must record the per-harness last error"
    assert st.get("session_capture_receipt_pi") is None


def test_mcp_failed_recapture_names_the_stored_harness(tmp_path, monkeypatch):
    """#4898: the MCP capture's failure path must attribute
    ``session_capture_last_error_<harness>`` to the SESSION's stored harness,
    never to the tool's raw ``harness`` argument — the same stored-or-claimed
    resolution (``_observed_capture_harness``) the REST capture applies to both
    per-harness keys (#3681 / #3700).

    The forged replay below is the #3681 shape, one key down: a ``claude``
    session re-captured with ``harness='cursor'`` and a FAILING payload. Before
    the fix the error is written under the CALLER's declaration
    (``session_capture_last_error_cursor``), so the dashboard paints the
    failure on the cursor row — a harness the server's own Session record
    contradicts.

    RED mutation: restore ``_record_capture_last_error(org_id, harness, ...)``
    (the raw tool argument) → the key becomes ``..._cursor`` → the stored-
    harness assertion fails. GREEN: the key names the stored ``claude`` and no
    cursor key is written.
    """
    from tortoise.mcp_server import tortoise_session_capture
    with _mcp_team_context(tmp_path, monkeypatch):
        # 1) a successful capture stamps the Session's stored harness = claude
        first = tortoise_session_capture(
            conversation=_CONV, harness="claude", session_id="s-4898")
        assert not first.get("error"), first
        # 2) forged replay: SAME session_id, a DIFFERENT caller harness, and a
        # payload that FAILS at the empty-transcript 422 gate (the error path)
        failed = tortoise_session_capture(
            conversation=[], harness="cursor", session_id="s-4898")
        st = _ha._get_onboarding_state("team-1727-mcp")
    assert failed.get("status") == 422, failed
    # the registered key set is always present (None-valued when unset), so
    # read the SET keys — exactly one: the Session's stored harness.
    error_keys = {k for k, v in st.items()
                  if k.startswith("session_capture_last_error_") and v}
    assert error_keys == {"session_capture_last_error_claude"}, (
        f"a forged caller harness named the last-error key: {sorted(error_keys)}")
    assert st.get("session_capture_last_error_cursor") is None, (
        "the caller's declared harness claimed the last-error key")


def test_mcp_capture_missing_max_sessions_fails_closed(tmp_path, monkeypatch):
    """#4010: the capture bridge carries `max_sessions` only when it is
    actually PRESENT, so a keyless limits dict reaches the sessions gate and
    fails closed (#310 GAP-B) rather than being normalized to unlimited.

    This is the degraded `mcp_auth` shape — `{"org_id": ...}` after a
    registry resolution failure. Mutation this REDs:
    `org["max_sessions"] = limits.get("max_sessions")`, which would turn a
    failed resolution into a SUCCESSFUL unlimited capture — the exact
    fail-open class #4010 removes, and it would make MCP succeed where REST
    returns 500 for the same dict.
    """
    from tortoise.mcp_auth import _current_org_id, _current_org_limits
    from tortoise.mcp_server import tortoise_session_capture
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with patched_tortoise_sdk(str(tmp_path / "mcp-keyshape.db")):
        _provision_team("team-1727-keyshape")
        tok_t = _current_org_id.set("team-1727-keyshape")
        # `max_sessions` deliberately ABSENT — not set to None.
        tok_l = _current_org_limits.set(
            {"org_id": "team-1727-keyshape", "tier": "free",
             "max_points": 100000})
        try:
            result = tortoise_session_capture(
                conversation=_CONV, harness="pi", session_id="s-keyshape")
        finally:
            _current_org_id.reset(tok_t)
            _current_org_limits.reset(tok_l)
    assert result.get("status") == 500, result
    assert "max_sessions" in str(result.get("error", "")), result


def test_session_capture_tool_stdio_honest_error(tmp_path, monkeypatch):
    """Task 13: stdio (no team context / selfhost) → honest 'requires hosted
    mode' error — no local fallback that bypasses the gates."""
    from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id
    from tortoise.mcp_server import tortoise_session_capture
    tok = _current_org_id.set(SELFHOST_ORG_ID)
    try:
        result = tortoise_session_capture(conversation=_CONV, harness="claude")
    finally:
        _current_org_id.reset(tok)
    assert "error" in result, result
    assert "hosted mode" in result["error"], result


# ── #4288: the per-org capture_extract setting on the AGENT-FACING surface ───
# #4258 landed the setting on the hosted REST route + the dashboard. #4288
# tracks the MCP/SDK surface. The WRITE half (a tool parameter or a state-PATCH
# tool) is owner-gated — a frozen-baseline re-cut, per #4288 and the #4282
# mandate — so it is NOT implemented here and NOT asserted. What these two
# tests pin is the non-gated half that was previously UNPROVEN: the MCP capture
# tool already HONOURS the setting (OFF = store, never refuse), and the setting
# is already READABLE through the existing agent-facing onboarding tool.

def test_session_capture_tool_honours_capture_extract_off_4288(tmp_path, monkeypatch):
    """#4288: with the per-org setting OFF (#4258, owner ruling on #3892), a
    capture through the MCP tool still STORES the session and its turns — never
    a refusal — and skips extraction, reporting that under its OWN
    ``extraction-disabled`` mode.

    WHY THIS IS NOT REDUNDANT WITH #4258's TESTS: those drive the REST route
    (``client.post("/v1/sessions")``) only. The MCP tool is a SEPARATE served
    surface — its own slot reservation, offload hand-off and receipt plumbing —
    and it is the surface agents actually capture through. An MCP-lane
    regression that extracted anyway would silently contradict the user's
    explicit opt-out on the path they use, and no existing test would catch it.

    The provider IS configured (the mock seam), so the user setting is the ONLY
    reason extraction is skipped — this is the guard against routing the OFF
    state through the keyless ``no-provider`` reason.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.mcp_server import tortoise_session_capture

    org = "team-4288-mcp"
    sid = "s-4288-extract-off"
    marker = "captureextractoffmcpzzq"
    with _mcp_team_context(tmp_path, monkeypatch, org_id=org):
        assert ha_mod._llm_provider_available() is True
        ha_mod._update_onboarding_state(org, capture_extract=False)

        result = tortoise_session_capture(
            conversation=[{"role": "user", "content": (
                f"we decided the {marker} session stores but does not extract")}],
            harness="pi", session_id=sid)

        # (1) never refused; the receipt is truthful under its OWN mode — not
        # folded into "llm" (a false claim) or "no-provider" (a false reason).
        assert not result.get("error"), result
        assert result["extraction_mode"] == "extraction-disabled", result
        assert result["extracted"] == 0, result
        assert result["turns"] == 1, result
        warnings = " ".join(result["warnings"])
        assert "capture_extract" in warnings, warnings
        assert "STORED" in warnings, warnings

        # (2) the capture LANDED: the Session and its turn Point exist …
        proj = ha_mod._make_sdk(namespace=org)._get_proj()
        assert proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) RETURN count(t)",
            params={"sid": sid}).result_set[0][0] == 1
        # … and NO extracted (non-episodic) point was minted.
        assert proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
            "WHERE p.is_episodic IS NULL OR p.is_episodic = false RETURN count(p)",
            params={"sid": sid}).result_set[0][0] == 0

        # (3) the durable lane separates the USER setting ("disabled") from the
        # transient keyless reason ("none", #4258), so a later re-capture with
        # the setting back ON re-attempts extraction (#2335 TRUE retry).
        row = proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.capture_extractor",
            params={"sid": sid}).result_set[0]
        assert row[0] is False and row[1] == "disabled", row

        # (4) the MCP surface wrote its own per-harness receipt — the
        # agent-facing evidence the dashboard row reads.
        st = ha_mod._get_onboarding_state(org)
        assert st.get("session_capture_receipt_pi"), st


def test_onboarding_state_tool_reads_capture_extract_4288(tmp_path, monkeypatch):
    """#4288, READ half: the setting is ALREADY readable on the agent-facing
    surface — ``tortoise_onboarding_state`` (the ``onboarding`` family, bound to
    GET /v1/onboarding/state) returns ``capture_extract`` for the org.

    This pins the half of #4288's outcome that needs NO frozen-baseline re-cut:
    an agent can already discover whether this org's captures will be extracted,
    before it decides how to report a capture's outcome. The WRITE half (a
    parameter on the capture tool, or a state-PATCH tool) is the owner-gated
    remainder tracked on #4288.

    Polarity (#4258): an older stored state with the key ABSENT must read ON,
    never OFF — so the first read happens on a team whose stored state is ``{}``.
    """
    from tortoise.mcp_server import tortoise_onboarding_state

    with _mcp_team_context(tmp_path, monkeypatch, org_id="team-4288-read",
                           seed_recording=False) as org:
        # a team provisioned BEFORE the setting existed (stored state ``{}``)
        default_state = tortoise_onboarding_state()
        assert default_state.get("capture_extract") is True, default_state

        _ha._update_onboarding_state(org, capture_extract=False)
        off_state = tortoise_onboarding_state()
        assert off_state.get("capture_extract") is False, off_state


# ═══════════════════════════════════════════════════════════════════════════
# #2600 (Phase 1, Task 2) — Session.actor_user_id stamp (MCP-lane + SDK
# mirror). The capture tool calls the impl DIRECTLY in-thread (no HTTP
# portal), so the sdk._current_actor_user_id ContextVar set here is visible
# to the impl's ``_merge_sets`` actor clause — the MCP middleware sets the
# SAME var (mcp_auth.TeamResolutionMiddleware), so the real-auth path and
# this seam are byte-identical on the var. The hosted REST stamp is covered
# by the real-auth-face E2E tests (Task 6) — the DI-override fixture is
# NEVER used for E2E-5 (#2600 constraint), and the REST impl reads the
# actor from the RESOLVED team dict (registry/session branches alias it),
# not from a test-set var.

def _session_actor(org_id: str, session_id: str):
    rows = _ha._make_sdk(namespace=org_id)._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.actor_user_id",
        params={"sid": session_id},
    ).result_set
    return rows[0][0] if rows and rows[0] else None


_ACTOR_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ACTOR_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_mcp_capture_stamps_resolved_actor(tmp_path, monkeypatch):
    """#2600 Task 2 (MCP lane): a capture through tortoise_session_capture
    with the middleware actor var set writes Session.actor_user_id — the
    coalesce clause fires (fresh session, actor present) and the graph node
    carries the canonical UUID."""
    from tortoise.sdk import _current_actor_user_id
    with _mcp_team_context(tmp_path, monkeypatch):
        from tortoise.mcp_server import tortoise_session_capture
        tok = _current_actor_user_id.set(_ACTOR_A)
        try:
            result = tortoise_session_capture(
                conversation=_CONV, harness="pi", session_id="s-mcp-2600-a")
            stamped = _session_actor("team-1727-mcp", "s-mcp-2600-a")
        finally:
            _current_actor_user_id.reset(tok)
    assert result.get("session_id") == "s-mcp-2600-a", result
    assert not result.get("error"), result
    assert stamped == _ACTOR_A, f"actor stamp missing: {stamped}"


def test_mcp_capture_actor_first_writer_wins_on_repost(tmp_path, monkeypatch):
    """#2600 Task 2 (MCP lane, coalesce): a re-POST of the SAME session_id
    by a DIFFERENT actor never overwrites — Session.actor_user_id keeps the
    FIRST writer (fresh-capture mint + idempotent MERGE no-op)."""
    from tortoise.sdk import _current_actor_user_id
    with _mcp_team_context(tmp_path, monkeypatch):
        from tortoise.mcp_server import tortoise_session_capture
        tok = _current_actor_user_id.set(_ACTOR_A)
        try:
            r1 = tortoise_session_capture(
                conversation=_CONV, harness="claude",
                session_id="s-mcp-2600-fww")
            assert not r1.get("error"), r1
            _current_actor_user_id.reset(tok)
            tok = _current_actor_user_id.set(_ACTOR_B)
            r2 = tortoise_session_capture(
                conversation=_CONV, harness="claude",
                session_id="s-mcp-2600-fww")
            assert not r2.get("error"), r2
            stamped = _session_actor("team-1727-mcp", "s-mcp-2600-fww")
        finally:
            _current_actor_user_id.reset(tok)
    assert stamped == _ACTOR_A, \
        "re-POST by a different actor must NOT overwrite the first writer"


def test_mcp_capture_no_actor_leaves_session_unattributed(tmp_path, monkeypatch):
    """#2600 Task 2 (MCP lane, negative): actor var UNSET (embedded/
    self-host-shaped captures have no server-resolved human) → the Session
    node is written WITHOUT actor_user_id (byte-identical legacy shape — no
    clause fires, no property present)."""
    with _mcp_team_context(tmp_path, monkeypatch):
        from tortoise.mcp_server import tortoise_session_capture
        result = tortoise_session_capture(
            conversation=_CONV, harness="claude", session_id="s-mcp-2600-none")
        # Read INSIDE the patched-SDK context (the TORTOISE_DB_PATH pin is
        # popped at exit — a post-exit _make_sdk resolves a different DB).
        stamped = _session_actor("team-1727-mcp", "s-mcp-2600-none")
    assert not result.get("error"), result
    # Assert PROPERTY ABSENCE, not None-value (a property key set to null
    # would be the drift this test exists to catch).
    assert stamped is None






# ═══════════════════════════════════════════════════════════════════════════
# W5 Phase E (#2104, S11) — ingestion-toggle disclosure marker data.
# The S11 409 gate / per-harness last-error / toggle read-write contract were
# shipped by #1927 + W5 Phase A/B and are covered above — Phase E ships ONLY
# the missing disclosure marker DATA (`surfaced`, §3.2.2 vocabulary) on the
# capture receipt + a REST+MCP single-enforcement drift-proof test.

def _graph_point_ids(proj) -> set:
    rows = proj.g.query(
        "MATCH (n:Point) RETURN n.id").result_set
    return {r[0] for r in rows}


def test_phase_e_surfaced_disclosure_marker_graph_truth(consent_client):
    """S11 disclosure marker data: a fresh capture's write-verb receipt
    carries ``surfaced`` — one entry per memory item THIS capture added
    (N = len = the disclosure count). Graph-truth only (anti-gaming): every
    entry's point_id is a minted claim the graph actually holds post-write
    (verified via the enrichment facts), every label is a deterministic
    content-derived label, and a folded (content_hash_hit) claim never
    appears."""
    from tortoise.write_verb import DEDUP_NEW
    _opt_in(enabled=True)
    r = consent_client.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "claude",
                                  "session_id": "s-phase-e-marker"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["protocol_version"] == "memory_write_v1", \
        "the disclosure marker rides the frozen write verb"
    assert "surfaced" in body, body.keys()
    points = body["points"]
    assert points, "fresh capture must mint claims"
    proj = _graph()
    g_ids = _graph_point_ids(proj)
    by_id = {p["id"]: p for p in points}
    surfaced = body["surfaced"]
    assert surfaced, "a fresh minted capture must expose marker data"
    assert len(surfaced) == len([p for p in points
                                 if p.get("dedup", DEDUP_NEW) == DEDUP_NEW]), (
        "N = len(surfaced) must equal the graph-verified minted count")
    for entry in surfaced:
        assert entry["point_id"] in by_id, \
            f"surfaced entry must name a point in this capture: {entry}"
        assert entry["point_id"] in g_ids, \
            f"surfaced entry must be graph-verified (present post-write): {entry}"
        assert by_id[entry["point_id"]].get("dedup", DEDUP_NEW) == DEDUP_NEW, \
            f"a folded claim must never be counted as added: {entry}"
        assert entry["label"], entry
        assert len(entry["label"]) <= 48, entry


def test_phase_e_surfaced_empty_on_replay(consent_client):
    """S11 disclosure marker data: a replay (re-POST same session_id —
    extraction skipped, 0 new nodes) reports ``surfaced: []`` — nothing was
    added, never a fabricated count."""
    _opt_in(enabled=True)
    payload = {"conversation": _CONV, "harness": "claude",
               "session_id": "s-phase-e-replay"}
    r1 = consent_client.post("/v1/sessions", json=payload)
    assert r1.status_code == 200, r1.text
    assert r1.json()["surfaced"], "first capture mints items"
    r2 = consent_client.post("/v1/sessions", json=payload)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["extraction_mode"] == "replayed", b2
    assert b2["extracted"] == 0, b2
    assert b2["surfaced"] == [], \
        "a replay adds zero memory items — surfaced must be empty"






def test_phase_e_rest_mcp_same_flag_drift_proof(tmp_path, monkeypatch):
    """S11 'can never drift' — ONE team, ONE flag state, both consumer
    surfaces through the SAME shared ``_capture_session_impl``: recording OFF
    ⇒ REST POST → 409 AND MCP tortoise_session_capture → {status: 409} with
    the SAME detail text; neither writes a Session; each harness's per-harness
    last-error is recorded. Recording ON ⇒ both 2xx with ``surfaced`` marker
    data + per-harness receipts."""
    from tortoise.hosted_api import get_current_org as _get_current_team
    from tortoise.mcp_auth import (
        _current_org_id,
        _current_org_limits,
        _transport_mode,
    )
    from tortoise.mcp_server import tortoise_session_capture

    org_id = "team-phase-e-drift"
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # m2 lane: claims are echo-derived from each conversation's OWN content,
    # so the REST and MCP captures below mint DISTINCT claims (the v2 mock is
    # fully deterministic — any two captures would content-hash-fold onto the
    # same canonical, which would make the MCP leg honestly report
    # surfaced: [] and prove nothing about marker data on the MCP path).
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    with patched_tortoise_sdk(str(tmp_path / "drift.db")):
        _provision_team(org_id)
        _opt_in(org_id, enabled=False)  # OFF first
        team = {"org_id": org_id, "tier": "free", "key_id": "k-1727",
                "legacy_full_access": True, "max_points": 100000,
                "max_sessions": None}
        app.dependency_overrides[_get_current_team] = lambda: dict(team)
        tok_t = _current_org_id.set(org_id)
        tok_l = _current_org_limits.set(
            {"org_id": org_id, "tier": "free", "max_points": 100000,
             "max_sessions": None})
        tok_m = _transport_mode.set("http")
        try:
            with verdict_client(app) as tc:
                # OFF: both surfaces reject with the SAME 409 state-conflict.
                r = tc.post("/v1/sessions",
                            json={"conversation": _CONV, "harness": "claude"})
                assert r.status_code == 409, r.text
                rest_detail = r.json()["detail"]
                mcp_res = tortoise_session_capture(
                    conversation=_CONV, harness="pi")
                assert mcp_res.get("status") == 409, mcp_res
                assert rest_detail == mcp_res.get("error"), (
                    "REST + MCP must surface the SAME recording-off message "
                    f"(shared impl): REST={rest_detail!r} MCP={mcp_res!r}")
                assert "disabled" in rest_detail
                assert _session_count(org_id) == 0, \
                    "recording OFF must not write a Session on either surface"
                st = _state(org_id)
                assert st.get("session_capture_last_error_claude"), \
                    "REST 409 must record its per-harness last error"
                assert st.get("session_capture_last_error_pi"), \
                    "MCP 409 must record its per-harness last error"
                assert st.get("session_capture_receipt_claude") is None
                assert st.get("session_capture_receipt_pi") is None
                # ON: both surfaces capture with the disclosure marker.  The
                # MCP capture uses DIFFERENT content so it genuinely mints
                # (a same-content re-capture would be a content-hash fold and
                # honestly report surfaced: [] — that anti-gaming is pinned in
                # test_phase_e_surfaced_cross_session_reingest_counts_zero_added).
                _opt_in(org_id, enabled=True)
                r2 = tc.post("/v1/sessions",
                             json={"conversation": _CONV, "harness": "claude",
                                   "session_id": "s-drift-rest"})
                assert r2.status_code == 200, r2.text
                assert r2.json()["surfaced"], r2.json()
                mcp_res2 = tortoise_session_capture(
                    conversation=_CONV_B, harness="pi",
                    session_id="s-drift-mcp")
                assert not mcp_res2.get("error"), mcp_res2
                assert mcp_res2.get("surfaced"), mcp_res2
                assert mcp_res2.get("protocol_version") == "memory_write_v1"
                st2 = _state(org_id)
                assert st2.get("session_capture_receipt_claude"), st2
                assert st2.get("session_capture_receipt_pi"), st2
                assert st2.get("session_capture_last_error_claude") is None
                assert st2.get("session_capture_last_error_pi") is None
        finally:
            _current_org_id.reset(tok_t)
            _current_org_limits.reset(tok_l)
            _transport_mode.reset(tok_m)





# ── W5 Phase F (#2104): concurrent session_id idempotency ──────────────────


def test_phase_f_deterministic_event_id_converges_two_mints(tmp_path):
    """W5 Phase F (#2104): the sessionCaptured Event id is DETERMINISTIC
    (derived from session_id) — two writers minting the same session's
    capture Event (the #1727 TOCTOU: two concurrent POSTs both observing
    session_existed=False) converge on ONE Event node, never two."""
    import hashlib

    from tortoise.sdk import _session_capture_event_id

    sid = "session_phase_f"
    eid = _session_capture_event_id(sid)
    # Byte-parity format lock: ev_<sha256("sessionCaptured:"+session_id)>
    # truncated to 62 hex chars — the SDK and hosted mint sites share this
    # exact value (a drift would mint two different Event nodes for one
    # session under the concurrent race).
    expected = "ev_" + hashlib.sha256(
        f"sessionCaptured:{sid}".encode()).hexdigest()[:62]
    assert eid == expected, (eid, expected)
    sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    try:
        sdk.create_event(f"session_{sid}", "sessionCaptured", _server_id=eid,
                         startedAt="now", endedAt="now", sessionId=sid,
                         is_episodic=True)
        sdk.create_event(f"session_{sid}", "sessionCaptured", _server_id=eid,
                         startedAt="now", endedAt="now", sessionId=sid,
                         is_episodic=True)
        proj = sdk._get_proj()
        n = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN count(e)",
            params={"eid": eid},
        ).result_set
        assert n[0][0] == 1, f"two mints must converge on ONE Event (got {n[0][0]})"
    finally:
        sdk.close()


def test_phase_f_event_id_is_server_only_channel(tmp_path):
    """W5 Phase F (#2104): the deterministic Event id is a SERVER-ONLY
    channel — an ``id`` arriving via the props passthrough (flat kwarg or
    MCP-style nested props dict) is rejected fail-closed by the
    ``_sanitize_props(reject_id=True)`` backstop, restoring the pre-Phase F
    posture (create_point's #52 pop is the only id-through-props surface).
    The ``_server_id`` keyword (internal capture mints) is the only way in."""
    from tortoise.sdk import _session_capture_event_id

    sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    try:
        sid = "session_server_only"
        eid = _session_capture_event_id(sid)
        # Genuine capture mint still works through the internal channel.
        ev = sdk.create_event(f"session_{sid}", "sessionCaptured",
                              _server_id=eid, is_episodic=True)
        assert ev.get("id") == eid, ev
        # Flat id kwarg — rejected (previously popped + accepted).
        try:
            sdk.create_event("x", "meeting", id="ev_forced")
            raise AssertionError("flat id kwarg must be rejected")
        except ValueError as ex:
            assert "server-managed" in str(ex), ex
        # MCP-style nested props id — flattened by _coerce_props, rejected by
        # the backstop (the MCP boundary only inspects top-level keys).
        try:
            sdk.create_event("x", "meeting", props={"id": "ev_forced"})
            raise AssertionError("nested props id must be rejected")
        except ValueError as ex:
            assert "server-managed" in str(ex), ex
        # create_entity event branch — same reject.
        try:
            sdk.create_entity("event", "x", eventKind="meeting",
                              id="ev_forced")
            raise AssertionError("create_entity id must be rejected")
        except ValueError as ex:
            assert "server-managed" in str(ex), ex
        # The server-only _server_id kwarg must NOT be reachable through the
        # props passthrough — the MCP boundary denylist catches top-level keys;
        # the post-_coerce_props reject catches nested-props flattens (a
        # props={"props": {...}} dict would otherwise splat-bind the
        # keyword-only param).
        try:
            sdk.create_event("x", "meeting",
                             props={"_server_id": "ev_forced"})
            raise AssertionError("nested _server_id props must be rejected")
        except ValueError as ex:
            assert "server-managed" in str(ex), ex
        try:
            sdk.create_entity("event", "x", eventKind="meeting",
                              props={"_server_id": "ev_forced"})
            raise AssertionError("nested _server_id props must be rejected")
        except ValueError as ex:
            assert "server-managed" in str(ex), ex
        # The EXPLICIT internal param stays the one working channel.
        ev3 = sdk.create_event("x", "meeting", _server_id="ev_internal_ok")
        assert ev3.get("id") == "ev_internal_ok", ev3
        # The ingest-bundle surface (Phase-2 **item splat into create_event)
        # must also reject _server_id at shape time — a tenant entity item
        # would otherwise bind the keyword-only param directly (it never lands
        # in props, so the post-coerce reject cannot see it).
        from tortoise.exceptions import BundleValidationError
        try:
            sdk.ingest({"entities": [{"type": "event", "name": "ing",
                                      "eventKind": "meeting",
                                      "_server_id": "ev_forced_ingest"}]})
            raise AssertionError("ingest _server_id item must be rejected")
        except BundleValidationError as ex:
            assert "_server_id" in str(ex), ex
        n_events = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:'ev_forced_ingest'}) RETURN count(e)"
        ).result_set[0][0]
        assert n_events == 0, "a rejected ingest must mint nothing"
        # A fresh ULID is still minted when no _server_id is supplied.
        ev2 = sdk.create_event("x", "meeting", is_episodic=False)
        assert ev2.get("id"), ev2
        assert ev2.get("id") != eid, "no _server_id must not reuse a prior id"
        # eventId is server-managed on the ENTITY-update surface (an Event
        # rename desyncs the live node from its EventRecorded journal entry).
        # Point eventId is a WRITABLE provenance property (create_point /
        # update_point authoring path, #1417) — only Event identity is guarded.
        try:
            sdk.update_entity(ev2.get("id"), eventId="ev_renamed")
            raise AssertionError("update_entity eventId must be rejected")
        except ValueError as ex:
            assert "server-managed" in str(ex), ex
        # ...while update_point still allows provenance property authoring.
        pt = sdk.create_point("statement", "a point")
        upd = sdk.update_point(pt["id"], eventId="ev_authorable")
        assert sdk._get_proj().g.query(
            "MATCH (p:Point {id:$i}) RETURN p.eventId",
            params={"i": pt["id"]}).result_set[0][0] == "ev_authorable", upd
        # The _update_entity reject is LABEL-AWARE: a Point target (matched by
        # id) keeps eventId authoring; only Event targets reject.
        sdk.update_entity(pt["id"], eventId="ev_authorable_2")
        assert sdk._get_proj().g.query(
            "MATCH (p:Point {id:$i}) RETURN p.eventId",
            params={"i": pt["id"]}).result_set[0][0] == "ev_authorable_2"
    finally:
        sdk.close()


def test_phase_f_sweep_event_delete_session_guard(tmp_path):
    """W5 Phase F (#2104, review r3): the orphaned-writes sweep's Event delete
    carries a session-absence guard (deterministic eventIds are shared by
    every capture of a session — a concurrent same-session re-capture must not
    lose its Event under the delete-during-capture race). Executes the EXACT
    production Cypher (_SWEEP_EVENT_DELETE_CYPHER) against the real graph in
    both states."""
    from tortoise import hosted_api
    from tortoise.sdk import _session_capture_event_id

    sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    try:
        sid = "session_sweep_guard"
        eid = _session_capture_event_id(sid)
        proj = sdk._get_proj()
        # Stage a Session + its deterministic capture Event (real graph).
        ev = sdk.create_event(f"session_{sid}", "sessionCaptured",
                              _server_id=eid, startedAt="now",
                              endedAt="now", sessionId=sid,
                              is_episodic=True)
        assert ev.get("id") == eid, ev
        proj.g.query("MERGE (s:Session {id:$sid})", params={"sid": sid})
        q = lambda: proj.g.query(  # noqa: E731
            "MATCH (e:Event {eventId:$eid}) RETURN count(e)",
            params={"eid": eid}).result_set[0][0]
        # Live Session present (the concurrent re-capture) → Event survives.
        proj.g.query(hosted_api._SWEEP_EVENT_DELETE_CYPHER,
                     params={"sid": sid, "eid": eid})
        assert q() == 1, "a live Session must protect its deterministic Event"
        # Session absent (true orphan) → Event is deleted.
        proj.g.query("MATCH (s:Session {id:$sid}) DETACH DELETE s",
                     params={"sid": sid})
        proj.g.query(hosted_api._SWEEP_EVENT_DELETE_CYPHER,
                     params={"sid": sid, "eid": eid})
        assert q() == 0, "an absent Session must let the orphaned Event through"
    finally:
        sdk.close()


# ── #2335 WI-1a: stats passthrough into capture meta + resp ────────────────
# The extract_session_v2 telemetry (recovery per-seam tokens, llm, chunks,
# error_census) was eval-lane-only; the product lane dropped it at the meta
# assembly. These tests pin the additive `stats` key on the meta (v2 real /
# replayed+M2 empty) + the resp body, per the #2335 measurement program.

class _TokenFakeLLMResp(_FakeLLMResp):
    """_FakeLLMResp + completion_tokens so the #2408 accumulator records
    per-seam out-tokens (guard: completion_tokens > 0)."""

    def json(self):
        base = super().json()
        base["usage"] = {"completion_tokens": 1234,
                         "prompt_tokens": 500}
        return base


def _install_token_fake_provider(monkeypatch, requests_log):
    """_install_fake_provider + token-bearing usage (S1/S2/S4 all report
    completion_tokens → recovery s2_out_tokens/s4_out_tokens recorded)."""
    import requests as _requests

    def _fake_post(self_or_url, url=None, **kwargs):
        requests_log.append(url)
        system = ((kwargs.get("json") or {}).get("messages") or [{}])[0].get("content", "")
        if "STORY SUMMARIZER" in system:
            content = "The session revealed a new strategy."
        else:
            content = _V2_EMBED_JSON
        return _TokenFakeLLMResp(content)

    monkeypatch.setattr(_requests.Session, "post", _fake_post)


def test_extract_session_v2_meta_carries_stats(sdk, monkeypatch):
    """#2335 WI-1a: the v2 (extracted, meta) contract surfaces the extractor
    stats — meta['stats'] present with the recovery per-seam out-tokens on a
    healthy capture (the #2408 accumulator keys ride through)."""
    from tortoise.model_adapters import _reset_failover_cooldown
    _reset_failover_cooldown()
    requests_log = []
    _install_token_fake_provider(monkeypatch, requests_log)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    _extracted, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "we decided"}],
        session_id="s-stats", now="2026-08-20T00:00:00Z")
    assert isinstance(meta.get("stats"), dict), "meta must carry the stats key"
    rec = (meta.get("stats") or {}).get("recovery") or {}
    # healthy v2 run → both seams record (the #2408 accumulator fired)
    assert rec.get("s2_out_tokens", 0) > 0, rec
    assert rec.get("s4_out_tokens", 0) > 0, rec
    assert isinstance(meta.get("stats", {}).get("llm"), dict)






# ── #2335 WI-1d: the observation leg (structured per-capture log line) ────





# ── #2335 WI-2: the customer-facing error contract (headline + diagnostics) ─









# ── #2335 WI-2b: TRUE retry — a FAILED session's re-capture re-attempts ──









def test_apply_supersessions_concurrent_divergent_fold_one_wins(
        tmp_path, monkeypatch):
    """#2242 indicator 3: two threads drive apply_supersessions on the SAME
    shared server graph (two TortoiseSDKs with the SAME db_path — the docker
    redirect derives one test_* server graph per path) superseding the same
    live Object with DIVERGENT successors, barrier-synced INSIDE the fold so
    BOTH threads pass the terminal/visible gates (both probe race-O live) and
    emit before either folds. Exactly ONE fold lands (applied total == 1),
    the loser warns (the live-path CAS "lost a concurrent race"), the end
    state is a SINGLE successor. Pre-fix (unconditional SET): both folds land
    → applied == 2, silent last-wins. Server-mode single-statement atomicity
    is the mechanism under test (metering.py doctrine) → docker lane only."""
    if not _server_uri_set():
        pytest.skip("requires TORTOISE_DB_URI (docker test-server lane)")
    import threading

    from tortoise.commit_ops import apply_supersessions

    shared = str(tmp_path / "race-shared.db")
    sdk1 = TortoiseSDK(shared)
    sdk2 = TortoiseSDK(shared)  # SAME path → SAME derived test_* graph
    proj1 = sdk1._get_proj()
    proj2 = sdk2._get_proj()
    try:
        sdk1.create_entity("object", "race-O", objectKind="core:strategy")
        sdk1.create_entity("object", "race-B", objectKind="core:strategy")
        sdk1.create_entity("object", "race-C", objectKind="core:strategy")
        # REVIEW (round 1, P1): assert the PREMISE before racing — BOTH projs
        # must resolve the SAME shared server graph with exactly ONE live
        # race-O. A double-fold could otherwise be SDK aliasing (each thread
        # folded its OWN race-O) rather than a server-atomicity gap; the
        # premise assert turns any aliasing into a deterministic pre-race
        # failure instead of a ~4% post-race flake. If the redirect ever
        # splits, every run fails HERE loudly.
        for label, proj in (("proj1", proj1), ("proj2", proj2)):
            n = proj.g.query(
                "MATCH (o:Object {name:'race-O'}) RETURN count(o)",
            ).result_set[0][0]
            assert n == 1, f"{label} must see exactly ONE race-O: {n}"
        n1 = proj1.g.query(
            "MATCH (o:Object {name:'race-O', status:'live'}) RETURN count(o)",
        ).result_set[0][0]
        n2 = proj2.g.query(
            "MATCH (o:Object {name:'race-O', status:'live'}) RETURN count(o)",
        ).result_set[0][0]
        assert n1 == 1 and n2 == 1, (
            f"both projs must see the SAME live race-O: proj1={n1} "
            f"proj2={n2}")
        barrier = threading.Barrier(2)
        orig = proj1.__class__._fold_object_superseded

        def _synced(self, ev, *, cas=False):
            barrier.wait(timeout=15)  # both threads passed the gates+emit
            return orig(self, ev, cas=cas)  # forward cas (commit_ops → True)

        monkeypatch.setattr(proj1.__class__, "_fold_object_superseded",
                            _synced)
        results: list = []

        def _run(sdk_, proj_, sby):
            warns: list[str] = []
            applied = apply_supersessions(
                proj_, sdk_,
                [{"superseded": "race-O", "supersedes_by": sby,
                  "evidence": "race"}],
                session_id=f"sess_race_{sby}", warn=warns.append)
            results.append((applied, warns, sby))

        t1 = threading.Thread(target=_run, args=(sdk1, proj1, "race-B"))
        t2 = threading.Thread(target=_run, args=(sdk2, proj2, "race-C"))
        t1.start()
        t2.start()
        t1.join(30)
        t2.join(30)
        assert not t1.is_alive() and not t2.is_alive(), "threads deadlocked"
        applied_total = sum(r[0] for r in results)
        # one node-count backstop: even if aliasing slipped past the premise
        # race (both threads folded DIFFERENT race-O nodes), the double-fold
        # signature would show 2 nodes here — a test-infra failure, not a
        # product bug.
        total_race_o = proj1.g.query(
            "MATCH (o:Object {name:'race-O'}) RETURN count(o)",
        ).result_set[0][0]
        assert total_race_o == 1, f"aliasing: {total_race_o} race-O nodes"
        assert applied_total == 1, f"exactly ONE fold must land: {results}"
        warn_joined = " | ".join(w for r in results for w in r[1])
        assert "lost a concurrent race" in warn_joined, \
            f"loser must warn (live-path CAS): {warn_joined}"
        rows = proj1.g.query(
            "MATCH (o:Object {name:'race-O'}) RETURN o.status, o.supersededBy",
        ).result_set
        assert rows and rows[0][0] == "superseded"
        assert rows[0][1] in ("race-B", "race-C"), \
            f"single successor, never both: {rows}"
        assert _object_superseded_events(proj1) == 2, (
            "both commits journaled (emit-before-fold; the concurrent "
            "journal-order residual is documented — rebuild resolves "
            "last-wins as today)")
    finally:
        sdk1.close()
        sdk2.close()
        # monkeypatch auto-restores at teardown even on exceptions


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8818: the deterministic refusal pin for this module's client seams.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. ``POST /v1/sessions`` is the
    route this file reads most and it is not exempt from the bound. The
    plain-client control is the non-vacuity leg — it is the exact
    ``assert 504 == 200`` shape this conversion removes, and it goes RED if
    the route is ever exempted (the middleware stops refusing, so the
    control's 504 assertion fails).
    """
    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("POST", "/v1/sessions")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(app) as raw:
        assert raw.post(route[1], json={}).status_code == 504
    with verdict_client(app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.post(route[1], json={})
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "POST /v1/sessions answered" in message
