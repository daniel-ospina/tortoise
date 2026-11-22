"""#14186 — ONE embedder seam (inprocess | worker | external) + bounded batching.

WHAT THESE ROWS PROTECT, and why each one would redden on the failure it exists
for rather than merely documenting it:

* **The hard maximum wait.** The batching literature is unambiguous that
  batching without a bound trades tail latency for throughput, and the issue's
  contradiction test (a) makes an unbounded collector unshippable. So the rows
  below do not assert "coalescing happened" — a collector that waited forever
  would pass that. They assert the WAIT a ticket actually observed never exceeds
  the configured window (plus one scheduling quantum), and that the window is
  anchored to the batch's FIRST arrival rather than extended by later ones.
* **Order preservation.** ``encode_batch_for_store`` indexes the result per
  input and the turn writers index it per windowed turn, so a coalesced batch
  that reordered its rows would silently attach one turn's vector to another —
  a wrong vector is worse than no vector, because nothing downstream can detect
  it.
* **The three backings are interchangeable.** Every row that exercises a
  non-default backing goes through the SAME public entry points
  (``encode_query`` / ``encode_texts``) the read and write paths call, so a
  backing that only works when called directly would redden here.
* **No silent fallback.** A worker that cannot start and an endpoint that
  answers 500 must both DECLINE with a named reason — never quietly encode
  in-process, which is how "we moved the embedder off the request path" becomes
  untrue while every surface says ok.
* **p50/p95 per backing, always all three.** The issue's indicator names the
  distribution PER BACKING; a table that only appears once you have already
  switched cannot answer "would the other one be better".

⛔ The fake backings here are deliberately NOT ``EmbeddingModel`` doubles: this
suite tests the SEAM (the queue, the bound, the bookkeeping, the transport). The
encoder's own behaviour is pinned by ``tests/test_embeddings.py`` and the read
path's decline ladder by ``tests/test_13528_read_never_blocks_on_embedder.py``.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import math
import os
import socket
import subprocess
import tempfile
import threading
import time

import numpy as np
import pytest

from tortoise import embed_seam

# ── doubles ──────────────────────────────────────────────────────────────────


class _RecordingBacking:
    """A backing that records every batch it is asked to encode.

    ``delay_s`` makes the encode slow enough for peers to queue behind it, which
    is how the "opportunistic coalescing at a zero window" property is observed.
    """

    name = "recording"

    def __init__(self, *, dim: int = 4, delay_s: float = 0.0) -> None:
        self.batches: list[list[str]] = []
        self.dim = dim
        self.delay_s = delay_s
        self._lock = threading.Lock()
        #: Set once an encode is IN PROGRESS — the only state in which a later
        #: arrival can queue behind it, which is the state review F1 is about.
        self.entered = threading.Event()
        #: Set by :meth:`close`, so a row can prove a retired backing was closed
        #: (and, in the F5 rows, that it was NOT closed under an in-flight encode).
        self.closed = False

    def encode(self, texts, *, timeout_s=None):
        self.entered.set()
        if self.delay_s:
            time.sleep(self.delay_s)
        with self._lock:
            self.batches.append(list(texts))
        # A deterministic, TEXT-DEPENDENT vector, so the rows can assert that
        # each ticket got ITS OWN row back and not a neighbour's.
        return [[float(len(t)), float(ord(t[0]) if t else 0)] + [0.0] * (self.dim - 2)
                for t in texts]

    def close(self) -> None:
        self.closed = True

    def describe(self) -> dict:
        return {"backing": self.name, "batches": len(self.batches)}


class _ExplodingBacking:
    name = "exploding"

    def __init__(self) -> None:
        #: How many times the seam actually ASKED this backing. The read-path row
        #: uses it to prove the seam was consulted at all: a tree that routes
        #: around the seam (``_seam_remote = False``) never increments it.
        self.calls = 0

    def encode(self, texts, *, timeout_s=None):
        self.calls += 1
        raise RuntimeError("encoder went away")

    def close(self) -> None:
        return None

    def describe(self) -> dict:
        return {"backing": self.name}


class _UnavailableBacking:
    name = "unavailable"

    def encode(self, texts, *, timeout_s=None):
        return None

    def close(self) -> None:
        return None

    def describe(self) -> dict:
        return {"backing": self.name}


@pytest.fixture
def backing():
    """Install a recording backing and reset the seam around the test."""
    def _install(**kwargs) -> _RecordingBacking:
        recorder = _RecordingBacking(**kwargs)
        embed_seam.install_backing(recorder)
        return recorder

    yield _install
    embed_seam.install_backing(None)


def _pump(count: int, start: threading.Barrier, results: list, errors: list,
          text_prefix: str = "q") -> list[threading.Thread]:
    """Start ``count`` threads that all call ``encode_query`` at once."""
    def _one(index: int) -> None:
        start.wait(timeout=10)
        try:
            results.append((index, embed_seam.encode_query(f"{text_prefix}{index}")))
        except BaseException as exc:
            errors.append((index, exc))

    threads = [
        threading.Thread(target=_one, args=(i,), daemon=True) for i in range(count)]
    for thread in threads:
        thread.start()
    return threads


def _join(threads, timeout: float = 30.0) -> None:
    for thread in threads:
        thread.join(timeout=timeout)
    alive = [t for t in threads if t.is_alive()]
    assert not alive, f"{len(alive)} encode_query call(s) never returned"


# ── the bound ────────────────────────────────────────────────────────────────


class TestTheCollectionWindowIsHard:
    def test_concurrent_queries_coalesce_into_far_fewer_encodes(
            self, backing, monkeypatch):
        """N concurrent single-text embeds must become << N encodes.

        ⛔ THE ROW THE ISSUE ASKS FOR. ``encode_query`` per query would be N
        calls; the whole premise (0.36 s/item batched vs 0.8-2 s single) is that
        the work is scheduled, not faster. With a 50 ms window and a 150 ms
        encode, eight simultaneous callers must land in ONE batch.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "50")
        recorder = backing(delay_s=0.15)

        results: list = []
        errors: list = []
        threads = _pump(8, threading.Barrier(8), results, errors)
        _join(threads)

        assert not errors, f"an encode_query raised: {errors}"
        assert len(results) == 8, "a caller got no answer"
        assert len(recorder.batches) == 1, (
            "eight concurrent query embeds were not coalesced into one encode: "
            f"{[len(b) for b in recorder.batches]} "
            f"(the collection window is not doing its job)")
        assert sorted(t for batch in recorder.batches for t in batch) == \
            [f"q{i}" for i in range(8)], "a query was encoded twice or dropped"
        # ⛔ ONE text per row back to the ticket that asked for it — see the
        # module docstring on why a reordered batch is worse than no batch.
        for index, vector in results:
            assert vector is not None
            assert vector[0] == float(len(f"q{index}")), (
                f"ticket q{index} received another ticket's row: {vector}")

    def test_the_wait_never_exceeds_the_window(self, backing, monkeypatch):
        """The measured COLLECTION wait is bounded by ``max_wait_ms``.

        The hard-max-wait property, measured on the number the window is actually
        answerable for: ``latency.collect_ms`` is dequeued→fired, so it cannot
        include time a ticket spent queued behind the PREVIOUS encode.

        ⛔ THIS ROW USED TO ASSERT ON ``wait_ms``, and had to allow 120 ms against
        a 40 ms window to do it (3x the bound it claimed to enforce) — because
        ``wait_ms`` is submission→fire and it legitimately carries that queueing
        (review F5). Retargeted here: the mutant that widens the window or drops
        the deadline check moves ``collect_ms.max`` to ~140 ms against a 40 ms
        bound, which the half-window allowance below catches.
        """
        window_ms = 40.0
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, str(window_ms))
        backing(delay_s=0.05)

        results: list = []
        errors: list = []
        threads = _pump(12, threading.Barrier(12), results, errors)
        _join(threads)

        assert not errors
        latency = embed_seam.seam_status()["latency"]
        collect = latency["collect_ms"]
        assert collect["n"] >= 1
        assert collect["p95"] is not None and collect["max"] is not None
        # A ticket that joins just as the window closes sees the FULL window; the
        # slack is for the collector being descheduled between reading the clock
        # and firing, not for the window itself — so it is UNDER half a window, and
        # a deadline that is not enforced cannot hide inside it.
        slack_ms = window_ms / 2.0
        assert collect["max"] <= window_ms + slack_ms, (
            f"the window stayed open {collect['max']} ms against a {window_ms} ms "
            "bound — the collection bound is not hard")
        # End-to-end latency is published BESIDE it and is larger by construction.
        # Asserting only that it was measured keeps the two quantities distinct.
        assert latency["wait_ms"]["n"] == 12, (
            "the end-to-end wait was not published alongside the bounded one")

    def test_a_late_arrival_does_not_extend_the_open_window(
            self, backing, monkeypatch):
        """The window is anchored to the FIRST arrival, never re-armed.

        ⛔ THIS IS THE PROPERTY A "wait until the queue is quiet" DESIGN LOSES, and
        seeing it requires an arrival that is GENUINELY late. The previous cut
        submitted a lone ticket, joined it, then fired a simultaneous burst — a
        shape in which one window and a window re-armed on every arrival both
        produce ONE batch, so the row could not redden for the behaviour its name
        claims (review F4a).

        Here the second ticket arrives at 0.8 x the window, and two independent
        assertions separate the designs:
          * ONE batch ⇒ the two shared a window (coalescing held), and
          * the batch fired ~1.0 x the window after the FIRST arrival, not ~1.8 x
            ⇒ the deadline the later arrival would have set was never used.
        The slack is half a window; a re-arm overshoots by 0.8 of one.
        """
        window_ms = 300.0
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, str(window_ms))
        recorder = backing(delay_s=0.0)

        results: list = []
        errors: list = []
        started = time.monotonic()
        early = _pump(1, threading.Barrier(1), results, errors, text_prefix="early")
        time.sleep(window_ms / 1000.0 * 0.8)
        late = _pump(1, threading.Barrier(1), results, errors, text_prefix="late")
        _join(late)
        _join(early)
        elapsed_s = time.monotonic() - started

        assert not errors, f"an encode_query raised: {errors}"
        assert len(results) == 2, "a caller got no answer"
        assert len(recorder.batches) == 1, (
            "a late arrival opened a SECOND window instead of riding the open "
            f"one: {recorder.batches}")
        assert sorted(recorder.batches[0]) == ["early0", "late0"], (
            f"the late ticket did not join the open batch: {recorder.batches[0]}")
        slack_s = (window_ms / 1000.0) / 2.0
        assert elapsed_s < (window_ms / 1000.0) + slack_s, (
            f"the batch fired {elapsed_s:.2f}s after the FIRST arrival against a "
            f"{window_ms} ms window — a later arrival extended the deadline")
        # The same fact on the seam's own published number: the window was open
        # for about one window, not for 1.8 of them.
        collect = embed_seam.seam_status()["latency"]["collect_ms"]
        assert collect["max"] <= window_ms + slack_s * 1000.0, (
            f"collect_ms recorded a {collect['max']} ms window against a "
            f"{window_ms} ms bound")

    def test_wait_ms_is_end_to_end_while_collect_ms_is_the_window(
            self, monkeypatch):
        """The two published waits are DIFFERENT quantities (review F5).

        ⛔ With a 0 ms window — the ``inprocess`` DEFAULT — a ticket can still
        wait a quarter of a second, because it arrived while the previous batch
        was encoding. That wait is real caller latency and it is what ``wait_ms``
        publishes, but it is NOT the window's doing, so the bound ``max_wait_ms``
        publishes (0.0) does not cover it. Publishing only ``wait_ms`` made the
        runbook read "a non-zero observation means the bound is broken" about a
        perfectly healthy process; ``collect_ms`` is the window-attributable
        number, and here it stays ~0 while ``wait_ms`` carries the queueing.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "0")
        encode_s = 0.25
        entered = threading.Event()
        batches: list = []

        class _SlowBacking:
            name = "slow"

            def encode(self, texts, *, timeout_s=None):
                entered.set()
                time.sleep(encode_s)
                batches.append(list(texts))
                return [[float(len(t))] for t in texts]

            def close(self) -> None:
                return None

            def describe(self) -> dict:
                return {"backing": self.name}

        embed_seam.install_backing(_SlowBacking())
        results: list = []
        errors: list = []
        try:
            first = _pump(1, threading.Barrier(1), results, errors, text_prefix="a")
            # ⛔ Wait until the encoder is INSIDE the first encode, so the second
            # ticket provably queues behind it rather than racing the dequeue.
            assert entered.wait(10), "the collector never started the first encode"
            second = _pump(1, threading.Barrier(1), results, errors, text_prefix="b")
            _join(second)
            _join(first)
            latency = embed_seam.seam_status()["latency"]
        finally:
            embed_seam.install_backing(None)

        assert not errors, f"an encode_query raised: {errors}"
        assert len(results) == 2, "a caller got no answer"
        assert len(batches) == 2, (
            f"a 0 ms window must not coalesce across an encode it could not see: "
            f"{batches}")
        collect = latency["collect_ms"]
        wait = latency["wait_ms"]
        assert collect["max"] <= 60.0, (
            f"a 0 ms window published a {collect['max']} ms collection wait — the "
            "window-attributable number is carrying queueing it does not own")
        assert wait["max"] >= 150.0, (
            f"wait_ms did not record the queueing it exists to report: {wait}")

    def test_a_ticket_whose_batch_never_fires_is_declined_not_parked(
            self, monkeypatch):
        """``submit``'s safety valve: a wedged backing must not hang a read.

        ``fired_by = window + request timeout + 1 s``, so when no batch fires the
        ticket RAISES inside that bound instead of blocking forever — the #13528
        shape (a read must not park indefinitely on the embedder). No row covered
        it, which made it an unproven bound on the one path where a wedged backing
        is otherwise silent.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "10")
        monkeypatch.setenv(embed_seam.ENV_TIMEOUT_S, "0.1")

        class _WedgedBacking:
            name = "wedged"

            def encode(self, texts, *, timeout_s=None):
                time.sleep(2.5)  # longer than the valve, shorter than the test
                return [[0.0] for _ in texts]

            def close(self) -> None:
                return None

            def describe(self) -> dict:
                return {"backing": self.name}

        embed_seam.install_backing(_WedgedBacking())
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError, match="no batch fired"):
                embed_seam.encode_query("x")
            elapsed_s = time.monotonic() - started
        finally:
            embed_seam.install_backing(None)
        assert elapsed_s < 2.0, (
            f"the safety valve did not fire: the ticket parked for {elapsed_s:.2f}s")

    def test_a_ticket_waits_out_a_worker_boot_instead_of_declining(
            self, monkeypatch):
        """The valve is sized per PHASE, not by one request budget (review F1).

        ⛔ THE SHIPPED DEFECT, DRIVEN THROUGH THE PUBLIC ENTRY POINT. The startup
        lever was split off so a boot would not be bounded by the 30 s request
        budget — and ``_Collector.submit`` still used ``window + request + 1``, so
        the FIRST query after every worker boot declined at ~31 s while the worker
        was legally loading (up to 600 s). The read then went keyword-only, and the
        runbook told the operator to read the ABSENCE of ``last_decline`` as
        "it was only the boot" — the one reading the defect inverts.

        So the backing here is a ``WorkerBacking`` whose start-up pays
        ``window + request + 1.5 s``: longer than the old valve, shorter than the
        boot budget. Pre-fix this RAISES ``TimeoutError``; it must return the
        vector.
        """
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "20")
        monkeypatch.setenv(embed_seam.ENV_TIMEOUT_S, "0.4")
        boot_s = 0.02 + 0.4 + 1.5

        class _BootingWorkerBacking(embed_seam.WorkerBacking):
            """Pays a boot inside ``encode``, then answers like a worker would."""

            def encode(self, texts, *, timeout_s=None):
                time.sleep(boot_s)
                return [[float(len(t))] for t in texts]

        booting = _BootingWorkerBacking()
        embed_seam.install_backing(booting)
        try:
            vector = embed_seam.encode_query("why did the window change")
            assert vector == [float(len("why did the window change"))], vector
            assert embed_seam.last_decline() is None, (
                "a healthy boot recorded a decline — the operator diagnostic the "
                f"runbook tells them to trust is inverted again "
                f"({embed_seam.last_decline()!r})")
        finally:
            embed_seam.install_backing(None)

    def test_a_sustained_load_keeps_coalescing_behind_a_busy_collector(
            self, backing, monkeypatch):
        """The window still coalesces when the collector was BUSY (review F1).

        ⛔ THE STATE THAT MATTERS IS A TICKET QUEUED BEHIND AN ENCODE, and every
        other coalescing row (and the first cut of this one) never reaches it:
        a burst fired on a barrier at an IDLE collector agrees with both anchors,
        and joining each round before starting the next keeps the collector idle
        too. So the later rounds here are submitted WHILE round 0 is inside the
        encoder (`entered`), which is ordinary sustained traffic.

        The shipped anchor was the ticket's SUBMISSION, which by then is up to a
        whole encode in the past, so ``remaining`` was already <= 0 and every
        queued ticket fired as a one-ticket batch — 29 encodes for 32 tickets,
        the exact opposite of this module's headline claim, on precisely the two
        backings whose window is non-zero. Anchored at the dequeue, the 28 queued
        tickets drain into one batch.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "20")
        recorder = backing(delay_s=0.15)

        rounds = 8
        per_round = 4
        results: list = []
        errors: list = []

        def _round(round_index: int) -> list[threading.Thread]:
            return _pump(
                per_round, threading.Barrier(per_round), results, errors,
                text_prefix=f"r{round_index}q")

        threads = _round(0)
        assert recorder.entered.wait(10), (
            "the collector never started the first encode, so no later ticket "
            "could queue behind it")
        # ⛔ Submitted WHILE round 0 is encoding — not after it returns.
        for round_index in range(1, rounds):
            threads.extend(_round(round_index))
        _join(threads)

        assert not errors, f"an encode_query raised: {errors}"
        assert len(results) == rounds * per_round, "a caller got no answer"
        # One encode per queued DRAIN is the target; the allowance covers a ticket
        # descheduled past the window. The broken anchor produces ~29 of 32.
        assert len(recorder.batches) <= 6, (
            f"{rounds * per_round} queries, {rounds} of them concurrent per "
            f"round, produced {len(recorder.batches)} encodes — the window is "
            "not coalescing behind a busy collector")
        assert sorted(t for b in recorder.batches for t in b) == sorted(
            f"r{r}q{i}" for r in range(rounds) for i in range(per_round)), (
            "a query was encoded twice or dropped")

    def test_max_batch_fires_the_batch_early(self, backing, monkeypatch):
        """Reaching ``max_batch`` must fire at once, not wait out the window."""
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "5000")
        monkeypatch.setenv(embed_seam.ENV_MAX_BATCH, "3")
        recorder = backing(delay_s=0.0)

        results: list = []
        errors: list = []
        started = time.monotonic()
        threads = _pump(3, threading.Barrier(3), results, errors)
        _join(threads)
        elapsed_s = time.monotonic() - started

        assert not errors
        assert len(recorder.batches) == 1, (
            f"3 tickets with max_batch=3 produced {len(recorder.batches)} batches")
        assert len(recorder.batches[0]) == 3
        # ⛔ The 5 s window is still OPEN when the third ticket arrives; a batch
        # that waited for it would take ~5 s. The generous 2 s allowance is for a
        # heavily loaded CI box, not for the window.
        assert elapsed_s < 2.0, (
            f"max_batch did not fire the batch early — it took {elapsed_s:.2f}s "
            "with a 5 s window open")

    def test_zero_window_never_delays_an_uncontended_query(
            self, backing, monkeypatch):
        """The in-process default adds NO latency to a lone query.

        ⛔ THE DEFAULT POSTURE, PINNED. A warm encode is 25-43 ms (#13224), so a
        20 ms window would be a comparable addition to the latency it is meant to
        hide. ``max_wait_ms`` is 0 for ``inprocess`` for exactly that reason, and
        this row is what stops a future "let's batch everything" from quietly
        making every query wait.
        """
        monkeypatch.delenv(embed_seam.ENV_MAX_WAIT_MS, raising=False)
        assert embed_seam.resolve_max_wait_ms(embed_seam.BACKING_INPROCESS) == 0.0
        recorder = backing(delay_s=0.0)

        started = time.monotonic()
        vector = embed_seam.encode_query("lonely")
        elapsed_ms = (time.monotonic() - started) * 1000.0

        assert vector is not None
        assert len(recorder.batches) == 1
        assert elapsed_ms < 100.0, (
            f"an uncontended query waited {elapsed_ms:.1f} ms on the in-process "
            "backing — the default window must be 0")

    def test_the_zero_window_still_coalesces_opportunistically(
            self, backing, monkeypatch):
        """With a 0 ms window, arrivals during an encode still share the next one.

        The other half of the default posture: 0 ms does not switch batching off.
        While the collector encodes batch 1, the tickets that arrive become batch
        2 — continuous batching at no admission cost. Four tickets, one slow
        encode: the first batch is small, and the remaining ones arrive together.
        """
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "0")
        recorder = backing(delay_s=0.25)

        results: list = []
        errors: list = []
        threads = _pump(6, threading.Barrier(6), results, errors)
        _join(threads)

        assert not errors, f"an encode_query raised: {errors}"
        assert len(results) == 6
        sizes = [len(batch) for batch in recorder.batches]
        assert len(recorder.batches) < 6, (
            f"a 0 ms window disabled coalescing entirely: {sizes}")
        assert sum(sizes) == 6, f"a query was dropped or encoded twice: {sizes}"


# ── decline semantics ────────────────────────────────────────────────────────


class TestDeclinesAreNamed:
    def test_a_raising_backing_surfaces_to_the_caller_and_is_recorded(self):
        """A backing failure RAISES, and the seam records why.

        ⛔ NO SILENT FALLBACK. The caller's own ``except Exception`` turns this
        into ``encode_failed`` — the vocabulary it already has — and the seam's
        ``last_decline`` names the actual cause for an operator. A fallback to
        in-process would put the 2-4 s back inside the request while every
        surface read "worker".
        """
        embed_seam.install_backing(_ExplodingBacking())
        try:
            with pytest.raises(RuntimeError, match="encoder went away"):
                embed_seam.encode_query("boom")
            assert "encoder went away" in (embed_seam.last_decline() or "")
            status = embed_seam.seam_status()
            assert status["last_decline_age_s"] is not None
            assert status["latency"]["declines"] == 1
            assert status["latency"]["batches"] == 1
        finally:
            embed_seam.install_backing(None)

    def test_a_batch_entry_raises_too_and_is_recorded(self):
        """``encode_texts`` has the same contract — the write path relies on it."""
        embed_seam.install_backing(_ExplodingBacking())
        try:
            with pytest.raises(RuntimeError, match="encoder went away"):
                embed_seam.encode_texts(["a", "b"])
            assert "encoder went away" in (embed_seam.last_decline() or "")
        finally:
            embed_seam.install_backing(None)

    def test_an_unavailable_encoder_answers_none_not_an_exception(self):
        """``None`` is the designed absence; the seam must not convert it.

        The distinction matters: ``None`` means "no encoder installed", which
        every caller degrades on silently by design, while a raise means "the
        encoder broke" and must be recorded. One word cannot carry both (the
        ``embed_get``/``embed_encode`` rule from ``#10893``).
        """
        embed_seam.install_backing(_UnavailableBacking())
        try:
            assert embed_seam.encode_query("anything") is None
            assert embed_seam.encode_texts(["anything"]) is None
            assert embed_seam.last_decline() is None, (
                "the designed absence was recorded as a failure")
        finally:
            embed_seam.install_backing(None)


# ── configuration ────────────────────────────────────────────────────────────


class TestConfiguration:
    def test_the_default_backing_is_in_process(self, monkeypatch):
        """Unset ⇒ the pre-#14186 path. The rollback must be the default."""
        monkeypatch.delenv(embed_seam.ENV_BACKING, raising=False)
        assert embed_seam.resolve_backing() == embed_seam.BACKING_INPROCESS

    @pytest.mark.parametrize("value", ["worker", "WORKER", " external "])
    def test_the_lever_selects_a_backing_case_insensitively(
            self, monkeypatch, value):
        monkeypatch.setenv(embed_seam.ENV_BACKING, value)
        assert embed_seam.resolve_backing() == value.strip().lower()

    def test_an_unknown_backing_is_refused_not_coerced_silently(
            self, monkeypatch, caplog):
        """A typo must never present as "the seam is deployed".

        ⛔ Coercing ``wroker`` to the default is the fail-open: the process runs
        in-process, the migration is un-done, and nothing says so. The WARNING is
        part of the contract, not decoration.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, "wroker")
        with caplog.at_level("WARNING"):
            assert embed_seam.resolve_backing() == embed_seam.BACKING_INPROCESS
        assert any("wroker" in record.getMessage() for record in caplog.records), (
            "an unknown backing was refused without saying which value was refused")

    def test_the_per_backing_windows_are_the_documented_posture(self, monkeypatch):
        """inprocess 0 ms, worker/external 20 ms — the stated default posture."""
        monkeypatch.delenv(embed_seam.ENV_MAX_WAIT_MS, raising=False)
        assert embed_seam.resolve_max_wait_ms(embed_seam.BACKING_INPROCESS) == 0.0
        assert embed_seam.resolve_max_wait_ms(embed_seam.BACKING_WORKER) == \
            embed_seam.DEFAULT_MAX_WAIT_MS
        assert embed_seam.resolve_max_wait_ms(embed_seam.BACKING_EXTERNAL) == \
            embed_seam.DEFAULT_MAX_WAIT_MS
        assert 0.0 < embed_seam.DEFAULT_MAX_WAIT_MS <= 50.0, (
            "the issue's stated bound is <= 50 ms")

    @pytest.mark.parametrize("raw", ["20ms", "-5", "nan", "inf", "-inf",
                                     "1e999", "1e12", ""])
    def test_an_unusable_window_falls_back_to_the_default_with_a_warning(
            self, monkeypatch, caplog, raw):
        """TOTAL: a bad number must not silently become 0 (i.e. no batching).

        ``inf`` / ``-inf`` / ``1e999`` are the P1 of review F1 at ``76e58a21bb``:
        the old guard (``not (value >= minimum) or value != value``) ACCEPTED an
        infinite window, because ``inf >= 0`` is true and ``inf != inf`` is false.
        ``1e12`` is the finite-but-absurd value — the same park, longer fuse.
        """
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, raw)
        with caplog.at_level("WARNING"):
            resolved = embed_seam.resolve_max_wait_ms(embed_seam.BACKING_WORKER)
        if raw == "":
            # An EMPTY value is "unset" by convention, not an error: an operator
            # clearing the lever gets the default and needs no warning.
            assert resolved == embed_seam.DEFAULT_MAX_WAIT_MS
        else:
            assert resolved == embed_seam.DEFAULT_MAX_WAIT_MS
            assert caplog.records, f"{raw!r} was accepted without a warning"

    def test_a_non_finite_window_cannot_park_a_ticket(self, monkeypatch):
        """The CONSEQUENCE of the P1, not just its parse: a bound stays a bound.

        The parser row above pins the refusal; this one pins why it matters. An
        infinite window made ``_collect``'s ``deadline = ... + window_s`` infinite
        (a lone query's batch could only fire on ``max_batch``) and sized
        ``submit``'s valve — ``window + budget + slack`` — to infinity: the
        unbounded wait the issue's contradiction test (a) makes unshippable,
        reachable by one typo. A future "accept it, clamp it later" reddens here.
        """
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "inf")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        window_ms = embed_seam.resolve_max_wait_ms()
        bound = embed_seam.resolve_ticket_wait_s(30.0)
        assert math.isfinite(window_ms), window_ms
        assert window_ms == 0.0, (
            "an `inf` window was accepted — the in-process default must apply")
        assert math.isfinite(bound), bound
        assert bound <= 31.0 + 0.001, (
            f"a non-finite window sized a ticket's wait bound to {bound}s")

    def test_the_other_wait_levers_refuse_infinity_too(self, monkeypatch, caplog):
        """Same class, the other two levers: an infinite request or boot budget.

        ``TORTOISE_EMBED_QUERY_TIMEOUT_S=inf`` makes ``sock.settimeout`` / the
        external ``urlopen`` bound outlive the process it is supposed to protect;
        an infinite ``..._WORKER_STARTUP_S`` parks every read on a boot that may
        never finish, because ``_ensure_started``'s deadline is
        ``time.monotonic() + timeout`` (#13528's shape).
        """
        monkeypatch.setenv(embed_seam.ENV_TIMEOUT_S, "inf")
        monkeypatch.setenv(embed_seam.ENV_WORKER_STARTUP_S, "1e999")
        with caplog.at_level("WARNING"):
            assert embed_seam.resolve_timeout_s() == embed_seam.DEFAULT_TIMEOUT_S
            assert embed_seam.resolve_worker_startup_s() == \
                embed_seam.DEFAULT_WORKER_STARTUP_S
        assert len(caplog.records) >= 2, (
            "an infinite budget was refused without saying which lever")

    def test_max_batch_refuses_a_non_positive_value(self, monkeypatch, caplog):
        monkeypatch.setenv(embed_seam.ENV_MAX_BATCH, "0")
        with caplog.at_level("WARNING"):
            assert embed_seam.resolve_max_batch() == embed_seam.DEFAULT_MAX_BATCH
        assert caplog.records


# ── the exported distributions ───────────────────────────────────────────────


class TestTheDistributionsAreExported:
    def test_the_status_lists_all_three_backings_with_their_windows(
            self, backing, monkeypatch):
        """``backings`` names EVERY backing, selected or not.

        ⛔ The issue's indicator is p50/p95 **per backing**. A table that only
        appears once you have switched cannot answer "would the other one be
        better" — the question an operator asks BEFORE switching.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        backing()
        embed_seam.encode_query("x")
        status = embed_seam.seam_status()

        assert set(status["backings"]) == set(embed_seam.BACKINGS)
        assert status["backings"][embed_seam.BACKING_INPROCESS]["selected"] is True
        assert status["backings"][embed_seam.BACKING_WORKER]["selected"] is False
        assert status["backings"][embed_seam.BACKING_EXTERNAL]["selected"] is False

    def test_the_selected_backing_publishes_percentiles_and_the_others_null(
            self, backing):
        """Measured percentiles for the backing that ran; ``None`` for the rest.

        ``None`` rather than 0: "never observed" and "cost nothing" are different
        claims, and a dashboard that cannot tell them apart reads an unwarmed
        process as an infinitely fast one.
        """
        backing()
        for index in range(4):
            embed_seam.encode_query(f"t{index}")
        status = embed_seam.seam_status()
        latency = status["latency"]

        assert latency["submissions"] == 4
        assert latency["batches"] == 4
        assert latency["encode_ms"]["n"] == 4
        assert latency["encode_ms"]["p50"] is not None
        assert latency["encode_ms"]["p95"] is not None
        assert latency["encode_ms"]["p50"] <= latency["encode_ms"]["p95"]
        assert latency["wait_ms"]["p95"] is not None
        assert latency["collect_ms"]["n"] == 4, (
            "the window-attributable wait was not published beside the "
            "end-to-end one")
        assert latency["collect_ms"]["p95"] is not None

        for other in (embed_seam.BACKING_WORKER, embed_seam.BACKING_EXTERNAL):
            assert status["backings"][other]["latency"] is None, (
                f"{other} never ran but published a distribution")

    def test_the_percentiles_are_observations_not_interpolations(self):
        """Nearest-rank: every published percentile is a number that was measured.

        An interpolated p95 can name a latency nothing ever observed, which makes
        "p95 = 1.9 s" unauditable against a log line. Also pins the two-clause
        contract of an empty reservoir: ``n == 0`` and all three percentiles
        ``None`` (never 0).
        """
        reservoir = embed_seam._Latency()
        assert reservoir.snapshot() == {
            "n": 0, "window": 0, "p50": None, "p95": None, "max": None}

        for value in range(1, 101):
            reservoir.note(float(value))
        view = reservoir.snapshot()
        assert view["n"] == 100
        # The textbook nearest-rank: the ceil(q*n)-th smallest of 1..100.
        assert view["p50"] == 50.0, "p50 is not a nearest-rank observation"
        assert view["p95"] == 95.0, "p95 is not a nearest-rank observation"
        assert view["max"] == 100.0

    def test_the_reservoir_rolls_over_without_losing_the_total(self):
        """A long-lived process keeps an honest ``n`` after the window rolls."""
        reservoir = embed_seam._Latency(window=4)
        for value in range(10):
            reservoir.note(float(value))
        view = reservoir.snapshot()
        assert view["window"] == 4
        assert view["n"] == 10
        assert view["max"] == 9.0

    def test_the_health_block_publishes_the_declared_shape(self, monkeypatch):
        """``/health``'s ``embed_seam`` block carries the seam's own key set.

        The block is built as ``{key: status.get(key) for key in KEYS}``, so this
        compares the app's own tuple with itself UNLESS the producer is checked
        too — hence the second assertion on :func:`embed_seam.seam_status`,
        which is what would silently redden a documented key to ``null``.
        """
        import tortoise.hosted_api as hosted_api

        block = hosted_api._embed_seam_health()
        # ⛔ THE LITERAL TUPLE, not ``set(KEYS)`` (review F6 at this head). The
        # block is PRODUCED as ``{key: status.get(key) for key in KEYS}``, so
        # comparing it with ``KEYS`` compares the tuple with itself and cannot
        # fail under any mutation — a tautology that reads as coverage. This is
        # the operator's ``jq .embed_seam.<key>`` contract, so it is spelled.
        assert set(block) == {
            "backing", "max_wait_ms", "max_batch", "timeout_s",
            "last_decline", "last_decline_age_s", "injected", "backing_state",
            "latency", "latency_stale", "backings",
        }, sorted(block)
        assert set(embed_seam.seam_status()) >= set(
            hosted_api._EMBED_SEAM_HEALTH_KEYS), (
            "`seam_status()` no longer produces a key `/health` publishes — the "
            "operator's read resolves to null forever")
        assert block["backing"] == embed_seam.BACKING_INPROCESS
        assert block["max_wait_ms"] == 0.0
        assert block["latency_stale"] is False, (
            "a fresh process read its own (never-observed) distributions as "
            "stale — the flag must answer 'did the lever move past the live "
            "backing', never 'has anything run yet'")

    def test_the_degraded_seam_arm_carries_its_reason_into_health(self, monkeypatch):
        """Review F4: ``seam_status`` NEVER RAISES, so its own ``error`` must ride.

        ``_embed_seam_health``'s ``except`` only catches a RAISE, but
        ``seam_status`` has its OWN degraded arm returning every key as ``None``
        PLUS ``error``. The reason was therefore dropped: ``/health`` published
        ``backing: null`` with nothing saying why, contradicting this module's own
        comment and the runbook ("every value null, plus ``error``"). The raise arm
        is pinned above; this drives the INTERNAL arm, which is the one that
        actually fires in a deployment.
        """
        import tortoise.hosted_api as hosted_api

        def _explode() -> str:
            raise RuntimeError("TORTOISE_EMBED_BACKING unreadable")

        # ``seam_status`` calls this FIRST, so the raise lands inside its own try.
        monkeypatch.setattr(embed_seam, "resolve_backing", _explode)
        block = hosted_api._embed_seam_health()
        assert "error" in block, (
            "the seam's own degraded arm arrived at /health as `backing: null` "
            "with no reason — the operator cannot tell a broken read from an "
            "unwarmed process")
        assert "TORTOISE_EMBED_BACKING unreadable" in block["error"]
        assert set(block) == set(hosted_api._EMBED_SEAM_HEALTH_KEYS) | {"error"}
        # The degraded arm's own shape: every key null except ``injected``, which
        # it answers ``False`` (nothing was injected into a read that failed).
        for key in hosted_api._EMBED_SEAM_HEALTH_KEYS:
            if key == "injected":
                assert block[key] is False, block[key]
                continue
            assert block[key] is None, (key, block[key])

    def test_the_health_producer_never_raises(self, monkeypatch):
        """A broken seam read must not 5xx ``/health``.

        ``health()``'s own docstring forbids it: a dead dependency is `degraded`,
        never a killed process. The DEGRADED arm publishes the SAME key set PLUS
        ``error`` — an extra key cannot ``KeyError`` a consumer, a missing one
        can.
        """
        import tortoise.hosted_api as hosted_api

        def _explode() -> dict:
            raise RuntimeError("seam state unreadable")

        monkeypatch.setattr(embed_seam, "seam_status", _explode)
        block = hosted_api._embed_seam_health()
        assert set(block) == set(hosted_api._EMBED_SEAM_HEALTH_KEYS) | {"error"}
        assert "seam state unreadable" in block["error"]
        for key in hosted_api._EMBED_SEAM_HEALTH_KEYS:
            assert block[key] is None


# ── the vector normaliser ────────────────────────────────────────────────────


class TestTheVectorNormaliser:
    def test_it_accepts_a_numpy_row_and_a_plain_list(self):
        """Both shapes, because the failure mode of accepting one is SILENT.

        ``compute_embeddings`` used to spell ``vec.tolist()``; on a plain list
        that raises ``AttributeError`` INSIDE a ``try`` whose handler returns
        ``[None] * n`` — a whole batch of computed vectors discarded, the #4280
        fail-open shape.
        """
        assert embed_seam.vector_to_list(np.array([1.5, 2.5], dtype=np.float32)) == \
            [1.5, 2.5]
        assert embed_seam.vector_to_list([1.5, 2.5]) == [1.5, 2.5]
        assert embed_seam.vector_to_list((1, 2)) == [1.0, 2.0]

    def test_a_float32_row_keeps_its_stored_precision(self):
        """The values are unchanged by the normaliser — no widening in the seam.

        #11821 P0's concern is the stored form, and a normaliser that
        round-tripped through a decimal string would move exactly those values.
        """
        row = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        assert embed_seam.vector_to_list(row) == [float(x) for x in row]


# ── the in-process backing is the pre-#14186 call ───────────────────────────


class TestTheInProcessBackingIsUnchangedWork:
    def test_it_calls_the_singleton_encode_with_no_extra_kwargs(self, monkeypatch):
        """``model.encode(list)``, exactly as ``compute_embeddings`` called it.

        ⛔ A nicer-looking kwarg (``show_progress_bar=False``, ``batch_size=32``)
        would raise ``TypeError`` inside every in-repo double that declares a
        narrower signature, and that ``TypeError`` is swallowed by the write
        paths' blanket handlers into "no vector stored" — the same fail-open in a
        new place.
        """
        calls: list[tuple] = []

        class _Model:
            def encode(self, texts):
                calls.append(tuple(texts))
                return np.array([[0.5, 0.25] for _ in texts], dtype=np.float32)

        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: _Model()))
        monkeypatch.setattr(
            "tortoise.embeddings.ensure_embed_threads", lambda: None)

        rows = embed_seam.encode_texts(["alpha", "beta"])
        assert rows == [[0.5, 0.25], [0.5, 0.25]]
        assert calls == [("alpha", "beta")], (
            f"the backing did not make the historical call: {calls}")

    def test_a_short_row_set_raises_instead_of_returning_a_ragged_batch(
            self, monkeypatch):
        """A short answer is REFUSED, because the callers index per input.

        ``encode_batch_for_store`` indexes the result per windowed turn; a short
        batch would raise ``IndexError`` inside the capture loop, after the
        Session write — a partial session.
        """
        class _ShortModel:
            def encode(self, texts):
                return np.array([[0.1, 0.2]], dtype=np.float32)

        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: _ShortModel()))
        monkeypatch.setattr(
            "tortoise.embeddings.ensure_embed_threads", lambda: None)

        with pytest.raises(ValueError, match="row"):
            embed_seam.encode_texts(["a", "b"])

    def test_the_collector_refuses_a_short_row_set_from_the_backing(
            self, monkeypatch):
        """The COLLECTOR's own short-answer guard, driven through ``encode_query``.

        ⛔ The row above drives ``encode_texts`` — the DIRECT path, which never
        reaches ``_Collector._fire`` — so deleting the collector's own
        ``len(rows) != len(batch)`` raise reddened nothing (review F4c). Two
        coalesced tickets and ONE row back is the shape that would otherwise hand
        a caller its neighbour's vector, or raise ``IndexError`` inside the ticket
        loop; each ticket must be DECLINED with the named refusal instead.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "60")

        class _ShortBacking:
            name = "short"

            def encode(self, texts, *, timeout_s=None):
                # ONE row for however many texts were asked for.
                return [[0.5, 0.25]]

            def close(self) -> None:
                return None

            def describe(self) -> dict:
                return {"backing": self.name}

        embed_seam.install_backing(_ShortBacking())
        try:
            results: list = []
            errors: list = []
            threads = _pump(2, threading.Barrier(2), results, errors)
            _join(threads)
        finally:
            embed_seam.install_backing(None)

        assert not results, f"a short batch produced vectors: {results}"
        assert len(errors) == 2, (
            f"both coalesced tickets must be refused, got {errors}")
        for _index, exc in errors:
            assert isinstance(exc, ValueError), exc
            assert "row" in str(exc), exc


# ── the external backing ────────────────────────────────────────────────────


class _Handler(http.server.BaseHTTPRequestHandler):
    """Answers whatever the enclosing test put in ``server.answer``.

    Records the HEADERS as well as the body (review F3): the documented request
    contract includes ``Authorization: Bearer <token>``, and a handler that keeps
    only ``json.loads(raw)`` cannot see the one field of that contract whose loss
    is a real deployment failure against any provider that requires it.
    """

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        self.server.requests.append(json.loads(raw.decode("utf-8")))
        self.server.headers.append(
            {name.lower(): value for name, value in self.headers.items()})
        body, code = self.server.answer
        payload = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) \
            else body
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:  # keep the suite's output clean
        return


@pytest.fixture
def http_endpoint():
    """A one-shot HTTP endpoint the external backing can be pointed at."""
    servers: list[http.server.HTTPServer] = []

    def _start(answer, code: int = 200):
        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        server.requests = []
        server.headers = []
        server.answer = (answer, code)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


class TestTheExternalBacking:
    def _backing(self, monkeypatch, server, *, token: str | None = None):
        port = server.server_address[1]
        monkeypatch.setenv(
            embed_seam.ENV_EXTERNAL_URL, f"http://127.0.0.1:{port}/embed")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        if token:
            monkeypatch.setenv(embed_seam.ENV_EXTERNAL_TOKEN, token)
        return embed_seam.ExternalBacking()

    @pytest.mark.parametrize("answer", [
        {"vectors": [[0.1, 0.2], [0.3, 0.4]]},
        {"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
        {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
    ])
    def test_it_accepts_the_three_published_answer_shapes(
            self, monkeypatch, http_endpoint, answer):
        """⛔ NO VENDOR IS BAKED IN — three answer shapes, one normalisation.

        Refusing two of the three would force an adapter service in front of a
        provider that already answers correctly, which is a vendor choice made by
        omission. The three are the shapes providers actually publish.
        """
        server = http_endpoint(answer)
        rows = self._backing(monkeypatch, server).encode(["a", "b"])
        assert rows == [[0.1, 0.2], [0.3, 0.4]]

    def test_the_request_carries_the_texts_model_and_optional_bearer(
            self, monkeypatch, http_endpoint):
        """The documented request contract, pinned field for field — headers too.

        ⛔ THE BEARER IS ASSERTED, NOT MERELY SENT (review F3). This row used to
        set a token and assert the BODY only, so deleting
        ``headers["authorization"]`` left it green while the prose above it
        claimed the contract was pinned "byte for byte". An unauthenticated POST
        is a real failure against any provider that requires the header, and it
        was the one field of the contract with no coverage.
        """
        server = http_endpoint({"vectors": [[0.5]]})
        monkeypatch.setenv(embed_seam.ENV_EXTERNAL_MODEL, "some-embed-v2")
        backing = self._backing(monkeypatch, server, token="tok-123")
        backing.encode(["hello"])
        sent = server.requests[0]
        assert sent == {"model": "some-embed-v2", "texts": ["hello"]}
        assert server.headers[0].get("authorization") == "Bearer tok-123", (
            "the request went out without its bearer — the documented contract's "
            f"one authenticated field is missing ({server.headers[0]})")

    def test_the_bearer_is_OMITTED_when_no_token_is_configured(
            self, monkeypatch, http_endpoint):
        """The control for the row above: an unset token must not send an empty one.

        ``Authorization: Bearer `` (empty) is a different request from no header
        at all, and several providers reject it. Without this row a fix that
        always sets the header would pass the bearer assertion.
        """
        server = http_endpoint({"vectors": [[0.5]]})
        monkeypatch.delenv(embed_seam.ENV_EXTERNAL_TOKEN, raising=False)
        backing = self._backing(monkeypatch, server)
        backing.encode(["hello"])
        assert "authorization" not in server.headers[0], (
            f"an unset token still sent an authorization header: {server.headers[0]}")

    def test_a_missing_url_is_a_named_decline_not_a_local_encode(
            self, monkeypatch):
        """⛔ NO SILENT FALLBACK: an unconfigured backing says so."""
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        monkeypatch.delenv(embed_seam.ENV_EXTERNAL_URL, raising=False)
        with pytest.raises(RuntimeError, match="nowhere to send"):
            embed_seam.ExternalBacking().encode(["a"])

    def test_an_http_error_is_raised_with_the_provider_message(
            self, monkeypatch, http_endpoint):
        """A 500 must not read as "these texts embed to nothing"."""
        server = http_endpoint(
            {"error": "capacity exhausted"}, code=503)
        with pytest.raises(RuntimeError, match="503"):
            self._backing(monkeypatch, server).encode(["a"])

    def test_an_unrecognised_answer_names_the_keys_it_got(
            self, monkeypatch, http_endpoint):
        """A validator that says only "bad shape" hides a provider migration."""
        server = http_endpoint({"result": []})
        with pytest.raises(ValueError, match="result"):
            self._backing(monkeypatch, server).encode(["a"])

    def test_a_short_answer_is_refused(self, monkeypatch, http_endpoint):
        server = http_endpoint({"vectors": [[0.1]]})
        with pytest.raises(ValueError, match="row"):
            self._backing(monkeypatch, server).encode(["a", "b"])

    def test_the_whole_seam_runs_on_the_external_backing(
            self, monkeypatch, http_endpoint):
        """The public entry points work unchanged against a remote encoder.

        This is the issue's first indicator — "the same code path can run the
        embedder … remotely without touching call sites" — exercised through
        ``encode_query`` exactly as the read path calls it.
        """
        server = http_endpoint({"vectors": [[7.0, 8.0]]})
        monkeypatch.setenv(
            embed_seam.ENV_EXTERNAL_URL,
            f"http://127.0.0.1:{server.server_address[1]}/embed")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        assert embed_seam.resolve_backing() == embed_seam.BACKING_EXTERNAL
        assert embed_seam.encode_query("anything") == [7.0, 8.0]
        assert embed_seam.seam_status()["backing"] == embed_seam.BACKING_EXTERNAL


# ── the worker backing ──────────────────────────────────────────────────────


class _ServingThread:
    """Serve ``embed_worker._serve_connection`` on a real UNIX socket.

    ⛔ The tests call the CONNECTION HANDLER, not ``serve()``: ``serve`` starts a
    stdin watcher that calls ``os._exit`` when the pipe closes, which under
    pytest would take the whole session down. The lifecycle rows below use a real
    subprocess for the parts that need one.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(path):
            os.unlink(path)
        self._server.bind(path)
        self._server.listen(8)
        self._server.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _ServingThread:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        self._thread.join(timeout=5)
        self._server.close()
        if os.path.exists(self.path):
            os.unlink(self.path)
        return False

    def _run(self) -> None:
        from tortoise import embed_worker

        while not self._stop.is_set():
            try:
                conn, _addr = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with contextlib.suppress(Exception):
                embed_worker._serve_connection(conn)


