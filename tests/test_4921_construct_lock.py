"""#4921: the per-`<dbdir>/<dbfilename>` construction lock.

The gap: `RedisMixin.__init__` decides a server is not running
(`client.py:449`) and then starts one (`client.py:462`) with no lock between
the two, so two concurrent constructions of one `<dbdir>/<dbfilename>` can each
bring up a server over the SAME RDB — two writers on one file.

These tests pin the three properties the seam forces (reentrant, cross-process,
per-key) plus the key derivation, which is the part that decides WHAT is
serialised. Every wait is bounded, so a regression fails the test instead of
hanging the suite.
"""

from __future__ import annotations

import contextlib
import os
import select
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embedded_lifecycle import (
    _CONSTRUCT_LOCKS,
    _construction_key,
    _construction_lock,
    _open_construct_lock,
)


def _key(tmp_path, name="db.rdb"):
    return str(tmp_path / name)


# ── key derivation: WHAT gets serialised ───────────────────────────────────


def test_key_uses_the_positional_db_filename(tmp_path):
    db = str(tmp_path / "db.rdb")
    assert _construction_key((db,), {}) == os.path.realpath(db)


def test_key_uses_the_dbfilename_kwarg(tmp_path):
    db = str(tmp_path / "db.rdb")
    assert _construction_key((), {"dbfilename": db}) == os.path.realpath(db)


def test_key_anchors_a_bare_name_to_the_cwd(tmp_path, monkeypatch):
    """redislite joins a bare `dbfilename` to the cwd (client.py:435-436)."""
    monkeypatch.chdir(tmp_path)
    assert _construction_key(("db.rdb",), {}) == os.path.realpath(
        os.path.join(str(tmp_path), "db.rdb"))


def test_key_resolves_two_spellings_of_one_file_to_one_lock(tmp_path):
    """A symlinked spelling must not get its own lock — that is the bug."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    via_real = _construction_key((str(real / "db.rdb"),), {})
    via_link = _construction_key((str(link / "db.rdb"),), {})
    assert via_real == via_link


@pytest.mark.parametrize("kwargs", [{"host": "127.0.0.1"}, {"port": 6379}])
def test_key_is_none_for_an_external_redis(kwargs, tmp_path):
    """`host`/`port` names a server we never start (client.py:407-412)."""
    assert _construction_key((str(tmp_path / "db.rdb"),), kwargs) is None


def test_key_is_none_when_no_db_file_is_named():
    """No db file: redislite mints its own `mkdtemp()`, unshareable by design."""
    assert _construction_key((), {}) is None
    assert _construction_key((None,), {}) is None
    assert _construction_key((), {"dbfilename": ""}) is None


def test_kwarg_wins_over_the_positional(tmp_path):
    """Review round 1: redislite overrides `args[0]` when `dbfilename` is present.

    `client.py:415-428` takes the positional first and then, UNCONDITIONALLY,
    `if 'dbfilename' in kwargs: db_filename = kwargs['dbfilename']`. Deriving the
    key the other way round locks a file redislite never opens and takes no lock
    on the RDB it does open — so #4921 stays open for that call shape.
    """
    a, b = str(tmp_path / "a.rdb"), str(tmp_path / "b.rdb")
    assert _construction_key((a,), {"dbfilename": b}) == os.path.realpath(b)


def test_bytes_filename_yields_no_key():
    """A `bytes` filename aborts redislite's own join (`os.path.join(str, bytes)`)."""
    assert _construction_key((b"/tmp/x.rdb",), {}) is None


# ── property 1: reentrant (the constructor nests) ──────────────────────────


def test_lock_is_reentrant_in_one_thread(tmp_path):
    key = _key(tmp_path)
    with _construction_lock(key):
        entry = _CONSTRUCT_LOCKS[key]
        assert entry.depth == 1
        assert entry.fd >= 0, "the flock must be taken at the 0->1 transition"
        with _construction_lock(key):          # the nesting must not deadlock
            assert entry.depth == 2, "the flock must NOT be retaken while nested"
        assert entry.depth == 1, "the flock must survive the inner release"
        assert entry.fd >= 0
    assert entry.depth == 0
    assert entry.fd == -1, "the flock must be released at the 1->0 transition"


def test_lock_releases_the_fd_exactly_once(tmp_path):
    """A leaked fd per construction is the failure this pins."""
    key = _key(tmp_path)
    for _ in range(3):
        with _construction_lock(key):
            pass
    assert _CONSTRUCT_LOCKS[key].fd == -1
    assert _CONSTRUCT_LOCKS[key].depth == 0


