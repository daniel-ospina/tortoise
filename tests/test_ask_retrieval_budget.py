"""#4105 — the ask lane's reader window: resolved, tandem, and HONEST.

Two defects are pinned here:

1. **The window starved the reader.** The answer-bearing turn is retrievable
   but ranked below the reader window. The ask lane's caps are now resolved
   IN TANDEM — ``limit >= context_item_cap`` and ``pool_size >= limit`` — so
   raising one can no longer be silently half-applied.
2. **A cap that silently did nothing (the serious one).** The reader-context
   byte ceiling was a hard 32 KiB literal at the ``assemble_context`` call
   site. A caller that raised the item/token cap believed it had widened the
   window; the reader still got 32 KiB. The byte ceiling is now resolved
   (env) and DERIVED from the token cap when unset, and ``assemble_context``
   reports which bound dropped hits, so the ask lane can warn.

Hermetic — no graph, no LLM. ``assemble_context`` and
``resolve_ask_retrieval_caps`` are pure; the ask-lane test drives a fake SDK.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import ask_lane
from tortoise.retrieval import (
    ASK_CONTEXT_BYTE_CAP_ENV,
    ASK_CONTEXT_ITEM_CAP_ENV,
    ASK_CONTEXT_TOKEN_CAP_ENV,
    ASK_POOL_SIZE_ENV,
    ASK_RETRIEVAL_LIMIT_ENV,
    BYTES_PER_TOKEN_FLOOR,
    DEFAULT_ASK_CONTEXT_ITEM_CAP,
    DEFAULT_ASK_CONTEXT_TOKEN_CAP,
    DEFAULT_ASK_POOL_SIZE,
    DEFAULT_ASK_RETRIEVAL_LIMIT,
    DEFAULT_CONTEXT_BYTE_CAP,
    DEFAULT_CONTEXT_TOKEN_CAP,
    assemble_context,
    render_context,
    resolve_ask_retrieval_caps,
    resolve_byte_cap_from_caps,
)

_CAP_ENVS = (ASK_RETRIEVAL_LIMIT_ENV, ASK_CONTEXT_ITEM_CAP_ENV,
             ASK_CONTEXT_TOKEN_CAP_ENV, ASK_CONTEXT_BYTE_CAP_ENV,
             ASK_POOL_SIZE_ENV)


@pytest.fixture(autouse=True)
def _clean_cap_env(monkeypatch):
    for name in _CAP_ENVS:
        monkeypatch.delenv(name, raising=False)
    # The ask lane refuses to run when a hosted URL is set (it is eval-only).
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    yield


def _hits(n: int, *, words: int = 300, content: str | None = None) -> list[dict]:
    body = content or ("alpha beta gamma delta epsilon zeta eta theta iota "
                       "kappa lambda mu nu xi omicron pi rho sigma tau " * 40)
    return [{"id": f"h{i}", "content": body[:max(words * 6, 6)],
             "session_id": f"s{i}"} for i in range(n)]


# ── the resolved caps: defaults, tandem invariants, honest byte ceiling ────

def test_resolve_defaults_are_the_measured_window():
    caps = resolve_ask_retrieval_caps()
    assert caps["limit"] == DEFAULT_ASK_RETRIEVAL_LIMIT
    assert caps["context_item_cap"] == DEFAULT_ASK_CONTEXT_ITEM_CAP
    assert caps["context_token_cap"] == DEFAULT_ASK_CONTEXT_TOKEN_CAP
    assert caps["pool_size"] == DEFAULT_ASK_POOL_SIZE
    # The byte ceiling is DERIVED from the token cap when unset — never a
    # literal that a token raise cannot move.
    assert caps["context_byte_cap"] == max(
        DEFAULT_CONTEXT_BYTE_CAP,
        DEFAULT_ASK_CONTEXT_TOKEN_CAP * BYTES_PER_TOKEN_FLOOR)


def test_item_cap_raise_carries_the_window_and_the_pool(monkeypatch):
    """Raising ONLY the item cap used to be a no-op: the retrieval call cut
    at ``result_ids[:limit]`` before assembly. The tandem invariant raises
    the window (and the pool) to admit it."""
    monkeypatch.setenv(ASK_CONTEXT_ITEM_CAP_ENV, "500")
    caps = resolve_ask_retrieval_caps()
    assert caps["context_item_cap"] == 500
    assert caps["limit"] >= 500
    assert caps["pool_size"] >= caps["limit"]


def test_pool_never_narrower_than_the_window(monkeypatch):
    monkeypatch.setenv(ASK_RETRIEVAL_LIMIT_ENV, "900")
    caps = resolve_ask_retrieval_caps()
    assert caps["limit"] == 900
    assert caps["pool_size"] >= 900


def test_token_raise_raises_the_derived_byte_ceiling(monkeypatch):
    """The historical bug: a token-cap raise was neutralised by the fixed
    32 KiB literal. The derived ceiling moves with the token cap."""
    monkeypatch.setenv(ASK_CONTEXT_TOKEN_CAP_ENV, "50000")
    caps = resolve_ask_retrieval_caps()
    assert caps["context_byte_cap"] >= 50000 * BYTES_PER_TOKEN_FLOOR
    assert caps["context_byte_cap"] > DEFAULT_CONTEXT_BYTE_CAP


def test_explicit_byte_cap_wins_and_garbage_falls_back(monkeypatch):
    monkeypatch.setenv(ASK_CONTEXT_BYTE_CAP_ENV, "4096")
    assert resolve_ask_retrieval_caps()["context_byte_cap"] == 4096
    monkeypatch.setenv(ASK_CONTEXT_BYTE_CAP_ENV, "not-a-number")
    caps = resolve_ask_retrieval_caps()
    assert caps["context_byte_cap"] == max(
        DEFAULT_CONTEXT_BYTE_CAP,
        caps["context_token_cap"] * BYTES_PER_TOKEN_FLOOR)


def test_out_of_range_window_env_falls_back_to_default(monkeypatch):
    """The resolution never hands the SDK a value it rejects: ``limit`` /
    ``item_cap`` are clamped to the same 1..10000 bound ``tortoise_fts_query``
    validates ``pool_size`` against, so an out-of-range env falls back to the
    default instead of failing every ask with a retrieval error."""
    monkeypatch.setenv(ASK_CONTEXT_ITEM_CAP_ENV, "20000")
    caps = resolve_ask_retrieval_caps()
    assert caps["limit"] <= 10000
    assert caps["pool_size"] <= 10000
    assert caps["context_item_cap"] == DEFAULT_ASK_CONTEXT_ITEM_CAP


def test_legacy_caps_dict_derives_the_byte_ceiling():
    """A caps dict that predates #4105 (no ``context_byte_cap``) must get a
    DERIVED ceiling from its own token cap — never the 32 KiB literal, which
    would re-open the silent no-op on that seam."""
    assert resolve_byte_cap_from_caps({"context_token_cap": 32000}) == (
        32000 * BYTES_PER_TOKEN_FLOOR)
    assert resolve_byte_cap_from_caps({}) == max(
        DEFAULT_CONTEXT_BYTE_CAP,
        DEFAULT_CONTEXT_TOKEN_CAP * BYTES_PER_TOKEN_FLOOR)
    assert resolve_byte_cap_from_caps({
        "context_token_cap": 32000, "context_byte_cap": 4096}) == 4096


# ── assemble_context: whole-hit byte drop + the binding-bound census ───────

def test_byte_cap_drops_whole_hits_and_is_a_hard_bound():
    hits = _hits(40)
    stats: dict = {}
    selected = assemble_context(
        hits, top_k=40, max_context_tokens=100000, context_item_cap=40,
        byte_cap=4096, stats=stats,
        question_date="2024-01-01")
    assert 0 < len(selected) < 40
    rendered = render_context(selected, question_date="2024-01-01")
    assert len(rendered.encode("utf-8")) <= 4096
    # whole-hit drop: every selected hit is a full original block
    ids = {h["id"] for h in hits}
    assert all(h["id"] in ids for h in selected)
    assert stats["byte_cap"] == 4096
    assert stats["dropped_by_byte_cap"] > 0
    assert stats["stopped_by"] in ("byte_cap", "item_cap")


def test_stats_name_the_byte_cap_as_the_binding_constraint():
    """The honest signal: hits the TOKEN cap admitted are refused by BYTES.
    ``dropped_by_byte_cap > 0`` is exactly "your byte budget, not your token
    budget, is what the caller cannot move"."""
    stats: dict = {}
    assemble_context(
        _hits(40), top_k=40, max_context_tokens=100000, context_item_cap=40,
        byte_cap=4096, stats=stats)
    assert stats["dropped_by_byte_cap"] > 0
    assert stats["dropped_by_token_cap"] == 0
    assert stats["stopped_by"] == "byte_cap"