class TestTheWorkerBacking:
    def test_the_protocol_round_trips_and_keeps_input_order(
            self, tmp_path, monkeypatch):
        """One JSON line in, one JSON line out, rows in input order."""
        class _Model:
            def encode(self, texts):
                # A text-dependent vector: a reordered worker answer would hand a
                # ticket its neighbour's row, which nothing downstream can spot.
                return np.array([[float(len(t))] for t in texts], dtype=np.float32)

        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: _Model()))
        monkeypatch.setattr(
            "tortoise.embeddings.ensure_embed_threads", lambda: None)

        path = str(tmp_path / "worker.sock")
        with _ServingThread(path):
            client = embed_seam.WorkerBacking()
            answer = embed_seam._LineJSONClient(path).request(
                {"texts": ["a", "bbbb", "cc"]}, timeout_s=5.0)
        assert answer["vectors"] == [[1.0], [4.0], [2.0]]
        client.close()

    def test_the_worker_backing_declines_when_the_encoder_is_absent(
            self, tmp_path, monkeypatch):
        """No encoder in the worker ⇒ a NAMED error, never an empty answer.

        ⛔ An empty ``vectors`` list would surface as the far less informative
        "returned 0 row(s)" at the client; the worker must say what actually
        happened so ``last_decline`` diagnoses the deployment.
        """
        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: None))
        monkeypatch.setattr(EmbeddingModel, "absent_reason", classmethod(
            lambda cls: "not_installed"))

        path = str(tmp_path / "worker.sock")
        with _ServingThread(path):
            client = embed_seam.WorkerBacking()
            # The client would start a real process; point it at the served path
            # instead, which is exactly what a healthy start-up resolves to.
            client._proc = _FakeProc()
            client._address = path
            with pytest.raises(RuntimeError, match="unavailable in the worker"):
                client.encode(["a"])
            client._proc = None
            client._address = None
            client.close()

    def test_a_real_worker_starts_answers_and_dies_with_its_stdin(self, tmp_path):
        """The LIFE-CYCLE, end to end, with a real subprocess.

        Three properties, and each one is a failure mode rather than a nicety:
        the socket file appears (a client that polls for it must not race a
        bind), a request gets a JSON answer (the wire is real, not a mock), and
        closing our stdin terminates the process — a worker that outlives its
        parent holds 432 MB with nothing to serve.

        ⛔ THE STDIN PATH IS DRIVEN DIRECTLY, NOT THROUGH ``close()`` (review F4).
        This row used to end with ``client.close()``, which closes stdin AND THEN
        calls ``proc.terminate()`` if the child is still alive — so deleting the
        ``_watch_stdin`` thread entirely left it GREEN, because SIGTERM kills the
        worker whether or not anything is watching the pipe. The pipe is the only
        mechanism that survives a SIGKILLed or OOM-killed parent (no signal can be
        delivered from a dead process), which is exactly the 432 MB leak the
        watcher exists to prevent. So the close here is the PIPE only, and the
        child must exit on its own.
        """
        path = str(tmp_path / "spawned.sock")
        client = embed_seam.WorkerBacking()
        client.socket_path = lambda: path  # type: ignore[method-assign]
        try:
            # ⛔ The START-UP budget, not a 30 s literal: readiness now includes
            # the worker's preload (the cold import plus the model load — 156.5 s
            # measured in #13674, and a real SentenceTransformer on a box that has
            # one), so a request-sized wait here would fail on exactly the
            # deployment the worker exists for.
            address = client._ensure_started(
                timeout_s=embed_seam.resolve_worker_startup_s())
            assert os.path.exists(address)
            # ⛔ Either answer is a PASS: on a box with the embeddings extra the
            # worker returns vectors, and on one without it returns a NAMED
            # error. What must not happen is a hang or a malformed reply.
            answer = embed_seam._LineJSONClient(address).request(
                {"texts": ["probe"]}, timeout_s=120.0)
            assert "vectors" in answer or "error" in answer, answer
            proc = client._proc
            assert proc is not None and proc.stdin is not None, (
                "the worker was spawned without the stdin pipe its life-cycle "
                "depends on")
            # ⛔ ONLY the pipe. No terminate, no kill — the child must notice the
            # EOF itself, which is what ``_watch_stdin`` is for.
            proc.stdin.close()
            proc.wait(timeout=30.0)
            assert proc.poll() is not None, (
                "the worker survived its parent's stdin closing — nothing in it "
                "is watching the pipe, so a SIGKILLed parent would orphan 432 MB")
        finally:
            client.close()
        assert not os.path.exists(path), "the worker left its socket path behind"


