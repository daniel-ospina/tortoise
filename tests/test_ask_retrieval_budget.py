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
    MAX_ASK_CONTEXT_BYTE_CAP,
    assemble_context,
    estimate_tokens_ask,
    render_context,
    resolve_ask_retrieval_caps,
    resolve_byte_cap_from_caps,
    resolve_item_cap_from_caps,
    resolve_limit_from_caps,
    resolve_token_cap_from_caps,
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
    # A LITERAL pin of the shipped window. Comparing only against the
    # ``DEFAULT_*`` constants cannot detect a changed literal (both sides
    # would move together), so the measured 200/200/200/16000/128000 window
    # is asserted here verbatim — a typo in ``DEFAULT_ASK_RETRIEVAL_LIMIT``
    # must fail this test.
    assert caps == {
        "limit": 200,
        "pool_size": 200,
        "context_item_cap": 200,
        "context_token_cap": 16000,
        "context_byte_cap": 128000,
    }
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
    # A typo must not pin the ceiling to the 32 KiB floor and silently
    # re-introduce the no-op, and a NON-POSITIVE value must never be honoured
    # (0/negative would drop every hit) — all of them derive instead.
    for garbage in ("not-a-number", "0", "-5", "", "  "):
        monkeypatch.setenv(ASK_CONTEXT_BYTE_CAP_ENV, garbage)
        caps = resolve_ask_retrieval_caps()
        assert caps["context_byte_cap"] == max(
            DEFAULT_CONTEXT_BYTE_CAP,
            caps["context_token_cap"] * BYTES_PER_TOKEN_FLOOR), garbage
    # …and the upper clamp is shared, so an absurd value cannot unbind it.
    monkeypatch.setenv(ASK_CONTEXT_BYTE_CAP_ENV, str(1 << 50))
    assert resolve_ask_retrieval_caps()["context_byte_cap"] == (
        MAX_ASK_CONTEXT_BYTE_CAP)


def test_a_token_cap_typo_cannot_resolve_an_unbounded_budget(monkeypatch):
    """The token cap feeds the DERIVED byte ceiling, so it needs the same
    out-of-range fallback + upper clamp as every other knob — otherwise one
    extra zero resolves a context budget unbounded in BOTH dimensions."""
    monkeypatch.setenv(ASK_CONTEXT_TOKEN_CAP_ENV, "1000000000000000")
    caps = resolve_ask_retrieval_caps()
    assert caps["context_token_cap"] == DEFAULT_ASK_CONTEXT_TOKEN_CAP
    assert caps["context_byte_cap"] == max(
        DEFAULT_CONTEXT_BYTE_CAP,
        caps["context_token_cap"] * BYTES_PER_TOKEN_FLOOR)


@pytest.mark.parametrize(("env", "key", "default"), (
    (ASK_RETRIEVAL_LIMIT_ENV, "limit", DEFAULT_ASK_RETRIEVAL_LIMIT),
    (ASK_CONTEXT_ITEM_CAP_ENV, "context_item_cap", DEFAULT_ASK_CONTEXT_ITEM_CAP),
    (ASK_POOL_SIZE_ENV, "pool_size", DEFAULT_ASK_POOL_SIZE),
))
@pytest.mark.parametrize("value", ("20000", "0", "-5", "garbage"))
def test_out_of_range_window_env_falls_back_to_default(
        monkeypatch, env, key, default, value):
    """The resolution never hands the SDK a value it rejects: EACH of
    ``limit`` / ``item_cap`` / ``pool_size`` is clamped to the same 1..10000
    bound ``tortoise_fts_query`` validates ``pool_size`` against, so an
    out-of-range env falls back to the default instead of failing every ask
    with a retrieval error.

    Parametrized per variable on purpose: probing only the item cap leaves
    the ``limit`` and ``pool_size`` clamps unpinned, because a fallback item
    cap makes ``limit <= 10000`` and ``pool_size <= 10000`` tautologies.
    """
    monkeypatch.setenv(env, value)
    caps = resolve_ask_retrieval_caps()
    assert caps[key] == default, (env, value, caps[key])
    assert caps["limit"] <= 10000
    assert caps["pool_size"] <= 10000


