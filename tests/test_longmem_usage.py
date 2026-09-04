"""UsageCollector tests (#2185 Task 3 — tools/longmem_eval/usage.py).

Pins the run-level collector contract:

- module-level collector singleton with ``reset()`` (#2185 A2) — the harness
  inits it in run_main before preflight and run_evaluation/_load_checkpoint
  consume the SAME instance (no parameter threading).
- question-key ContextVar attribution: rows fired while a qid key is set
  bucket under that qid; keyless rows (preflight / stray) bucket under the
  ``__no_key__`` sentinel → ``drain_overhead``.
- envelope schema ``{by_stage: {stage: {provider: {model: bucket}}}, total:
  {prompt_tokens, completion_tokens, calls}}`` + cache-detail keys
  (prompt_cache_hit_tokens etc.) preserved when present; JSON-safe.
- drain-swap atomicity: every row is drained exactly once (never double
  counted into two envelopes; late fires after a drain land in the next
  drain, and a re-drain of a completed qid returns None).
- ``attach`` walks RoutingModel .primary/.fallback + RotatingModel
  .providers, ASSIGNS ``usage_sink`` on members (A6 — members start WITHOUT
  a pre-set sink), and keys buckets by the REGISTERED (stage, provider)
  with the payload provider used only as fallback (A1 — a stub with no
  ``.provider`` attr still lands under the registered provider). attach is a
  no-op on mocks/non-adapters (no complete()).
- usage sanitization: only known scalar keys survive (prompt/completion/
  total_tokens, reasoning_tokens, prompt_cache_hit/miss_tokens,
  prompt_tokens_details.cached_tokens); unknown-only usage dicts log a loud
  warning instead of silently vanishing.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading

import pytest

from tools.longmem_eval import usage
from tools.longmem_eval.usage import UsageCollector


class _StubAdapter:
    """A chat adapter that FIRES its usage_sink (mirrors the #2185 seam)."""

    provider = None  # subclass sets; may stay None to test A1 keying

    def __init__(self, model_id, provider=None):
        self.id = model_id
        if provider is not None:
            self.provider = provider
        self.usage_sink = None
        self._calls = 0

    def complete(self, *, system, user, **kw):
        self._calls += 1
        sink = getattr(self, "usage_sink", None)
        if sink is not None:
            sink(provider=getattr(self, "provider", None),
                 model_id=self.id, usage={"prompt_tokens": 3,
                                          "completion_tokens": 2},
                 usage_present=True)
        return "ok"


def _fresh() -> UsageCollector:
    usage.reset_collector()
    return usage.get_collector()


@pytest.fixture(autouse=True)
def _auto_reset():
    # the question-key ContextVar leaks across tests otherwise (each test
    # body must not inherit a previous test's key)
    usage.clear_question_key()
    yield
    usage.clear_question_key()
    usage.reset_collector()





# ── singleton + reset (A2) ──────────────────────────────────────────────────

def test_singleton_and_reset():
    c1 = usage.get_collector()
    c2 = usage.get_collector()
    assert c1 is c2
    usage.reset_collector()
    assert usage.get_collector() is not c1


# ── question-key attribution + envelope accumulation ────────────────────────

def test_rows_bucket_by_question_key_and_stage_provider_model():
    c = _fresh()
    usage.set_question_key("q1")
    c.record(stage="reader", provider="openrouter", model_id="gpt-4o",
             usage={"prompt_tokens": 10, "completion_tokens": 4},
             usage_present=True)
    c.record(stage="judge", provider="openai", model_id="gpt-4o",
             usage={"prompt_tokens": 20, "completion_tokens": 1},
             usage_present=True)
    env = c.drain_question("q1")
    assert env is not None
    assert env["by_stage"]["reader"]["openrouter"]["gpt-4o"] == {
        "prompt_tokens": 10, "completion_tokens": 4, "calls": 1,
        "usage_present": True}
    assert env["by_stage"]["judge"]["openai"]["gpt-4o"]["calls"] == 1
    assert env["total"] == {"prompt_tokens": 30, "completion_tokens": 5,
                            "calls": 2}
    # second drain of the completed qid → None
    assert c.drain_question("q1") is None


def test_same_lane_sums_and_cache_detail_keys_preserved():
    c = _fresh()
    usage.set_question_key("q1")
    for _ in range(3):
        c.record(stage="ingest", provider="openrouter",
                 model_id="deepseek/deepseek-v4-flash",
                 usage={"prompt_tokens": 7, "completion_tokens": 3,
                        "prompt_cache_hit_tokens": 2},
                 usage_present=True)
    env = c.drain_question("q1")
    bucket = env["by_stage"]["ingest"]["openrouter"][
        "deepseek/deepseek-v4-flash"]
    assert bucket == {"prompt_tokens": 21, "completion_tokens": 9,
                      "calls": 3, "usage_present": True,
                      "prompt_cache_hit_tokens": 6}
    assert env["total"]["calls"] == 3
    assert env["total"]["prompt_tokens"] == 21


def test_usage_present_false_row_counts_call_zero_tokens():
    c = _fresh()
    usage.set_question_key("q1")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={}, usage_present=False)
    env = c.drain_question("q1")
    bucket = env["by_stage"]["reader"]["openrouter"]["m"]
    assert bucket["calls"] == 1
    assert bucket["prompt_tokens"] == 0
    assert bucket["usage_present"] is False
    assert env["total"]["calls"] == 1