class TestWorkerReadinessIsTheSocketExisting:
    """#14186 review F3: the socket must not exist before the encoder is loaded.

    The client treats the path's existence as "ready". With the encoder imported
    lazily inside ``_encode`` and ``bind`` first, that check reported ready for a
    process that would pay the 156.5 s cold import (#13674) inside a 30 s request
    budget — so the FIRST query after every boot declined, and the runbook told
    the operator to repair a worker that had merely just started.
    """

    def test_the_encoder_is_preloaded_before_the_socket_is_bound(
            self, tmp_path, monkeypatch):
        """``serve`` preloads the encoder BEFORE it binds — order, not intent.

        Structural on purpose: the ordering IS the contract, and it cannot be
        observed from a healthy socket (the whole point is that both states look
        identical from outside). ``bind`` records its own event, so a tree that
        binds first — or never preloads — derives a different order here.
        """
        from tortoise import embed_worker

        events: list = []

        class _StopServe(Exception):
            """Unwind ``serve`` at the first bind."""

        # The real watchers take the session down (``_watch_stdin`` calls
        # ``os._exit`` on EOF), which is why this replaces them instead.
        monkeypatch.setattr(embed_worker, "_watch_stdin", lambda *a, **k: None)
        monkeypatch.setattr(embed_worker, "_watch_parent", lambda *a, **k: None)

        def _preload() -> bool:
            events.append("preload")
            return False  # an absent encoder must still let the worker serve

        monkeypatch.setattr(embed_worker, "_preload_encoder", _preload)

        class _FakeSocket:
            def bind(self, _path: str) -> None:
                events.append("bind")
                raise _StopServe()

            def listen(self, _backlog: int) -> None:
                return None

            def settimeout(self, _timeout: float) -> None:
                return None

            def accept(self):  # pragma: no cover — unreachable via _StopServe
                raise AssertionError("accept must not be reached")

            def close(self) -> None:
                return None

        class _SocketShim:
            AF_UNIX = socket.AF_UNIX
            SOCK_STREAM = socket.SOCK_STREAM

            @staticmethod
            def socket(*_a, **_k):
                return _FakeSocket()

        # ⛔ The module's REFERENCE, not the stdlib module: patching
        # ``embed_worker.socket.socket`` would swap the global ``socket`` module's
        # constructor for every thread during this test.
        monkeypatch.setattr(embed_worker, "socket", _SocketShim())

        with pytest.raises(_StopServe):
            embed_worker.serve(str(tmp_path / "ready.sock"), parent_pid=os.getpid())

        assert events == ["preload", "bind"], (
            f"the worker did not publish readiness before accepting: {events}")

    def test_the_client_waits_for_readiness_with_the_startup_budget(
            self, tmp_path, monkeypatch):
        """``encode`` passes the START-UP budget to ``_ensure_started``.

        The request budget bounds a WARM round trip; a boot is the cold import
        plus the load bound. Passing the request budget there is the defect F3
        names, and this drives it through the client rather than reading the call
        site, so a revert reddens instead of being described.
        """
        class _Model:
            def encode(self, texts):
                return np.array([[float(len(t))] for t in texts], dtype=np.float32)

        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: _Model()))
        monkeypatch.setattr(
            "tortoise.embeddings.ensure_embed_threads", lambda: None)
        monkeypatch.setenv(embed_seam.ENV_TIMEOUT_S, "7")
        monkeypatch.setenv(embed_seam.ENV_WORKER_STARTUP_S, "123")

        path = str(tmp_path / "budget.sock")
        seen: dict = {}
        with _ServingThread(path):
            client = embed_seam.WorkerBacking()
            monkeypatch.setattr(
                client, "_ensure_started",
                lambda timeout_s: (seen.__setitem__("t", timeout_s), path)[1])
            client.encode(["a"])

        assert seen.get("t") == 123.0, (
            f"encode waited for readiness with {seen.get('t')!r}s — the request "
            "budget, not the start-up one")

    def test_the_default_startup_budget_covers_the_whole_boot(self):
        """The default start-up budget covers cold import AND the load bound.

        A budget below their sum converts a slow but HEALTHY boot into the very
        decline the lever exists to remove, so the two measured terms are pinned
        rather than left to a reviewer's memory: 156.5 s is the cold import the
        worker moves off the serving process (#13674, quoted in both module
        docstrings), and ``_LOAD_TIMEOUT_S`` is the encoder's own bound.
        """
        from tortoise.embeddings import EmbeddingModel

        assert embed_seam.DEFAULT_WORKER_STARTUP_S >= 156.5 + EmbeddingModel._LOAD_TIMEOUT_S
        assert embed_seam.DEFAULT_WORKER_STARTUP_S > embed_seam.DEFAULT_TIMEOUT_S, (
            "the start-up lever is not larger than the request lever — one number "
            "cannot bound both a boot and a warm round trip")

    def test_the_worker_seam_declines_rather_than_encoding_locally(
            self, monkeypatch, backing):
        """A worker that cannot start must NOT quietly use the local encoder.

        ⛔ The whole point of the row: ``backing`` installs a recording in-process
        replacement, and the assertion is that it was never called. If a
        fallback existed, this would pass the wrong way — the encode would
        succeed and the caller would never learn the worker was dead.
        """
        recorder = backing()
        client = embed_seam.WorkerBacking()
        monkeypatch.setenv(
            embed_seam.ENV_WORKER_SOCKET, "/nonexistent/dir/worker.sock")

        def _no_start(timeout_s):
            raise RuntimeError("embed worker exited during start-up (code 1)")

        client._ensure_started = _no_start  # type: ignore[method-assign]
        embed_seam.install_backing(client)
        try:
            with pytest.raises(RuntimeError, match="exited during start-up"):
                embed_seam.encode_query("a")
            assert recorder.batches == [], (
                "the seam silently fell back to an in-process encode after the "
                "worker failed — the migration's failure is now invisible")
            assert "start-up" in (embed_seam.last_decline() or "")
        finally:
            embed_seam.install_backing(None)


    def test_the_published_socket_path_is_connectable_the_moment_it_exists(
            self, tmp_path, monkeypatch):
        """Review F2: existence must imply LISTENING, not merely ``bind``ed.

        ⛔ THE WIDENED WINDOW IS THE PROOF. ``bind()`` creates the path one
        statement before ``listen()`` makes it connectable, so a client that won
        the readiness poll could ``connect()`` to a bound-but-not-listening socket
        and get ``ECONNREFUSED`` (``unix(7)``) — a DECLINE recorded against a
        perfectly healthy worker, in ``last_decline``, where the runbook told an
        operator to read it as a fault. The real gap is microseconds, so this row
        delays ``listen`` by 0.4 s: pre-fix the published path exists during that
        delay and the immediate connect is refused; post-fix the path the worker
        binds is a SIBLING until after ``listen``, renamed into place atomically.
        """
        from tortoise import embed_worker

        # The real watchers take the session down (``_watch_stdin`` calls
        # ``os._exit`` on EOF) — same reason as the ordering row above.
        monkeypatch.setattr(embed_worker, "_watch_stdin", lambda *a, **k: None)
        monkeypatch.setattr(embed_worker, "_watch_parent", lambda *a, **k: None)
        monkeypatch.setattr(embed_worker, "_preload_encoder", lambda: True)

        path = tmp_path / "ready.sock"
        stop = threading.Event()
        real_socket = socket.socket

        class _SlowListen:
            """A real AF_UNIX listening socket whose ``listen`` is late."""

            def __init__(self) -> None:
                self._sock = real_socket(socket.AF_UNIX, socket.SOCK_STREAM)

            def bind(self, path: str) -> None:
                self._sock.bind(path)

            def listen(self, backlog: int) -> None:
                time.sleep(0.4)
                self._sock.listen(backlog)

            def accept(self):
                self._sock.settimeout(0.05)
                try:
                    return self._sock.accept()
                except TimeoutError as exc:
                    raise OSError("stopped") from exc

            def close(self) -> None:
                self._sock.close()

        class _SocketShim:
            AF_UNIX = socket.AF_UNIX
            SOCK_STREAM = socket.SOCK_STREAM

            @staticmethod
            def socket(*_a, **_k):
                return _SlowListen()

        # The module's REFERENCE, not the stdlib module (see the row above).
        monkeypatch.setattr(embed_worker, "socket", _SocketShim())

        thread = threading.Thread(
            target=embed_worker.serve,
            kwargs={"socket_path": str(path), "parent_pid": os.getpid(),
                    "stop": stop},
            daemon=True)
        thread.start()
        try:
            # EXACTLY what the client does, polls the path and nothing else.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not os.path.exists(path):
                time.sleep(0.005)
            assert os.path.exists(path), "the worker never published its socket"
            # …and the instant it exists, a client must be able to connect.
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            try:
                client.connect(str(path))
            except ConnectionRefusedError as exc:  # pragma: no cover — the defect
                raise AssertionError(
                    "the worker published its socket path while nothing was "
                    "LISTENING on it — a client that wins the readiness poll "
                    "gets ECONNREFUSED and records a decline on a healthy "
                    f"worker (#14186 review F2): {exc}") from exc
            finally:
                client.close()
        finally:
            stop.set()
            thread.join(timeout=10.0)

    def test_a_client_retries_a_path_with_no_listener_behind_it(
            self, tmp_path, monkeypatch):
        """The client half: a path can exist with NO listener, so retry briefly.

        A predecessor killed with SIGKILL leaves its socket FILE behind (nothing
        unlinks it), and a predecessor mid-``unlink``/``bind`` leaves a window.
        Both answer ``ECONNREFUSED``, and pre-fix either one was a recorded
        decline on a healthy worker. The retry is BOUNDED
        (``_CONNECT_REFUSED_GRACE_S``, 250 ms against a 30 s request budget), so a
        genuinely dead path still declines promptly.

        Driven with a real stale socket file and a real listener that appears
        inside the grace: deterministic, no sleep-based race.
        """
        path = str(tmp_path / "stale.sock")
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(path)
        stale.close()  # the FILE remains — exactly a SIGKILLed worker's residue
        assert os.path.exists(path), "the stale socket file did not survive close"

        # ⛔ THE GRACE IS WIDENED FOR THIS ROW ONLY. The shipped grace is 250 ms
        # and the fake listener appears after a 100 ms sleep, so on a loaded box
        # overshooting 150 ms turned this row into an error rather than a signal
        # (review P3) — the load-sensitivity class this PR already hardened in
        # ``TestStopIsAStop``. The companion row below keeps the SHIPPED bound
        # under test, so widening here does not unpin it.
        monkeypatch.setattr(
            embed_seam._LineJSONClient, "_CONNECT_REFUSED_GRACE_S", 2.0)

        serving = threading.Event()
        stop = threading.Event()

        def _listen_late() -> None:
            assert serving.wait(5.0)
            time.sleep(0.1)
            if os.path.exists(path):
                os.unlink(path)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(8)
            server.settimeout(0.05)
            while not stop.is_set():
                try:
                    conn, _addr = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                conn.close()
            server.close()
            with contextlib.suppress(OSError):
                os.unlink(path)

        thread = threading.Thread(target=_listen_late, daemon=True)
        thread.start()
        client = embed_seam._LineJSONClient(path)
        serving.set()
        try:
            stream = client.open_stream(timeout_s=5.0)
            stream.close()
        finally:
            stop.set()
            thread.join(timeout=5.0)

    def test_a_dead_path_still_declines_within_the_grace(self, tmp_path):
        """The belt is BOUNDED: no listener ever → raise, do not park.

        Without this the retry could be widened into the unbounded park the
        issue's contradiction test (a) forbids.
        """
        path = str(tmp_path / "never.sock")
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(path)
        stale.close()
        started = time.monotonic()
        with pytest.raises(ConnectionRefusedError):
            embed_seam._LineJSONClient(path).open_stream(timeout_s=5.0)
        elapsed = time.monotonic() - started
        assert elapsed < 2.0, (
            f"a path with no listener behind it parked for {elapsed:.1f}s — the "
            "retry is not bounded")
        # ⛔ AND THE SHIPPED BOUND ITSELF IS PINNED HERE (review P3). The row
        # above proves the retry TERMINATES; without this line a regression that
        # widened the grace to 1.9 s would pass both rows while quietly spending
        # most of a request budget on a dead socket. The widened row above sets
        # the constant for itself only (``monkeypatch``), so this reads the
        # shipped value.
        assert embed_seam._LineJSONClient._CONNECT_REFUSED_GRACE_S == 0.25, (
            "the shipped connect-retry grace moved — it is bounded so a dead "
            "path cannot park a request: "
            f"{embed_seam._LineJSONClient._CONNECT_REFUSED_GRACE_S}")