def test_legacy_caps_dict_derives_the_byte_ceiling(monkeypatch):
    """A caps dict that predates #4105 (no ``context_byte_cap``) must get a
    DERIVED ceiling from its own token cap — never the 32 KiB literal, which
    would re-open the silent no-op on that seam."""
    assert resolve_byte_cap_from_caps({"context_token_cap": 32000}) == (
        32000 * BYTES_PER_TOKEN_FLOOR)
    # No token cap in the dict → the ASK-LANE default token cap, so the
    # unset-env case agrees with ``resolve_ask_retrieval_caps`` too (the
    # shared 8000-token default would have resolved a third value).
    assert resolve_byte_cap_from_caps({}) == max(
        DEFAULT_CONTEXT_BYTE_CAP,
        DEFAULT_ASK_CONTEXT_TOKEN_CAP * BYTES_PER_TOKEN_FLOOR)
    assert resolve_byte_cap_from_caps({}) == \
        resolve_ask_retrieval_caps()["context_byte_cap"]
    assert resolve_byte_cap_from_caps({
        "context_token_cap": 32000, "context_byte_cap": 4096}) == 4096
    # A non-positive or absurd dict value is validated + clamped on the SAME
    # parser the env path uses, so the two resolutions cannot disagree.
    derived = max(DEFAULT_CONTEXT_BYTE_CAP,
                  DEFAULT_ASK_CONTEXT_TOKEN_CAP * BYTES_PER_TOKEN_FLOOR)
    for bad in (0, -1):
        assert resolve_byte_cap_from_caps({"context_byte_cap": bad}) == derived
    assert resolve_byte_cap_from_caps(
        {"context_byte_cap": 1 << 50}) == MAX_ASK_CONTEXT_BYTE_CAP


def test_legacy_token_cap_values_are_validated_on_the_dict_seam(monkeypatch):
    """The DERIVED leg is the last resort, so it must not be the one place a
    nominal value slips through unvalidated: an unvalidated dict token cap
    would resolve a different ceiling on the dict seam than the env seam
    resolves for the same nominal input (and would RAISE on ``None``/str)."""
    derived = max(DEFAULT_CONTEXT_BYTE_CAP,
                  DEFAULT_ASK_CONTEXT_TOKEN_CAP * BYTES_PER_TOKEN_FLOOR)
    # out-of-range / non-positive / non-int dict values fall back, exactly as
    # the env knob does — never a raise, never a third ceiling.
    for bad in (0, -1, 1 << 30, None, "not-a-number", 4096.5, True):
        assert resolve_byte_cap_from_caps(
            {"context_token_cap": bad}) == derived, bad
    # a numeric STRING is accepted on both seams, to the same value
    monkeypatch.setenv(ASK_CONTEXT_TOKEN_CAP_ENV, "32000")
    assert resolve_byte_cap_from_caps({"context_token_cap": "32000"}) == 256000
    assert resolve_ask_retrieval_caps()["context_byte_cap"] == 256000
    monkeypatch.delenv(ASK_CONTEXT_TOKEN_CAP_ENV)
    assert resolve_byte_cap_from_caps(
        {"context_token_cap": "32000"}) == 256000
    # The env leg must be honoured too, or a legacy-dict caller assembles at a
    # different ceiling than the env-pinned ask lane and an A/B across the two
    # seams compares budgets instead of behaviour.
    monkeypatch.setenv(ASK_CONTEXT_BYTE_CAP_ENV, "32768")
    assert resolve_byte_cap_from_caps({}) == 32768
    assert resolve_byte_cap_from_caps({"context_token_cap": 8000}) == 32768
    assert resolve_ask_retrieval_caps()["context_byte_cap"] == 32768
    # …and an explicit dict key still wins over the env.
    assert resolve_byte_cap_from_caps({"context_byte_cap": 4096}) == 4096