# ── property 2: mutual exclusion, in-process and across processes ──────────


def test_lock_excludes_a_second_thread(tmp_path):
    key = _key(tmp_path)
    inside = threading.Event()
    finished = threading.Event()

    def worker():
        with _construction_lock(key):
            inside.set()
            finished.wait(10)

    with _construction_lock(key):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert not inside.wait(0.5), "a second thread entered while the lock was held"
    assert finished.is_set() is False
    finished.set()
    assert inside.wait(10), "the second thread never acquired the released lock"


def test_distinct_keys_do_not_serialise(tmp_path):
    """A global lock would pass every test above and fail this one."""
    a, b = _key(tmp_path, "a.rdb"), _key(tmp_path, "b.rdb")
    entered = threading.Event()

    def worker():
        with _construction_lock(b):
            entered.set()

    with _construction_lock(a):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert entered.wait(10), "an unrelated key was serialised behind this one"
    t.join(5)


_CHILD = """
import sys, time
sys.path.insert(0, {root!r})
from tortoise.embedded_lifecycle import _construction_lock
with _construction_lock({key!r}):
    sys.stdout.write("held\\n")
    sys.stdout.flush()
    time.sleep({hold})
"""


def test_lock_excludes_another_process(tmp_path):
    """The race is CROSS-PROCESS; an in-process-only lock cannot close it."""
    key = _key(tmp_path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(root=root, key=key, hold=60)],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": root})
    try:
        assert _readline_bounded(child.stdout).strip() == "held", "the child never took the lock"
        start = time.monotonic()
        with _construction_lock(key):
            elapsed = time.monotonic() - start
        assert elapsed > 1.0, (
            f"acquired the lock in {elapsed:.2f}s while another process held it — "
            "the flock is not engaging")
    finally:
        child.terminate()
        child.wait(10)


def test_an_unrelated_key_is_not_blocked_by_a_held_flock(tmp_path):
    """Review round 1 P1: the module mutex must NOT be held across the flock wait.

    An external holder keeps a flock for as long as it likes, so holding
    `_CONSTRUCT_LOCKS_GUARD` across that wait serialises every OTHER key in this
    process — the per-key property this lock exists to provide — and completes a
    wait-for cycle with any peer's nested construction.
    """
    held, other = _key(tmp_path, "held.rdb"), _key(tmp_path, "other.rdb")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(root=root, key=held, hold=60)],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": root})
    parked_evt = threading.Event()

    def _park():
        with _construction_lock(held):
            parked_evt.wait(60)

    parked = threading.Thread(target=_park, daemon=True)
    try:
        assert _readline_bounded(child.stdout).strip() == "held"
        # Park a thread of THIS process inside `_open_construct_lock` (past the
        # registry lookup, into the cross-process wait). The entry appearing in
        # the registry is the signal it got that far — and it is the state the
        # round-1 defect needs, because the module mutex is process-local: a
        # child's flock alone makes nobody here wait, so an external holder
        # cannot detect the defect however long it holds.
        from tortoise.embedded_lifecycle import _CONSTRUCT_LOCKS

        parked.start()
        deadline = time.monotonic() + 30
        while held not in _CONSTRUCT_LOCKS and time.monotonic() < deadline:
            time.sleep(0.05)
        assert held in _CONSTRUCT_LOCKS, "the thread never parked on the held key"
        start = time.monotonic()
        with _construction_lock(other):
            pass
        assert time.monotonic() - start < 1.0, (
            "an unrelated key waited behind a held flock — the module mutex is "
            "held across the cross-process wait")
    finally:
        parked_evt.set()
        child.terminate()
        child.wait(10)


def _readline_bounded(stream, timeout=60):
    """`readline()` that cannot block forever: a wedged child must fail, not hang."""
    ready, _, _ = select.select([stream], [], [], timeout)
    assert ready, f"the helper process printed nothing within {timeout}s"
    return stream.readline()


