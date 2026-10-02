"""#4488 — per-org EMBEDDING-ENCODE workload measurement.

WHAT THIS FILE IS
-----------------
The token meter (#3359/#3824) covers LLM PROVIDER calls, which have a billable
token count. A local ``sentence-transformers`` encode consumes CPU seconds and
RAM and produces no such count, so embedding work was invisible. #4488 asks for
a per-org workload figure (texts, characters, wall time), the model identity
that produced it, and a way for a person to read it from an existing query —
and explicitly NOT for any price, tier or quota.

The figure rides the EXISTING metering ledger: a ``ContextVar`` tally that
``embeddings.compute_embeddings`` mutates in place, taken-and-reset by a
work-owning boundary and written through ``metering.record_embedding_usage``.

THE FLEET BAR FOR THIS FILE
---------------------------
The integration tests drive the REAL writer/reader (registry lane) or the real
seam (Supabase lane); the unit tests below them replace
``metering.record_embedding_usage`` with a recording stub. The HTTP boundary is
driven end-to-end through the real pure-ASGI middleware (``TestMiddleware``)
and the real ``/v1/team`` route (``TestTeamRoute``).

DECLARED RESIDUAL (not a claim)
-------------------------------
A capture request cancelled while its pool worker still runs flushes at the
request boundary; the worker's later encode notes land in an already-consumed
tally and are NOT attributed. That is the #4451-class on-loop residual, bounded
and documented — ``test_cancelled_capture_residual_is_a_declared_loss`` asserts
the LOSS, not a guarantee, so no reader can mistake it for coverage.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import re
import tempfile
from pathlib import Path

import pytest

import tortoise.embed_metering as em
import tortoise.metering as metering

# The anchor writer is shared with #3825's file so the two lanes cannot drift on
# how a window is established. The registry-lane org fixture is defined locally
# (below) rather than imported: a module-level import shadowed by every test
# parameter named ``reg_org`` is an F811 redefinition.
from tests.test_metering_period_window import _anchor

ORG = "team-embed-1"


@pytest.fixture
def reg_org(monkeypatch, tmp_path):
    """A registry (embedded) SDK with one org — the lane the writer and reader
    both run on when Supabase mode is off."""
    from tortoise.sdk import TortoiseSDK

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    # Pin the PATH too: ``metering._reg_sdk()`` resolves the registry from
    # TORTOISE_DB_PATH (tempfile fallback), so without the pin the writer and
    # the fixture's sdk would address DIFFERENT databases.
    db = str(tmp_path / "embed.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    team = sdk.org_create(name="embed-test")
    yield sdk, team["id"]
    sdk.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _capture_writer(monkeypatch) -> list[dict]:
    """Replace the ledger writer with a recorder; return the calls list."""
    calls: list[dict] = []

    def _fake(org_id, **kw):
        calls.append({"org_id": org_id, **kw})
        return {"embed_calls": kw.get("calls", 0)}

    monkeypatch.setattr(metering, "record_embedding_usage", _fake)
    return calls


def _capture_alert(monkeypatch) -> list[dict]:
    alerts: list[dict] = []
    monkeypatch.setattr(metering, "report_unmetered_increment",
                        lambda **kw: alerts.append(kw))
    return alerts


@pytest.fixture(autouse=True)
def _clean_context():
    """Every test starts and ends with NO active tally (no cross-test bleed)."""
    token = em._ACTIVE.set(None)
    yield
    em._ACTIVE.reset(token)


# ════════════════════════════════════════════════════════════════════════════
# The tally primitive
# ════════════════════════════════════════════════════════════════════════════

class TestTallyPrimitive:
    def test_arm_is_idempotent_and_notes_accumulate(self):
        # Mutation: arm() returning a fresh tally on every call → the second
        # identity assert fails (notes would be split across two objects).
        t1 = em.arm(ORG)
        t2 = em.arm()
        assert t1 is t2
        em.note_encode(texts=3, chars=30, wall_ms=1.5)
        em.note_encode(texts=1, chars=5, wall_ms=0.5)
        assert (t1.calls, t1.texts, t1.chars, t1.wall_ms) == (2, 4, 35, 2.0)
        assert t1.org_id == ORG

    def test_unarmed_note_is_a_noop(self):
        # Mutation: note_encode() dereferencing _ACTIVE.get() without a None
        # check → AttributeError here.
        em.note_encode(texts=1, chars=1, wall_ms=1.0)
        em.note_skip(2)
        assert em.current_tally() is None

    def test_take_and_reset_clears_and_is_single_shot(self):
        # Mutation: take_and_reset() not clearing _ACTIVE → the second take
        # returns a tally instead of None, and a later request inherits it.
        em.arm(ORG)
        em.note_encode(texts=1, chars=10, wall_ms=1.0)
        t = em.take_and_reset()
        assert t is not None and t.texts == 1
        assert em.take_and_reset() is None
        assert em.current_tally() is None

    def test_identity_mixed_within_one_tally(self, monkeypatch):
        # Mutation: note_encode() overwriting model/revision on every call
        # without latching the change → identity_mixed stays False.
        import tortoise.embeddings as emb
        monkeypatch.setattr(emb, "embedding_identity", lambda: ("m-A", "r-1"))
        t = em.arm(ORG)
        em.note_encode(texts=1, chars=1, wall_ms=1.0)
        assert (t.model, t.revision, t.identity_mixed) == ("m-A", "r-1", False)
        monkeypatch.setattr(emb, "embedding_identity", lambda: ("m-B", "r-2"))
        em.note_encode(texts=1, chars=1, wall_ms=1.0)
        # FIRST identity kept (the writer compares against the STORED pair),
        # the swap carried by the flag.
        assert (t.model, t.revision, t.identity_mixed) == ("m-A", "r-1", True)


# ════════════════════════════════════════════════════════════════════════════
# meted() — ownership for detached work
# ════════════════════════════════════════════════════════════════════════════

class TestMeted:
    def test_sync_meted_installs_fresh_and_flushes_on_exit(self, monkeypatch):
        # Mutation: meted() inheriting the ambient tally instead of installing a
        # fresh one → the outer tally's counts change (the second assert).
        calls = _capture_writer(monkeypatch)
        outer = em.arm(ORG)
        with em.meted(ORG) as inner:
            assert inner is not outer
            em.note_encode(texts=2, chars=20, wall_ms=3.0)
            assert outer.calls == 0          # NOT inherited
        assert len(calls) == 1
        assert calls[0]["texts"] == 2 and calls[0]["chars"] == 20
        assert outer.calls == 0              # outer untouched
        assert em.current_tally() is outer   # prior context restored

    def test_async_meted_flushes_off_loop(self, monkeypatch):
        # Mutation: __aexit__ calling flush_tally inline → still passes, so this
        # asserts the FLUSH (the offload is proven by test_async_flush_is_
        # offloaded below); here we prove the async arm writes at all.
        calls = _capture_writer(monkeypatch)

        async def _run():
            async with em.meted(ORG):
                em.note_encode(texts=5, chars=50, wall_ms=7.0)

        asyncio.run(_run())
        assert len(calls) == 1 and calls[0]["calls"] == 1

    def test_async_flush_is_offloaded_off_the_event_loop(self, monkeypatch):
        """The async arm must hand the blocking ledger write to ANOTHER thread.

        Mutation proven RED: awaiting the writer inline on the loop. Asserted by
        THREAD IDENTITY, not by timing — the previous version of this test
        started its spinner AFTER the ``async with`` block, so its 30 ticks were
        produced under either behaviour and the assertion was invariant under
        the mutation (the fleet's dominant defect class: a guard that cannot
        fail).
        """
        import threading
        seen: dict = {}
        loop_thread = threading.get_ident()

        def _writer(org_id, **kw):
            seen["thread"] = threading.get_ident()
            return None

        monkeypatch.setattr(metering, "record_embedding_usage", _writer)

        async def _run():
            async with em.meted(ORG):
                em.note_encode(texts=5, chars=50, wall_ms=7.0)

        asyncio.run(_run())
        assert seen, "the writer never ran"
        assert seen["thread"] != loop_thread, (
            "the flush ran ON the event loop — it must be offloaded")

    def test_sync_arm_offloads_when_called_from_the_loop(self, monkeypatch):
        """The SYNC arm is invoked from on-loop callers (the seed runners, and
        FastMCP's direct sync tool dispatch), so it must not run the blocking
        ledger write under the loop either. Off the loop it stays inline (the
        pool-thread case), which the sibling tests cover.

        Mutation: ``_finish`` calling ``flush_tally`` inline unconditionally →
        the writer's thread is the loop thread → RED.
        """
        import threading
        seen: dict = {}
        loop_thread = threading.get_ident()

        def _writer(org_id, **kw):
            seen["thread"] = threading.get_ident()
            return None

        monkeypatch.setattr(metering, "record_embedding_usage", _writer)

        async def _run():
            with em.meted(ORG):          # sync CM, entered from a coroutine
                em.note_encode(texts=1, chars=1, wall_ms=1.0)
            await asyncio.sleep(0.05)    # let the scheduled task run

        asyncio.run(_run())
        assert seen, "the writer never ran"
        assert seen["thread"] != loop_thread, (
            "the sync arm blocked the event loop")

    def test_concurrent_flush_writes_exactly_once(self, monkeypatch):
        """CONTRACT test: two boundaries racing the same tally write ONE row.

        This pins the contract, not the mechanism. The critical section is a
        single contiguous GIL-bound span, so even with the lock REMOVED this
        structure usually still writes once — measured: 5000/5000 single writes
        against a no-op `_LOCK`. The lock itself is pinned deterministically by
        `test_the_consumed_transition_is_inside_the_critical_section`.
        """
        import threading
        calls: list = []
        monkeypatch.setattr(
            metering, "record_embedding_usage",
            lambda org_id, **kw: calls.append(org_id))
        tally = em.EmbedTally(calls=1, texts=4, chars=40, wall_ms=1.0,
                              org_id=ORG)
        barrier = threading.Barrier(2)

        def _race():
            barrier.wait()
            em.flush_tally(tally, ORG)

        threads = [threading.Thread(target=_race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1, calls

    def test_async_arm_skips_the_offload_when_nothing_encoded(
            self, monkeypatch):
        # Same guard on the async arm: a decorated runner that encoded nothing
        # (the common case for the seed runners) must not pay a thread hop.
        # Mutation: dropping the ``is_empty()`` guard in ``__aexit__`` → RED.
        calls = _capture_writer(monkeypatch)
        hops: list = []
        real = asyncio.to_thread

        async def _spy(fn, *a, **kw):
            hops.append(fn)
            return await real(fn, *a, **kw)

        monkeypatch.setattr(asyncio, "to_thread", _spy)

        async def _run():
            async with em.meted(ORG):
                pass                          # deliberately encodes nothing

        asyncio.run(_run())
        assert calls == []
        assert hops == []

    def test_meted_flushes_even_when_the_body_raises(self, monkeypatch):
        # Mutation: only flushing on the happy path → no write after the raise.
        calls = _capture_writer(monkeypatch)
        with pytest.raises(ValueError, match="boom"), em.meted(ORG):
            em.note_encode(texts=1, chars=4, wall_ms=1.0)
            raise ValueError("boom")
        assert len(calls) == 1


# ════════════════════════════════════════════════════════════════════════════
# flush() — empty / unbound / dropped
# ════════════════════════════════════════════════════════════════════════════

class TestFlush:
    def test_the_consumed_transition_is_inside_the_critical_section(
            self, monkeypatch):
        """The mechanism, pinned deterministically.

        `_LOCK` is replaced by a probe that records the tally's state at BOTH
        ends of the critical section. Mutation: moving the
        `if tally.consumed / tally.consumed = True` pair out of the lock → the
        transition is ALREADY observable at `__enter__` → RED. (Timing-based
        concurrency tests cannot see this: the span is one GIL slice, so the
        barrier test above passes 5000/5000 even with the lock removed.)
        """
        import threading
        monkeypatch.setattr(
            metering, "record_embedding_usage", lambda org_id, **kw: None)
        tally = em.EmbedTally(calls=1, texts=1, chars=1, wall_ms=1.0,
                              org_id=ORG)
        observed: list = []
        real = threading.Lock()

        class _ProbeLock:
            def __enter__(self):
                real.acquire()
                observed.append(("enter", tally.consumed))
                return self

            def __exit__(self, *exc):
                observed.append(("exit", tally.consumed))
                real.release()
                return False

        monkeypatch.setattr(em, "_LOCK", _ProbeLock())
        assert em.flush_tally(tally, ORG) is None      # writer returns None
        assert observed == [("enter", False), ("exit", True)], observed

    def test_note_encode_takes_the_same_lock_the_snapshot_uses(self,
                                                              monkeypatch):
        """The snapshot is only consistent if the WRITER is locked too.

        `flush_tally` claims the consumed transition and the counter snapshot
        share ONE critical section, so a concurrent note cannot land "in
        between". That was true of the READER and false of the WRITER:
        `note_encode` mutated `calls`/`texts`/`chars`/`wall_ms` with no lock at
        all. In the declared cancellation-residual window — a pool thread still
        running when the boundary flushes — the tally could be read
        mid-increment and produce an internally inconsistent row
        (`embed_calls` up, `embed_texts` not). Found in review.

        Deterministic, not timing-based: `_LOCK` is replaced by a probe that
        counts acquisitions, so the assertion is "did the writer take the lock",
        not "did it look slow".
        """
        import threading

        class _ProbeLock:
            def __init__(self):
                self._real = threading.Lock()
                self.acquired = 0

            def __enter__(self):
                self.acquired += 1
                return self._real.__enter__()

            def __exit__(self, *exc):
                return self._real.__exit__(*exc)

        probe = _ProbeLock()
        monkeypatch.setattr(em, "_LOCK", probe)

        em.EmbedTally().note_encode(texts=2, chars=20, wall_ms=4.0)

        assert probe.acquired == 1, (
            "note_encode mutated the counters outside the lock the snapshot "
            "holds — the two are not one critical section, so a concurrent "
            "flush can read a half-applied encode")

    def test_the_counter_snapshot_is_taken_inside_the_critical_section(
            self, monkeypatch):
        """The OTHER half of the fix, and the one that was unprotected.

        Moving only the `snap = {...}` dict out of the `with _LOCK:` block (the
        transition left inside) kept all 50 tests green — the probe above
        records `consumed`, never WHEN the counters were read. So the lost-
        increment race the fix closed had a regression test for one half and
        none for the other.

        The probe mutates the tally the instant the critical section ends — a
        concurrent boundary's note landing exactly at the release boundary.
        Correct code has already snapshotted `calls=1`; a snapshot taken after
        the release reports 101. Deterministic, not timing-based.
        """
        import threading
        calls = _capture_writer(monkeypatch)
        tally = em.EmbedTally(calls=1, texts=1, chars=1, wall_ms=1.0,
                              org_id=ORG)
        real = threading.Lock()

        class _MutatingLock:
            def __enter__(self):
                real.acquire()
                return self

            def __exit__(self, *exc):
                real.release()
                tally.calls += 100       # a note arriving at the release
                return False

        monkeypatch.setattr(em, "_LOCK", _MutatingLock())
        em.flush_tally(tally, ORG)
        assert calls[0]["calls"] == 1, calls

    def test_second_flush_is_a_no_op(self, monkeypatch):
        # The CONSUMED guard, pinned directly on a held tally: the same object
        # offered to the writer twice records once. (``take_and_reset``'s
        # single-shot semantics are pinned separately by
        # test_take_and_reset_clears_and_is_single_shot — routing this through
        # ``flush()`` would let the two mechanisms mask each other, which is
        # exactly what the earlier version of this test did: deleting the
        # consumed guard left it green.)
        calls = _capture_writer(monkeypatch)
        tally = em.EmbedTally(calls=1, texts=1, chars=10, wall_ms=1.0,
                              org_id=ORG)
        assert em.flush_tally(tally, ORG) is not None
        assert em.flush_tally(tally, ORG) is None
        assert len(calls) == 1

    def test_empty_flush_records_nothing_and_calls_no_rpc(self, monkeypatch):
        # Mutation: flushing an empty tally → a zero row per non-GET request,
        # drowning the ledger in no-op rows.
        calls = _capture_writer(monkeypatch)
        em.arm(ORG)
        assert em.flush(ORG) is None
        assert calls == []

    def test_unbound_nonempty_flush_alerts(self, monkeypatch):
        # Mutation: dropping an unattributable tally silently → alerts == [].
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)
        em.arm()                              # no org anywhere
        em.note_encode(texts=1, chars=1, wall_ms=1.0)
        assert em.flush() is None
        assert calls == []                    # nothing written without an org
        assert [a["lane"] for a in alerts] == ["embed"]

    def test_unresolvable_window_alerts_and_does_not_raise(self, monkeypatch):
        # Mutation: letting the writer's raise escape flush() → the encode path
        # fails a committed write (#3981 forbids that).
        def _raise(org_id, **kw):
            raise RuntimeError("window unresolvable")

        monkeypatch.setattr(metering, "record_embedding_usage", _raise)
        alerts = _capture_alert(monkeypatch)
        em.arm(ORG)
        em.note_encode(texts=1, chars=1, wall_ms=1.0)
        assert em.flush(ORG) is None          # absorbed, not raised
        assert [a["lane"] for a in alerts] == ["embed"]
        assert alerts[0]["org_id"] == ORG

    def test_cancelled_capture_residual_is_a_declared_loss(self, monkeypatch):
        """The DECLARED residual: a note arriving after the boundary flushed is
        lost, not attributed. Asserting the loss keeps it honest — the module
        docstring must never be read as claiming cancellation coverage."""
        calls = _capture_writer(monkeypatch)
        tally = em.arm(ORG)
        # The pool worker's context was COPIED at submit time (hosted_api
        # ``_submit_off_loop``), so it holds the SAME tally object even after
        # the request boundary retires it — this is what makes the residual real
        # rather than hypothetical.
        worker_ctx = contextvars.copy_context()
        em.note_encode(texts=1, chars=1, wall_ms=1.0)
        assert em.flush(ORG) is not None
        # The worker finishes late and notes into the retired (consumed) tally.
        worker_ctx.run(em.note_encode, texts=9, chars=900, wall_ms=99.0)
        assert len(calls) == 1 and calls[0]["texts"] == 1   # the 9 are LOST
        assert tally.texts == 10                             # mutated, unattributed
        assert em.current_tally() is None


# ════════════════════════════════════════════════════════════════════════════
# The encode funnel hook
# ════════════════════════════════════════════════════════════════════════════

class _FakeModel:
    def __init__(self, dim=3, fail=False):
        self.dim, self.fail, self.calls = dim, fail, 0

    def encode(self, texts, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("encoder blew up")
        return [type("V", (), {"tolist": lambda self, d=self.dim: [0.0] * d})()
                for _ in texts]


class TestEmbeddingsHook:
    def test_compute_embeddings_notes_the_encode(self, monkeypatch):
        # Mutation: hooking OUTSIDE the model branch (or not at all) → no tally.
        import tortoise.embeddings as emb
        model = _FakeModel()
        monkeypatch.setattr(emb.EmbeddingModel, "get", classmethod(
            lambda cls: model))
        # Deterministic identity for the assertion.
        monkeypatch.setattr(emb, "EMBEDDING_MODEL", "m-A")
        monkeypatch.setattr(emb, "EMBEDDING_MODEL_REVISION", "r-1")
        t = em.arm(ORG)
        out = emb.compute_embeddings(["hello", "world!!"], max_tokens=512)
        assert len(out) == 2 and out[0] == [0.0, 0.0, 0.0]
        assert (t.calls, t.texts) == (1, 2)
        assert t.chars == len("hello") + len("world!!")
        assert t.wall_ms >= 0.0
        assert (t.model, t.revision) == ("m-A", "r-1")

    def test_compute_embeddings_notes_skips_when_model_missing(self, monkeypatch):
        # Mutation: treating "no model" as zero work → skipped stays 0 and a
        # zero figure becomes unreadable as "the embedder never ran".
        import tortoise.embeddings as emb
        monkeypatch.setattr(emb.EmbeddingModel, "get", classmethod(
            lambda cls: None))
        t = em.arm(ORG)
        assert emb.compute_embeddings(["a", "b"]) == [None, None]
        assert (t.calls, t.skipped) == (0, 2)

    def test_unarmed_compute_embeddings_is_unchanged(self, monkeypatch):
        # REGRESSION GUARD, not a mutation pin: the hook must leave the
        # un-instrumented call byte-identical (the eval doubles and the longmem
        # harness call this with no tally armed). Two independent total-guards
        # protect it — ``note_encode``'s None check (pinned by
        # test_unarmed_note_is_a_noop) and ``_note_embed_encode``'s except
        # (pinned by test_the_hook_cannot_cost_the_caller_its_vectors) — so no
        # SINGLE production mutation reds this test.
        import tortoise.embeddings as emb
        monkeypatch.setattr(emb.EmbeddingModel, "get", classmethod(
            lambda cls: _FakeModel()))
        out = emb.compute_embeddings(["x"])
        assert out and out[0] == [0.0, 0.0, 0.0]

    def test_the_hook_cannot_cost_the_caller_its_vectors(self, monkeypatch):
        # ``_note_embed_encode``'s except, pinned on its own. The hook sits on
        # the VALUE-RETURN path (deliberately outside every value-guarding
        # try), so this except is the only thing between a measurement fault
        # and a discarded batch — the #4280 shape.
        # Mutation: removing the except → the RuntimeError below propagates out
        # of compute_embeddings → RED.
        import tortoise.embeddings as emb
        monkeypatch.setattr(emb.EmbeddingModel, "get", classmethod(
            lambda cls: _FakeModel()))

        def _boom(**kw):
            raise RuntimeError("measurement fault")

        monkeypatch.setattr(em, "note_encode", _boom)
        assert emb.compute_embeddings(["x"]) == [[0.0, 0.0, 0.0]]

    def test_tfidf_fallback_is_not_counted_as_an_encode(self, monkeypatch):
        # Mutation: noting the encode BEFORE the model call (so a failed encode
        # is counted as work that never happened).
        import tortoise.embeddings as emb
        monkeypatch.setattr(emb.EmbeddingModel, "get", classmethod(
            lambda cls: _FakeModel(fail=True)))
        t = em.arm(ORG)
        assert emb.compute_embeddings(["x"]) == [None]
        assert (t.calls, t.texts) == (0, 0)
        # ... and it is counted as SKIPPED, so the figure (0 calls, 0 skipped)
        # cannot be confused with "no embedder ran" (#4488's failure-mode table).
        assert t.skipped == 1


# ════════════════════════════════════════════════════════════════════════════
# Registry lane — real writer + real reader
# ════════════════════════════════════════════════════════════════════════════

class TestRegistryLane:
    def test_record_then_read_back(self, reg_org):
        # Mutation: any rename/typo in the MERGE params or the RETURN order →
        # the read-back disagrees with what was written.
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)  # D13: calendar month
        written = metering.record_embedding_usage(
            tid, calls=3, texts=12, chars=480, wall_ms=25.5, skipped=1,
            model="m-A", revision="r-1")
        assert written is not None and written["embed_calls"] == 3
        got = metering.get_embedding_usage(tid)
        assert got["embed_calls"] == 3
        assert got["embed_texts"] == 12
        assert got["embed_chars"] == 480
        assert got["embed_wall_ms"] == pytest.approx(25.5)
        assert got["embed_skipped"] == 1
        assert got["embed_model"] == "m-A"
        assert got["embed_revision"] == "r-1"
        assert got["embed_identity_mixed"] is False

    def test_two_increments_accumulate_on_one_row(self, reg_org):
        # Mutation: SET instead of coalesce(+n) → the second write OVERWRITES.
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        metering.record_embedding_usage(tid, calls=1, texts=2, chars=20,
                                        wall_ms=1.0, model="m-A",
                                        revision="r-1")
        metering.record_embedding_usage(tid, calls=2, texts=5, chars=60,
                                        wall_ms=2.0, model="m-A",
                                        revision="r-1")
        got = metering.get_embedding_usage(tid)
        assert (got["embed_calls"], got["embed_texts"], got["embed_chars"]) \
            == (3, 7, 80)
        assert got["embed_identity_mixed"] is False

    def test_reader_zeros_for_a_fresh_org(self, reg_org):
        # P2-14: a successful read with NO row is ZEROS, never an error.
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        got = metering.get_embedding_usage(tid)
        assert got["embed_calls"] == 0 and got["embed_texts"] == 0
        assert got["embed_model"] is None
        assert got["embed_identity_mixed"] is False

    def test_identity_mixed_is_sticky_across_windows(self, reg_org):
        # Mutation: computing the flag after overwriting the stored identity →
        # A→B would latch (stored==B, incoming==B, no change) = FALSE, i.e. the
        # swap becomes invisible. A→B→B must therefore stay TRUE.
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=1.0, model="m-A",
                                        revision="r-1")
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=1.0, model="m-B",
                                        revision="r-2")
        assert metering.get_embedding_usage(tid)["embed_identity_mixed"] is True
        # ... and a THIRD write under the NEW identity must not clear it.
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=1.0, model="m-B",
                                        revision="r-2")
        got = metering.get_embedding_usage(tid)
        assert got["embed_identity_mixed"] is True
        assert got["embed_model"] == "m-B"      # identity follows the latest

    def test_revision_only_change_latches(self, reg_org):
        # Mutation: comparing only the model name → a revision bump is
        # invisible, which is exactly the "#5004 journal identity" half.
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=1.0, model="m-A",
                                        revision="r-1")
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=1.0, model="m-A",
                                        revision="r-2")
        assert metering.get_embedding_usage(tid)["embed_identity_mixed"] is True

    def test_skipped_only_flush_preserves_identity_and_flag(self, reg_org):
        # Mutation: `embed_model = p_model` (no coalesce) → the skip-only flush
        # ERASES the identity; worse, a non-coalesced mixed term could latch it.
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=1.0, model="m-A",
                                        revision="r-1")
        # A window in which the embedder was unavailable: no model work at all.
        metering.record_embedding_usage(tid, calls=0, texts=0, chars=0,
                                        skipped=4)
        got = metering.get_embedding_usage(tid)
        assert got["embed_model"] == "m-A"           # NOT erased
        assert got["embed_revision"] == "r-1"
        assert got["embed_identity_mixed"] is False  # NOT falsely latched
        assert got["embed_skipped"] == 4

    def test_exemptions_record_nothing(self, reg_org):
        # Mutation: dropping the `not org_id` / selfhost guard → an exempt write
        # creates a row (metering a self-hosted / stdio caller).
        _sdk, tid = reg_org
        assert metering.record_embedding_usage(None, calls=1, texts=1) is None
        assert metering.record_embedding_usage(tid, calls=1, texts=1,
                                               _selfhost_transport=True) is None
        assert metering.get_embedding_usage(tid)["embed_calls"] == 0

    def test_non_finite_wall_ms_is_dropped_to_zero(self, reg_org):
        # Mutation: letting nan reach the ledger → the figure an operator reads
        # is poisoned (the #3665 cost_usd precedent).
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        metering.record_embedding_usage(tid, calls=1, texts=1, chars=1,
                                        wall_ms=float("nan"), model="m-A")
        assert metering.get_embedding_usage(tid)["embed_wall_ms"] == 0.0

    def test_spend_ceiling_cannot_see_the_embed_columns(self, reg_org):
        """#4488 is a MEASUREMENT, not a billing change. The spend ceiling must
        not move when embedding workload is recorded.

        Mutation: including the embed columns in the cohort aggregate → the
        assertion would fire only if they carried a cost, which they do not —
        so this pins the CONSTRUCTION (the aggregate's column set), not a
        coincidence of units.
        """
        import inspect
        sdk, tid = reg_org
        _anchor(sdk._get_registry(), tid, None, None, sub_id=None)
        metering.record_embedding_usage(tid, calls=9, texts=900, chars=9000,
                                        wall_ms=1234.5, model="m-A")
        src = inspect.getsource(metering.get_cohort_spend_usd)
        assert "embed_" not in src, "the spend ceiling reads an embed column"
        # ... and the figure really did land on the row the ceiling reads.
        assert metering.get_embedding_usage(tid)["embed_calls"] == 9

    def test_reader_degrades_on_unresolvable_window(self, reg_org, monkeypatch):
        # #923: the READ never raises. An unresolvable window has no row, so
        # the degradation is the zero view — never a month key.
        _sdk, tid = reg_org
        monkeypatch.setattr(metering, "_current_period",
                            lambda org: (_ for _ in ()).throw(
                                RuntimeError("no anchor")))
        got = metering.get_embedding_usage(tid)
        assert got["embed_calls"] == 0 and got["embed_model"] is None


# ════════════════════════════════════════════════════════════════════════════
# Supabase lane — migration shape + the seam through the fake plane
# ════════════════════════════════════════════════════════════════════════════

MIGRATION = (Path(__file__).resolve().parents[1] / "supabase" / "migrations"
             / "20260925000003_metering_embedding_columns.sql")
MIGRATIONS_DIR = MIGRATION.parent


def _sql_function_bodies(sql: str, name: str) -> list[str]:
    """EVERY ``CREATE ... FUNCTION public.<name>(`` body in one migration file.

    Plural on purpose, and the reason this helper exists: a function can be
    ``DROP``ped and re-``CREATE``d by a later migration, so an earlier body is
    DEAD. A guard that reads only the first definition — or only one file —
    checks something that is not deployed, and goes green while the live
    ceiling is edited. That is what a first version of the SQL guard below did.

    Anchored on ``CREATE``: ``REVOKE ALL ON FUNCTION public.<name>(...)`` and
    ``GRANT EXECUTE ON FUNCTION public.<name>(...)`` name the same function, and
    matching those ran the body search past the real definition into whatever
    function came next.
    """
    pattern = re.compile(
        rf"CREATE(?:\s+OR\s+REPLACE)?\s+FUNCTION\s+public\.{re.escape(name)}\s*\(")
    bodies: list[str] = []
    for m in pattern.finditer(sql):
        end = sql.find("$$;", m.start())
        if end == -1:
            break
        bodies.append(sql[m.start():end])
    return bodies


class TestMigrationShape:
    def test_every_counter_is_not_null_default_zero(self):
        # Mutation: a nullable counter → `NULL + n` stays NULL forever on a row
        # first created by another lane, and the reader renders 0 with no error
        # (a permanently dead figure). The capture column's precedent.
        sql = MIGRATION.read_text(encoding="utf-8")
        for col in ("embed_calls", "embed_texts", "embed_chars",
                    "embed_wall_ms", "embed_skipped"):
            line = next(ln for ln in sql.splitlines()
                        if f"ADD COLUMN IF NOT EXISTS {col}" in ln)
            assert "NOT NULL DEFAULT 0" in line, (col, line)
        mixed = next(ln for ln in sql.splitlines()
                     if "ADD COLUMN IF NOT EXISTS embed_identity_mixed" in ln)
        assert "NOT NULL DEFAULT false" in mixed

    def test_rpc_is_window_keyed_and_upserts_the_pk(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "p_period_start   timestamptz" in sql
        assert "p_period_end     timestamptz" in sql
        assert "ON CONFLICT (org_id, period_start)" in sql
        # The NOT NULL no-default trap: period + period_end must be supplied.
        assert "to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM')" in sql
        assert "p_period_start, p_period_end," in sql
        # Belt-and-braces additive terms.
        assert "coalesce(public.metering_records.embed_calls, 0) + p_calls" in sql
        # Degenerate window is refused, not minted as an unmatchable row.
        assert "IF p_period_end <= p_period_start THEN" in sql

    def test_every_sql_spend_ceiling_definition_avoids_the_embed_columns(self):
        """The OTHER half of the scope guard — the registry lane is the first.

        `test_spend_ceiling_cannot_see_the_embed_columns` asserts on
        `metering.get_cohort_spend_usd`, the REGISTRY lane. The Supabase lane is
        the SQL function `metering_cohort_spend`, and nothing pinned it.

        ⛔ It is checked in EVERY migration that defines it, not in one chosen
        file. `metering_cohort_spend` is defined twice: the 2-arg body in
        `20260917000001` is DROPPED by `20260918000001:313` and replaced by the
        3-arg overlap-window version that is actually deployed. A first version
        of this test read only the first file — so adding an `embed_*` term to
        the LIVE ceiling reddened nothing at all, which is precisely the hole
        it was written to close. Found in review, twice.
        """
        checked: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            for body in _sql_function_bodies(sql, "metering_cohort_spend"):
                # The guard must be ABLE to fail on this body: it really does
                # aggregate the two cost columns, so an assertion that stopped
                # matching the function entirely cannot pass vacuously.
                assert ("ask_cost_usd" in body and "capture_cost_usd" in body), (
                    path.name, body)
                assert "embed" not in body.lower(), (
                    f"{path.name} defines a metering_cohort_spend that reads an "
                    "embed column — the embed figure is workload, never "
                    f"spend: {body}")
                checked.append(path.name)
        # ...and we really did see both the superseded and the live definition.
        # Without this the guard could silently cover one body again.
        assert len(checked) >= 2, (
            f"expected the superseded AND the live metering_cohort_spend "
            f"definition across the migrations; found {checked}")

    def test_rpc_is_security_definer_and_service_role_only(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "DROP FUNCTION IF EXISTS public.metering_increment_embedding(" in sql
        assert "SECURITY DEFINER" in sql
        assert "SET search_path = ''" in sql
        assert "FROM public, anon, authenticated" in sql
        assert "TO service_role" in sql

    def test_seam_and_rpc_parameter_names_agree(self):
        # Mutation: a renamed p_* param on ONE side → the RPC 404s at runtime and
        # the increment is silently dropped (best-effort by contract).
        import inspect

        import tortoise.supabase_control as sc
        src = inspect.getsource(sc.metering_increment_embedding)
        sql = MIGRATION.read_text(encoding="utf-8")
        for p in ("p_org_id", "p_period_start", "p_period_end", "p_calls",
                  "p_texts", "p_chars", "p_wall_ms", "p_skipped", "p_model",
                  "p_revision", "p_identity_mixed"):
            assert f'"{p}"' in src, p
            assert p in sql, p


class TestSupabaseLane:
    def test_fake_route_records_and_reads_back(self, monkeypatch):
        # Mutation: no fake branch → the unhandled fn returns None, the row is
        # never written, and a supabase-lane write+read test reads zeros.
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        start = "2026-09-01T00:00:00+00:00"
        sc.metering_increment_embedding(fake, ORG, start,
                                        "2026-10-01T00:00:00+00:00",
                                        calls=2, texts=9, chars=99,
                                        wall_ms=8.5, skipped=1, model="m-A",
                                        revision="r-1")
        assert any(c[0] == "metering_increment_embedding"
                   for c in fake.rpc_calls)
        got = sc.metering_get_embed_usage(fake, ORG, start)
        assert (got["embed_calls"], got["embed_texts"], got["embed_chars"]) \
            == (2, 9, 99)
        assert got["embed_model"] == "m-A"
        assert got["embed_identity_mixed"] is False
        # Sticky + skip-safe, mirrored in the fake.
        sc.metering_increment_embedding(fake, ORG, start,
                                        "2026-10-01T00:00:00+00:00",
                                        calls=1, texts=1, chars=1, model="m-B")
        sc.metering_increment_embedding(fake, ORG, start,
                                        "2026-10-01T00:00:00+00:00",
                                        calls=0, texts=0, chars=0, skipped=2)
        got = sc.metering_get_embed_usage(fake, ORG, start)
        assert got["embed_identity_mixed"] is True
        assert got["embed_model"] == "m-B"
        assert got["embed_skipped"] == 3

    def test_fake_route_refuses_a_degenerate_window(self, monkeypatch):
        # Mutation: no guard in the fake → a window production RAISES on is
        # accepted and asserted green (the SQL never runs in CI, so the fake is
        # the only behavioural proxy for this lane).
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane
        fake = FakeControlPlane()
        start = "2026-09-01T00:00:00+00:00"
        with pytest.raises(Exception, match="period_end must be after"):
            sc.metering_increment_embedding(fake, ORG, start, start, calls=1)
        with pytest.raises(Exception, match="period_end must be after"):
            sc.metering_increment_embedding(
                fake, ORG, "2026-10-01T00:00:00+00:00", start, calls=1)


# ════════════════════════════════════════════════════════════════════════════
# The HTTP boundary — the pure-ASGI middleware
# ════════════════════════════════════════════════════════════════════════════

class TestMiddleware:
    @staticmethod
    def _run(scope, note=True):
        seen: list[bool] = []

        async def _app(scope, receive, send):
            if note:
                em.note_encode(texts=2, chars=20, wall_ms=4.0)
            seen.append(True)

        mw = em.EmbedMeteringMiddleware(_app)

        async def _receive():
            return {"type": "http.request"}

        async def _send(msg):
            return None

        asyncio.run(mw(scope, _receive, _send))
        return seen

    def test_the_middleware_is_actually_installed_on_the_app(self):
        """A guard that cannot fail is not a guard.

        `app.add_middleware(_EmbedMeteringMiddleware)` is the ONLY thing that
        puts the HTTP lane on the ledger. Deleting that one line left the whole
        suite green — every test in this class builds its OWN app, so nothing
        would notice. Every HTTP-lane encode, and the capture-lane attribution
        the module docstring describes, would then go unmeasured with no red
        anywhere. Found in review.
        """
        import tortoise.embed_metering as embed_mod
        import tortoise.hosted_api as ha_mod

        classes = [m.cls for m in ha_mod.app.user_middleware]
        assert embed_mod.EmbedMeteringMiddleware in classes, (
            "the embed metering middleware is not installed — the HTTP lane "
            f"would record nothing. Installed: {classes}")
        # ...and a neighbour, so this cannot pass by the stack being replaced
        # wholesale with something that happens to contain the class.
        assert ha_mod.InFlightMiddleware in classes

    def test_post_arms_and_flushes_binding_the_scope_org(self, monkeypatch):
        # Mutation: reading only the tally's org (never scope["state"]) → the
        # figure is unattributable on a key-auth route and an alert fires.
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)
        self._run({"type": "http", "method": "POST",
                   "state": {"org_id": ORG}})
        assert len(calls) == 1 and calls[0]["org_id"] == ORG
        assert alerts == []

    def test_get_does_not_arm(self, monkeypatch):
        # Mutation: arming on GET → the tally is armed, so the encode below
        # becomes attributable and the writer is called → RED. (The earlier
        # version noted NOTHING, so an empty tally made the flush a no-op
        # either way and the test passed with the GET guard deleted.)
        calls = _capture_writer(monkeypatch)
        self._run({"type": "http", "method": "GET",
                   "state": {"org_id": ORG}}, note=True)
        assert calls == []

    def test_no_offload_when_the_request_encoded_nothing(self, monkeypatch):
        # P2 (round 5): the thread hop is spent BEFORE ``flush_tally`` discovers
        # the tally is empty, so a zero-encode non-GET request used to pay for a
        # call that does nothing — on the SAME small default executor the real
        # ledger writes use, which the request's ``finally`` then waits behind.
        # Measured: that no-op hop was essentially the entire per-request cost
        # of this middleware, paid by every POST and every OPTIONS preflight.
        # Mutation: dropping the ``is_empty()`` guard → one hop → RED.
        calls = _capture_writer(monkeypatch)
        hops: list = []
        real = asyncio.to_thread

        async def _spy(fn, *a, **kw):
            hops.append(fn)
            return await real(fn, *a, **kw)

        monkeypatch.setattr(asyncio, "to_thread", _spy)
        self._run({"type": "http", "method": "POST",
                   "state": {"org_id": ORG}}, note=False)
        assert calls == []
        assert hops == []

    def test_post_with_no_org_alerts(self, monkeypatch):
        # Mutation: silently dropping the work when no org is resolvable → the
        # operator never learns the figure is missing.
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)
        self._run({"type": "http", "method": "POST", "state": {}})
        assert calls == []
        assert [a["lane"] for a in alerts] == ["embed"]

    def test_tally_org_wins_over_scope_state(self, monkeypatch):
        # The route bound the org explicitly (a session-auth lane) → that wins.
        calls = _capture_writer(monkeypatch)

        async def _app(scope, receive, send):
            em.bind_org("explicit-org")
            em.note_encode(texts=1, chars=1, wall_ms=1.0)

        async def _receive():
            return {"type": "http.request"}

        async def _send(msg):
            return None

        asyncio.run(em.EmbedMeteringMiddleware(_app)(
            {"type": "http", "method": "POST", "state": {"org_id": ORG}},
            _receive, _send))
        assert calls[0]["org_id"] == "explicit-org"

    def test_a_real_session_auth_request_attributes_through_starlette(
            self, monkeypatch):
        """The session-lane fix depends on Starlette's `Request.state` being
        backed by `scope["state"]`. A duck-typed request cannot prove that
        link, and `TestMiddleware` builds the scope dict by hand — so until
        this test, nothing drove the real object across the real middleware.

        Mutation: the middleware reading the org from anywhere but scope state
        → the session-authed write is dropped AND falsely alerted → RED.
        """
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)

        async def _handler(request: Request):
            request.state.org_id = ORG        # exactly what session auth does
            em.note_encode(texts=7, chars=70, wall_ms=3.0)
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/x", _handler, methods=["POST"])])
        app.add_middleware(em.EmbedMeteringMiddleware)
        with TestClient(app) as client:
            assert client.post("/x").status_code == 200
        assert len(calls) == 1, calls
        assert calls[0]["org_id"] == ORG
        assert calls[0]["texts"] == 7
        assert alerts == []


# ════════════════════════════════════════════════════════════════════════════
# The swallow-site completeness fence (the seventh lane)
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# The person-facing read path — /v1/team renders the figure
# ════════════════════════════════════════════════════════════════════════════

TEAM_ROUTE = "team-embed-route"
TOKEN_ROUTE = "tt_embed_route_0001"


class TestTeamRoute:
    """#4488 indicator 3: a person reads the figure from an existing query.

    Drives the REAL route, the REAL reader and the REAL writer (via the fake
    control plane's RPC emulation) — not a monkeypatched get_embedding_usage,
    which would only prove the field is spelled correctly.
    """

    @pytest.fixture
    def env(self, monkeypatch):
        os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        import tortoise.supabase_control as sc
        from tests._http_fixtures import patched_tortoise_sdk
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()
        fake.seed("organizations", [{
            "id": TEAM_ROUTE, "name": "embed-route", "tier": "free",
            "email": "owner@embed.test", "graph_name": f"org_{TEAM_ROUTE}",
            "max_users": 1, "max_graphs": 1, "ops_allowance": 10000,
            "graph_size_cap": 100000, "suspended_at": None, "flagged_at": None,
        }])
        from tortoise.auth import lookup_hash
        fake.seed("api_keys", [{
            "id": "key-embed", "org_id": TEAM_ROUTE,
            "lookup_hash": lookup_hash(TOKEN_ROUTE),
            "key_prefix": TOKEN_ROUTE[:10], "created_via": "provisioned",
            "created_by": "user-1",
            "created_at": "2026-08-01T00:00:00+00:00",
            "expires_at": None, "revoked_at": None,
        }])
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        import tortoise.hosted_api as ha
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patched_tortoise_sdk(os.path.join(tmpdir, "embed.db")),
        ):
            yield {"fake": fake, "app": ha.app}

    @staticmethod
    def _auth():
        return {"Authorization": f"Bearer {TOKEN_ROUTE}"}

    def test_renders_zeros_then_the_recorded_figure(self, env):
        # Mutation: dropping the additive fields from OrgInfoResponse / the
        # render → the keys are absent (or stuck at the model defaults), which
        # is exactly the "no verifiable deliverable" P1 the scope review caught.
        from fastapi.testclient import TestClient
        tc = TestClient(env["app"])
        fresh = tc.get("/v1/team", headers=self._auth())
        assert fresh.status_code == 200, fresh.text
        body = fresh.json()
        # P2-14: a fresh org renders ZEROS, not an error and not a missing key.
        assert body["embed_calls"] == 0
        assert body["embed_texts"] == 0
        assert body["embed_model"] is None
        assert body["embed_identity_mixed"] is False

        # The REAL writer, through the REAL seam, onto the fake ledger row.
        metering.record_embedding_usage(
            TEAM_ROUTE, calls=2, texts=7, chars=140, wall_ms=12.25, skipped=1,
            model="BAAI/bge-small-en-v1.5", revision="rev-x")

        got = tc.get("/v1/team", headers=self._auth())
        assert got.status_code == 200, got.text
        b = got.json()
        assert b["embed_calls"] == 2
        assert b["embed_texts"] == 7
        assert b["embed_chars"] == 140
        assert b["embed_wall_ms"] == pytest.approx(12.25)
        assert b["embed_skipped"] == 1
        assert b["embed_model"] == "BAAI/bge-small-en-v1.5"
        assert b["embed_revision"] == "rev-x"
        assert b["embed_identity_mixed"] is False
        # The figure rides ALONGSIDE the existing fields — no pre-existing key
        # was displaced (#4488 is additive).
        assert "write_ops_used" in b and "ask_calls" in b


# ════════════════════════════════════════════════════════════════════════════
# Work-owning runner wiring (decorator + write-op boundary)
# ════════════════════════════════════════════════════════════════════════════

class TestSessionLaneAttribution:
    """#4488 P1 (code-review cycle 1): the SESSION-auth lane resolved an org
    WITHOUT stamping ``request.state.org_id``, so ``EmbedMeteringMiddleware``
    could not attribute the tally on any dashboard write — every session
    ``POST /v1/points`` / ``/v1/objects`` / ``/v1/subjects`` that encoded was
    dropped AND misreported as a bookkeeping fault (UNMETERED_INCREMENT on a
    perfectly resolvable org).
    """

    def test_session_lane_stamps_the_org_on_request_state(self, monkeypatch):
        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        monkeypatch.setattr(sc, "user_memberships",
                            lambda cp, uid: [{"org_id": ORG, "role": "owner"}])
        monkeypatch.setattr(sc, "_orgs_row_fail_soft",
                            lambda cp, oid, **kw: {"tier": "free"})
        monkeypatch.setattr(ha, "_org_node_sync_limits", lambda oid: {})

        async def _noop(*a, **kw):
            return None

        monkeypatch.setattr(ha, "_abuse_post_auth", _noop)

        class _Req:
            def __init__(self):
                self.query_params: dict = {}
                self.state = type("S", (), {})()

        req = _Req()
        org = asyncio.run(ha._session_user_org(req, {"user_id": "u1"}))
        assert org["org_id"] == ORG
        # Mutation: dropping the stamp line → AttributeError here (and, in
        # production, an unattributable tally + a spurious operator incident).
        assert req.state.org_id == ORG


class TestRunnerWiring:
    def test_decorator_attributes_the_runner_org_sync(self, monkeypatch):
        # Mutation: relying on the middleware only → the internal seed lanes
        # (which never set scope["state"]["org_id"]; the session lane DOES
        # stamp it, since the round-1 fix) drop their work into an
        # unattributable tally and fire a spurious alert.
        import tortoise.hosted_api as ha
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)

        @ha._embed_metered
        def _runner(org_id, *, org_name=None):
            em.note_encode(texts=4, chars=40, wall_ms=2.0)
            return "ok"

        assert _runner("org-X", org_name="n") == "ok"
        assert len(calls) == 1 and calls[0]["org_id"] == "org-X"
        assert calls[0]["texts"] == 4
        assert alerts == []

    def test_decorator_binds_org_id_by_name_not_position(self, monkeypatch):
        # __run_indexing(job_id, org_id, …) — org_id is NOT the first parameter.
        # Mutation: taking args[0] as the org → every index job is attributed to
        # a job id.
        import tortoise.hosted_api as ha
        calls = _capture_writer(monkeypatch)

        @ha._embed_metered
        def _runner(job_id, org_id, repos=None):
            em.note_encode(texts=2, chars=2, wall_ms=1.0)

        _runner("job-1", "org-Z")
        assert calls and calls[0]["org_id"] == "org-Z"

    def test_decorator_async_arm(self, monkeypatch):
        import tortoise.hosted_api as ha
        calls = _capture_writer(monkeypatch)

        @ha._embed_metered
        async def _runner(org_id, key=None):
            em.note_encode(texts=1, chars=1, wall_ms=1.0)

        asyncio.run(_runner("org-Y", key="org-Y::g"))
        assert calls and calls[0]["org_id"] == "org-Y"

    def test_decorator_flushes_when_the_runner_raises(self, monkeypatch):
        import tortoise.hosted_api as ha
        calls = _capture_writer(monkeypatch)

        @ha._embed_metered
        def _runner(org_id):
            em.note_encode(texts=3, chars=3, wall_ms=1.0)
            raise RuntimeError("runner failed")

        with pytest.raises(RuntimeError, match="runner failed"):
            _runner("org-R")
        assert calls and calls[0]["texts"] == 3

    def test_mcp_quota_gated_without_org_does_not_alert(self, monkeypatch):
        """The stdio transport has NO org (``_enforce_quota`` says so), and every
        sibling writer exempts ``not org_id``. Arming a tally with no org would
        make every stdio write that encodes fire an UNMETERED_INCREMENT incident
        on a lane that has no tenant BY DESIGN — a permanent false alarm about
        attribution, on a transport where nothing is wrong.

        Mutation: arming unconditionally → alerts fires → RED.
        """
        import tortoise.mcp_auth as mcp_auth
        import tortoise.mcp_server as mcp
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)
        monkeypatch.setattr(mcp, "_enforce_quota", lambda resource: None)
        monkeypatch.setattr(mcp_auth, "_current_org_id",
                            contextvars.ContextVar("t", default=None))
        monkeypatch.setattr(mcp_auth, "_current_org_limits",
                            contextvars.ContextVar("l", default=None))

        def _write():
            em.note_encode(texts=3, chars=3, wall_ms=1.0)
            return "written"

        assert mcp._quota_gated(_write, resource="points")() == "written"
        assert calls == []
        assert alerts == []

    def test_record_write_op_does_not_add_a_second_ledger_write(self, monkeypatch):
        """#4488: the write path must NOT gain a SECOND blocking ledger write.

        The HTTP boundary already flushes the tally in the middleware, which
        runs the write OFF the event loop; flushing again from ``_record_write_op``
        (a synchronous, on-loop site — the #4451 residual) would double the
        blocking ledger work per write and lengthen every response, on a
        transport that carries a hard wait bound. This test pins the ABSENCE.
        """
        import tortoise.hosted_api as ha
        calls = _capture_writer(monkeypatch)
        monkeypatch.setattr(metering, "record_write_ops", lambda *a, **k: None)
        em.arm("org-W")
        em.note_encode(texts=3, chars=3, wall_ms=1.0)
        ha._record_write_op({"org_id": "org-W"})
        assert calls == []                     # no write from this boundary
        assert em.current_tally() is not None  # still owned by the request

    def test_mcp_quota_gated_owns_a_fresh_tally(self, monkeypatch):
        # Mutation: meted() inheriting the ambient tally → the hosted capture
        # lane's request tally would be double-counted by the MCP lane.
        import tortoise.mcp_auth as mcp_auth
        import tortoise.mcp_server as mcp
        calls = _capture_writer(monkeypatch)
        monkeypatch.setattr(mcp, "_enforce_quota", lambda resource: None)
        monkeypatch.setattr(mcp_auth, "_current_org_id",
                            contextvars.ContextVar("t", default="org-M"))
        monkeypatch.setattr(mcp_auth, "_current_org_limits",
                            contextvars.ContextVar("l", default=None))

        def _write():
            em.note_encode(texts=6, chars=60, wall_ms=5.0)
            return "written"

        gated = mcp._quota_gated(_write, resource="points")
        outer = em.arm("outer")
        assert gated() == "written"
        assert calls and calls[0]["org_id"] == "org-M" and calls[0]["texts"] == 6
        assert outer.calls == 0                # the request tally is untouched


# ════════════════════════════════════════════════════════════════════════════
# The swallow-site completeness fence (the seventh lane)
# ════════════════════════════════════════════════════════════════════════════

def test_embed_lane_is_declared_and_censused():
    # Mutation: calling report_unmetered_increment POSITIONALLY in
    # embed_metering.py → the fence's keyword-only census misses the lane and
    # the declared seventh entry has no emitted pair (RED in the #3981 file,
    # asserted here too so the failure is local and legible).
    import re

    from tests.test_metering_window_admission import SITE_LANES
    assert SITE_LANES["embed"] == "tortoise/embed_metering.py"
    src = (Path(__file__).resolve().parents[1]
           / "tortoise" / "embed_metering.py").read_text(encoding="utf-8")
    lanes = set(re.findall(
        r'report_unmetered_increment\(\s*lane="([a-z_]+)"', src))
    assert lanes == {"embed"}, lanes