def test_raising_the_item_cap_without_bytes_is_visible_not_silent():
    """#4105 second defect, reproduced as a test. At the historical 32 KiB
    ceiling, raising the item cap admits NO additional hits — but it is now
    OBSERVABLE via the census (and the ask lane warns), never a silent
    accepted-and-dropped budget."""
    small = _hits(40)
    big = _hits(200)
    stats_small: dict = {}
    stats_big: dict = {}
    sel_small = assemble_context(
        small, top_k=40, max_context_tokens=100000, context_item_cap=40,
        byte_cap=32768, stats=stats_small)
    sel_big = assemble_context(
        big, top_k=200, max_context_tokens=100000, context_item_cap=200,
        byte_cap=32768, stats=stats_big)
    assert stats_big["dropped_by_byte_cap"] > 0
    # The pool is richer but the reader window is byte-bound: the raise is a
    # no-op on the admitted set, and the census says so.
    assert len(sel_big) <= len(sel_small) + 1
    assert stats_big["stopped_by"] == "byte_cap"


def test_raising_the_byte_cap_makes_the_item_raise_real():
    stats: dict = {}
    selected = assemble_context(
        _hits(200), top_k=200, max_context_tokens=100000,
        context_item_cap=200, byte_cap=1 << 22, stats=stats)
    assert len(selected) == 200
    assert stats["dropped_by_byte_cap"] == 0