_FORK_CHILD = """
import os, sys
sys.path.insert(0, {root!r})
import tortoise.embedded_lifecycle as el
key = {key!r}
# Hold the lock, then fork FROM INSIDE it — the exact state the `after_in_child`
# hook is for. A fresh interpreter (what the first version of this test used)
# starts with an empty registry, so it could never detect the reset being
# removed: it passed with the reset deleted.
with el._construction_lock(key):
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            # Read the module ATTRIBUTE, never a `from ... import` alias taken
            # before the fork: the hook REBINDS the name to a fresh dict, so an
            # alias would still point at the parent's dict and this check would
            # report an inherited lock that the child does not actually have.
            # Every code below is exactly 2 bytes, because the parent reads 2.
            if el._CONSTRUCT_LOCKS:
                # An inherited entry is an RLock whose owner thread does not
                # exist in this child: its next construction of that key blocks
                # forever, and the inherited lock-file fd would let the child
                # LOCK_UN the parent's flock on the way out.
                os.write(w, b"no")
            else:
                # A DIFFERENT key: the parent's flock on `key` is still held and
                # is correctly shared across the fork, so the child must not
                # touch it.
                with el._construction_lock(key + ".child"):
                    os.write(w, b"ok")
        except BaseException:
            os.write(w, b"er")
        finally:
            os._exit(0)
    os.close(w)
    os.waitpid(pid, 0)
    os.write(1, os.read(r, 2))
"""