def test_no_calls_drain_is_none():
    c = _fresh()
    usage.set_question_key("qA")
    assert c.drain_question("qA") is None
    assert c.drain_overhead() is None


# ── drain-swap atomicity (never double-counted / late fires) ────────────────

def test_late_fire_after_drain_lands_in_second_drain_only():
    c = _fresh()
    usage.set_question_key("q1")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5, "completion_tokens": 1},
             usage_present=True)
    env1 = c.drain_question("q1")
    assert env1["total"]["calls"] == 1
    # a late daemon-thread fire (e.g. a deadline-killed call finishing after
    # the outcome was built) lands in a fresh bucket — NOT recounted into
    # env1, and NOT lost:
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5, "completion_tokens": 1},
             usage_present=True)
    env2 = c.drain_question("q1")
    assert env2 is not None and env2["total"]["calls"] == 1
    assert c.drain_question("q1") is None


def test_threaded_fires_single_drain_no_lost_rows():
    c = _fresh()
    usage.set_question_key("q1")
    n = 40
    ctx = contextvars.copy_context()  # mirrors _call_once's daemon behavior

    def _fire():
        c.record(stage="ingest", provider="openrouter", model_id="m",
                 usage={"prompt_tokens": 1, "completion_tokens": 1},
                 usage_present=True)

    threads = [threading.Thread(target=lambda: ctx.run(_fire))
               for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    env = c.drain_question("q1")
    assert env["total"]["calls"] == n
    assert env["total"]["prompt_tokens"] == n


# ── ContextVar mechanics: key set in caller, row in that qid; keyless → overhead ──

def test_keyless_rows_go_to_overhead_and_clear_question_key():
    c = _fresh()
    # preflight-style: no question key set yet
    c.record(stage="preflight", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 2, "completion_tokens": 1},
             usage_present=True)
    usage.set_question_key("q1")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 3, "completion_tokens": 1},
             usage_present=True)
    env = c.drain_question("q1")
    assert env["total"]["calls"] == 1
    usage.clear_question_key()
    c.record(stage="ingest", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 4, "completion_tokens": 1},
             usage_present=True)
    oh = c.drain_overhead()
    assert oh is not None
    # preflight + the post-clear keyless row
    assert oh["by_stage"]["preflight"]["openrouter"]["m"]["calls"] == 1
    assert oh["by_stage"]["ingest"]["openrouter"]["m"]["calls"] == 1
    assert oh["total"]["calls"] == 2


def test_question_key_propagates_to_spawned_thread_via_copy_context():
    """The Task 1 _call_once fix exists so a daemon thread's sink fire sees
    the caller's question key — pin the collector side of that contract."""
    c = _fresh()
    usage.set_question_key("qThreaded")
    ctx = contextvars.copy_context()

    def _worker():
        c.record(stage="judge", provider="openai", model_id="gpt-4o",
                 usage={"prompt_tokens": 5, "completion_tokens": 1},
                 usage_present=True)

    t = threading.Thread(target=lambda: ctx.run(_worker))
    t.start()
    t.join()
    env = c.drain_question("qThreaded")
    assert env is not None and env["total"]["calls"] == 1
    usage.clear_question_key()


# ── attach: wiring + keying (A1/A6) ─────────────────────────────────────────

