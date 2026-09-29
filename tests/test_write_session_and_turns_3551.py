"""#3551 — the shared ``_write_session_and_turns`` primitive.

Behaviour-preserving extraction: this file proves the two server capture
writers (W1 ``hosted_api._capture_session_impl``, W2 ``TortoiseSDK.capture_session``)
each delegate the ``:Session`` MERGE / turn store to ONE primitive, and that the
extraction did not change a byte of what either lane writes.

The load-bearing tests are ``test_sdk_writer_is_behaviour_equivalent`` and
``test_hosted_writer_is_behaviour_equivalent_and_parity``: a refactor's
failure mode is a silent behaviour change that every "an id exists" test passes
through, so those tests pin the EXACT pre-refactor Cypher/parameters recorded by
running both lanes on a non-trivial turn list (odd roles, ``None`` content, and a
newline). The pinning constants were captured from the pre-refactor tree
(``877fa52d1``) by a query recorder — see the PR/report for the raw before/after
diff (identical).

``machine_id`` / ``model`` are deliberately ASYMMETRIC (W1 writes them, W2 does
not) and that asymmetry is RECORDED here as knowingly-absent, not fixed:
``derive_machine_id()`` is machine-local, and stamping it on the SDK/audit path
would misattribute the machine that actually captured the session. The primitive
keeps the set-if-absent ``coalesce`` so a re-capture from a second machine can
never overwrite the first machine's id.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect

import pytest

from tests.test_hosted_api import TEST_ORG_ID
from tests.test_hosted_api import client as client
from tortoise import hosted_api as ha_mod
from tortoise import sdk as sdk_mod
from tortoise.sdk import TortoiseSDK, _capture_turn_id

# ── the non-trivial window the equivalence tests pin ───────────────────────
# Two odd shapes on purpose: a role/None content that only the isinstance-first
# coercion handles (#721), and an embedded newline that the stored-text/role
# split must preserve verbatim.
_CONV = [
    {"role": "user", "content": "  first turn  "},
    {"role": "assistant", "content": "second turn\nwith a newline"},
    {"role": "weird", "content": "x" * 30},
    {"role": None, "content": None},
    {"role": "user", "content": "fifth"},
]

_SDK_SID = "equiv-sdk"
_HOSTED_SID = "equiv-hosted"

#: The pre-refactor W2 ``:Session`` MERGE text (no harness/actor/machine/model
#: inputs supplied). Byte-identical after the extraction.
_SDK_SESSION_CYPHER = (
    "MERGE (s:Session {id:$sid}) SET "
    "s.created_at=coalesce(s.created_at, $now), s.turn_count=$tc, "
    "s.is_episodic=true"
)
#: The pre-refactor W1 ``:Session`` MERGE text — the SAME base clauses plus the
#: conditional harness/machine_id/model clauses this request supplied.
_HOSTED_SESSION_CYPHER = (
    "MERGE (s:Session {id:$sid}) SET "
    "s.created_at=coalesce(s.created_at, $now), s.turn_count=$tc, "
    "s.is_episodic=true, s.harness=$harness, "
    "s.machine_id=coalesce(s.machine_id, $mid), "
    "s.model=coalesce(s.model, $model)"
)
#: sha256 of the whitespace-normalized ``UNWIND $turns`` turn-store Cypher.
#: Identical for W1 and W2 and identical pre-/post-refactor. Pinned as a hash
#: (not by identity to ``_TURN_WRITE_CYPHER``) so a future edit to the text is
#: caught rather than trivially satisfied.
_TURN_CYPHER_SHA256 = \
    "fe0c0e87c14993c6fd185fc72c71d75fd5c0d68cccae21d5584417818afdfdd9"

#: The pre-refactor per-turn params (minus the environment-dependent vector and
#: clock) for the 5-turn window above.
_TURN_ROWS = [
    {"c": "[user]   first turn  ",
     "ch": "f401f37dc5779d26311604b3d9337dc3c763eedd054e14a7c361af1fa0029f90",
     "k": "event", "s": "draft", "speaker": "user"},
    {"c": "[assistant] second turn\nwith a newline",
     "ch": "faaa821bd5b03827a792784345e15f1cd1a03509d22b183f2a3f5eccdb210ddd",
     "k": "event", "s": "draft", "speaker": "assistant"},
    {"c": "[weird] " + "x" * 30,
     "ch": "2c5f950926c4e5e9d7f67a8dd5f6630d0cbe46b732ba641165ce887f38dab1c2",
     "k": "event", "s": "draft", "speaker": "weird"},
    {"c": "[unknown] ",
     "ch": "511688687f76db6c56c5e176d5bb333adacfd9dc2a0a1776d594275f19a87b59",
     "k": "event", "s": "draft", "speaker": "unknown"},
    {"c": "[user] fifth",
     "ch": "6cd790f742730c4c1077227aac6b04c04358dae74705da1107fd5a862344e1da",
     "k": "event", "s": "draft", "speaker": "user"},
]


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Offline MockModel extraction seam (#822) — no provider/network."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _normalize(params: dict | None) -> dict:
    """Strip the clock and the environment-dependent vector from a write."""
    out = dict(params or {})
    if "now" in out:
        out["now"] = "<NOW>"
    if "turns" in out:
        out["turns"] = [
            {k: ("<EMB>" if k == "emb" and v is not None else v)
             for k, v in row.items()}
            for row in out["turns"]
        ]
    return out


@contextlib.contextmanager
def _record_writes(sdk_obj):
    """Record the normalized ``:Session`` MERGE and turn-store writes."""
    graph_cls = type(sdk_obj._get_proj().g)
    orig = graph_cls.query
    seen: dict = {"session_writes": [], "turn_writes": []}

    def _query(self, cypher, params=None, *args, **kwargs):
        flat = " ".join(str(cypher).split())
        if "MERGE (s:Session" in flat:
            seen["session_writes"].append((flat, _normalize(params)))
        elif "UNWIND $turns AS turn" in flat:
            seen["turn_writes"].append((flat, _normalize(params)))
        return orig(self, cypher, params, *args, **kwargs)

    graph_cls.query = _query
    try:
        yield seen
    finally:
        graph_cls.query = orig


def _session_props(sdk, sid: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.is_episodic, "
        "s.capture_ok, s.actor_user_id, s.machine_id, s.model, "
        "s.harness, s.turn_count",
        params={"sid": sid}).result_set
    assert rows, f"no :Session {sid!r}"
    r = rows[0]
    return {"is_episodic": r[0], "capture_ok": r[1], "actor_user_id": r[2],
            "machine_id": r[3], "model": r[4], "harness": r[5],
            "turn_count": r[6]}


def _turn_props(sdk, tid: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point {id:$tid}) RETURN t.pointKind, t.speaker, "
        "t.is_episodic, t.status, t.embedding",
        params={"tid": tid}).result_set
    assert rows, f"no turn Point {tid!r}"
    r = rows[0]
    return {"pointKind": r[0], "speaker": r[1], "is_episodic": r[2],
            "status": r[3], "embedding": r[4]}


# ── the primitive owns the optional ``embed_fn`` call ──────────────────────

def test_write_session_and_turns_embed_fn_called(tmp_path):
    """``embed_fn`` is invoked once, over the writer's OWN stored text, and its
    result is what the turn writer stores."""
    sdk = TortoiseSDK(db_path=str(tmp_path / "embed.db"))
    proj = sdk._get_proj()
    calls: list[list[str]] = []
    sentinel: list = [None] * len(_CONV)
    seen: dict = {}
    orig = sdk_mod._write_capture_turns

    def _spy(*args, **kwargs):
        seen["embs"] = kwargs["turn_embs"]
        return orig(*args, **kwargs)

    sdk_mod._write_capture_turns = _spy
    try:
        out = sdk_mod._write_session_and_turns(
            proj, sdk, "prim-embed", _CONV,
            now="2026-01-01T00:00:00+00:00",
            embed_fn=lambda texts: (calls.append(list(texts)), sentinel)[1])
    finally:
        sdk_mod._write_capture_turns = orig

    assert len(calls) == 1, "embed_fn was not called exactly once"
    assert calls[0] == out["turn_texts"], (
        "embed_fn must be called over the writer's own stored text")
    assert seen["embs"] is sentinel, "embed_fn's result was not the batch written"
    # The nodes exist and carry the (None) sentinel vectors.
    assert _turn_props(sdk, "prim-embed_t0")["pointKind"] == "event"


def test_write_session_and_turns_embed_fn_none_is_a_noop(tmp_path):
    """``embed_fn=None`` (the default) writes NO vector — the switch the
    deferred embedding work flips."""
    sdk = TortoiseSDK(db_path=str(tmp_path / "noop.db"))
    proj = sdk._get_proj()
    seen: dict = {}
    orig = sdk_mod._write_capture_turns

    def _spy(*args, **kwargs):
        seen["embs"] = kwargs["turn_embs"]
        return orig(*args, **kwargs)

    sdk_mod._write_capture_turns = _spy
    try:
        sdk_mod._write_session_and_turns(
            proj, sdk, "prim-noop", _CONV, now="2026-01-01T00:00:00+00:00")
    finally:
        sdk_mod._write_capture_turns = orig

    assert seen["embs"] == [None] * len(_CONV)
    assert _turn_props(sdk, "prim-noop_t0")["embedding"] is None


# ── offset-aware turn-id derivation ────────────────────────────────────────

def test_turn_id_derivation_is_offset_aware_and_pre_refactor_identical():
    """Offset 0 is byte-identical to the pre-refactor ``f"{sid}_t{i}"``; a
    non-zero offset shifts the whole window (an append never re-mints index 0)."""
    sid = "deriv"
    assert [_capture_turn_id(sid, i) for i in range(5)] == [
        "deriv_t0", "deriv_t1", "deriv_t2", "deriv_t3", "deriv_t4"]
    assert _capture_turn_id(sid, 0) == f"{sid}_t0"
    assert _capture_turn_id(sid, 4) == f"{sid}_t4"
    assert [_capture_turn_id(sid, i, 3) for i in range(5)] == [
        "deriv_t3", "deriv_t4", "deriv_t5", "deriv_t6", "deriv_t7"]


def test_turn_offset_shifts_the_written_ids(tmp_path):
    """End-to-end: an offset write mints the shifted ids and does NOT touch the
    ids below its window.

    The prefix is seeded first ON PURPOSE.  Without a populated prefix the stale
    sweep below has nothing to sweep, so the test passes even if the offset
    protection is deleted and asserts only id derivation.  Verified: the mutant
    ``_first_live = 0`` (offset protection removed) PASSED the earlier version of
    this test and hard-deletes ``off_t0``/``off_t1`` at step 2 here.
    """
    sdk = TortoiseSDK(db_path=str(tmp_path / "offset.db"))
    proj = sdk._get_proj()

    # 1. Seed the prefix window at offset 0.
    sdk_mod._write_session_and_turns(
        proj, sdk, "off", _CONV[:2], now="2026-01-01T00:00:00+00:00",
        turn_offset=0)
    assert sorted(sdk_mod._capture_turn_ids(proj, "off")) == [
        "off_t0", "off_t1"]

    # 2. APPEND at offset 2: the minted ids shift AND the prefix must SURVIVE.
    #    This is the assertion the offset protection exists for.
    sdk_mod._write_session_and_turns(
        proj, sdk, "off", _CONV[2:4], now="2026-01-01T00:01:00+00:00",
        turn_offset=2, session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "off")) == [
        "off_t0", "off_t1", "off_t2", "off_t3"]

    # 3. A SHORTER re-capture of the same window must still sweep it: the bound
    #    is "at or beyond the window", not "never sweep".  ``off_t3`` is above
    #    the new window's end so it goes; the prefix still stands.
    sdk_mod._write_session_and_turns(
        proj, sdk, "off", _CONV[2:3], now="2026-01-01T00:02:00+00:00",
        turn_offset=2, session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "off")) == [
        "off_t0", "off_t1", "off_t2"]


def test_non_int_parseable_suffix_does_not_break_capture(tmp_path):
    """A turn-shaped id whose suffix passes the SHAPE filter but is not
    ``int()``-parseable must not break the stale sweep (#3551 review).

    ``str.isdigit()`` is strictly wider than ``int()`` — ``'²'.isdigit()`` is
    True yet ``int('²')`` raises — so an id shaped ``<sid>_t²`` used to be
    admitted by ``_capture_turn_ids`` and then blow up the sweep's parse AFTER
    the caller's :Session MERGE had committed, turning a benign cleanup into a
    failed capture. The guard now uses ``isdecimal()``, which is True exactly
    for the suffixes ``int()`` parses, so the id is left untouched instead.
    """
    sdk = TortoiseSDK(db_path=str(tmp_path / "wide.db"))
    proj = sdk._get_proj()

    # Seed a real capture so the :Session node exists for the sweep to read.
    sdk_mod._write_session_and_turns(
        proj, sdk, "wide", _CONV[:2], now="2026-01-01T00:00:00+00:00")

    # Inject an episodic turn-shaped Point whose suffix passes the OLD guard
    # but is not int()-parseable.
    weird = "wide_t²"
    assert weird[len("wide_t"):].isdigit()          # admitted by `isdigit()`
    assert not weird[len("wide_t"):].isdecimal()    # ... not by `isdecimal()`
    proj.g.query(
        "MATCH (s:Session {id:$sid}) "
        "CREATE (t:Point {id:$tid, is_episodic:true, pointKind:'event'}) "
        "CREATE (s)-[:CONTAINS]->(t)",
        params={"sid": "wide", "tid": weird})

    # Re-capture: this drives the stale sweep. Pre-fix, `int('²')` raised.
    sdk_mod._write_session_and_turns(
        proj, sdk, "wide", _CONV[:2], now="2026-01-01T00:01:00+00:00")

    # The malformed id was neither swept nor allowed to break the capture.
    rows = proj.g.query(
        "MATCH (t:Point {id:$tid}) RETURN t.id",
        params={"tid": weird}).result_set
    assert rows, "the non-parseable id was wrongly swept"


# ── neither caller holds independent MERGE text ────────────────────────────

def test_neither_writer_holds_independent_merge_text():
    """W1 and W2 call the primitive and carry no copy of the Session/turn text."""
    for fn in (ha_mod._capture_session_impl, TortoiseSDK.capture_session):
        src = inspect.getsource(fn)
        assert "_write_session_and_turns" in src, (
            f"{fn.__qualname__} does not call the shared primitive")
        assert "MERGE (s:Session" not in src, (
            f"{fn.__qualname__} still holds a :Session MERGE literal")
        assert "s.created_at=coalesce(s.created_at, $now)" not in src, (
            f"{fn.__qualname__} still holds the Session field list")
        assert "s.turn_count=$tc" not in src, (
            f"{fn.__qualname__} still holds the Session field list")
        assert "_write_capture_turns(" not in src, (
            f"{fn.__qualname__} bypasses the primitive for the turn store")
    # And the primitive itself is the single entry point both share.
    assert ha_mod._write_session_and_turns is sdk_mod._write_session_and_turns


# ── behaviour equivalence (the refactor's real gate) ───────────────────────

def test_sdk_writer_is_behaviour_equivalent(tmp_path):
    """W2 writes the EXACT pre-refactor Cypher/params, and deliberately omits
    machine_id/model."""
    sdk = TortoiseSDK(db_path=str(tmp_path / "sdk.db"))
    with _record_writes(sdk) as seen:
        sdk.capture_session(_CONV, session_id=_SDK_SID)

    session_cypher, session_params = seen["session_writes"][-1]
    turn_cypher, turn_params = seen["turn_writes"][-1]
    assert session_cypher == _SDK_SESSION_CYPHER
    assert session_params == {
        "sid": _SDK_SID, "now": "<NOW>", "tc": 5}
    assert (hashlib.sha256(turn_cypher.encode()).hexdigest()
            == _TURN_CYPHER_SHA256)
    assert [r["id"] for r in turn_params["turns"]] == [
        f"{_SDK_SID}_t{i}" for i in range(5)]
    assert [r["speaker"] for r in turn_params["turns"]] == [
        "user", "assistant", "weird", "unknown", "user"]
    assert turn_params["redactions"] == 0
    for got, want in zip(turn_params["turns"], _TURN_ROWS, strict=True):
        assert got["c"] == want["c"]
        assert got["ch"] == want["ch"]
        assert got["k"] == want["k"]
        assert got["s"] == want["s"]

    # ── the SDK's machine_id/model absence is KNOWINGLY-ABSENT ─────────────
    # derive_machine_id() is machine-local; the SDK/audit path has no server
    # capture credential, so stamping it here would attribute the session to the
    # machine that READ it, not the machine that CAPTURED it. Do NOT "fix" this
    # into a write — the epic pins the audit-time retrofit later.
    props = _session_props(sdk, _SDK_SID)
    assert props["machine_id"] is None
    assert props["model"] is None
    assert props["is_episodic"] is True
    assert props["actor_user_id"] is None


def test_hosted_writer_is_behaviour_equivalent_and_parity(client):
    """W1 writes the EXACT pre-refactor Cypher/params; its parity with W2 over
    the five disputed fields is asserted per-lane."""
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    with _record_writes(sdk) as seen:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV,
            "session_id": _HOSTED_SID,
            "harness": "claude",
            "machine_id": "machine-abc",
            "model": "claude-opus-4",
        })
    assert r.status_code == 200, r.text[:400]

    session_cypher, session_params = seen["session_writes"][-1]
    turn_cypher, turn_params = seen["turn_writes"][-1]
    assert session_cypher == _HOSTED_SESSION_CYPHER
    assert session_params == {
        "sid": _HOSTED_SID, "now": "<NOW>", "tc": 5, "harness": "claude",
        "mid": "machine-abc", "model": "claude-opus-4"}
    assert (hashlib.sha256(turn_cypher.encode()).hexdigest()
            == _TURN_CYPHER_SHA256)
    assert [r["id"] for r in turn_params["turns"]] == [
        f"{_HOSTED_SID}_t{i}" for i in range(5)]
    assert [r["speaker"] for r in turn_params["turns"]] == [
        "user", "assistant", "weird", "unknown", "user"]
    for got, want in zip(turn_params["turns"], _TURN_ROWS, strict=True):
        assert got["c"] == want["c"]
        assert got["ch"] == want["ch"]
        assert got["k"] == want["k"]
        assert got["s"] == want["s"]

    # ── five disputed fields, per lane ─────────────────────────────────────
    sdk_props = _session_props(sdk, _HOSTED_SID)
    assert sdk_props["is_episodic"] is True
    # W1 writes the client-claimed fields; W2 does not (see the SDK test).
    assert sdk_props["machine_id"] == "machine-abc"
    assert sdk_props["model"] == "claude-opus-4"
    # actor_user_id: neither lane has an actor in this test (no ContextVar on the
    # SDK side; TEST_TEAM carries no actor_user_id) — parity by absence.
    assert sdk_props["actor_user_id"] is None
    turn = _turn_props(sdk, f"{_HOSTED_SID}_t0")
    assert turn["speaker"] == "user"
    assert turn["is_episodic"] is True
    assert turn["pointKind"] == "event"


# ── the demo seeder (W7) is excluded from the primitive ────────────────────

def test_demo_seed_is_excluded_from_the_primitive(client):
    """``_seed_demo_graph`` keeps its own demo shape and its rows are not
    reachable through the capture turn-id space."""
    src = inspect.getsource(ha_mod._seed_demo_graph)
    assert "_write_session_and_turns" not in src
    assert "_write_capture_turns" not in src
    assert "_capture_turn_id" not in src
    # It still writes its OWN deliberately-different demo Session (3 turns, no
    # is_episodic / capture_ok) — the reason it must stay out.
    assert "s.turn_count=3" in src

    demo_org = "demo-3551-org"
    ha_mod._seed_demo_graph(demo_org)
    sdk = ha_mod._make_sdk(namespace=demo_org)
    proj = sdk._get_proj()
    demo_sid = f"session_demo_{demo_org[:8]}"

    # The capture writer's own turn-id reader finds NO capture turns for it.
    assert sdk_mod._capture_turn_ids(proj, demo_sid) == []
    props = _session_props(sdk, demo_sid)
    assert props["is_episodic"] is None
    assert props["capture_ok"] is None
    rows = proj.g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH $p RETURN t.id",
        params={"p": f"{demo_sid}_t"}).result_set
    assert not rows, "a demo row leaked into the capture turn-id space"