class _FakeProc:
    """Stands in for a live ``Popen`` — ``poll() is None`` means "running"."""

    pid = 4242

    def poll(self):
        return None


class TestTheClientSaysWhenItCannotVerifyThePeer:
    """⛔ An ABSENT credential is not a pass (review P3, cycle 6).

    `_socket_peer_uid` answers ``None`` on a kernel without ``SO_PEERCRED``
    (macOS's ``LOCAL_PEERCRED`` is not implemented here), and the client's check
    was then silently a no-op — while the runbook promised the answering peer is
    always verified. `TORTOISE_EMBED_WORKER_REQUIRE_PEER` cannot help: the WORKER
    reads it. So the client must SAY which defence is left, exactly once.
    """

    def test_a_kernel_without_the_credential_is_named_in_the_log(
            self, tmp_path, monkeypatch, caplog):
        import logging as _logging

        from tortoise import embed_seam as seam

        monkeypatch.setattr(seam, "_socket_peer_uid", lambda sock: None)
        monkeypatch.setattr(seam._LineJSONClient, "_peer_note_emitted", False,
                            raising=False)
        path = str(tmp_path / "peer.sock")
        with _ServingThread(path):
            client = seam._LineJSONClient(path)
            with caplog.at_level(_logging.WARNING, logger="tortoise.embed_seam"):
                with client.open_stream(5.0):
                    pass
                # Latched: a second connect adds no line.
                with client.open_stream(5.0):
                    pass
        notes = [r for r in caplog.records
                 if "SO_PEERCRED" in r.getMessage()]
        assert len(notes) == 1, [r.getMessage() for r in notes]
        assert "UNCHECKED" in notes[0].getMessage()
        assert "0700" in notes[0].getMessage(), (
            "the note does not say what is left defending the socket: "
            f"{notes[0].getMessage()!r}")

    def test_a_FOREIGN_uid_is_still_refused(self, tmp_path, monkeypatch):
        """The check itself, so the note above cannot hide a deleted refusal."""
        from tortoise import embed_seam as seam

        monkeypatch.setattr(seam, "_socket_peer_uid", lambda sock: os.getuid() + 1)
        path = str(tmp_path / "peer2.sock")
        with _ServingThread(path):
            client = seam._LineJSONClient(path)
            with pytest.raises(RuntimeError, match="uid"):
                client.open_stream(5.0)


