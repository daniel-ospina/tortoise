"""Product-lane ask LLM regression (#1987 Task 12) — the repeatable
counterpart to the (b) product-lane known-answer smoke.

GATING (P2-9): SKIPPED unless ``TORTOISE_ASK_LLM_REGRESSION=1`` is set OR a
live provider key env is present (DEEPSEEK_API_KEY / OPENROUTER_API_KEY /
VENICE_API_KEY). Two modes:

  * FIXTURE mode (env var set, no keys): runs the COMMITTED recorded-
    transport transcripts at ``tests/fixtures/ask_llm_transcripts/`` in the
    docker/unit lane — deterministic replayed ``complete()`` transcripts for
    (a) gold-verbatim-commit (gold string in context → MUST commit,
    abstained=False), (b) decision-commit (the decision IS in context → MUST
    answer, not abstain), (c) genuine-absence-abstain (→ MUST abstain),
    PLUS (d) all-superseded-stale-evidence (MUST answer from stale evidence
    WITH the [SUPERSEDED BY] markers present — SINGLE-SIDED assertion: a
    behavior flip on the stale-evidence class fails the fixture; the graded
    ``_abs`` verdict covers EVAL-SHAPED evidence only, this fixture covers
    PRODUCT-SHAPED superseded/degraded/annotated evidence — P2-18).
  * LIVE-KEY mode (a provider key env present): runs the REAL lane
    (``build_reader_model``) against the same seeded fixtures. The
    fixture-replay byte-equality tests SKIP in this mode — a real build
    does not reproduce the recorded bytes; ``test_live_key_mode_real_lane``
    covers live mode with loose assertions.

TRANSCRIPT FIDELITY GUARD (P2-25/P2-19): the recorded PROMPT HASH must
match the current ``reader_prompt_constants()`` and the replayed user
message must BYTE-EQUAL the pipeline's current rendered output for the same
hits — a stale transcript cannot replay an old prompt's outputs, and
``render_context`` formatting changes (markers, headers, ordering) force
fixture REGENERATION (``tools/gen_ask_transcripts.py``).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.reader import reader_prompt_constants  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402
from tortoise.retrieval import (  # noqa: E402
    DEFAULT_CONTEXT_ITEM_CAP,
    DEFAULT_CONTEXT_TOKEN_CAP,
    assemble_context,
    dedup_pool,
    render_context,
)

TRANSCRIPTS_DIR = Path(__file__).parent / "fixtures" / "ask_llm_transcripts"

_LIVE_KEY_ENVS = ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "VENICE_API_KEY")


def _live_keys_present() -> bool:
    return any(bool(os.environ.get(k)) for k in _LIVE_KEY_ENVS)


def _fixture_mode() -> bool:
    return os.environ.get("TORTOISE_ASK_LLM_REGRESSION") == "1"


pytestmark = pytest.mark.skipif(
    not (_fixture_mode() or _live_keys_present()),
    reason="TORTOISE_ASK_LLM_REGRESSION=1 or a live provider key required "
           "(#1987 Task 12 gating, P2-9)")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_transcripts() -> list[dict]:
    out = []
    for path in sorted(TRANSCRIPTS_DIR.glob("*.json")):
        with open(path) as f:
            out.append(json.load(f))
    return out


def _seed(sdk: TortoiseSDK, seeds: list[dict]) -> None:
    """Seed the fixture's points + Event nodes (startedAt from session_date)
    so annotate_ask_hits reproduces the annotated hits; ``supersedes_into``
    creates a real CORRECTS supersession so the D8 markers render."""
    proj = sdk._get_proj()
    ids: dict[str, str] = {}
    for i, seed in enumerate(seeds):
        point = sdk.create_point(seed.get("kind", "statement"),
                                 seed["content"], tags=seed.get("tags", []))
        pid = point["id"]
        ids[seed.get("label", f"s{i}")] = pid
        event_id = seed.get("eventId") or f"ev-{i}"
        sdate = seed.get("session_date") or "2026-08-20"
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": event_id, "st": f"{sdate}T10:00:00Z"},
        )
        proj.g.query(
            "MATCH (p:Point {id: $pid}) SET p.eventId = $eid, p.sessionId = $sid",
            params={"pid": pid, "eid": event_id,
                    "sid": seed.get("sessionId") or f"sess-{i}"},
        )
    for seed in seeds:
        succ = seed.get("supersedes_into")
        if succ and succ in ids and seed.get("label") in ids:
            sdk.supersede_point(ids[seed["label"]], ids[succ])


class _ReplayReader:
    """Replays the transcript completion; records the user message it was
    sent (for the byte-equality fidelity assertion)."""

    def __init__(self, completion: str):
        self.completion = completion
        self.last_completion_tokens = 12
        self.user_message: str | None = None
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.user_message = user
        return self.completion

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    """Isolated per-test DB + a reset ask-reader cache (fresh namespace)."""
    import tortoise.sdk as sdk_mod
    sdk_mod._reset_ask_reader_cache_for_tests()
    yield
    sdk_mod._reset_ask_reader_cache_for_tests()


def _run_fixture(tx: dict, monkeypatch) -> dict:
    """Seed + run the ask pipeline for one transcript. FIXTURE mode (the env
    var set — the CI shape, deterministic) uses the replay reader; LIVE-KEY
    mode (env var unset, provider keys present) uses the real factory."""
    import tortoise.sdk as sdk_mod
    sdk_mod._reset_ask_reader_cache_for_tests()
    db = os.path.join(tempfile.mkdtemp(prefix="ask_reg_"), "t.db")
    sdk = TortoiseSDK(db)
    _seed(sdk, tx["seeds"])
    replay = None
    if _fixture_mode():
        replay = _ReplayReader(tx["completion"])
        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                            lambda: replay)
    try:
        return sdk.ask(tx["question"], question_date=tx["question_date"])
    finally:
        sdk.close()
        if replay is not None:
            monkeypatch.undo()
    # unreachable — keep the linter quiet
    return {}


def _transcripts():
    return _load_transcripts()


@pytest.mark.parametrize("tx", _transcripts(), ids=lambda t: t["fixture"])
def test_fixture_replay_verdict(tx: dict, monkeypatch) -> None:
    """(a)/(b)/(c)/(d) — the replayed pipeline reproduces the recorded user
    message BYTE-EQUAL (fidelity) and the verdict matches the pinned
    expectation (MUST commit / MUST answer / MUST abstain / MUST answer from
    stale evidence)."""
    if _live_keys_present() and not _fixture_mode():
        pytest.skip("live-key mode — replay byte-equality only meaningful "
                    "in fixture mode (test_live_key_mode_real_lane covers "
                    "live mode with loose assertions)")
    result = _run_fixture(tx, monkeypatch)
    assert result["abstained"] is tx["expected_abstained"], (
        tx["fixture"], result["answer"])
    if tx.get("expect_superseded_markers"):
        # P2-13/P2-18: the stale-evidence fixture MUST answer FROM STALE
        # EVIDENCE WITH the [SUPERSEDED BY] markers present in the evidence
        # (single-sided — abstain is NOT an acceptable outcome).
        assert "[SUPERSEDED BY:" in result["evidence"], tx["fixture"]
    if tx["expected_abstained"]:
        from tortoise.reader import NO_EVIDENCE_TEXT
        # abstained fixtures may carry the canonical text (blank output) OR
        # the recorded abstention phrasing — either is an abstention
        assert result["answer"] in (tx["completion"], NO_EVIDENCE_TEXT)
    else:
        # commit fixtures: the answer is the recorded completion (the pinned
        # commit), NOT an abstention
        assert result["answer"] == tx["completion"].strip(), tx["fixture"]


def test_transcript_fidelity_prompt_hash() -> None:
    """P2-25: the recorded PROMPT HASH must match the current
    ``reader_prompt_constants()`` — a stale transcript (old prompt) fails,
    forcing fixture REGENERATION whenever the golden-string snapshot
    changes."""
    if _live_keys_present() and not _fixture_mode():
        pytest.skip("live-key mode — fixture-prompt-hash check only "
                    "meaningful in fixture mode")
    generic, fragments = reader_prompt_constants()
    current = _sha256(json.dumps(
        {"system": generic, "fragments": fragments}, sort_keys=True))
    for tx in _load_transcripts():
        assert tx["prompt_hash"] == current, (
            f"stale transcript {tx['fixture']} — regenerate via "
            f"tools/gen_ask_transcripts.py (P2-25)")


def test_fixture_replay_user_message_byte_equal(monkeypatch) -> None:
    """P2-19: the replayed user message BYTE-EQUALS the pipeline's current
    rendered output for the same hits — render_context formatting changes
    (markers, headers, ordering) force fixture regeneration."""
    import tortoise.sdk as sdk_mod
    for tx in _load_transcripts():
        sdk_mod._reset_ask_reader_cache_for_tests()
        db = os.path.join(tempfile.mkdtemp(prefix="ask_reg_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            _seed(sdk, tx["seeds"])
            replay = _ReplayReader(tx["completion"])
            monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                                lambda: replay)
            sdk.ask(tx["question"], question_date=tx["question_date"])
            assert replay.user_message == tx["user_message"], (
                f"rendered context drifted for {tx['fixture']} — regenerate")
        finally:
            sdk.close()
            monkeypatch.undo()


def test_live_key_mode_real_lane(monkeypatch) -> None:
    """LIVE-KEY mode: the REAL ``build_reader_model`` lane answers the
    known-answer fixture (the (b) product-lane smoke — the RoutingModel
    transport delta vs the eval's OpenAICompatModel)."""
    if _fixture_mode():
        pytest.skip("fixture mode set — live-key lane not exercised")
    if not _live_keys_present():
        pytest.skip("no provider keys — live-key mode not exercisable")
    tx = next(t for t in _load_transcripts()
              if t["fixture"] == "gold-verbatim-commit")
    result = _run_fixture(tx, monkeypatch)
    assert result["abstained"] is False
    assert result["answer"].strip()  # answered, not abstained
    assert "9am to 5pm" in result["answer"].lower() or "office hours" in \
        result["answer"].lower()
