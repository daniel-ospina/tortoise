"""Shared concurrency harness for the epic #900 index suite (T3, cycle-14 pin).

OPT-IN named module (the plan's precedent name): helper functions + opt-in
fixtures only — NO autouse scope, NO module-level env mutation (the existing
``tests/conftest.py`` has session-scoped DB fixtures five non-epic test files
depend on; autouse wiring would trip their exact-count assertions).

Consumed in-process by E2E-9/E2E-8 (threads legs with per-thread SDK
instances + barrier + marker-node warm-up on the shared daemon) and by the
subprocess legs (helper functions around the process boundary — a conftest
cannot deliver fixtures to a nohup'd child).

Backend pin (§7 harness conventions): FalkorDBLite/redislite is SINGLE-WRITER
(#6761 — the prohibition is two daemons/processes owning one DB file, not two
client connections to one daemon). Threads legs therefore construct a
PER-THREAD TortoiseSDK against the SAME db path — redislite reuses the live
daemon via its pid registry — and a single shared connection is barred
(redis-py connections are not thread-safe).

Choreography (cycle-2/3 pins): the SDK instances are constructed SEQUENTIALLY
in the caller (constructing two SDKs from two racing threads can start TWO
daemons — the redislite registry is written only once the daemon is up, and a
second constructor racing ahead of the first's daemon-start creates a second
daemon: split-brain). Daemon reuse is then PROVEN by a cross-instance marker
check (thread 0 warms the marker; EVERY thread asserts visibility through its
OWN instance). A platform where reuse does not hold → ``reuse_holds()`` is
False and the caller SKIPS the leg (the plan's sanctioned disposition).
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from tortoise.sdk import TortoiseSDK

# Sentinel marker label used to verify graph identity before a barrier
# release (daemon warm-up pin): a marker written via thread 0's instance
# must be visible from every other instance before the concurrent runs start.
MARKER_LABEL = "e2e900_marker"


def make_db(tmp_path: Path | None = None) -> str:
    """Fresh embedded DB path (a temp dir + t.db) for an isolated test graph."""
    base = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
    return os.path.join(base, "t.db")


def new_sdk(db_path: str, namespace: str = "e2e-900") -> TortoiseSDK:
    """A TortoiseSDK instance against the shared embedded daemon.

    Constructed SEQUENTIALLY by the caller (never from racing threads) so
    redislite daemon reuse is deterministic (the race would start a second
    daemon — split-brain).
    """
    return TortoiseSDK(db_path, namespace=namespace)


def marker_warmup(sdk: TortoiseSDK) -> None:
    """Write the marker node (thread 0's instance — the warm-up writer)."""
    sdk._get_proj().g.query(
        "MERGE (m:Marker {label:$l}) ON CREATE SET m.t=1",
        params={"l": MARKER_LABEL},
    )


def assert_marker_visible(sdk: TortoiseSDK) -> bool:
    """Assert the marker (written by ANOTHER instance) is visible here.

    This is the daemon-reuse proof: if this instance were on its own daemon,
    the marker written by thread 0's instance would be absent. Returns False
    when reuse does not hold (the caller skips the leg per §7).
    """
    rows = sdk._get_proj().g.query(
        "MATCH (m:Marker {label:$l}) RETURN count(m)",
        params={"l": MARKER_LABEL},
    ).result_set
    return bool(rows and rows[0][0] >= 1)


def barrier_index_runs(corpus: str, db_path: str, n_runs: int = 2,
                       namespace: str = "e2e-900", **index_kwargs):
    """Run ``index_directory`` concurrently on N threads (barrier-released).

    SDK instances are constructed SEQUENTIALLY here (daemon-reuse
    deterministic), thread 0 warms the marker, EVERY thread asserts
    cross-instance marker visibility (reuse proof), then all runs are
    barrier-released. Returns ``(results, reuse_holds)`` — the caller skips
    the leg when ``reuse_holds`` is False.

    ``index_kwargs`` (extract_metadata, progress_file, …) are passed to every
    run.
    """
    sdks = [new_sdk(db_path, namespace) for _ in range(n_runs)]
    barrier = threading.Barrier(n_runs)
    results: list[dict] = [None] * n_runs  # type: ignore[list-item]
    errors: list[BaseException] = []
    reuse_holds = True

    def _worker(i: int) -> None:
        nonlocal reuse_holds
        sdk = sdks[i]
        try:
            barrier.wait(timeout=60)   # all SDKs constructed
            if i == 0:
                marker_warmup(sdk)
            barrier.wait(timeout=60)   # marker written
            if not assert_marker_visible(sdk):
                reuse_holds = False
            barrier.wait(timeout=60)   # reuse verified (or not)
            if not reuse_holds:
                return
            results[i] = sdk.index_directory(corpus, **index_kwargs)
        except BaseException as e:  # noqa: BLE001, RUF100
            errors.append(e)
            try:  # noqa: SIM105
                barrier.wait(timeout=60)
            except Exception:  # noqa: BLE001, RUF100
                pass
        finally:
            sdk.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_runs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=600)
    if errors:
        raise errors[0]
    return results, reuse_holds