def test_attach_wires_sink_and_records_under_registered_provider():
    c = _fresh()
    adapter = _StubAdapter("deepseek-v4-flash", provider="openrouter")
    adapter.usage_sink = None  # explicitly NO pre-set sink (A6)
    n = c.attach(adapter, stage="ingest", provider=None)
    assert n == 1
    usage.set_question_key("q1")
    adapter.complete(system="s", user="u")
    env = c.drain_question("q1")
    assert env["by_stage"]["ingest"]["openrouter"]["deepseek-v4-flash"][
        "calls"] == 1


def test_attach_registered_provider_keys_providerless_model():
    """A1: a model with NO .provider attr still lands under the REGISTERED
    provider (the reader/judge lanes — OpenAICompatModel/OfficialJudgeModel
    carry none)."""
    c = _fresh()
    adapter = _StubAdapter("gpt-4o-2024-08-06")  # provider stays None
    c.attach(adapter, stage="reader", provider="openrouter")
    usage.set_question_key("q1")
    adapter.complete(system="s", user="u")
    env = c.drain_question("q1")
    assert env["by_stage"]["reader"]["openrouter"]["gpt-4o-2024-08-06"][
        "calls"] == 1


def test_attach_walks_routing_and_rotating_members():
    c = _fresh()

    class RoutingLike:
        def __init__(self, primary, fallback=None):
            self.primary = primary
            self.fallback = fallback

    class RotatingLike:
        def __init__(self, providers):
            self.providers = providers

    p1 = _StubAdapter("deepseek/deepseek-v4-flash", provider="openrouter")
    p2 = _StubAdapter("deepseek-v4-flash", provider="deepseek-direct")
    routed = RoutingLike(p1)
    rot = RotatingLike([p1, p2])
    assert c.attach(routed, stage="ingest", provider=None) == 1
    assert c.attach(rot, stage="ingest", provider=None) == 2
    assert p1.usage_sink is not None and p2.usage_sink is not None
    usage.set_question_key("q1")
    p1.complete(system="s", user="u")
    p2.complete(system="s", user="u")
    env = c.drain_question("q1")
    assert env["total"]["calls"] == 2
    assert env["by_stage"]["ingest"]["deepseek-direct"][
        "deepseek-v4-flash"]["calls"] == 1


def test_attach_noop_on_non_adapter():
    c = _fresh()

    class MockLike:  # reader/judge mocks carry no complete()/sink path
        pass

    assert c.attach(MockLike(), stage="reader", provider="openrouter") == 0


def test_attach_descends_through_complete_bearing_wrappers():
    """#2185 regression (smoke run): a REAL RoutingModel HAS its own
    ``complete()`` (delegating to its members' transports), so a leaf-test on
    ``hasattr(complete)`` bound the sink to the WRAPPER — where usage never
    fires — and a real extractor run recorded zero rows. The walk must
    descend through complete-bearing wrappers to the leaf adapters."""
    c = _fresh()

    class RoutingLike:
        def __init__(self, primary, fallback=None):
            self.primary = primary
            self.fallback = fallback

        def complete(self, *, system, user, **kw):
            # real RoutingModel.complete delegates to a member transport
            return self.primary.complete(system=system, user=user, **kw)

    p1 = _StubAdapter("deepseek/deepseek-v4-flash", provider="openrouter")
    f1 = _StubAdapter("deepseek-v4-flash", provider="deepseek-direct")
    routed = RoutingLike(p1, f1)
    assert c.attach(routed, stage="ingest", provider=None) == 2
    # members bound; the wrapper itself must NOT be the sole bind
    assert p1.usage_sink is not None and f1.usage_sink is not None
    usage.set_question_key("q1")
    routed.complete(system="s", user="u")  # delegates to p1's transport
    env = c.drain_question("q1")
    assert env["by_stage"]["ingest"]["openrouter"][
        "deepseek/deepseek-v4-flash"]["calls"] == 1
    assert env["total"]["calls"] == 1


# ── sanitizer + envelope JSON-safety ────────────────────────────────────────

