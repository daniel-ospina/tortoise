"""#2070 — ask-lane retrieval optimisation loop tests.

Spec items 9/10 of docs/planning/2026-08-31-2070-scoping-package.md:

- 4 gold-turn-inclusion fixtures (ceb54acb / 1de5cff2 / gpt4_d84a3211 /
  1d4e3b97 — Acceptance Indicator 1 / Target 0: the gold turn id must land
  in the ASSEMBLED context under the caps). **Runtime prerequisite (plan
  A2): the vector leg (embeddings extra) is required for the lexical-trio
  class — when the embedder is absent the fixture skips with that reason,
  exactly as the bench's docker lane does. On the degraded embedded lane
  the honest assertions are POOL MEMBERSHIP (the plan's "fix membership
  before ordering" step), which is what A1/A4/A5 deliver there.**
- **Runtime prerequisite (#11056): the bench is ARMED EXPLICITLY.** The
  dataset defaults to an ambient home-directory cache, so before #11056 a
  fleet box's `$HOME` — not CI config — decided whether a ~20-minute bench
  ran inline in the FAST gating shard, and `test (c)` was withheld on a diff
  that never touched this file. The bench now arms on `TORTOISE_LME_BENCH=1`
  or an explicit `TORTOISE_LME_DATASET` path, and skips with the reason that
  actually applies. Run it with:
  `TORTOISE_LME_BENCH=1 uv run pytest tests/test_ask_retrieval_levers.py`
  (dataset cached, embeddings extra installed).**
- Same-value numeric fixture (A1): a money question that itself carries an
  amount retrieves the amount-carrying turn with keep_numeric on.
- OR-cap preservation (A4): injected expansion aliases never displace the
  ORIGINAL query's tokens (the regression guard).
- Search-lane byte-identity control (A1/A4/A3): the search lane passes no
  ask knobs → identical query bytes and results.
- retrieval_degraded honesty (A2): the ask lane still reports degraded
  when the vector leg is absent — never a silent success.
- Ask-lane (eval-only) rerank degrade-path (A7): tortoise/rerank.py degrades to
  untouched (applied False) on scorer failure, never raises.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_ask_cap_env(monkeypatch):
    """The mirror's byte ceiling is DERIVED from its own token cap, so an
    ambient ``TORTOISE_ASK_CONTEXT_BYTE_CAP`` would silently redirect it to
    the env value and break the mirror contract (same guard as
    ``tests/test_ask_retrieval_budget.py``)."""
    from tortoise.retrieval import ASK_CONTEXT_BYTE_CAP_ENV
    monkeypatch.delenv(ASK_CONTEXT_BYTE_CAP_ENV, raising=False)
    yield


_CACHED_DATASET = Path(
    os.environ.get(
        "TORTOISE_LME_DATASET",
        "~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json",
    )).expanduser()
_MINIFIX = _REPO / "tests" / "fixtures" / "longmemeval_mini.json"
RECORDED_FAILURES = ["ceb54acb", "1de5cff2", "gpt4_d84a3211", "1d4e3b97"]

# #11056: the EXPLICIT arm for the LongMemEval bench — see `_bench_opted_in()`.
_BENCH_OPT_IN_ENV = "TORTOISE_LME_BENCH"


def _bench_opted_in() -> bool:
    """Whether the LongMemEval bench is explicitly ARMED (#11056).

    `_CACHED_DATASET` defaults to an **ambient home-directory file**, so before
    #11056 whether a ~20-minute bench ran inline in the FAST gating shard was
    decided by whichever fleet box the shard landed on, not by CI config.
    Measured on the self-hosted pool (run 42120750405, job `test (c)`,
    `pi-runner-cmux-x64-7`): this file alone cost **1,252 s of a 1,560 s**
    shard watchdog, and `test_gold_turn_in_pool_membership_embedded` seeds
    three haystacks (~570 s) inside a per-test `--timeout=300`, so the shard
    first reported a `FAILED` and then died on its own budget — a required
    check withheld by a box's `$HOME`, on a diff that never touched this file.

    Two things make that ambient default wrong as a CI prerequisite: the repo's
    stated CI assumption is the opposite (`tests/longmem_eval/`
    `test_gold_evidence_claims.py` skips with *"frozen LongMemEval-S dataset
    not cached (CI)"*), and a **shared runner's `$HOME` is not a CI
    prerequisite** — it is an accident of the box.

    So the prerequisite is now EXPLICIT, on the same shape its sibling
    `tests/test_ask_regression_llm.py` already uses for
    `TORTOISE_ASK_LLM_REGRESSION`: the bench arms only when it is ASKED for —
    `TORTOISE_LME_BENCH=1`, or an explicit `TORTOISE_LME_DATASET` path (a
    deliberate act, unlike the default it overrides). The ambient
    `~/.cache/tortoise-longmemeval/` default alone arms nothing, so the SKIP
    path is deterministic on every runner and the run path is where someone
    opted in. Nothing is weakened: with the opt-in set the bench still runs
    end to end wherever the dataset is cached.

    ⛔ AN EXPLICIT OFF OUTRANKS THE PATH ARM, and this is not decoration. Without
    it the gate still has no authoritative "no": a fleet box that exports
    `TORTOISE_LME_DATASET` (the ordinary way someone points the bench at a
    dataset) re-arms the measured 1,252 s bench in the fast gating shard, and
    `TORTOISE_LME_BENCH=0` is silently ignored — the same accident class this
    gate exists to remove, one indirection shallower. With the OFF in place the
    only way to arm it is a spelling that asks for it.
    """
    flag = (os.environ.get(_BENCH_OPT_IN_ENV) or "").strip()
    # ⛔ EVERY ORDINARY "OFF" SPELLING IS AN OFF, not just the literal "0" (review finding,
    # 2026-11-06). `false` is the commonest CI spelling of "off", and it used to fall through
    # to the dataset arm: with `TORTOISE_LME_DATASET` exported (the ordinary way someone points
    # the bench at a dataset) `TORTOISE_LME_BENCH=false` ARMED the measured 1,252 s inline bench
    # in the fast gating shard — the withheld-required-check failure #11056 exists to remove,
    # reached from a spelling that reads as a refusal. The sibling gate this mirrors
    # (`tests/test_ask_regression_llm.py:62`) arms on `== "1"` only, so every other spelling is
    # off there; this predicate keeps the explicit path as an opt-in, so it must enumerate the
    # OFF spellings instead. Armed ONLY by a spelling that asks for it.
    # ⛔ DENY-BY-DEFAULT: the set of "off" spellings is OPEN, the set of ARMING
    # spellings is not. A blacklist (`0/false/no/off`) was still a fail-open: with
    # `TORTOISE_LME_DATASET` exported, `disabled`, `none`, `n`, `2` and the empty
    # string all ARMED the measured 1,252 s inline bench in the fast gating shard —
    # a refusal that reads as a refusal and does the opposite, which is the exact
    # failure #11056 exists to remove (#11065 cycle 3, review finding). So the
    # predicate is inverted: anything set that is not an ARMING spelling is an OFF.
    # An UNSET flag is not a veto — the dataset path alone still arms it (ARM 3).
    _ARMING = {"1", "true", "yes", "on"}
    if flag and flag.lower() not in _ARMING:
        return False
    return flag.lower() in _ARMING or bool(os.environ.get("TORTOISE_LME_DATASET"))


def _bench_questions() -> dict[str, dict]:
    """The recorded questions this module asserts against, or a SKIP naming
    WHICH prerequisite is missing.

    The two prereqs are DIFFERENT facts and are reported as such: a fleet
    runner that skips because nobody armed the bench must never read as "the
    dataset is gone", and a missing dataset must never read as "not opted
    in". Every skip is explicit, so no assertion here can pass vacuously.
    """
    if not _bench_opted_in():
        pytest.skip(
            "LongMemEval bench not armed — set TORTOISE_LME_BENCH=1 (or an "
            "explicit TORTOISE_LME_DATASET) to run it. The ambient "
            "~/.cache/tortoise-longmemeval/ default does NOT arm it: a shared "
            "runner's $HOME is not a CI prerequisite (#11056)")
    questions = _recorded_questions()
    if not questions:
        pytest.skip("cached LongMemEval dataset absent (bench prerequisite)")
    return questions


def _embedder_present() -> bool:
    from tortoise.embeddings import EmbeddingModel
    return EmbeddingModel.get() is not None


def _fresh_sdk() -> TortoiseSDK:
    td = tempfile.mkdtemp(prefix="ask2070_")
    return TortoiseSDK(os.path.join(td, "lme.db"))


def _gold_turn_ids(question: dict) -> set[str]:
    qid = question["question_id"]
    ids: set[str] = set()
    for si, session in enumerate(question.get("haystack_sessions") or []):
        for ti, turn in enumerate(session):
            if turn.get("has_answer"):
                ids.add(f"lme:{qid}:s{si}:t{ti}")
    return ids


def _seed(sdk, question: dict) -> None:
    from tools.longmem_eval.ingest import ingest_haystack
    ingest_haystack(sdk, question)


def _ask_pipeline(sdk, question: str, *, keep_numeric: bool = False,
                  search_keys_prf: bool = False,
                  evidence_boost: bool = False,
                  limit: int = 40, item_cap: int = 40) -> list[dict]:
    """The ask lane's retrieval→annotate→dedup→boost→assemble sequence
    (the retrieval SEQUENCE mirrors tools/ask_recall_bench.py::
    _retrieve_pipeline; no reader).

    The BYTE ceiling deliberately does NOT mirror the bench's frozen
    ``BYTE_CAP = 32768`` (that bench is the pre-#4105 baseline of record): it
    is the LANE's resolved cap, derived below from this helper's own
    8000-token budget (#4105 — the lane's ceiling was a 32 KiB literal, which
    silently capped every item/token raise above it). A byte ceiling derived
    from a token cap this helper does not apply would admit a different set
    than the pipeline it mirrors."""
    from tortoise.retrieval import (
        DEFAULT_MAX_CHUNKS_PER_SESSION,
        apply_evidence_boost,
        ask_session_key,
        assemble_context,
        dedup_pool,
        resolve_ask_boost_multipliers,
        resolve_byte_cap_from_caps,
    )
    # The byte ceiling is DERIVED from this helper's own 8000-token cap (not
    # the product's 16000-token default) so the mirror stays a mirror: a byte
    # ceiling derived from a token cap the helper does not apply would admit a
    # different set than the pipeline it mirrors. ``_clean_ask_cap_env``
    # pins the byte env OFF for the same reason — with it set, the derivation
    # follows the ENV rather than this helper's token cap.
    byte_cap = resolve_byte_cap_from_caps(
        {"context_token_cap": 8000})
    hits = sdk.tortoise_fts_query(
        question, limit=limit, pool_size=120, include_terminal=True,
        keep_numeric=keep_numeric, search_keys_prf=search_keys_prf)
    annotated = sdk.annotate_ask_hits(hits)
    # #4155: the lane's key verbatim — the mirror must not hold a stale copy.
    deduped = dedup_pool(
        annotated, max_chunks_per_session=DEFAULT_MAX_CHUNKS_PER_SESSION,
        session_key=ask_session_key)
    if evidence_boost:
        mult = resolve_ask_boost_multipliers()
        deduped, _ = apply_evidence_boost(
            deduped, boost_answer_string=mult["answer_string"],
            boost_verbatim=mult["verbatim"], boost_source=mult["source"])
    return assemble_context(
        deduped, top_k=item_cap, max_context_tokens=8000,
        context_item_cap=item_cap, byte_cap=byte_cap)


def _recorded_questions() -> dict[str, dict]:
    """The recorded questions the bench asserts against — **the ONE gate**.

    Returns `{}` unless the bench is explicitly armed (#11056), so no caller
    can re-arm it by accident: a future test that reads this reader directly
    fails closed to "nothing to assert" instead of silently spending a
    shard's budget on an ambient `$HOME` cache. `_bench_questions()` builds
    the honest SKIP reason on top of it; nothing bypasses it.
    """
    if not _bench_opted_in():
        return {}
    if not _CACHED_DATASET.exists():
        return {}
    data = json.loads(_CACHED_DATASET.read_text(encoding="utf-8"))
    return {q["question_id"]: q for q in data if q["question_id"] in RECORDED_FAILURES}


def test_the_bench_is_armed_explicitly_never_by_an_ambient_home_cache(
        monkeypatch, tmp_path):
    """#11056: an ambient `$HOME` cache must NOT decide whether a ~20-minute
    bench runs inline in the FAST gating shard.

    This is the pin for the arm that withheld a required check: on the
    self-hosted pool the default `~/.cache/tortoise-longmemeval/...` path
    EXISTS, so `_recorded_questions()` returned the dataset's questions with
    no opt-in anywhere and this file cost 1,252 s of a 1,560 s shard watchdog
    (run 42120750405, job `test (c)`) — on a diff that never touched it.

    Non-vacuous in THREE directions:

    * ARM 1 reds the PRE-FIX code, which had no predicate at all — there
      `_recorded_questions()` returned `{qid}` with the dataset merely
      present, so `== {}` fails.
    * ARM 2 reds a gate tightened into a NO-OP that can never be armed (a
      `return False`, or one that demands the dataset path AND the flag):
      `TORTOISE_LME_BENCH=1` alone must arm it with `TORTOISE_LME_DATASET`
      unset.
    * ARM 3 reds a gate that ignores the explicit path — the documented way to
      run the bench at a fetched dataset (`TORTOISE_LME_DATASET`), which is a
      DELIBERATE act and therefore a valid opt-in on its own.

    The dataset file is written to `tmp_path` and installed as the module's
    `_CACHED_DATASET`, so the arms do not depend on — and never touch — the
    real home-directory cache: a runner without it must fail this pin too,
    not skip it.
    """
    mod = sys.modules[__name__]
    qid = RECORDED_FAILURES[0]
    dataset = tmp_path / "longmemeval_s_cleaned.json"
    dataset.write_text(json.dumps([{
        "question_id": qid,
        "question": "a recorded question",
        "haystack_sessions": [],
    }]), encoding="utf-8")
    monkeypatch.setattr(mod, "_CACHED_DATASET", dataset)
    monkeypatch.delenv(_BENCH_OPT_IN_ENV, raising=False)
    monkeypatch.delenv("TORTOISE_LME_DATASET", raising=False)

    # ARM 1 — the dataset is PRESENT and readable, and the bench is STILL not
    # armed. Presence alone is the ambient-$HOME decision this pin removes,
    # and `_recorded_questions()` is the ONE gate, so a future caller that
    # reads it directly also fails closed.
    assert dataset.exists(), "the fixture must be present for arm 1 to bite"
    assert _bench_opted_in() is False
    assert _recorded_questions() == {}
    # ⛔ `match=` is load-bearing: a bare `pytest.raises(pytest.skip.Exception)`
    # accepts ANY skip, so the two prereqs could be SWAPPED — or the dataset-absent
    # branch deleted outright — with this pin still green (#11065 cycle 3, review
    # finding; both mutations were measured green). `_bench_questions()`'s own
    # docstring says these "must never read as" each other; ARM 1 and ARM 6 are what
    # enforce it.
    with pytest.raises(pytest.skip.Exception, match="not armed"):
        _bench_questions()

    # ARM 2 — the flag alone arms it, with TORTOISE_LME_DATASET unset.
    monkeypatch.setenv(_BENCH_OPT_IN_ENV, "1")
    assert _bench_opted_in() is True
    assert set(_recorded_questions()) == {qid}

    # ARM 3 — an explicit dataset PATH arms it on its own (#11056's "make the
    # prerequisite explicit rather than ambient").
    monkeypatch.delenv(_BENCH_OPT_IN_ENV)
    monkeypatch.setenv("TORTOISE_LME_DATASET", str(dataset))
    assert _bench_opted_in() is True
    assert set(_recorded_questions()) == {qid}

    # ARM 4 — an explicit OFF OUTRANKS the dataset-path arm. Without this arm
    # the gate has no authoritative "no": a box that exports
    # TORTOISE_LME_DATASET re-arms the 1,252 s bench from a spelling that reads
    # as a refusal, and CI cannot turn it off. Fails RED on the pre-ARM-4 code.
    monkeypatch.setenv(_BENCH_OPT_IN_ENV, "0")
    assert dataset.exists(), "arm 4 must bite on a PRESENT dataset path"
    assert _bench_opted_in() is False
    assert _recorded_questions() == {}
    with pytest.raises(pytest.skip.Exception, match="not armed"):
        _bench_questions()

    # ARM 5 — every ORDINARY "off" spelling is an off, not just the literal "0" (review
    # finding, 2026-11-06). `false` is the commonest CI spelling of "off" and it used to ARM
    # the bench when a dataset path was exported — a refusal that reads as a refusal and does
    # the opposite. Each spelling is asserted separately so a fix that only widened to one of
    # them cannot pass.
    for off_spelling in ("false", "False", "FALSE", "no", "off", "OFF"):
        monkeypatch.setenv(_BENCH_OPT_IN_ENV, off_spelling)
        assert dataset.exists(), f"arm 5 must bite on a PRESENT dataset path ({off_spelling})"
        assert _bench_opted_in() is False, (
            f"TORTOISE_LME_BENCH={off_spelling!r} ARMED the ~20-minute bench while "
            f"TORTOISE_LME_DATASET is exported — a spelling that reads as a refusal must be "
            f"one (#11056)"
        )
        assert _recorded_questions() == {}, f"{off_spelling!r} must not reach the dataset"

    # ARM 6 — the OTHER prerequisite is reported as itself (#11065 cycle 3, review
    # finding). ARMED, with the dataset file pointed at a path that is not there: the
    # skip must name the DATASET, never "not armed". Without this arm the two branches
    # could be swapped, or the dataset branch deleted, with ARM 1 still green.
    # `_CACHED_DATASET` is the module global `_recorded_questions()` actually reads, so
    # this arm moves IT (moving only the env var would leave the present fixture in
    # place and the arm would pass for the wrong reason).
    monkeypatch.setenv(_BENCH_OPT_IN_ENV, "1")
    monkeypatch.delenv("TORTOISE_LME_DATASET", raising=False)
    monkeypatch.setattr(mod, "_CACHED_DATASET", tmp_path / "absent-dataset.json")
    assert _bench_opted_in() is True, "ARM 6 must be an ARMED run to isolate the dataset arm"
    with pytest.raises(pytest.skip.Exception, match="dataset absent"):
        _bench_questions()


# ── Indicator 1 / Target 0: 4 gold-turn-in-context fixtures ────────────────

@pytest.mark.parametrize("qid", RECORDED_FAILURES)
def test_gold_turn_in_context_recorded_failure(qid):
    """Indicator 1 / Target 0: each recorded retrieval-gap failure retrieves
    its gold turn WITHIN the context caps on the vector-leg lane (A2's
    runtime prerequisite — the plan's acceptance surface). Verified live on
    the docker lane: 4/4 gold-in-ctx(40) with A1+A4+A5 levers, caps at 40."""
    if not _embedder_present():
        pytest.skip(
            "vector leg absent (embeddings extra not installed) — the "
            "lexical-trio class needs it (plan A2 runtime prerequisite); "
            "see the pool-membership tests for the degraded-lane assertions")
    questions = _bench_questions()
    q = questions.get(qid)
    if q is None:
        pytest.skip(f"{qid} not in cached dataset")
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        gold = _gold_turn_ids(q)
        assert gold, f"{qid}: dataset question carries no has_answer turns"
        ctx = _ask_pipeline(
            sdk, q["question"], keep_numeric=True,
            search_keys_prf=True, evidence_boost=True)
        ctx_ids = {h["id"] for h in ctx}
        assert ctx_ids & gold, (
            f"{qid}: gold turns {sorted(gold)} all missed the assembled "
            f"context (got {len(ctx_ids)} ids)"
        )
    finally:
        sdk.close()


# ── Degraded-lane honesty: pool membership (the "fix membership" step) ─────

def test_gold_turn_in_pool_membership_embedded():
    """On the degraded embedded lane (no vector leg), the A1/A4/A5 levers
    deliver POOL MEMBERSHIP for the rank/cap + thin-overlap classes (the
    plan's "fix membership before ordering" step). gpt4_d84a3211 is the
    numeric-invisible class whose membership requires the vector leg (A2) —
    asserted separately with its documented prerequisite."""
    questions = _bench_questions()
    for qid, q in questions.items():
        if qid == "gpt4_d84a3211":
            continue  # documented: needs the vector leg (A2), not A1
        sdk = _fresh_sdk()
        try:
            _seed(sdk, q)
            gold = _gold_turn_ids(q)
            assert gold, f"{qid}: no has_answer turns"
            hits = sdk.tortoise_fts_query(
                q["question"], limit=120, pool_size=120,
                include_terminal=True, keep_numeric=True,
                search_keys_prf=True)
            pool_ids = {h["id"] for h in hits}
            assert pool_ids & gold, (
                f"{qid}: gold {sorted(gold)} not even in the 120-pool on "
                f"the degraded lane — membership lever failed")
        finally:
            sdk.close()


def _embedder_installed() -> bool:
    """Cheap, import-time-safe capability probe for MARKER conditions.

    Deliberately NOT `_embedder_present()`: that one calls
    `EmbeddingModel.get()`, which LOADS a model (cold ~57 s, bounded by
    `_LOAD_TIMEOUT_S` = 90 s). A `pytest.mark.xfail(condition=...)` is evaluated
    at module import, so using the decisive probe there would block collection
    for every run of this file — including `--collect-only` and deselected
    runs — and, worse, a transient load failure returns None and populates the
    negative cache, misclassifying a vector-capable checkout as the keyword
    lane and silently XFAILing the very assertion that is worst-case red. The
    decisive probe stays inside the test body, where a failure fails the
    assertion instead of converting it to an XFAIL.
    """
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None


@pytest.mark.xfail(
    strict=True,
    condition=not _embedder_installed(),
    reason="Accepted regression from the #4155 grouping fix — root-caused as "
           "#5821 — on the KEYWORD LANE, which is what this marker's condition "
           "now encodes. Measured 2026-09-28 at origin/main 20e4819be: on the "
           "keyword lane the #4155 key deepens the pool (dedup 91 → 119) while "
           "the assembly step keeps 58, so the gold moves from rank 55 (kept "
           "70 — it landed) to rank 68 post-boost (> 58 — cut). Isolating the "
           "#4155 key alone reproduces exactly that: with the pre-#4155 key the "
           "gold lands on this lane. On the VECTOR lane the dense leg holds the "
           "gold at rank 9 and it lands at item_cap=40, so this test XPASSes "
           "an unconditional strict marker and reds the suite on any "
           "vector-capable checkout — the canonical dev env. The 58-item keep is "
           "the TOKEN cap's doing, not the byte cap this marker used to name; "
           "the measured attribution is pinned by "
           "test_ask_cap_attribution_5821 below. strict=True on the keyword lane "
           "so that a real fix there makes this an XPASS failure and forces the "
           "marker's removal.",
)
def test_gold_turn_in_context_cap_review_embedded_1d4e3b97():
    """A6 measurement-gated cap review, embedded lane: 1d4e3b97 (in-pool,
    thin overlap) retrieves its gold IN CONTEXT once the retrieval window
    and item cap are raised together (40→120) — the A6 fix as measured in
    Step 0. Default-off knobs are exercised explicitly.

    ⚠️ Known-failing by owner decision, tracked as #5821 — see the marker
    reason. **The failure is lane-dependent and the marker now says so.** On
    the keyword lane the #4155 key deepens the pool (dedup 91 → 119) while the
    assembly step keeps 58, so the gold moves from rank 55 post-boost in a pool
    of 91 with a keep of 70 (it landed) to rank 68 post-boost in a pool of 119
    with a keep of 58 (> 58 — cut). On the vector lane the dense leg holds the
    gold at rank 9 and it lands at `item_cap=40`,
    so the test passes and an unconditional `strict=True` would red the suite.
    That is why the marker carries `condition=not _embedder_installed()` — it
    encodes the lane the regression belongs to instead of asserting it
    universally, and it probes cheaply because marker conditions are evaluated
    at import.

    This test raises the window and the item cap but not the token cap, so on
    the keyword lane those two raises cannot lift the keep above 58 (a
    token-cap decision belongs to #5821's Task 3 and is deliberately NOT made
    here). On the keyword lane it is `xfail(strict)` so a real fix there cannot
    land without removing the marker.
    """
    questions = _bench_questions()
    q = questions.get("1d4e3b97")
    if q is None:
        pytest.skip("1d4e3b97 not in cached dataset")
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        gold = _gold_turn_ids(q)
        ctx = _ask_pipeline(
            sdk, q["question"], keep_numeric=True, search_keys_prf=True,
            evidence_boost=True, limit=120, item_cap=120)
        assert {h["id"] for h in ctx} & gold, (
            "1d4e3b97: gold must land in context when the window+cap are "
            "raised together (A6 tandem threading)")
    finally:
        sdk.close()


def test_ask_cap_attribution_5821():
    """#5821 Task 1: pin WHICH cap binds, so this gate cannot mis-attribute it.

    The #4155 marker originally blamed the byte cap for the 58-item keep.
    Measured on main at b2448869d, BOTH caps bind: with no explicit or
    env-provided ceiling the derived one is `token x 8` (floored at 32768), so
    at the lane default the two move together — but the 58 is the TOKEN cap's
    doing, because holding the byte ceiling at the lane's own 64000 while
    raising tokens admits more than 58. This records that attribution as a
    PROPERTY rather than a fixed count, so it pins the mechanism without
    becoming a place to widen a budget (#5821 Task 3 keeps that decision out
    of code).

    What value makes this test fail: if the token cap stops being the tighter
    constraint at the 8000-token lane (property 2 breaks), if relaxing the cap
    pair no longer raises the keep (property 1 breaks), or if the byte cap
    stops constraining anything (property 3 breaks) — any of those makes the
    attribution recorded on the #5821 marker false again, and this test says so
    instead of letting the comment drift.

    The fixture is what makes those falsifying values reachable: the seeded
    `1d4e3b97` question yields a 119-item post-boost pool in which the gold turn
    sits at rank 68, so the pool is deeper than the byte-capped 78 that
    property 3 needs to be discriminating. The test SKIPS (rather than passing
    vacuously) when the cached LongMemEval dataset is absent.
    """
    questions = _bench_questions()
    q = questions.get("1d4e3b97")
    if q is None:
        pytest.skip("1d4e3b97 not in cached dataset")

    from tortoise.retrieval import (
        DEFAULT_MAX_CHUNKS_PER_SESSION,
        apply_evidence_boost,
        ask_session_key,
        assemble_context,
        dedup_pool,
        resolve_ask_boost_multipliers,
        resolve_byte_cap_from_caps,
    )

    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        hits = sdk.tortoise_fts_query(
            q["question"], limit=120, pool_size=120, include_terminal=True,
            keep_numeric=True, search_keys_prf=True)
        ann = sdk.annotate_ask_hits(hits)
        assert ann, "retrieval returned nothing — the premise is broken"
        ded = dedup_pool(
            ann, max_chunks_per_session=DEFAULT_MAX_CHUNKS_PER_SESSION,
            session_key=ask_session_key)
        mult = resolve_ask_boost_multipliers()
        bo, _ = apply_evidence_boost(
            ded, boost_answer_string=mult["answer_string"],
            boost_verbatim=mult["verbatim"], boost_source=mult["source"])

        def kept(token_cap: int, byte_cap: int | None = None) -> int:
            bc = byte_cap if byte_cap is not None else resolve_byte_cap_from_caps(
                {"context_token_cap": token_cap})
            return len(assemble_context(
                bo, top_k=120, max_context_tokens=token_cap,
                context_item_cap=120, byte_cap=bc))

        # (1) relaxing the token cap — and, by derivation, the byte cap it
        #     carries with it — raises the keep. This deliberately does NOT
        #     claim to isolate the token cap: `byte_cap=None` makes the helper
        #     DERIVE the ceiling, so both move together here. The isolation is
        #     (2), which holds the byte ceiling fixed.
        assert kept(16000) > kept(8000), (
            "relaxing the token cap did not raise the keep — the cap pair is "
            "no longer doing what the #5821 attribution describes")

        # (2) with the byte ceiling held constant at the 8000-token lane's own
        #     64000, raising only the token budget still raises the keep — so
        #     the 58 is the TOKEN cap's doing, and the assertion fails if the
        #     byte cap is the constraining one at that lane.
        assert kept(16000, byte_cap=64000) > kept(8000), (
            "holding the byte ceiling at the lane's 64000, raising only the "
            "token budget did not raise the keep — the 58 is not the token "
            "cap's doing, so the #5821 attribution is wrong")

        # (3) the byte cap is a real, independently binding constraint — it is
        #     NOT inert, and no comment should say it is.
        assert kept(64000, byte_cap=64000) < kept(64000, byte_cap=1000000), (
            "raising the byte ceiling alone did not change the keep — the byte "
            "cap is inert here, contradicting the measured attribution")
    finally:
        sdk.close()


def test_gold_turn_in_context_short_history_control():
    """Target 1 control: a SHORT-history question (mini fixture) still
    retrieves its gold turn — no regression of the short baseline."""
    mini = json.loads(_MINIFIX.read_text(encoding="utf-8"))
    q = mini[0]
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        gold = _gold_turn_ids(q)
        assert gold, "mini q0 should carry has_answer turns"
        ctx = _ask_pipeline(sdk, q["question"])
        assert {h["id"] for h in ctx} & gold, (
            "short-history control: gold turn missing from context")
    finally:
        sdk.close()


# ── A1: same-value numeric policy ──────────────────────────────────────────

def test_numeric_tokens_kept_with_keep_numeric():
    """A1: a money question that itself carries an amount keeps the numeric
    token with keep_numeric on; the search lane (off) keeps dropping it."""
    from tortoise.sparse import tokenize_sparse_query
    assert "40" not in tokenize_sparse_query("how much did I spend on "
                                             "lights at $40?")
    assert "40" in tokenize_sparse_query(
        "how much did I spend on lights at $40?", keep_numeric=True)


def test_numeric_policy_ask_lane_only_flag_default_off():
    """A1: the search lane is byte-identical — keep_numeric defaults False."""
    from tortoise.sparse import build_or_query
    assert build_or_query("price was $40") == build_or_query(
        "price was $40", keep_numeric=False)


# ── A4: OR-cap preservation ────────────────────────────────────────────────

def test_expansion_never_displaces_original_tokens():
    """A4 regression guard: injected aliases fill ONLY the tail after the
    original tokens' slots — a long alias can never crowd out a shorter
    original token."""
    from tortoise.sparse import DEFAULT_MAX_EXPANSION_TERMS, build_or_query
    q = "my favorite board game is catan"
    original = build_or_query(q)                      # 12-cap original
    original_tokens = original.split("|")
    expanded = build_or_query(
        q, expansion_terms=["supercalifragilisticexpialidocious",
                            "bike", "helmet", "chain", "cassette"])
    expanded_tokens = expanded.split("|")
    # every original token keeps its slot, in order
    assert set(original_tokens) <= set(expanded_tokens)
    assert expanded_tokens[:len(original_tokens)] == original_tokens
    # expansion is bounded at DEFAULT_MAX_EXPANSION_TERMS beyond the cap
    assert len(expanded_tokens) <= 12 + DEFAULT_MAX_EXPANSION_TERMS


# ── Search-lane byte-identity control ──────────────────────────────────────

def test_search_lane_byte_identity():
    """A1/A3/A4 control: the product search lane passes no ask knobs →
    identical query bytes; run_fts_query defaults are unchanged."""
    import inspect

    from tortoise.search_engine import run_fts_query
    from tortoise.sparse import build_or_query
    q = "what is ava's favorite board game?"
    assert build_or_query(q) == build_or_query(
        q, keep_numeric=False, expansion_terms=None)
    sig = inspect.signature(run_fts_query)
    assert sig.parameters["keep_numeric"].default is False
    assert sig.parameters["expansion_terms"].default is None


# ── A2: retrieval_degraded honesty ─────────────────────────────────────────

def test_retrieval_degraded_honest_when_embedder_absent():
    """A2: the ask lane still surfaces degradation honestly when the vector
    leg is absent — the fix never papers over a degraded lane."""
    if _embedder_present():
        pytest.skip("embedder present — degraded-absence path not exercised")
    mini = json.loads(_MINIFIX.read_text(encoding="utf-8"))
    q = mini[0]
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        from tortoise.embeddings import EmbeddingModel
        assert EmbeddingModel.get() is None  # the honest precondition
        # the degraded flag is resolved by the ask lane from leg_trace;
        # the existing test_ask_sdk retrieval_degraded tests pin that path.
        # Here we pin the #4105-resolved cap posture (the ask lane's window
        # is now env-resolvable AND honest — the historical 40/40/8000
        # truncated at a 32 KiB literal, so a raise above it was a no-op).
        # Here we pin the shipped window LITERALLY: the cap posture is the
        # measured 200/400/200/16000/128000. Inequalities alone cannot
        # detect a changed literal, and the pre-#4105 pin was an exact dict —
        # replacing it with three tautologies left the window unpinned.
        from tortoise.retrieval import resolve_ask_retrieval_caps
        caps = resolve_ask_retrieval_caps()
        assert caps == {
            "limit": 200,
            "pool_size": 400,
            "context_item_cap": 200,
            "context_token_cap": 16000,
            "context_byte_cap": 128000,
        }
    finally:
        sdk.close()


# ── A7: ask-lane (eval-only) rerank degrade-path (ported eval contract) ────

def test_product_rerank_off_by_default():
    """A7: the ask-lane rerank is fail-safe OFF (env truthy-only)."""
    os.environ.pop("TORTOISE_ASK_RERANK", None)
    from tortoise.rerank import rerank_enabled
    assert rerank_enabled() is False
    os.environ["TORTOISE_ASK_RERANK"] = "1"
    try:
        assert rerank_enabled() is True
    finally:
        os.environ.pop("TORTOISE_ASK_RERANK", None)
    os.environ["TORTOISE_ASK_RERANK"] = "garbage"
    try:
        assert rerank_enabled() is False  # typo never flips the knob
    finally:
        os.environ.pop("TORTOISE_ASK_RERANK", None)


def test_product_rerank_degrade_to_untouched_on_failure():
    """A7: ask_lane_rerank degrades to untouched (applied False) on scorer
    failure / empty scorer — never raises (the eval's degrade contract)."""
    from tortoise.rerank import ask_lane_rerank
    mini = json.loads(_MINIFIX.read_text(encoding="utf-8"))
    q = mini[0]
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        hits = sdk.tortoise_fts_query(q["question"], limit=40, pool_size=120)
        annotated = sdk.annotate_ask_hits(hits)
        out, stats = ask_lane_rerank(
            q["question"], annotated, proj=sdk._get_proj(), top_k=40)
        assert out == annotated, "degrade-to-untouched must preserve order"
        assert stats.get("applied") is False
        assert stats.get("degrade_reason")
    finally:
        sdk.close()
