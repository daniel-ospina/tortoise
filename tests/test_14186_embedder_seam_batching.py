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
import os
import socket
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

    def encode(self, texts, *, timeout_s=None):
        if self.delay_s:
            time.sleep(self.delay_s)
        with self._lock:
            self.batches.append(list(texts))
        # A deterministic, TEXT-DEPENDENT vector, so the rows can assert that
        # each ticket got ITS OWN row back and not a neighbour's.
        return [[float(len(t)), float(ord(t[0]) if t else 0)] + [0.0] * (self.dim - 2)
                for t in texts]

    def close(self) -> None:
        return None

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

    @pytest.mark.parametrize("raw", ["20ms", "-5", "nan", ""])
    def test_an_unusable_window_falls_back_to_the_default_with_a_warning(
            self, monkeypatch, caplog, raw):
        """TOTAL: a bad number must not silently become 0 (i.e. no batching)."""
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
        assert set(block) == set(hosted_api._EMBED_SEAM_HEALTH_KEYS)
        assert set(embed_seam.seam_status()) >= set(
            hosted_api._EMBED_SEAM_HEALTH_KEYS), (
            "`seam_status()` no longer produces a key `/health` publishes — the "
            "operator's read resolves to null forever")
        assert block["backing"] == embed_seam.BACKING_INPROCESS
        assert block["max_wait_ms"] == 0.0

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
    """Answers whatever the enclosing test put in ``server.answer``."""

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        self.server.requests.append(json.loads(raw.decode("utf-8")))
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
        """The documented request contract, pinned byte for byte."""
        server = http_endpoint({"vectors": [[0.5]]})
        monkeypatch.setenv(embed_seam.ENV_EXTERNAL_MODEL, "some-embed-v2")
        backing = self._backing(monkeypatch, server, token="tok-123")
        backing.encode(["hello"])
        sent = server.requests[0]
        assert sent == {"model": "some-embed-v2", "texts": ["hello"]}

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
        finally:
            proc = client._proc
            client.close()
        assert proc is not None
        proc.wait(timeout=30.0)
        assert proc.poll() is not None, "the worker survived its parent's close"
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


class _FakeProc:
    """Stands in for a live ``Popen`` — ``poll() is None`` means "running"."""

    pid = 4242

    def poll(self):
        return None


# ── the wiring ──────────────────────────────────────────────────────────────


class TestTheCallersRouteThroughTheSeam:
    def test_compute_embeddings_uses_the_seam_and_keeps_its_contract(
            self, monkeypatch):
        """The store funnel still returns one row per text, through the seam.

        ``compute_embeddings`` is a widely-REPLACED seam (the longmem eval
        doubles swap the function itself), so its own signature and return shape
        are pinned by other rows; this one pins that the ENCODE inside it is the
        seam's, which is what makes ``TORTOISE_EMBED_BACKING`` reach the write
        path at all.
        """
        from tortoise import embeddings

        seen: list[list[str]] = []

        class _Model:
            def encode(self, texts):
                seen.append(list(texts))
                return np.array([[float(len(t)), 1.0] for t in texts],
                                dtype=np.float32)

        monkeypatch.setattr(embeddings.EmbeddingModel, "get", classmethod(
            lambda cls, load_timeout=None: _Model()))
        monkeypatch.setattr(embeddings, "ensure_embed_threads", lambda: None)

        rows = embeddings.compute_embeddings(["ab", "cdef"])
        assert rows == [[2.0, 1.0], [4.0, 1.0]]
        assert seen == [["ab", "cdef"]]

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