def test_unknown_only_usage_logs_loud_warning(caplog):
    c = _fresh()
    with caplog.at_level(logging.WARNING, logger="tools.longmem_eval.usage"):
        c.record(stage="reader", provider="openrouter", model_id="m",
                 usage={"weird_future_field": 1, "other": "x"},
                 usage_present=True)
    assert any("usage" in r.message.lower() and "unknown" in r.message.lower()
               for r in caplog.records)
    # the row still counts as a call
    usage.set_question_key("q1")  # record above was keyless — check overhead
    oh = c.drain_overhead()
    assert oh["total"]["calls"] == 1
    assert oh["by_stage"]["reader"]["openrouter"]["m"][
        "usage_present"] is True
    assert "prompt_tokens" in oh["by_stage"]["reader"]["openrouter"]["m"]


def test_envelope_json_serializable_and_nested_detail_flattened():
    c = _fresh()
    usage.set_question_key("q1")
    c.record(
        stage="reader", provider="openrouter", model_id="m",
        usage={"prompt_tokens": 10, "completion_tokens": 2,
               "prompt_tokens_details": {"cached_tokens": 8}},
        usage_present=True)
    env = c.drain_question("q1")
    # nested detail flattened into a scalar key on the bucket
    bucket = env["by_stage"]["reader"]["openrouter"]["m"]
    assert bucket["prompt_tokens_details_cached_tokens"] == 8
    json.dumps(env)  # must not raise


# ── Task 4 additions: drain_to_overhead / fold_replica / sweep ─────────────

def test_drain_to_overhead_moves_and_returns_env():
    c = _fresh()
    usage.set_question_key("qFail")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5, "completion_tokens": 1},
             usage_present=True)
    env = c.drain_to_overhead("qFail")
    assert env is not None and env["total"]["calls"] == 1
    # question drain now empty; overhead holds the moved rows
    assert c.drain_question("qFail") is None
    oh = c.drain_overhead()
    assert oh["by_stage"]["reader"]["openrouter"]["m"]["calls"] == 1


def test_fold_replica_full_fold_when_qid_absent():
    c = _fresh()
    env = {"by_stage": {"reader": {"openrouter": {"m": {
        "prompt_tokens": 5, "completion_tokens": 1, "calls": 1,
        "usage_present": True}}}}, "total": {"prompt_tokens": 5,
                                                "completion_tokens": 1,
                                                "calls": 1}}
    assert c.fold_replica("qLost", env["by_stage"]) is True
    oh = c.drain_overhead()
    assert oh["total"]["prompt_tokens"] == 5
    assert oh["total"]["calls"] == 1


def test_fold_replica_shortfall_only_idempotent():
    """A4: partial payload (kill-9 between upsert and save) → fold ONLY the
    shortfall; a replica already fully folded folds NOTHING (resume
    idempotency)."""
    c = _fresh()
    usage.set_question_key("qFail")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 10, "completion_tokens": 2},
             usage_present=True)
    c.drain_to_overhead("qFail")  # attempt-1 move (payload partial state)
    payload = c.overhead_payload()
    oh_c = c.drain_overhead()
    assert oh_c["total"]["prompt_tokens"] == 10

    # a NEW resume process folds the payload + a replica whose un-saved
    # rows (attempt-2 burn, same lane) exceed the payload
    c2 = UsageCollector()
    c2.merge_overhead_payload(payload)
    rep = {"reader": {"openrouter": {"m": {
        "prompt_tokens": 20, "completion_tokens": 4, "calls": 2,
        "usage_present": True}}}}
    assert c2.fold_replica("qFail", rep) is True
    # fold AGAIN (same replica on a second resume) → idempotent no-op
    assert c2.fold_replica("qFail", rep) is False
    oh = c2.drain_overhead()
    assert oh["total"]["prompt_tokens"] == 20
    assert oh["total"]["calls"] == 2
    # re-drain (rows removed again) → a later fold re-adds the replica
    assert c2.fold_replica("qFail", rep) is True


def test_sweep_to_overhead_catches_late_strays():
    c = _fresh()
    usage.set_question_key("qDone")
    c.record(stage="judge", provider="openai", model_id="gpt-4o",
             usage={"prompt_tokens": 3, "completion_tokens": 1},
             usage_present=True)
    usage.set_question_key("qLate")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 4, "completion_tokens": 1},
             usage_present=True)
    c.drain_question("qDone")  # completed normally
    # the late daemon fire under qLate was never drained → sweep catches it
    assert c.sweep_to_overhead() == 1
    assert c.drain_question("qLate") is None
    oh = c.drain_overhead()
    assert oh["total"]["calls"] == 1