# ── assemble_context: whole-hit byte drop + the binding-bound census ───────

def test_an_absent_token_key_follows_the_env_on_the_dict_seam(monkeypatch):
    """#4105 review fix: on the dict seam an ABSENT token key must resolve the
    same env knob the env seam resolves — otherwise the DERIVED byte ceiling
    (and the budget the assembly enforces) diverge from the env-pinned lane
    for the same nominal input."""
    monkeypatch.setenv(ASK_CONTEXT_TOKEN_CAP_ENV, "32000")
    assert resolve_token_cap_from_caps({}) == 32000
    assert resolve_byte_cap_from_caps({}) == 32000 * BYTES_PER_TOKEN_FLOOR
    assert resolve_byte_cap_from_caps({}) == \
        resolve_ask_retrieval_caps()["context_byte_cap"]
    # an explicit dict key still wins over the env, on BOTH halves
    assert resolve_token_cap_from_caps({"context_token_cap": 8000}) == 8000
    assert resolve_byte_cap_from_caps({"context_token_cap": 8000}) == max(
        DEFAULT_CONTEXT_BYTE_CAP, 8000 * BYTES_PER_TOKEN_FLOOR)


def test_item_cap_and_limit_are_validated_on_the_dict_seam(monkeypatch):
    """The same validation the env seam applies: a legacy caps dict must not
    turn a bad entry into a failed ask, or into a silently different window."""
    for bad in (0, -1, 1 << 30, None, "not-a-number", 4096.5, True):
        assert resolve_item_cap_from_caps({"context_item_cap": bad}) == \
            DEFAULT_ASK_CONTEXT_ITEM_CAP
        assert resolve_limit_from_caps({"limit": bad}) == \
            DEFAULT_ASK_RETRIEVAL_LIMIT
    # ``limit >= context_item_cap`` is re-applied on this seam
    assert resolve_limit_from_caps({"context_item_cap": 500}) >= 500
    assert resolve_limit_from_caps(
        {"limit": 10, "context_item_cap": 500}) >= 500
    # an absent key resolves the env knob, exactly like the env seam
    monkeypatch.setenv(ASK_RETRIEVAL_LIMIT_ENV, "777")
    assert resolve_limit_from_caps({}) == 777


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
    # whole-hit drop: every selected hit's FULL content is present (an
    # implementation that truncated a hit's content to fit the byte budget
    # would keep the id and still pass an `id in ids` check, so assert on
    # the rendered text instead).
    for h in selected:
        assert h["content"] in rendered
    assert stats["byte_cap"] == 4096
    assert stats["dropped_by_byte_cap"] > 0
    assert stats["stopped_by"] == "byte_cap"


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
        _hits(250), top_k=200, max_context_tokens=100000,
        context_item_cap=200, byte_cap=1 << 22, stats=stats)
    assert len(selected) == 200
    assert stats["dropped_by_byte_cap"] == 0
    # The pool ran PAST the item bound and nothing was dropped by a budget —
    # so the ITEM cap is the bound that cut it.
    assert stats["stopped_by"] == "item_cap"


def test_stopped_by_is_none_when_the_pool_merely_ended():
    """A pool that ENDS exactly at the item bound did not have the item cap
    bind — reporting ``item_cap`` there mislabels the census a caller reads to
    decide which cap to raise."""
    stats: dict = {}
    selected = assemble_context(
        _hits(3), top_k=200, max_context_tokens=100000,
        context_item_cap=200, byte_cap=1 << 22, stats=stats)
    assert len(selected) == 3
    assert stats["stopped_by"] is None


def test_token_cap_census_is_reported_separately():
    stats: dict = {}
    assemble_context(
        _hits(200), top_k=200, max_context_tokens=60,
        context_item_cap=200, byte_cap=None, stats=stats)
    assert stats["dropped_by_token_cap"] > 0
    assert stats["dropped_by_byte_cap"] == 0
    assert stats["stopped_by"] == "token_cap"