def test_token_cap_census_is_reported_separately():
    stats: dict = {}
    assemble_context(
        _hits(200), top_k=200, max_context_tokens=60,
        context_item_cap=200, byte_cap=None, stats=stats)
    assert stats["dropped_by_token_cap"] > 0
    assert stats["dropped_by_byte_cap"] == 0


def test_stats_absent_is_a_no_op_for_pure_callers():
    assert assemble_context(_hits(3), top_k=3, max_context_tokens=100000)


# ── the ask lane threads the resolved caps and warns when bytes bind ───────

class _FakeProj:
    pass


class _FakeSDK:
    _namespace = "w3a-test"

    def __init__(self, n_hits: int = 40):
        self.n_hits = n_hits
        self.query_kwargs: dict = {}
        self.seen: dict = {}

    def _get_proj(self):
        return _FakeProj()

    def tortoise_fts_query(self, query, **kwargs):
        self.query_kwargs = kwargs
        return _hits(self.n_hits, words=4000)
    def annotate_ask_hits(self, hits, **kwargs):
        return [dict(h) for h in hits]


class _CaptureReader:
    model = "capture"
    provider = "capture"
    route = "capture"
    last_route = "capture"
    last_prompt_tokens = 0
    last_completion_tokens = 0
    last_finish_reason = "stop"

    def __init__(self):
        self.user = None

    def complete(self, *, system, user, max_tokens=None):
        self.user = user
        return "I could not find that information. NO EVIDENCE."

    def close(self):
        pass


def _run(sdk, monkeypatch, **env):
    for k in _CAP_ENVS:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    reader = _CaptureReader()
    monkeypatch.setattr(ask_lane, "_default_ask_reader_factory",
                        lambda: reader)
    ask_lane._reset_ask_reader_cache_for_tests()
    try:
        res = ask_lane.run_ask_lane(sdk, "how many items?",
                                    question_date="2024-01-01")
    finally:
        ask_lane._reset_ask_reader_cache_for_tests()
    return res, reader


def test_ask_lane_threads_the_resolved_caps_into_retrieval(monkeypatch):
    sdk = _FakeSDK()
    _run(sdk, monkeypatch)
    assert sdk.query_kwargs["limit"] == DEFAULT_ASK_RETRIEVAL_LIMIT
    assert sdk.query_kwargs["pool_size"] == DEFAULT_ASK_POOL_SIZE


def test_ask_lane_passes_the_resolved_byte_cap_to_assembly(monkeypatch):
    sdk = _FakeSDK()
    seen: dict = {}
    real = __import__("tortoise.retrieval", fromlist=["assemble_context"])
    original = real.assemble_context

    def _spy(pool, **kwargs):
        seen.update(kwargs)
        return original(pool, **kwargs)

    monkeypatch.setattr(real, "assemble_context", _spy)
    _run(sdk, monkeypatch, TORTOISE_ASK_CONTEXT_BYTE_CAP=4096)
    assert seen["byte_cap"] == 4096
    assert seen["context_item_cap"] == DEFAULT_ASK_CONTEXT_ITEM_CAP


def test_ask_lane_warns_when_the_byte_cap_is_what_dropped_hits(
        monkeypatch, caplog):
    """The honest-budget contract: when the byte ceiling — not the token
    ceiling — is the binding constraint, the lane SAYS SO. Never accept a
    budget and drop evidence silently."""
    sdk = _FakeSDK(n_hits=40)
    with caplog.at_level(logging.WARNING, logger=ask_lane.__name__):
        _run(sdk, monkeypatch, TORTOISE_ASK_CONTEXT_BYTE_CAP=4096)
    assert any("byte cap" in r.message and "dropped" in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_ask_lane_does_not_warn_when_bytes_do_not_bind(monkeypatch, caplog):
    sdk = _FakeSDK(n_hits=5)
    with caplog.at_level(logging.WARNING, logger=ask_lane.__name__):
        _run(sdk, monkeypatch)
    assert not any("byte cap" in r.message for r in caplog.records)