def test_a_forked_child_is_not_wedged_by_an_inherited_lock(tmp_path):
    """#4926's hazard, on the third registry: a child must not inherit a held lock.

    Asserts the child's INHERITED STATE rather than racing the parent for the
    same `${key}`: the parent's flock is genuinely shared across the fork, so a
    child that took the same key would (correctly) wait for the parent — while
    the parent waits for the child. What the `after_in_child` hook must prevent
    is the INHERITED-but-unreleasable state: an `RLock` whose owner thread does
    not exist in the child, and the module mutex the parent was holding. Bounded
    subprocess on purpose — without the reset the child blocks forever, and a
    deadlocked test would hang the suite instead of failing it.
    """
    if not hasattr(os, "fork"):
        pytest.skip("POSIX only")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key = _key(tmp_path)

    # Fork from a process that holds the lock, i.e. the exact state the hook is
    # for. `os.fork` here is the REAL one (a plain subprocess cannot reproduce
    # "parent holds the lock at fork time").
    holder = subprocess.Popen(
        [sys.executable, "-c", _FORK_CHILD.format(root=root, key=key)],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": root})
    try:
        out = holder.communicate(timeout=60)[0]
    except subprocess.TimeoutExpired:
        holder.kill()
        pytest.fail("the forked child hung — the `after_in_child` reset is missing")
    assert out == "ok", (
        f"child inherited a wedged/held construction lock (out={out!r}) — the "
        "`after_in_child` reset is missing or incomplete")


# ── the seam: the installer wraps the constructor that contains the gap ────


def test_installer_wraps_the_constructor_and_is_idempotent():
    import tortoise  # noqa: F401  (arms the redislite guard installer)
    redislite_client = pytest.importorskip("redislite.client")
    mixin = redislite_client.RedisMixin
    assert getattr(mixin, "_tortoise_construct_lock_guard", False), (
        "the construction lock is not installed — #4921's gap is open")
    first = mixin.__init__
    from tortoise.embedded_lifecycle import _install_construct_lock_guard
    _install_construct_lock_guard()
    assert mixin.__init__ is first, "a second install re-wrapped the constructor"


def test_missing_directory_yields_no_lock_instead_of_raising(tmp_path):
    """A gone dbdir is #3653's refusal to make, by name, not this guard's."""
    gone = str(tmp_path / "does-not-exist" / "db.rdb")
    assert _open_construct_lock(gone) == -1


# ── the race itself: one server over one RDB, forced to interleave ───────
#
# #4921's Acceptance asks for exactly this and warns that a guard which cannot
# be shown to fail is not evidence: "A test that fails before the lock and
# passes after, asserting ONE LIVE SERVER OVER ONE RDB under a forced
# interleave — not merely 'no exception'." The two tests below are that pair.
# They construct real embedded servers, so they belong to the URI-unset
# carve-out lane and skip under the docker redirect, where the seam under test
# is never reached.


def _run_id(client) -> str:
    """The identity of the server `client` is actually talking to.

    redislite's client installs a response callback that parses INFO into a
    nested dict (`{"server": {"run_id": ...}}`), so this reads that shape and
    falls back to the raw bulk string.
    """
    info = client.execute_command("INFO", "server")
    if isinstance(info, bytes):
        info = info.decode()
    if isinstance(info, dict):
        for value in info.values():
            if isinstance(value, dict) and "run_id" in value:
                return value["run_id"]
        if "run_id" in info:
            return info["run_id"]
        raise AssertionError(f"no run_id in parsed INFO: {sorted(info)}")
    for line in info.splitlines():
        if line.startswith("run_id:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("no run_id in INFO server")


def _forced_interleave_constructs(tmp_path, monkeypatch, *, guard: bool) -> list:
    """Construct one RDB twice, forcing both reads to happen before either start.

    The patch sits ON THE READ (`_is_redis_running`) and makes a `False` verdict
    wait at a bounded barrier for a peer. That is the window #4921 is about: if
    both reads return False before either construction starts, both will start a
    server unless something serialises them. `guard=False` neutralises the lock
    by making the key underivable, which is what the wrapper consults.

    reproduced, so the red-half assertion below can distinguish "could not
    force the interleave" (skip — inconclusive) from "the defect did not
    reproduce" (fail).

    Returns `(clients, forced)`.
    """
    if os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("docker redirect: the embedded seam is not reached")
    redislite_client = pytest.importorskip("redislite.client")
    from redislite import Redis

    import tortoise  # noqa: F401  (arms the redislite guard installer)
    from tortoise import embedded_lifecycle

    if not guard:
        monkeypatch.setattr(embedded_lifecycle, "_construction_key", lambda *a, **k: None)

    rdb = str(tmp_path / "interleave.rdb")
    original = redislite_client.RedisMixin._is_redis_running
    # Generous on purpose: this barrier is not a synchronisation primitive the
    # test depends on for correctness, it is the mechanism that FORCES the
    # window — and a short timeout turns a loaded box into a FALSE FAILURE of
    # the test that is supposed to be the evidence. If it still breaks, that is
    # reported (see `forced`) rather than guessed at.
    barrier = threading.Barrier(2, timeout=20)
    forced = []

    def rendezvous(self):
        running = original(self)
        if not running:
            try:
                barrier.wait()          # both reads False before either start
                forced.append(True)
            except threading.BrokenBarrierError:
                pass
        return running

    monkeypatch.setattr(redislite_client.RedisMixin, "_is_redis_running", rendezvous)

    clients, errors = [], []

    def build():
        try:
            clients.append(Redis(rdb))
        except Exception as exc:        # a construction that fails still counts
            errors.append(exc)

    # `daemon=True`: a construction can block indefinitely in `flock` if the
    # guard is broken, and a non-daemon thread would be joined again by
    # `threading._shutdown` at interpreter exit — hanging the suite instead of
    # failing it, which is the one thing this file's tests must not do.
    threads = [threading.Thread(target=build, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
        assert not t.is_alive(), "a construction hung"
    assert len(clients) == 2, f"constructions failed: {errors!r}"
    return clients, bool(forced)


def test_forced_interleave_starts_exactly_one_server_over_one_rdb(tmp_path, monkeypatch):
    """#4921 green: with the construction lock, both clients share ONE server."""
    clients, _forced = _forced_interleave_constructs(tmp_path, monkeypatch, guard=True)
    try:
        # `forced` is expected to be FALSE here and that is not a shortfall: with
        # the guard working, the second thread cannot reach the read while the
        # first holds the lock, so the barrier never fills — the barrier filling
        # AT ALL is only possible in the unguarded arm below, which is why the
        # red half is the arm that can be gated on it. This arm asserts the
        # OUTCOME (one server) under a concurrent construction, not the window.
        ids = {_run_id(c) for c in clients}
        assert len(ids) == 1, (
            f"two servers were started over one RDB (run_ids={ids}) — the "
            "construction lock did not serialise the check-then-act span")
    finally:
        for c in clients:
            with contextlib.suppress(Exception):
                c.shutdown(nosave=True)


def test_without_the_lock_two_servers_can_start_over_one_rdb(tmp_path, monkeypatch):
    """#4921 red, kept as evidence: neutralise the guard and the bug reproduces.

    This is the half that makes the pair evidence rather than assertion — if the
    guard were somehow always in force, this test fails instead of quietly
    passing. It pins the DEFECT, so it is expected to keep failing once the
    defect is properly impossible (a different mechanism than this lock);
    delete it then, with the replacement named.
    """
    clients, forced = _forced_interleave_constructs(tmp_path, monkeypatch, guard=False)
    try:
        if not forced:
            pytest.skip(
                "the interleave was not achieved (the barrier broke) — the "
                "defect cannot be observed without it")
        ids = {_run_id(c) for c in clients}
        assert len(ids) == 2, (
            f"expected the unguarded race to start two servers over one RDB, got "
            f"{len(ids)} (run_ids={ids}) — either the interleave did not "
            f"reproduce or something else now serialises constructions")
    finally:
        for c in clients:
            with contextlib.suppress(Exception):
                c.shutdown(nosave=True)