def test_overhead_payload_checkpoint_form_renames_keyless():
    c = _fresh()
    c.record(stage="preflight", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 2, "completion_tokens": 1},
             usage_present=True)
    cp = c.overhead_payload(checkpoint_form=True)
    assert "__preflight__" in cp and "__no_key__" not in cp
    # load side normalizes back
    c2 = UsageCollector()
    c2.merge_overhead_payload(cp)
    oh = c2.drain_overhead()
    assert oh["total"]["calls"] == 1
    assert oh["by_stage"]["preflight"]["openrouter"]["m"]["calls"] == 1



def test_move_failed_qid_to_overhead_then_payload_roundtrip():
    c = _fresh()
    usage.set_question_key("qFail")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5, "completion_tokens": 1},
             usage_present=True)
    usage.clear_question_key()
    c.record(stage="preflight", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 2, "completion_tokens": 1},
             usage_present=True)
    moved = c.move_failed_qid_to_overhead("qFail")
    assert moved is True
    # qFail no longer drainable as a question
    assert c.drain_question("qFail") is None
    payload = c.overhead_payload()
    assert "__no_key__" in payload
    assert "qFail" in payload
    # resume = a FRESH collector in a new process folds the payload
    # additively (never merge a payload back into the collector that
    # produced it — that would double-count moved qids)
    c2 = UsageCollector()
    c2.merge_overhead_payload(payload)
    c2.record(stage="judge", provider="openai", model_id="gpt-4o",
              usage={"prompt_tokens": 1, "completion_tokens": 1},
              usage_present=True)
    oh = c2.drain_overhead()
    assert oh["total"]["calls"] == 3
    assert oh["total"]["prompt_tokens"] == 8
    assert oh["by_stage"]["judge"]["openai"]["gpt-4o"]["calls"] == 1
    # overhead payload round-trips through JSON (checkpoint wire form)
    json.dumps(payload)
    # normalize: rename __no_key__ → __preflight__ then merge into c3
    payload2 = {("__preflight__" if k == "__no_key__" else k): v
                for k, v in payload.items()}
    c3 = UsageCollector()
    c3.merge_overhead_payload(payload2)
    oh3 = c3.drain_overhead()
    assert oh3["total"]["calls"] == 2


# ── round-2 code-review regressions (#2250) ─────────────────────────────────

def test_sanitizer_rejects_poison_usage_never_raises():
    """Security review P2: non-dict / NaN / Infinity / 1e400-int / bool
    usage payloads degrade to {} — a poisoned provider response or tampered
    checkpoint can never crash aggregation (the sanitizer is the choke
    point that keeps poison out of every row)."""
    c = _fresh()
    usage.set_question_key("qPoison")
    poison = [
        ["not", "a", "dict"],           # malformed provider response
        {"prompt_tokens": float("nan")},
        {"prompt_tokens": float("inf")},
        {"prompt_tokens": 10 ** 400},
        {"prompt_tokens": True},
    ]
    for bad in poison:
        c.record(stage="reader", provider="openrouter", model_id="m",
                 usage=bad, usage_present=True)
    # a VALID row with an unknown extra key keeps the known scalar (the
    # sanitizer drops only the unknown key, never the whole row)
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5, "unknown_key": {"a": 1}},
             usage_present=True)
    env = c.drain_question("qPoison")
    assert env["total"]["prompt_tokens"] == 5
    assert env["total"]["completion_tokens"] == 0
    assert env["total"]["calls"] == len(poison) + 1