class TestTheWorkerSocketCannotBeClaimedByAnotherUser:
    """Review F2: readiness is a PATH, so the path must be ours alone.

    The client treats the socket's first EXISTENCE as READY, and the worker
    creates that path only after its preload — so for the whole boot the expected
    path is ABSENT and the client is polling for anyone to create it. In a shared
    temp directory any local user could bind it first, be accepted as the encoder,
    and have its vectors written as record-truth embeddings (#5004's class: the
    damage is undetectable downstream, unlike a refused read).

    Two controls, and each row pins the one it names: a ``0700`` directory the
    backing mints for its socket (nobody else can traverse it, so nobody else can
    create the path), and a kernel peer-credential check on accept (the belt for
    an operator who points the lever at a shared path).
    """

    def test_the_socket_lives_in_a_private_directory_this_uid_owns(self):
        backing = embed_seam.WorkerBacking()
        try:
            path = backing.socket_path()
            directory = os.path.dirname(path)
            assert directory != os.environ.get("TMPDIR", "/tmp"), (
                f"the worker socket is in the SHARED temp dir ({directory}) — any "
                "local user can create it during the boot and become the embedder")
            mode = os.stat(directory).st_mode
            assert mode & 0o077 == 0, (
                f"the socket directory is mode {oct(mode & 0o777)} — anyone who can "
                "traverse it can bind the path the client is waiting for")
            assert os.stat(directory).st_uid == os.getuid()
            # Stable: the pre-load poll, the spawn argument and ``describe()`` must
            # all name the SAME path, or the client would watch a path the worker
            # never creates.
            assert backing.socket_path() == path
        finally:
            backing.close()
        assert not os.path.exists(directory), (
            "the private socket directory outlived the backing")

    def test_an_explicit_lever_path_is_honoured_verbatim(self, monkeypatch):
        """An operator pointing at a shared path still gets that path (the belt)."""
        monkeypatch.setenv(
            embed_seam.ENV_WORKER_SOCKET, "/var/run/tortoise/embed.sock")
        assert embed_seam.WorkerBacking().socket_path() == \
            "/var/run/tortoise/embed.sock"

    def test_a_peer_on_our_own_uid_is_served_and_a_foreign_uid_is_refused(
            self):
        """The kernel's own credential decides, and a refusal is NAMED.

        Directed through a real ``AF_UNIX`` socket pair, so the accepted
        connection has a genuine peer uid — the assertion is about
        ``_peer_allowed``'s decision, not a stub of it. ``SO_PEERCRED`` is what a
        foreign process cannot spoof; filesystem permissions are the primary
        control and this is what holds when the lever points somewhere shared.
        """
        from tortoise import embed_worker

        ours, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            if embed_worker._peer_uid(ours) is None:  # pragma: no cover
                pytest.skip("this kernel offers no SO_PEERCRED")
            assert embed_worker._peer_allowed(ours) is True

            import tortoise.embed_worker as worker_module
            real = worker_module._peer_uid
            worker_module._peer_uid = lambda _conn: os.getuid() + 1
            try:
                assert embed_worker._peer_allowed(ours) is False, (
                    "a foreign uid was accepted as the embedder — its vectors "
                    "would be written as record-truth")
            finally:
                worker_module._peer_uid = real
        finally:
            ours.close()
            peer.close()

    def test_an_unreadable_peer_credential_is_reported_not_silent(
            self, caplog, monkeypatch):
        """A kernel without a peer credential says so instead of pretending.

        Fail-safe in the honest direction: the private directory is still in force
        on such a platform, and refusing every request would turn a hardening
        detail into an outage — but the operator is told the check is not running.
        """
        from tortoise import embed_worker

        ours, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        monkeypatch.setattr(embed_worker, "_peer_uid", lambda _conn: None)
        # The note is emitted ONCE per process, so reset it or this row would
        # pass on the first test to reach the branch and silently skip for every
        # later one.
        monkeypatch.setattr(embed_worker, "_peer_credential_note_emitted", False)
        try:
            with caplog.at_level("WARNING"):
                assert embed_worker._peer_allowed(ours) is True
        finally:
            ours.close()
            peer.close()
        assert any("SO_PEERCRED" in record.message for record in caplog.records), (
            "the unchecked peer identity was silently allowed")


# ── the wiring ──────────────────────────────────────────────────────────────


class TestTheCallersRouteThroughTheSeam:
    def test_compute_embeddings_uses_the_seam_and_keeps_its_contract(
            self, monkeypatch):
        """The store funnel still returns one row per text, THROUGH the seam.

        ``compute_embeddings`` is a widely-REPLACED seam (the longmem eval
        doubles swap the function itself), so its own signature and return shape
        are pinned by other rows; this one pins that the ENCODE inside it is the
        seam's, which is what makes ``TORTOISE_EMBED_BACKING`` reach the write
        path at all.

        ⛔ THE ROUTING IS ASSERTED, NOT INFERRED FROM THE VECTORS (review F9).
        This row used to double ``EmbeddingModel.get`` and check the rows came
        back — but the PRE-fix function does exactly ``get()`` →
        ``ensure_embed_threads()`` → ``model.encode(...)`` →
        ``[vec.tolist() ...]`` and returns the same values, so both assertions
        passed verbatim on the code this change replaces. A backing injected
        through ``install_backing`` is only ever called BY the seam, so its call
        log is the routing evidence — and the local model is forced absent, so a
        funnel that still reached for it would have to fail rather than answer.
        """
        from tortoise import embeddings

        class _RecordingModel:
            name = "recording"

            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def encode(self, texts, *, timeout_s=None):
                self.calls.append(list(texts))
                return [[float(len(t)), 1.0] for t in texts]

            def close(self) -> None:
                return None

            def describe(self) -> dict:
                return {"backing": self.name}

        recorder = _RecordingModel()
        embed_seam.install_backing(recorder)
        # A REMOTE backing is where the routing matters (the in-process path is
        # deliberately unchanged): it is also the only way past
        # ``compute_embeddings``' local-model pre-check, so a funnel that still
        # asked the local model would answer ``[None, None]`` here instead of
        # quietly encoding.
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        monkeypatch.setattr(embeddings.EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: None))
        try:
            rows = embeddings.compute_embeddings(["ab", "cdef"])
        finally:
            embed_seam.install_backing(None)

        assert rows == [[2.0, 1.0], [4.0, 1.0]], rows
        # ⛔ The routing evidence. A batch funnel calls ``encode_texts``, the
        # DIRECT entry point (no coalescing — the batch is already whole), so the
        # proof is the injected backing's own call log, not a collector counter.
        assert recorder.calls == [["ab", "cdef"]], (
            "the store funnel did not encode through the seam — its batch never "
            f"reached an injected backing ({recorder.calls})")

    def test_an_armed_process_refuses_on_the_write_path_when_the_seam_declines(
            self, monkeypatch):
        """#5275 binds on the WRITE path too (review F2).

        ⛔ Before the seam, ``EmbeddingModel.get()`` was this path's only exit
        and it RAISES in an armed process (via ``_unavailable``). A worker or
        external backing never calls ``get()``, so an armed process whose worker
        is dead stored NO vector and named NO refusal — the silent #2985 shape
        this PR closes on the read path, left open here.
        """
        from tortoise import embeddings
        from tortoise.exceptions import EmbedderUnavailableError

        monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        exploding = _ExplodingBacking()
        embed_seam.install_backing(exploding)
        try:
            with pytest.raises(EmbedderUnavailableError):
                embeddings.compute_embeddings(["a", "b"])
            # ⛔ ROUTING EVIDENCE (review F4 at this head): the refusal must come
            # from the SEAM's decline, not from ``EmbeddingModel.get()`` returning
            # None. On a model-less box (``uv sync`` without the embeddings extra —
            # a normal state, per AGENTS.md) the pre-fix body refuses by itself, so
            # without this count the row passes on the code it claims to replace.
            assert exploding.calls == 1, (
                "the write path never asked the seam before refusing — it is "
                f"routing around it ({exploding.calls} call(s))")
        finally:
            embed_seam.install_backing(None)

    def test_an_unarmed_process_still_degrades_the_write_path_silently(
            self, monkeypatch):
        """The same dead backing is a SILENT ``[None] * n`` unless the flag is armed.

        This is the control for the row above: it is what stops that row from
        passing on a tree that raises unconditionally, which would turn every
        embedder blip into a write failure.
        """
        from tortoise import embeddings

        monkeypatch.delenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", raising=False)
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        exploding = _ExplodingBacking()
        embed_seam.install_backing(exploding)
        try:
            assert embeddings.compute_embeddings(["a", "b"]) == [None, None]
            # Control for the SAME reason as the armed row: the degrade must be the
            # seam's decline, not a local model that was simply absent (review F4).
            assert exploding.calls == 1, (
                "the silent degrade did not come through the seam — it came from "
                f"the local model being absent ({exploding.calls} call(s))")
        finally:
            embed_seam.install_backing(None)

    def test_compute_embeddings_does_not_require_a_local_model_when_remote(
            self, monkeypatch, http_endpoint):
        """A remote backing must not be gated on the LOCAL model being present.

        ⛔ This is the clause that makes the migration possible at all: if
        ``compute_embeddings`` still demanded ``EmbeddingModel.get()``, an
        external backing would decline on every write and the embedder would
        stay in the process no matter what the lever said.
        """
        from tortoise import embeddings

        server = http_endpoint({"vectors": [[1.0, 2.0], [3.0, 4.0]]})
        monkeypatch.setenv(
            embed_seam.ENV_EXTERNAL_URL,
            f"http://127.0.0.1:{server.server_address[1]}/embed")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        monkeypatch.setattr(embeddings.EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: None))

        assert embeddings.compute_embeddings(["a", "b"]) == [[1.0, 2.0], [3.0, 4.0]]

    def test_the_read_path_falls_back_to_encode_failed_on_a_backing_raise(
            self, monkeypatch, tmp_path):
        """A RAISING backing reaches the read as ``encode_failed``, not an error.

        The read path already owns this vocabulary, and a keystroke-level change
        to it would turn a degraded read into a 500 — the ``#2952`` contract is
        that a read that cannot run its dense leg DECLARES itself.

        ⛔ THE ROW DRIVES THE RAISE, and it pins TWO things, because one was not
        enough. It used to assert on ``inspect.getsource(...)`` text, which stayed
        GREEN on the mutant that disables the whole remote branch
        (``_seam_remote = False``) — the literals it looked for were still in the
        source (review F4b). Now:

        * the backing genuinely RAISES, and ``last_decline`` proves it did; and
        * the backing was actually CALLED (``calls == 1``), which is the routing
          the old text assertion was trying to describe. Under that same mutant
          the seam is never asked, so ``calls`` stays 0 and this reddens.

        ``EmbeddingModel.get`` is forced absent so the LOCAL fallback branch is
        resolved the same way on every box: otherwise a developer machine with a
        real encoder would run that branch to success and the row would mean
        different things in different environments (measured: it passed under the
        mutant on a box where the model loads).
        """
        monkeypatch.delenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", raising=False)
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)

        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: None))
        exploding = _ExplodingBacking()
        embed_seam.install_backing(exploding)

        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(db_path=str(tmp_path / "read14186_raise.db"))
        trace: list = []
        try:
            sdk._tortoise_fts_query("retention window", limit=3, leg_trace=trace)
        finally:
            sdk.close()
            embed_seam.install_backing(None)

        assert exploding.calls == 1, (
            "the read path never asked the seam to encode its query — it is "
            f"routing around it ({exploding.calls} call(s))")
        assert "encoder went away" in (embed_seam.last_decline() or ""), (
            "the backing never raised, so this row says nothing about a raise "
            f"(last_decline={embed_seam.last_decline()!r})")
        vector_entries = [e for e in trace if e.get("leg") == "vector"]
        assert vector_entries, f"the read recorded no vector entry: {trace!r}"
        entry = vector_entries[0]
        assert entry.get("ran") is False, entry
        assert entry.get("reason") == "encode_failed", (
            f"a raising backing did not reach the read as encode_failed: {entry!r}")


# ── the swap path must not deadlock ─────────────────────────────────────────


class TestReplacingTheBackingNeverStallsUnderTheModuleLock:
    """The module lock is never held across a collector JOIN.

    ⛔ WHY THIS ROW EXISTS, and what the failure actually was (review F6).
    Stopping the collector joins its thread, and that thread calls
    ``active_backing()`` — which takes the module lock — on every batch. A caller
    that held the lock across the join would wait for a thread waiting for the
    lock it holds. That is a BOUNDED stalemate of every ``encode_query`` for the
    join's duration (``_Collector.stop`` joins with an explicit 5 s bound), not a
    deadlock — commit ``0eb23c2f``'s subject called it one and overstated it. The
    fix is unchanged and still necessary; only the consequence is stated as what
    it was. Reached by nothing more exotic than a concurrent submit while the
    lever (or a test) swaps the backing.

    A hang is a terrible thing to assert on directly, so this pins the INVARIANT
    the fix is made of — observable, deterministic, and false on the pre-fix
    shape: every ``stop()`` is recorded with whether the module lock was held.
    """

    def test_the_collector_is_never_stopped_while_the_module_lock_is_held(
            self, backing):
        recorder = backing()
        assert embed_seam.encode_query("warm") is not None
        collector = embed_seam._collector
        assert collector is not None, "no collector was started, so this proves nothing"

        observed: list[bool] = []
        real_stop = collector.stop

        def _stop() -> None:
            observed.append(embed_seam._lock.locked())
            real_stop()

        collector.stop = _stop  # type: ignore[method-assign]
        try:
            embed_seam.install_backing(None)
        finally:
            embed_seam.reset_seam()

        assert observed, "the collector was never stopped — nothing was measured"
        assert not any(observed), (
            "the collector was stopped while the module lock was HELD. Its thread "
            "takes that lock on every batch, so a concurrent submit would have "
            "stalled every encode_query for the whole join (a bounded stall, not "
            "a deadlock — but still a stall the seam must not have)."
        )
        assert recorder.batches, "the recording backing never ran"


# ── the #5275 contract survives the move off the local model ────────────────


class TestTheRequiredEmbedderFlagBindsOnARemoteBacking:
    """``TORTOISE_EMBEDDING_MODEL_REQUIRED`` must still refuse on a remote backing.

    ⛔ WHY THIS IS A REAL GAP AND NOT A NICETY. #5275's contract is that a
    process which ARMED the flag must never be handed a keyword-only read. The
    only place the flag used to decide this is ``EmbeddingModel.get()``'s refusal
    path — which the remote branch never reaches, because there is no local model
    to acquire. Left unhandled, arming the flag next to a dead worker would
    silently produce the designed-absence degrade with no error and no signal:
    exactly the #2985 shape #5275 was opened for, in a new place.

    These rows drive the ONE read path (``_tortoise_fts_query``) rather than a
    helper, because the obligation lives in that branch of that function.
    """

    @staticmethod
    def _sdk(tmp_path):
        from tortoise.sdk import TortoiseSDK

        return TortoiseSDK(db_path=str(tmp_path / "read14186.db"))

    @staticmethod
    def _dead_backing(monkeypatch):
        # A backing that is configured but cannot answer: the decline path.
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        monkeypatch.delenv(embed_seam.ENV_EXTERNAL_URL, raising=False)

    def test_an_armed_process_refuses_instead_of_reading_keyword_only(
            self, monkeypatch, tmp_path):
        from tortoise.exceptions import EmbedderUnavailableError

        monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
        self._dead_backing(monkeypatch)
        sdk = self._sdk(tmp_path)
        try:
            with pytest.raises(EmbedderUnavailableError):
                sdk._tortoise_fts_query("retention window", limit=3)
        finally:
            sdk.close()

    def test_an_unarmed_process_declares_the_degrade_with_the_existing_reason(
            self, monkeypatch, tmp_path):
        """Unarmed ⇒ the read still answers, and its trace names the failure.

        The vocabulary is the one the local encode arm already uses
        (``encode_failed``) — a new synonym would split one operator-facing
        population into two for the same event.
        """
        monkeypatch.delenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", raising=False)
        self._dead_backing(monkeypatch)
        sdk = self._sdk(tmp_path)
        trace: list = []
        try:
            sdk._tortoise_fts_query("retention window", limit=3, leg_trace=trace)
        finally:
            sdk.close()
        vector_entries = [e for e in trace if e.get("leg") == "vector"]
        assert vector_entries, f"the read recorded no vector entry: {trace!r}"
        entry = vector_entries[0]
        assert entry.get("ran") is False, entry
        assert entry.get("reason") == "encode_failed", (
            f"the remote decline did not use the local encode arm's vocabulary: "
            f"{entry!r}")