def test_stopped_by_is_none_when_no_bound_dropped_a_hit():
    stats: dict = {}
    assemble_context(_hits(3), top_k=3, max_context_tokens=100000,
                     context_item_cap=200, byte_cap=1 << 22, stats=stats)
    assert stats["items_selected"] == 3
    assert stats["dropped_by_token_cap"] == 0
    assert stats["dropped_by_byte_cap"] == 0
    assert stats["stopped_by"] is None


def test_nonascii_text_is_bounded_by_the_token_cap_not_only_the_byte_cap():
    """#4105 review fix. The token budget is whitespace-word based, so it is
    blind to unspaced CJK/emoji runs; the old 32 KiB literal happened to
    compensate on those pools, and the DERIVED ceiling does not. The
    estimator's surcharge is therefore charged in the same accounting, so
    ``estimate_tokens_ask(evidence) <= token cap`` holds on every script —
    for ASCII and non-ASCII alike."""
    for label, run in (("cjk", "中"), ("emoji", "🏡")):
        # 3000 chars per hit (``_hits`` truncates to ``words * 6``).
        hits = _hits(10, words=500, content=run * 3000)
        stats: dict = {}
        selected = assemble_context(
            hits, top_k=200, max_context_tokens=16000,
            context_item_cap=200, byte_cap=128000,
            nonascii_token_surcharge=True, stats=stats)
        evidence = render_context(selected)
        assert len(evidence.encode("utf-8")) <= 128000, label
        assert estimate_tokens_ask(evidence) <= 16000, (label,
                                                        evidence[:40])
        assert stats["dropped_by_token_cap"] > 0, label
        assert stats["stopped_by"] == "token_cap", label
        assert stats["nonascii_token_surcharge"] > 0, label


def test_ascii_accounting_is_unchanged_by_the_surcharge():
    """The surcharge is ZERO for pure-ASCII text, so ASCII-only callers (and
    the frozen transcripts) are byte-identical to the pre-#4105 arithmetic."""
    stats: dict = {}
    selected = assemble_context(
        _hits(8), top_k=200, max_context_tokens=16000,
        context_item_cap=200, byte_cap=128000,
        nonascii_token_surcharge=True, stats=stats)
    assert stats["nonascii_token_surcharge"] == 0
    assert selected == assemble_context(
        _hits(8), top_k=200, max_context_tokens=16000,
        context_item_cap=200, byte_cap=128000,
        nonascii_token_surcharge=True)


def test_surcharge_is_opt_in_so_the_eval_reexport_is_byte_identical():
    """#2070 boundary (recorded decision, docs/planning/
    2026-08-31-2070-scoping-package.md:78): the eval re-export of
    ``assemble_context`` must be byte-identical to the pre-#4105 function
    unless the measurement opts in. The surcharge is therefore OPT-IN — the
    DEFAULT call charges nothing, even on a CJK pool — and only the ask-lane
    call sites pass ``nonascii_token_surcharge=True``."""
    hits = _hits(10, words=500, content="中" * 3000)
    default_stats: dict = {}
    default_selected = assemble_context(
        hits, top_k=200, max_context_tokens=16000,
        context_item_cap=200, byte_cap=128000, stats=default_stats)
    assert default_stats["nonascii_token_surcharge"] == 0
    opted_stats: dict = {}
    opted_selected = assemble_context(
        hits, top_k=200, max_context_tokens=16000,
        context_item_cap=200, byte_cap=128000,
        nonascii_token_surcharge=True, stats=opted_stats)
    assert opted_stats["nonascii_token_surcharge"] > 0
    # The opted-in accounting is strictly tighter: it can only ever drop
    # MORE from the same pool, never admit a hit the default refused.
    assert len(opted_selected) <= len(default_selected)


def test_stats_absent_is_a_no_op_for_pure_callers():
    hits = _hits(3)
    assert assemble_context(hits, top_k=3, max_context_tokens=100000) == \
        assemble_context(hits, top_k=3, max_context_tokens=100000, stats={})


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