def test_merge_and_fold_preserve_detail_keys():
    """Bug-scan P2: a fixed-key merge silently dropped reasoning_tokens /
    flattened nested detail whenever spend passed through the overhead
    store; merges and folds now sum over the UNION of scalar keys."""
    c = _fresh()
    usage.set_question_key("qFail")
    c.record(stage="ingest", provider="deepseek-direct",
             model_id="deepseek-v4-flash",
             usage={"prompt_tokens": 10, "completion_tokens": 4,
                    "reasoning_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 6}},
             usage_present=True)
    env = c.drain_to_overhead("qFail")
    assert env is not None
    lane = env["by_stage"]["ingest"]["deepseek-direct"]
    lane = lane["deepseek-v4-flash"]
    assert lane["reasoning_tokens"] == 3
    assert lane["prompt_tokens_details_cached_tokens"] == 6
    oh = c.drain_overhead()
    lane = oh["by_stage"]["ingest"]["deepseek-direct"]
    lane = lane["deepseek-v4-flash"]
    assert lane["reasoning_tokens"] == 3
    assert lane["prompt_tokens_details_cached_tokens"] == 6
    # fold path preserves the detail keys too
    c2 = UsageCollector()
    assert c2.fold_replica(
        "qFail", {"ingest": {"deepseek-direct": {"deepseek-v4-flash": {
            "prompt_tokens": 10, "completion_tokens": 4, "calls": 1,
            "reasoning_tokens": 3, "usage_present": True}}}}) is True
    oh2 = c2.drain_overhead()
    lane2 = oh2["by_stage"]["ingest"]["deepseek-direct"]
    assert lane2["deepseek-v4-flash"]["reasoning_tokens"] == 3


def test_drain_to_overhead_returns_cumulative_envelope():
    """Bug-scan P2: a --retry-failed re-attempt whose burn is SMALLER than
    the already-persisted payload must still fold its exact un-saved delta;
    drain_to_overhead returns payload + candidate (the A4 replica is now
    cumulative), so the shortfall fold reconstructs the spend exactly."""
    c = _fresh()
    usage.set_question_key("qFail")
    # attempt-1 terminal failure: 300 prompt burned + drained + payload saved
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 300, "completion_tokens": 10},
             usage_present=True)
    c.drain_to_overhead("qFail")
    payload = c.overhead_payload()
    # a NEW resume process loads the payload, re-attempts, and burns FEWER
    # tokens (200) before a kill-9 between the failure upsert and the
    # trailing save.
    c2 = UsageCollector()
    c2.merge_overhead_payload(payload)
    usage.set_question_key("qFail")
    c2.record(stage="reader", provider="openrouter", model_id="m",
              usage={"prompt_tokens": 200, "completion_tokens": 5},
              usage_present=True)
    rep = c2.drain_to_overhead("qFail")
    assert rep["total"]["prompt_tokens"] == 500  # payload 300 + burn 200
    # a third process loads payload 300 and folds the cumulative replica
    c3 = UsageCollector()
    c3.merge_overhead_payload(payload)
    assert c3.fold_replica("qFail", rep["by_stage"]) is True
    oh = c3.drain_overhead()
    assert oh["total"]["prompt_tokens"] == 500
    assert oh["total"]["calls"] == 2


def test_calls_without_usage_disclosed_and_lane_flags_conservative():
    """Bug-scan P2: a lane whose rows MIX usage-bearing and usage-less
    responses keeps usage_present False (unknown spend is never silently
    priced) AND discloses the count of unknown rows."""
    c = _fresh()
    usage.set_question_key("q")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5}, usage_present=True)
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage=None, usage_present=False)
    env = c.drain_question("q")
    lane = env["by_stage"]["reader"]["openrouter"]["m"]
    assert lane["usage_present"] is False
    assert lane["calls_without_usage"] == 1
    assert lane["prompt_tokens"] == 5
    # same semantics survive the overhead merge (union sum, AND flag)
    c2 = _fresh()
    usage.set_question_key("q2")
    c2.record(stage="reader", provider="openrouter", model_id="m",
              usage={"prompt_tokens": 5}, usage_present=True)
    c2.record(stage="reader", provider="openrouter", model_id="m",
              usage=None, usage_present=False)
    c2.drain_to_overhead("q2")
    oh = c2.drain_overhead()
    lane = oh["by_stage"]["reader"]["openrouter"]["m"]
    assert lane["usage_present"] is False
    assert lane["calls_without_usage"] == 1


def test_keyless_rows_after_clear_land_in_overhead():
    """Security review P2: clear_question_key() unbinds the main-thread
    key — a straggler call fired AFTER the question's drains complete lands
    under __no_key__ overhead, never on the last question's bucket."""
    c = _fresh()
    usage.set_question_key("qLast")
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 5}, usage_present=True)
    c.drain_question("qLast")
    usage.clear_question_key()
    c.record(stage="reader", provider="openrouter", model_id="m",
             usage={"prompt_tokens": 7}, usage_present=True)
    assert c.drain_question("qLast") is None
    oh = c.drain_overhead()
    lane = oh["by_stage"]["reader"]["openrouter"]["m"]
    assert lane["prompt_tokens"] == 7