class TestStopIsAStop:
    """Review F5: a stopped collector fires nothing and closes nothing early.

    ``stop()`` set a flag and joined for a bounded time, but ``_collect`` only
    exited on that flag when the queue was EMPTY — and a ticket had already been
    popped into the batch. So after ``stop()`` returned, ``_run`` still fired an
    encode: it recorded a decline that teardown itself caused, and after
    ``reset_seam`` had cleared the globals it constructed a FRESH backing through
    ``active_backing`` (for ``worker``, a 432 MB process nothing would close).
    The rows below drive exactly that state — an encode IN FLIGHT, more tickets
    queued behind it, then teardown.
    """

    @staticmethod
    def _start_in_flight(recorder) -> tuple[list, list]:
        """One encode running, plus two tickets queued behind it.

        Returns ``(threads, outcome)``, where ``outcome`` is filled with
        ``(text, value_or_exception)`` as each caller returns.
        """
        outcome: list = []

        def _one(text: str) -> None:
            try:
                outcome.append((text, embed_seam.encode_query(text)))
            except BaseException as exc:  # recorded, asserted by the caller
                outcome.append((text, exc))

        first = threading.Thread(target=_one, args=("first",), daemon=True)
        first.start()
        assert recorder.entered.wait(10), "the collector never started an encode"
        queued = [threading.Thread(target=_one, args=(f"q{i}",), daemon=True)
                  for i in range(2)]
        for thread in queued:
            thread.start()
        # ⛔ Wait for them to be IN the queue, not merely started: `_collect`
        # drains under the lock, so a ticket that has not been appended yet would
        # make this row prove nothing.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if len(embed_seam._collector._queue) == 2:
                break
            time.sleep(0.01)
        assert len(embed_seam._collector._queue) == 2, (
            "the two later tickets never reached the queue")
        return [*queued, first], outcome

    def test_a_stopped_collector_fires_no_further_batch(self, backing):
        recorder = backing(delay_s=1.0)
        try:
            threads, outcome = self._start_in_flight(recorder)
            collector = embed_seam._collector
            assert collector is not None
            # A join budget far below the encode's own duration: this is the
            # state a real teardown meets (an encode is 2-4 s; worker boots are
            # longer), and it is the state the pre-fix path fired from.
            collector.stop(timeout_s=0.05)
            for thread in threads:
                thread.join(timeout=5)
            still_waiting = [t for t in threads if t.is_alive()]
            assert not still_waiting, (
                f"{len(still_waiting)} caller(s) were still parked after stop() "
                "returned — a stop must DECLINE what it will not encode, not "
                "leave the caller waiting out its whole bound")
            answers = dict(outcome)
            assert set(answers) == {"first", "q0", "q1"}, answers
            # ⛔ The in-flight ticket (fired BEFORE stop) is answered normally.
            assert not isinstance(answers["first"], BaseException), answers["first"]
            # ⛔ The queued tickets were DECLINED, not encoded: they name the stop.
            for text in ("q0", "q1"):
                value = answers[text]
                assert isinstance(value, RuntimeError), (
                    f"a ticket queued when stop() was called was answered "
                    f"({value!r}) — the stop was not a stop")
                assert "stopped" in str(value)
            # ⛔ And NOTHING fired after stop() returned: one encode, not three.
            time.sleep(1.2)
            assert len(recorder.batches) == 1, (
                f"{len(recorder.batches)} encodes fired after the collector was "
                "stopped — teardown is doing work the deployment already tore "
                f"down ({recorder.batches})")
        finally:
            embed_seam.reset_seam()

    def test_a_backing_in_use_is_not_closed_until_its_encode_finishes(
            self, backing, monkeypatch):
        """The retired backing is closed AFTER the in-flight encode, not under it.

        ⛔ The pre-fix teardown closed the backing as soon as ``stop()`` returned
        — while the collector could still be inside ``encode``. That turns a
        normal retirement into an encoder failure, recorded as a decline the
        DEPLOYMENT did not have, and it can kill a forward pass mid-flight.

        Driven through :func:`_retire` rather than a lever change: that function
        IS the retirement path (``active_backing`` and ``reset_seam`` both hand it
        a ``_Retired(backing, collector)``), and an INJECTED backing is never
        closed by either, so going through them would test the fixture instead of
        the sequence under review.
        """
        recorder = backing(delay_s=1.0)
        # Shrink the join so an in-flight encode is guaranteed to outlast it.
        monkeypatch.setattr(embed_seam, "_STOP_JOIN_S", 0.05)
        # ⛔ AND SHRINK THE RETIRE BOUND TOO. ``_RETIRE_JOIN_S`` is a MODULE
        # CONSTANT computed at import (``_STOP_JOIN_S + DEFAULT_TIMEOUT_S +
        # 30``), so patching ``_STOP_JOIN_S`` alone left it at 65 s while this row
        # waited only 10 s — the close legally had 65 s, the assertion allowed 10.
        # That is the load-sensitivity a review measured as a flake (one red on
        # `_close_when_idle` under a busy box, then six green repeats). Pinning it
        # explicitly makes the code's bound and the row's wait SELF-CONSISTENT: the
        # encode is 1 s, the retire wait 3 s, the row's wait 10 s.
        monkeypatch.setattr(embed_seam, "_RETIRE_JOIN_S", 3.0)
        try:
            threads, _outcome = self._start_in_flight(recorder)
            collector = embed_seam._collector
            assert collector is not None
            embed_seam._retire(embed_seam._Retired(recorder, collector))
            assert recorder.closed is False, (
                "the backing was CLOSED while an encode was still running on it "
                "— that encode fails and the teardown's own decline is recorded "
                "as a deployment fault")
            for thread in threads:
                thread.join(timeout=10)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not recorder.closed:
                time.sleep(0.02)
            assert recorder.closed, (
                "the backing was never closed after its encode finished — a "
                "retired worker would leak its 432 MB process")
        finally:
            embed_seam.reset_seam()

    def test_the_retire_wait_is_bounded_and_an_overrun_is_named(
            self, backing, monkeypatch, caplog):
        """Review: ``_close_when_idle`` joined UNBOUNDED inside a daemon thread.

        A forward pass that never returns kept the retire thread parked for the
        life of the process and the retired worker's 432 MB held forever — and
        silently, because the only code that would have reported it is the code
        that never ran. The wait is now bounded, and overrunning it is a WARNING
        rather than silence.
        """
        recorder = backing(delay_s=2.0)
        # Both bounds shrunk: stop() must return while the encode is still in
        # flight (or it would join it out and never reach _close_when_idle), and
        # the retire wait must expire before the encode does.
        monkeypatch.setattr(embed_seam, "_STOP_JOIN_S", 0.05)
        monkeypatch.setattr(embed_seam, "_RETIRE_JOIN_S", 0.08)
        try:
            threads, _outcome = self._start_in_flight(recorder)
            collector = embed_seam._collector
            assert collector is not None
            with caplog.at_level("WARNING"):
                embed_seam._retire(embed_seam._Retired(recorder, collector))
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not recorder.closed:
                    time.sleep(0.01)
            assert recorder.closed, (
                "the retired backing was never closed — an overrunning encode "
                "held its 432 MB for the life of the process")
            assert any("STILL inside an encode" in record.message
                       for record in caplog.records), (
                "the overrun was silent — nothing names a collector that never "
                "returned from its forward pass")
            for thread in threads:
                thread.join(timeout=10)
        finally:
            embed_seam.reset_seam()


def test_every_seam_lever_is_cleared_between_tests():
    """Review F8: the fixture's lever census cannot miss one again.

    ``_isolated_embed_seam`` clears ``SEAM_ENV_LEVERS``, and that list used to be
    spelled by hand — it had missed ``ENV_WORKER_STARTUP_S``, so a shell exporting
    it made the real-worker row depend on the ambient environment (a false pass in
    one direction, a spurious red in the other). This pins the census against the
    module's own ``ENV_*`` constants, so a new lever is either listed or this row
    reddens.
    """
    declared = {
        name: value for name, value in vars(embed_seam).items()
        if name.startswith("ENV_") and isinstance(value, str)
    }
    assert declared, "no ENV_* constants found — this row would pass vacuously"
    missing = set(declared.values()) - set(embed_seam.SEAM_ENV_LEVERS)
    assert not missing, (
        f"{sorted(missing)} are read by the seam but not cleared between tests — "
        "a value exported in a developer or CI shell leaks into unrelated rows")
    assert embed_seam.ENV_WORKER_STARTUP_S in embed_seam.SEAM_ENV_LEVERS


# ── teardown ────────────────────────────────────────────────────────────────


def test_reset_seam_leaves_nothing_running(backing):
    """``reset_seam`` stops the collector thread — no thread leaks between tests."""
    backing()
    embed_seam.encode_query("a")
    before = {t.name for t in threading.enumerate()}
    assert "embed-seam-collector" in before, (
        "the collector thread was never started, so this row proves nothing")
    embed_seam.reset_seam()
    time.sleep(0.2)
    after = {t.name for t in threading.enumerate()}
    assert "embed-seam-collector" not in after, (
        "reset_seam left the collector thread running")


# ── the configured seam, not an injected one ────────────────────────────────


class _BootingBacking(_RecordingBacking):
    """An encode that first spends time reaching READY, like a cold worker.

    ``boot_s`` is the cold import plus the model load (#13674) and is reported
    through :meth:`last_startup_ms`; ``encode_s`` is the encode proper.
    """

    name = "booting"

    def __init__(self, *, boot_s: float, encode_s: float) -> None:
        super().__init__()
        self.boot_s = boot_s
        self.encode_s = encode_s
        self._last_boot_ms = 0.0

    def encode(self, texts, *, timeout_s=None):
        started = time.monotonic()
        time.sleep(self.boot_s)
        self._last_boot_ms = (time.monotonic() - started) * 1000.0
        time.sleep(self.encode_s)
        with self._lock:
            self.batches.append(list(texts))
        return [[float(len(t))] + [0.0] * (self.dim - 1) for t in texts]

    def last_startup_ms(self) -> float:
        return self._last_boot_ms


class TestTheConfiguredSeamIsWhatTheRowsDrive:
    """Review F3: the first batch's counters must not die with the collector.

    ``_collector_for()`` used to be taken BEFORE any backing was resolved, so the
    collector's very first ``_fire`` called ``active_backing()`` — whose first
    resolution RETIRES the collector it is running on. The first batch's
    ``collect_ms``/``wait_ms``/``declines`` landed on an object the module no
    longer pointed at, so ``/health`` published ``submissions: 0`` immediately
    after a cold start had served its first batch. Every counter row in this file
    installs a backing, and ``install_backing`` short-circuits
    ``active_backing`` — which is exactly why none of them could see it.
    """

    def test_the_first_coalesced_batch_publishes_its_counters(self, monkeypatch):
        recorder = _RecordingBacking()
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "200")
        monkeypatch.setattr(embed_seam, "_construct", lambda _name: recorder)

        results: list = []
        errors: list = []
        _join(_pump(2, threading.Barrier(2), results, errors))

        assert not errors, errors
        assert len(recorder.batches) == 1, (
            f"the two concurrent queries did not share one batch: {recorder.batches}")
        latency = embed_seam.seam_status()["latency"]
        assert latency["batches"] >= 1, (
            "the first batch's counters were discarded — /health publishes "
            "'no submissions ever' right after a cold start served them")
        assert latency["submissions"] == 2, (
            f"submissions read {latency['submissions']} after two encode_query "
            "calls — the collector that served them was retired by its own "
            "_fire resolving the backing")

    def test_a_backing_that_ran_before_the_switch_is_marked_stale_not_null(
            self, monkeypatch):
        """Review F7: ``backings[*].latency`` must answer what its docstring says.

        A backing that ran earlier in this process keeps its last distribution —
        flagged ``latency_stale`` — instead of ``null``. Without it the map could
        never answer "would the other one be better", which is the only reason it
        lists all three.
        """
        first = _RecordingBacking()
        second = _RecordingBacking()
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setattr(
            embed_seam, "_construct",
            lambda name: second if name == embed_seam.BACKING_EXTERNAL else first)

        embed_seam.encode_query("a")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        embed_seam.encode_query("b")

        backings = embed_seam.seam_status()["backings"]
        assert backings["external"]["latency"] is not None
        assert backings["external"]["latency"]["submissions"] >= 1
        stale = backings["inprocess"]["latency"]
        assert stale is not None, (
            "a backing that served traffic before the switch reports null — the "
            "per-backing table cannot answer 'would the other one be better'")
        assert stale["submissions"] >= 1
        assert backings["inprocess"]["latency_stale"] is True

    def test_a_lever_flip_before_the_next_encode_is_marked_stale(
            self, monkeypatch):
        """Review F3: ``backing`` is CONFIGURED; the counters are the LIVE one's.

        Between a lever flip and the next encode — the documented rollout path,
        where the runbook tells the operator to verify with
        ``jq .embed_seam.backing`` — the configured name has already moved while
        ``_backing``/``_collector`` still belong to the OLD backing. Pre-fix
        ``/health`` published ``backing: "external"`` with the in-process
        collector's counters as ``backings.external.latency``, unflagged: the
        PRE-switch distribution was presented as the new backing's, fresh, at the
        exact moment an operator was reading it to verify the switch.
        """
        first = _RecordingBacking()
        second = _RecordingBacking()
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        monkeypatch.setattr(
            embed_seam, "_construct",
            lambda name: second if name == embed_seam.BACKING_EXTERNAL else first)
        embed_seam.encode_query("a")  # the in-process backing served one batch

        # ⛔ THE LEVER MOVES AND NOTHING ENCODES AFTER IT — the window itself.
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        status = embed_seam.seam_status()

        assert status["backing"] == embed_seam.BACKING_EXTERNAL, (
            "`backing` must stay the CONFIGURED choice — the documented read")
        assert status["latency_stale"] is True, (
            "the pre-switch distribution was published as the configured "
            "backing's, unflagged — an operator verifying a rollout reads it "
            "as post-switch")
        # ``backing_state.backing`` names what the state actually describes, so
        # the two disagreeing IS the signal, and it is on the wire.
        assert status["backing_state"]["backing"] != status["backing"], (
            status["backing_state"])
        backings = status["backings"]
        assert backings[embed_seam.BACKING_EXTERNAL]["latency"] is None, (
            "the configured backing has never run, so its distribution must be "
            "null — the old collector's counters were filed under it as fresh")
        assert backings[embed_seam.BACKING_INPROCESS]["latency"]["submissions"] \
            >= 1, "the live collector's observations were dropped, not relocated"
        assert backings[embed_seam.BACKING_INPROCESS]["latency_stale"] is True


class TestTheTicketBoundIsSizedForTheNextPhase:
    def test_a_lever_flipped_to_worker_sizes_the_bound_for_the_boot(
            self, monkeypatch):
        """Review: the phase sizing asked the OLD backing.

        ``docs/infra-runbook.md`` documents retargeting without a restart, so a
        lever that now says ``worker`` while the live object is not one is a
        SUPPORTED path — and the next encode pays the whole boot. Reading only the
        candidate answered "no boot in flight" about a backing that is no longer
        the configured one, so the ticket was bounded by the request budget and a
        healthy boot declined.
        """
        # ⛔ A REAL retarget, not the injected hatch (review P3, cycle 5): while an
        # injected backing is installed `active_backing` returns IT and never
        # consults the lever, so injected + a lever of `worker` is not the next
        # encode's boot — the arm below pins that separately. This one drives the
        # path the runbook documents: the live backing IS the resolved one.
        live = _RecordingBacking()  # name = "recording"
        monkeypatch.setattr(embed_seam, "_backing", live)
        monkeypatch.setattr(embed_seam, "_backing_name", live.name)
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setenv(embed_seam.ENV_WORKER_STARTUP_S, "123")
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "0")
        try:
            bound = embed_seam.resolve_ticket_wait_s(1.0)
            assert bound == pytest.approx(0.0 + 123.0 + 1.0), (
                f"a lever flipped to worker sized the ticket bound at {bound}s — "
                "the request budget, not the boot budget, so the first query "
                "declines during a healthy boot")
        finally:
            monkeypatch.setattr(embed_seam, "_backing", None)
            monkeypatch.setattr(embed_seam, "_backing_name", None)

    def test_an_INJECTED_backing_is_answered_as_itself(self, monkeypatch):
        """⛔ And the mirror: with one installed, the LEVER is not the question.

        `active_backing` returns `_injected` before it consults the lever, so the
        next encode uses the injected object whatever the environment says. Sizing
        its ticket by the configured `worker` boot (a) answered about a backing
        that will never answer this ticket, and (b) in the failing direction: the
        valve then abandoned a healthy ticket after ~1.4 s of a real boot
        (`test_a_ticket_waits_out_a_worker_boot_instead_of_declining`).
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setenv(embed_seam.ENV_WORKER_STARTUP_S, "123")
        monkeypatch.setenv(embed_seam.ENV_MAX_WAIT_MS, "0")
        embed_seam.install_backing(_RecordingBacking())  # no startup_pending
        try:
            assert embed_seam.backing_booting() is False, (
                "an injected non-worker backing was sized as a worker boot — the "
                "lever answered for an object it does not govern")
        finally:
            embed_seam.install_backing(None)


class TestEncodeMsMeasuresTheEncodeNotTheBoot:
    def test_a_worker_boot_is_not_charged_to_encode_ms(self, monkeypatch):
        """Review F4: ``encode_ms`` is the number the switch decision rests on.

        The clock in ``_fire`` starts before the backing is resolved, and a
        worker's encode begins with ``_ensure_started(600 s)`` — so a cold first
        sample was the 156.5 s import plus the model load, published as
        ``encode_ms`` and described in the runbook as "directly comparable across
        backings". With nearest-rank percentiles the first reading after a switch
        IS the p95 (``n < 20``), so this is the sample operators actually see.
        """
        booty = _BootingBacking(boot_s=0.5, encode_s=0.03)
        embed_seam.install_backing(booty)

        assert embed_seam.encode_query("a") is not None
        encode = embed_seam.seam_status()["latency"]["encode_ms"]
        assert encode["max"] is not None
        assert encode["max"] < 250.0, (
            f"encode_ms.max is {encode['max']} ms for a 30 ms encode — the "
            "worker's boot was charged to the encode, making it incomparable "
            "across backings")
        assert encode["max"] >= 10.0, (
            "encode_ms collapsed to ~0 — the boot was subtracted but the encode "
            "was not measured")


class TestASubmitToAStoppedCollectorIsRefused:
    def test_a_stopped_collector_never_restarts(self, backing):
        """Review: ``stop()``'s "nothing fires afterwards" was false for a late submit.

        ``_ensure_thread_unlocked`` cleared ``_stop`` and started a fresh thread on
        the SAME collector, so a submit on a collector captured before
        ``reset_seam`` ran a batch through a backing constructed after teardown —
        one nothing would ever close.
        """
        recorder = backing()
        assert embed_seam.encode_query("a") is not None
        collector = embed_seam._collector
        assert collector is not None
        embed_seam.reset_seam()
        fired_before = len(recorder.batches)

        with pytest.raises(RuntimeError) as excinfo:
            collector.submit("late", timeout_s=1.0)
        assert "cannot be reused" in str(excinfo.value), excinfo.value
        time.sleep(0.3)
        assert len(recorder.batches) == fired_before, (
            "a submit to a stopped collector ENCODED — the stop was not terminal")
        assert not collector.alive()


# ── the worker's reap arms ──────────────────────────────────────────────────


class _ReapSpyProc:
    """A ``Popen`` double that records every kill/wait the seam performs.

    ``exit_code`` is what ``poll()`` reports (``None`` ⇒ still running);
    ``creates`` is a socket path the child "binds", created on the first poll
    (the readiness contract). ``wait_raises_first`` makes the FIRST ``wait``
    raise ``TimeoutExpired``, which is the arm ``close()`` exists for.
    """

    pid = 4243

    def __init__(self, *, exit_code=None, creates=None,
                 wait_raises_first=False) -> None:
        self._exit_code = exit_code
        self.returncode = exit_code
        self._creates = creates
        self._wait_raises_first = wait_raises_first
        self.kills = 0
        self.terminates = 0
        self.waits = 0
        self.stdin = None

    def poll(self):
        if self._creates is not None:
            with contextlib.suppress(OSError):
                open(self._creates, "w").close()
            self._creates = None
        return self._exit_code

    def kill(self) -> None:
        self.kills += 1
        self._exit_code = -9
        self.returncode = -9

    def terminate(self) -> None:
        self.terminates += 1

    def wait(self, timeout=None):
        self.waits += 1
        if self._wait_raises_first and self.waits == 1:
            raise subprocess.TimeoutExpired("embed_worker", timeout or 0)
        return self.returncode


class TestTheWorkerReapArmsArePinned:
    """Review F5: ``rg -c TimeoutExpired`` over this file returned 0.

    Three arms reap a child — the failed-startup wait, the start-up timeout's
    kill+wait, and ``close()``'s terminate→kill→wait — and none of them executed
    in any row: the start-up rows replace ``_ensure_started`` wholesale, and the
    real-subprocess row's worker dies on SIGTERM without ever reaching the
    timeout. Deleting any of the three left the suite green, so a zombie leak (or
    a 432 MB process surviving teardown) had no detector at all.
    """

    def test_a_worker_that_exits_during_startup_is_reaped_before_the_raise(
            self, monkeypatch):
        proc = _ReapSpyProc(exit_code=1)
        monkeypatch.setattr(embed_seam.subprocess, "Popen",
                            lambda *a, **k: proc)
        worker = embed_seam.WorkerBacking()
        try:
            with pytest.raises(RuntimeError) as excinfo:
                worker._ensure_started(5.0)
            assert "exited during start-up" in str(excinfo.value)
            assert proc.waits >= 1, (
                "an exited child was never waited for — it stays a zombie, one "
                "per failed start-up attempt")
        finally:
            worker.close()

    def test_a_startup_timeout_kills_and_reaps_the_child(self, monkeypatch):
        proc = _ReapSpyProc()
        monkeypatch.setattr(embed_seam.subprocess, "Popen",
                            lambda *a, **k: proc)
        worker = embed_seam.WorkerBacking()
        try:
            with pytest.raises(TimeoutError):
                worker._ensure_started(1.0)
            assert proc.kills == 1, (
                "a start-up that timed out was not killed — the child keeps "
                "loading a model nothing is waiting for")
            assert proc.waits >= 1, (
                "a SIGKILLed child was never waited for — one zombie per attempt")
        finally:
            worker.close()

    def test_close_kills_a_child_that_outlives_terminate(self, monkeypatch):
        worker = embed_seam.WorkerBacking()
        proc = _ReapSpyProc(
            creates=worker.socket_path(), wait_raises_first=True)
        monkeypatch.setattr(embed_seam.subprocess, "Popen",
                            lambda *a, **k: proc)
        worker._ensure_started(2.0)
        worker.close()
        assert proc.terminates == 1
        assert proc.kills == 1, (
            "terminate() did not end the child and close() did not escalate to "
            "kill — a 432 MB worker survives teardown")
        assert proc.waits >= 2, (
            "the SIGKILL was not reaped (TimeoutExpired must be followed by a "
            "second wait)")


# ── the peer credential, both sides ────────────────────────────────────────


class TestTheClientVerifiesItsPeer:
    def test_a_socket_answered_by_a_foreign_uid_is_refused(self, tmp_path,
                                                           monkeypatch):
        """Review: the peer-credential belt was on the wrong side.

        ``_peer_allowed`` runs in the process that BOUND the socket, so it cannot
        defend against the attack it names — a process that binds the path FIRST
        and becomes the server, in which case the check that runs on ``accept`` is
        the attacker's own code. The client must verify whoever is ANSWERING.
        """
        path = str(tmp_path / "embed.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(4)
        monkeypatch.setattr(embed_seam, "_socket_peer_uid",
                            lambda _conn: os.getuid() + 1)
        try:
            with pytest.raises(RuntimeError) as excinfo:
                embed_seam._LineJSONClient(path).open_stream(1.0)
            assert "uid" in str(excinfo.value)
            assert "foreign process" in str(excinfo.value)
        finally:
            server.close()

    def test_the_require_peer_lever_refuses_an_unverifiable_peer(
            self, monkeypatch):
        """The fail-open default is selectable, not assumed.

        Unset, an unreadable peer is warned about once and allowed (the private
        ``0700`` directory is still in force). Set, it is REFUSED — the posture an
        operator at a shared lever path needs, and the only thing that makes the
        platform gap a decision instead of an accident.
        """
        from tortoise import embed_worker

        ours, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        monkeypatch.setattr(embed_worker, "_peer_uid", lambda _conn: None)
        monkeypatch.setenv(embed_worker.ENV_WORKER_REQUIRE_PEER, "1")
        try:
            assert embed_worker._peer_allowed(ours) is False, (
                "an unverifiable peer was allowed even though the lever requires "
                "peer identity")
        finally:
            ours.close()
            peer.close()

    def test_the_accept_loop_consults_the_peer_policy(self, tmp_path,
                                                      monkeypatch):
        """Review: the belt's WIRING was unpinned.

        Only ``_peer_allowed``'s decision had a row; deleting the call from
        ``serve``'s accept loop left the suite green, so the hardening could be
        removed without a red. Driven through a real socket, once with the policy
        denying and once with it allowing — the deny run must NOT be served.
        """
        from tortoise import embed_worker

        monkeypatch.setattr(embed_worker, "_watch_stdin", lambda _s: None)
        monkeypatch.setattr(embed_worker, "_watch_parent", lambda _pid, _stop: None)
        monkeypatch.setattr(embed_worker, "_preload_encoder", lambda: True)
        monkeypatch.setattr(
            embed_worker, "_encode", lambda texts: [[1.0] for _ in texts])

        def _ask(path: str) -> bytes:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            try:
                client.connect(path)
                # A refused peer is closed by the server, which a client sees as
                # a reset rather than an empty answer — both mean "not served".
                with contextlib.suppress(OSError):
                    client.sendall(b'{"texts": ["a"]}\n')
                try:
                    return client.recv(65536)
                except OSError:
                    return b""
            finally:
                client.close()

        def _run(path: str, allowed: bool) -> bytes:
            stop = threading.Event()
            failures: list = []

            def _serve() -> None:
                try:
                    embed_worker.serve(
                        path, parent_pid=0,
                        peer_allowed=lambda _conn: allowed, stop=stop)
                except BaseException as exc:  # recorded by the caller
                    failures.append(exc)

            thread = threading.Thread(target=_serve, daemon=True)
            thread.start()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not os.path.exists(path):
                time.sleep(0.01)
            assert os.path.exists(path), "the test worker never bound its socket"
            answer = _ask(path)
            stop.set()
            # Wake the blocked accept so the loop can observe the stop flag.
            with contextlib.suppress(OSError):
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close()
            with contextlib.suppress(OSError):
                _ask(path)
            thread.join(timeout=10)
            assert not failures, failures
            return answer

        allowed_answer = _run(str(tmp_path / "allowed.sock"), True)
        assert b"vectors" in allowed_answer, (
            f"the control run was not served at all ({allowed_answer!r}) — this "
            "row would pass vacuously")

        denied_answer = _run(str(tmp_path / "denied.sock"), False)
        assert b"vectors" not in denied_answer, (
            "a peer the policy REFUSED was served — serve() never consulted the "
            "peer credential, so the socket could be hijacked by any local user")


class TestThePrivateSocketDirectoryLifecycle:
    def test_a_diagnostics_read_does_not_mint_the_private_directory(self):
        """Review: ``describe()`` minted a directory that only ``close()`` removed.

        A ``/health`` read on a worker-configured process whose boot never
        completed therefore left an empty ``tortoise-embed-worker-*`` dir behind
        for the life of the process. A diagnostic must have no side effect.
        """
        worker = embed_seam.WorkerBacking()
        assert worker.describe()["socket"] is None
        assert worker._socket_dir is None, (
            "describe() minted the private socket directory")

    def test_a_directory_named_for_a_dead_pid_is_pruned(self, monkeypatch):
        """A SIGKILLed process cannot clean up after itself; nothing else did.

        ⛔ THE RESIDUE SHAPE MATTERS (review P2). The row used to build an EMPTY
        directory — a shape the code never produces — while a killed worker always
        leaves its SOCKET inside, and ``os.rmdir`` refuses a non-empty directory.
        So the prune was inert and the row that pinned it was green by
        construction. This builds the real shape: the socket file inside.
        """
        root = tempfile.gettempdir()
        stale = os.path.join(root, "tortoise-embed-worker-999999-stale")
        os.makedirs(stale, exist_ok=True)
        socket_file = os.path.join(stale, "embed.sock")
        holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        holder.bind(socket_file)
        holder.close()  # the FILE survives, exactly as after SIGKILL
        assert os.path.exists(socket_file)
        monkeypatch.setattr(embed_seam, "_pid_alive",
                            lambda pid: pid != 999999)
        worker = embed_seam.WorkerBacking()
        try:
            worker.socket_path()
        finally:
            worker.close()
        assert not os.path.exists(stale), (
            "a socket directory left by a dead worker was never pruned — "
            "`os.rmdir` cannot remove the socket file inside it")

    def test_a_close_removes_its_own_directory_with_the_socket_inside(
            self, monkeypatch):
        """``close()`` reaps its private directory, residue and all (review P2).

        The sibling residue a crash can leave beside the socket made ``rmdir``
        fail here too, so a retired backing left its ``0700`` directory behind.
        """
        worker = embed_seam.WorkerBacking()
        address = worker.socket_path()
        directory = os.path.dirname(address)
        assert os.path.isdir(directory)
        # A socket file of the kind a killed predecessor leaves.
        socket_file = os.path.join(directory, "embed.sock")
        residue = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        residue.bind(socket_file)
        residue.close()
        worker.close()
        assert not os.path.exists(directory), (
            "close() left the private socket directory behind because it was "
            "not empty — `rmdir` refuses exactly the shape a crash leaves")

    def test_a_close_does_not_remove_a_directory_it_did_not_mint(self):
        """The close/describe interleave must not re-mint.

        ``close()`` used to clear ``_socket_dir`` at the END, so a ``describe()``
        in between minted a NEW directory and the ``rmdir`` then removed that one —
        the path a just-closed worker had bound.
        """
        worker = embed_seam.WorkerBacking()
        minted = worker.socket_path()
        directory = os.path.dirname(minted)
        worker.close()
        assert not os.path.exists(directory)
        # A second close is a no-op and mints nothing.
        worker.close()
        assert worker._socket_dir is None


def _indent_at(source: str, offset: int) -> int:
    """The leading-whitespace width of the line containing ``offset``.

    Read from the WHOLE line, not the prefix before ``offset``: an offset that IS
    a line start has an empty prefix, and the prefix form answers 0 for it — which
    made a block-boundary scan end the block on its own first body line.
    """
    line_start = source.rfind("\n", 0, offset) + 1
    line_end = source.find("\n", line_start)
    line = source[line_start:line_end if line_end != -1 else len(source)]
    return len(line) - len(line.lstrip())


class TestTheReadinessBoundsKnowWhereTheEncoderLives:
    """#14186 review P1 — the write/read bounds must not kick a load that is not theirs.

    ``await_write_ready_reason`` / ``await_read_ready_reason`` exist to bound a
    LOCAL load, and with a ``worker`` / ``external`` backing the local model is
    not this path's encoder. Two review rounds named the two ways that can go
    wrong — a 432 MB local load started for nothing, and the create's budget
    charged to the backing's BOOT (``#4055``/``#13224`` forbid charging process
    initialization to a request) — so the write answers DECLINE-with-the-right-
    cause and kicks nothing, and the read answers READY (it is defence in depth:
    ``sdk`` takes its ``_seam_remote`` branch before this ladder).
    """

    @pytest.fixture
    def cold_model(self, monkeypatch):
        """A genuinely cold singleton, so "already warm" cannot mask the gate.

        ⛔ "COLD" IS THREE LATCHES, NOT TWO. Clearing `_instance` and
        `_warm_up_started` left `_last_failure_kind` alone, and that latch is
        process-global: any earlier row that drove a local load in a venv without
        the `embeddings` extra records the DESIGNED ABSENCE (`not_installed`), and
        `await_write_ready_reason` answers that — before it can kick anything — for
        the rest of the process. Measured: the rows in this class pass alone and
        `test_the_in_process_backing_still_kicks_the_load` fails when
        `TestTheRequiredEmbedderFlagBindsOnARemoteBacking` runs first, because its
        SDK read latched `not_installed`. So the fixture states the whole premise.
        """
        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "_instance", None, raising=False)
        monkeypatch.setattr(EmbeddingModel, "_warm_up_started", False,
                            raising=False)
        monkeypatch.setattr(EmbeddingModel, "_warm_up_thread", None, raising=False)
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", None,
                            raising=False)
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None,
                            raising=False)
        kicked: list[tuple] = []
        monkeypatch.setattr(
            EmbeddingModel, "start_warm_up",
            classmethod(lambda cls: kicked.append((cls.__name__,)) or None))
        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
        return kicked

    def test_a_BOOTING_worker_declines_without_kicking_a_local_load(
            self, cold_model, monkeypatch):
        """A boot in flight ⇒ decline, having loaded and waited for nothing."""
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: True)
        from tortoise.embeddings import _WRITE_DECLINE_BACKING_COLD, EmbeddingModel

        reason = EmbeddingModel.await_write_ready_reason(0.05)
        assert reason == _WRITE_DECLINE_BACKING_COLD, (
            "a bounded write while the backing boots answered "
            f"{reason!r} — the causes are distinct and an operator repairs "
            "them differently")
        assert EmbeddingModel.await_read_ready_reason(0.05) is None, (
            "the read bound declined on a worker backing")
        assert cold_model == [], (
            "the readiness bounds kicked a LOCAL 432 MB load with the encoder "
            f"served elsewhere: {cold_model}")

    def test_a_WARM_worker_is_READY_and_does_not_decline(self, cold_model,
                                                         monkeypatch):
        """⛔ The gate is on the BACKING's phase, not on the LEVER (review P2).

        Declining on the lever alone would have stored no derived vector for the
        LIFE of the process, not for one boot: with `worker` configured, a warm
        worker answered `backing_cold` forever. The predicate is
        `embed_seam.backing_booting()`, so a warm backing is READY and the encode
        goes through the seam in milliseconds.
        """
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: False)
        from tortoise.embeddings import EmbeddingModel

        assert EmbeddingModel.await_write_ready_reason(0.05) is None, (
            "a WARM worker backing declined a bounded write — every derived "
            "fold would store no vector for the life of the process")
        assert cold_model == []

    def test_the_REACHABLE_funnel_declines_without_kicking_the_local_load(
            self, cold_model, monkeypatch):
        """The row the P1 named: the FUNNEL, not only the readiness predicate (review P1).

        ``bounded=True`` is what the derived-entity folds pass, so the first bounded
        write after a deploy is the call an operator actually pays. At the reviewed
        head `encode_for_row` handed it to `await_write_ready_reason` with no backing
        check, which kicked a 432 MB LOCAL load in a process whose encoder is the
        worker — and then declined the row blaming a cold local embedder.
        """
        from tortoise import monitoring
        from tortoise.embeddings import BOUNDED_SCOPE_GRAPH_FOLD, encode_for_row

        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: True)
        label = f"{BOUNDED_SCOPE_GRAPH_FOLD}:backing_cold"
        before = monitoring.derived_embed_decline_counts().get(label, 0)

        assert encode_for_row("a derived row", None, bounded=True) is None, (
            "a bounded write while the backing boots must DECLINE — it must not "
            "block on a worker boot it did not choose to pay")
        assert cold_model == [], (
            "the FUNNEL kicked a LOCAL 432 MB load with the encoder served "
            f"elsewhere: {cold_model}")
        after = monitoring.derived_embed_decline_counts().get(label, 0)
        assert after == before + 1, (
            "the decline was not counted on the derived-embed channel, so an "
            "operator cannot see the rows the dense leg is missing")

    def test_the_REACHABLE_funnel_ENCODES_when_the_worker_is_warm(
            self, cold_model, monkeypatch):
        """The other half: a WARM worker must not lose the vector for good.

        Declining on the LEVER alone answered `backing_cold` for a warm worker too,
        so every derived fold would have stored no vector for the LIFE of the
        process rather than for one boot.
        """
        from tortoise import embeddings as embeddings_module
        from tortoise.embeddings import encode_for_row

        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: False)
        encoded: list[tuple[str, int | None]] = []
        monkeypatch.setattr(
            embeddings_module, "encode_for_store",
            lambda content, expected_dim: (
                encoded.append((content, expected_dim)) or [0.5] * 4))

        assert encode_for_row("a derived row", 4, bounded=True) == [0.5] * 4, (
            "a WARM worker backing stored no vector for the row — every derived "
            "fold would lose its vector for the life of the process")
        assert encoded == [("a derived row", 4)], (
            "the funnel declined instead of encoding through the seam: "
            f"{encoded!r}")
        assert cold_model == []

    def test_the_boot_predicate_reads_the_backing_not_the_lever(self, monkeypatch):
        """``backing_booting()``: warm worker ⇒ False, booting ⇒ True, external ⇒ False."""
        class _Fake:
            #: A real WorkerBacking's own name — the boot predicate also answers
            #: True when the LEVER says worker but the live backing is not one.
            name = embed_seam.BACKING_WORKER

            def __init__(self, pending: bool) -> None:
                self._pending = pending

            def startup_pending(self) -> bool:
                return self._pending

            def encode(self, texts, *, timeout_s=None):  # pragma: no cover
                return None

            def close(self) -> None:
                return None

            def describe(self) -> dict:
                return {}

        try:
            monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
            # Nothing constructed and the lever says worker ⇒ the FIRST encode
            # pays a boot, which is the same wait for this question.
            embed_seam.install_backing(None)
            embed_seam._backing = None
            embed_seam._backing_name = None
            assert embed_seam.backing_booting() is True

            embed_seam.install_backing(_Fake(pending=True))
            assert embed_seam.backing_booting() is True
            embed_seam.install_backing(_Fake(pending=False))
            assert embed_seam.backing_booting() is False

            # `external` is a round trip: no boot to wait for, ever.
            monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
            embed_seam.install_backing(None)
            embed_seam._backing = None
            embed_seam._backing_name = None
            assert embed_seam.backing_booting() is False
        finally:
            embed_seam.install_backing(None)
            embed_seam.reset_seam()

    def test_the_boot_predicate_reads_the_REAL_backing_phase(self, tmp_path):
        """⛔ Every row above stubs `startup_pending`, and the production one was
        WRONG in a state no stub could represent (review P1, cycle 4): `_proc`
        set but DEAD — or alive with its socket gone — is a boot the NEXT encode
        pays, because `_ensure_started` respawns in both cases. Answering "not
        booting" then sized the bound for steady state, so a bounded write took
        the seam and charged 600 s of worker start-up to the create.

        So this row asks the REAL class, in the four shapes readiness actually
        takes. The `False` row is not decoration: it is what stops a future fix
        from over-declining a healthy worker — cycle 3's finding.
        """
        class _Live:
            pid = 4242

            def poll(self):
                return None

        class _Dead:
            pid = 4243

            def poll(self):
                return 137

        backing = embed_seam.WorkerBacking()
        try:
            path = str(tmp_path / "phase.sock")
            # 1. Nothing spawned yet: the first encode pays the whole boot.
            assert backing.startup_pending() is True
            # 2. Spawned, then the child DIED: `_ensure_started` respawns.
            backing._proc = _Dead()
            backing._address = path
            assert backing.startup_pending() is True, (
                "a DEAD worker read as ready — the next encode respawns it, so "
                "a bounded write would have paid the boot on the request thread")
            # 3. Alive, but the socket it bound is gone (a retired instance, or
            #    anything that removed the path): also a respawn.
            backing._proc = _Live()
            assert backing.startup_pending() is True
            # 4. Alive AND bound: warm, so this must NOT decline.
            open(path, "w").close()
            backing._address = path
            assert backing.startup_pending() is False, (
                "a WARM worker read as booting — every derived fold would store "
                "no vector for the life of the process")
        finally:
            backing._proc = None
            backing._address = None
            backing.close()

    def test_a_bounded_write_on_a_WARM_worker_still_uses_the_seam(
            self, monkeypatch):
        """A loaded LOCAL model must not answer for a seam this process does not run.

        ⛔ The seam gate sat BELOW `inst._model is not None` (review P1, cycle 4),
        so in any process holding a loaded local model it never ran: that process
        answered READY during the worker's boot and the create paid it. Reachable
        two documented ways — the lever is re-read per call, and
        `entity_resolver`/`cross_lens` load the local model even under `worker`.
        """
        from tortoise.embeddings import _WRITE_DECLINE_BACKING_COLD, EmbeddingModel

        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: True)

        class _WarmLoadedLocalModel:
            """The state that used to short-circuit the gate: model loaded HERE."""

            _model = object()

        monkeypatch.setattr(EmbeddingModel, "_instance",
                            _WarmLoadedLocalModel(), raising=False)
        assert EmbeddingModel.await_write_ready_reason(0.05) == \
            _WRITE_DECLINE_BACKING_COLD, (
                "a loaded LOCAL model answered READY for a worker that is "
                "booting — the write then charges the boot to the create")

    def test_the_warm_up_OPT_OUT_does_not_rescue_a_worker_backing(
            self, cold_model, monkeypatch):
        """⛔ What the docstring now says, executably (review P2, cycle 5).

        `TORTOISE_EMBEDDER_WARMUP=0` keeps the pre-#13224 synchronous contract
        **for an `inprocess` backing** — no background lane, nothing to bound. It
        is NOT a statement about a deployment whose encoder lives elsewhere: there
        the flag governs a local lane this process does not have, and honouring it
        would send the create into the seam to pay the worker's 600 s boot. So the
        flag-off promise is pinned on one arm and its LIMIT on the other; the
        docstring previously claimed the first for both.
        """
        from tortoise.embeddings import _WRITE_DECLINE_BACKING_COLD, EmbeddingModel

        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: True)
        assert EmbeddingModel.await_write_ready_reason(0.05) == \
            _WRITE_DECLINE_BACKING_COLD, (
                "the warm-up opt-out answered READY for a booting worker — the "
                "write then charges the worker's boot to the create")
        assert cold_model == [], (
            f"the opt-out path kicked a local load anyway: {cold_model}")

        # And the promise that DOES hold: in-process, flag off ⇒ ready.
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        assert EmbeddingModel.await_write_ready_reason(0.05) is None, (
            "with the flag off on an IN-PROCESS backing the bound declined — the "
            "pre-#13224 synchronous contract is broken for dev/test")

    def test_a_bounded_write_hands_the_cause_TO_the_log_arm(self, monkeypatch,
                                                            caplog):
        """⛔ The HAND-OFF, not the arm (review P3, cycle 4).

        The row below calls ``_note_cold_write`` by hand, so hardcoding a
        different ``reason=`` at the decline site left it green while the log
        line and the counter named the wrong cause. This drives the real
        ``encode_for_row`` decline path instead and asks the three channels an
        operator actually reads: no vector returned, the backing_cold line
        emitted, and the counter labelled ``backing_cold`` — not merely a line
        someone hand-wrote.
        """
        import logging as _logging

        from tortoise import embeddings as embeddings_module
        from tortoise import monitoring
        from tortoise.embeddings import _note_cold_write

        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: True)
        monkeypatch.setattr(embed_seam, "encodes_in_process", lambda *a, **k: False)
        embeddings_module._COLD_WRITE_REPORTED.clear()
        before = dict(monitoring.derived_embed_decline_counts())
        with caplog.at_level(_logging.DEBUG, logger="tortoise.embeddings"):
            assert embeddings_module.encode_for_row("hello", None,
                                                    bounded=True) is None, (
                "a bounded write on a booting worker returned a vector — it "
                "went on into the seam and paid the boot")
        after = dict(monitoring.derived_embed_decline_counts())
        key = f"{embeddings_module.BOUNDED_SCOPE_GRAPH_FOLD}:backing_cold"
        assert after.get(key, 0) - before.get(key, 0) == 1, (
            f"the decline was not counted under {key!r}: "
            f"{ {k: v - before.get(k, 0) for k, v in after.items() if v != before.get(k, 0)} }")
        lines = [r for r in caplog.records
                 if r.name == "tortoise.embeddings" and "vector" in r.getMessage()]
        assert lines, "the decline produced no line at all"
        assert "after waiting" not in lines[0].getMessage(), (
            f"the hand-off lost the cause and fell through to the wait text: "
            f"{lines[0].getMessage()!r}")
        # And the arm itself still works when called by the site's own contract.
        _note_cold_write(embeddings_module.WRITE_EMBED_BUDGET_S,
                         reason=embeddings_module._WRITE_DECLINE_BACKING_COLD)

    def test_the_backing_cold_line_names_the_backing_and_claims_no_wait(
            self, cold_model, monkeypatch, caplog):
        """⛔ The arm that reports `backing_cold` had no row (review P3).

        Deleting it silently fell through to the budget-expired text — *"stored
        WITHOUT a vector after waiting 0.25s"* — for the one cause where nothing
        was waited, which is the exact false claim #13229 F2 removed. So: INFO
        (not a fault to repair), no wait claim, the backing named, the backfill
        named, and ONE line per process per cause.
        """
        import logging as _logging

        from tortoise import embeddings as embeddings_module
        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        monkeypatch.setattr(embed_seam, "backing_booting", lambda *a, **k: True)
        embeddings_module._COLD_WRITE_REPORTED.clear()
        with caplog.at_level(_logging.DEBUG, logger="tortoise.embeddings"):
            assert EmbeddingModel.await_write_ready_reason(0.05) == \
                embeddings_module._WRITE_DECLINE_BACKING_COLD
            embeddings_module._note_cold_write(
                0.25, reason=embeddings_module._WRITE_DECLINE_BACKING_COLD)
            # Latched: a second call adds nothing.
            embeddings_module._note_cold_write(
                0.25, reason=embeddings_module._WRITE_DECLINE_BACKING_COLD)
        lines = [r for r in caplog.records
                 if r.name == "tortoise.embeddings"
                 and "vector" in r.getMessage()]
        assert len(lines) == 1, [r.getMessage() for r in lines]
        msg = lines[0].getMessage()
        assert lines[0].levelno == _logging.INFO, (
            f"`backing_cold` is logged at {lines[0].levelname} — nothing local "
            f"needs repairing, so this is not a fault line: {msg!r}")
        assert "after waiting" not in msg, (
            f"the line claims a wait for a cause that waited 0 s: {msg!r}")
        assert embed_seam.BACKING_WORKER in msg, (
            f"the backing that is booting is not named: {msg!r}")
        assert "backfill_embeddings.py" in msg, (
            f"the repair an operator runs is not named: {msg!r}")

    def test_the_in_process_backing_still_kicks_the_load(self, cold_model,
                                                        monkeypatch):
        """The control: the gate is a gate, not a blanket skip."""
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        from tortoise.embeddings import EmbeddingModel

        EmbeddingModel.await_write_ready_reason(0.0)
        assert cold_model, (
            "the IN-PROCESS backing stopped kicking its own warm-up — the cold "
            "window would now be paid synchronously by the first write")

    def test_the_sdk_engine_kick_follows_the_backing(self, monkeypatch):
        """``_embedder_engine_kick_allowed`` — the engine-init kick's decision.

        ⛔ FAIL-SAFE DIRECTION: an unreadable seam answers TRUE (warm), because a
        silent skip is a load that never happens on the configuration that needs
        it — the mirror of ``hosted_api._lifespan``'s arm, which keeps the
        pre-warm ON when the seam cannot be read.
        """
        from tortoise import sdk as sdk_module

        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_WORKER)
        assert sdk_module._embedder_engine_kick_allowed() is False
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_EXTERNAL)
        assert sdk_module._embedder_engine_kick_allowed() is False
        monkeypatch.setenv(embed_seam.ENV_BACKING, embed_seam.BACKING_INPROCESS)
        assert sdk_module._embedder_engine_kick_allowed() is True

        def _unreadable() -> bool:
            raise RuntimeError("the seam cannot be read")

        monkeypatch.setattr(embed_seam, "encodes_in_process", _unreadable)
        assert sdk_module._embedder_engine_kick_allowed() is True, (
            "an unreadable seam silently skipped the engine-init warm-up")

    def test_the_engine_init_site_CONSULTS_the_gate(self):
        """The wiring, not just the decision (review P3).

        Deleting the gate at the call site and calling
        ``EmbeddingModel.start_warm_up()`` unconditionally left every suite green:
        the helper's own row cannot see its caller. ``sdk.py`` is too large to
        boot for a unit row, so this pins the ONE fact the wiring is — the
        engine-init kick sits inside a branch on the helper, with the call in the
        same block. Same source-level technique ``tests/test_fly_secret_drift.py``
        already uses to pin a citation.
        """
        import inspect

        from tortoise import sdk as sdk_module

        source = inspect.getsource(sdk_module)
        gate = source.index("if _embedder_engine_kick_allowed():")
        warm = source.index("EmbeddingModel.start_warm_up()", gate)
        gate_line = source.rfind("\n", 0, gate) + 1
        gate_indent = _indent_at(source, gate)
        warm_indent = _indent_at(source, warm)
        assert warm_indent > gate_indent, (
            "the engine-init warm-up call is not guarded by "
            "_embedder_engine_kick_allowed() — the SDK kick ignores the embed "
            "backing again")

        # ⛔ THE BLOCK BOUNDARY, not just the indent (review P3). An indent test
        # alone passed `if gate(): pass` / `else: EmbeddingModel.start_warm_up()`
        # — the call is deeper than the gate line but NOT inside its block — and
        # passed a kick moved to any later deeper block. So: the gate's block ENDS
        # at the first following line at (or left of) the gate's own indent, the
        # call must fall inside it, and no `else:`/`elif:` may appear in it (that
        # is the polarity inversion).
        block_end = len(source)
        cursor = gate_line
        while True:
            newline = source.find("\n", cursor)
            if newline == -1:
                break
            cursor = newline + 1
            line_end = source.find("\n", cursor)
            line = source[cursor:line_end if line_end != -1 else len(source)]
            if line.strip() and _indent_at(source, cursor) <= gate_indent:
                block_end = cursor
                break
        assert source.count("EmbeddingModel.start_warm_up()") == 1, (
            "the engine-init site calls start_warm_up() more than once — the "
            "pin only inspects the first call after the gate, so an UNGATED kick "
            "beside the gated one stays green (review P3)")
        assert gate < warm < block_end, (
            "the engine-init warm-up call is not INSIDE the "
            "_embedder_engine_kick_allowed() block (an `else:`/`elif:` inversion "
            "or a moved call both look guarded by indentation alone) — the SDK "
            "kick ignores the embed backing again")
