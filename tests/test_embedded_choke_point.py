"""#2204 regression pin — the embedded choke point creates its OWN data dir (#12363).

Why this file exists
--------------------
The published `tortoise-graph` 0.2.0 wheel predates #2204's `os.makedirs`, so
`tortoise init` on a clean machine exits 1 with redislite's raw

    *** FATAL CONFIG FILE ERROR (Redis 8.10.2) ***
    >>> 'dir '<home>/.tortoise''
    No such file or directory

#2204 FIXED that in `tortoise/__init__.py`'s guarded `FalkorDB.__init__`, and the
fix is what the wheel is missing (measured 2026-11-13: `grep -c makedirs` is 1 in
the tree, 0 in the published wheel). But nothing in `tests/` pinned the fix: the
only `makedirs` in the suite are `tests/_embedded.py`'s own copies, so a delete of
that one line reds no test — it just stops being true, and the next release ships
the same breakage. What this lane adds is a gate that can FAIL on it (#12363).

Hermetic on purpose
-------------------
`_build_guarded_falkordb` is handed a stand-in base class, so this exercises the
guard (path expansion, relative-path rejection, data-dir creation) without
starting a redis server: no Docker, no socket, no daemon left behind. The
`super().__init__` call is the only thing replaced — the code under test is the
shipped code.
"""

from __future__ import annotations

import os

import pytest

from tortoise import _build_guarded_falkordb


class _RecordingBase:
    """Stand-in for redislite's FalkorDB: records the forwarded arguments."""

    def __init__(self, *args, **kwargs):
        self.calls = [(args, kwargs)]


@pytest.fixture()
def guarded(monkeypatch):
    """The shipped guard over a stand-in base, with the post-init seams stubbed.

    `enforce_embedded_fork_safety` / the lifecycle registry act on a real
    redislite client (`execute_command`, a socket path). This test is about the
    path handling that runs BEFORE them, so they are no-ops here — the assertion
    would otherwise depend on machinery that has its own tests.
    """
    import atexit

    import tortoise as tortoise_pkg
    import tortoise.embedded_lifecycle as lifecycle

    monkeypatch.setattr(tortoise_pkg, "enforce_embedded_fork_safety", lambda *a, **k: None)
    # ⛔ The guard imports these INSIDE the constructor body, so they must be stubbed on
    # their own module — patching `tortoise.<name>` would miss the late import. Left live,
    # each construction registers the stand-in in the process-global weak registry (and a
    # bound atexit handler keeps it alive), so this file would leak entries for the whole
    # `core` shard — into a registry that a reaper reads as "live embedded clients"
    # (#12363 round 4). `atexit.register` is stubbed for the same reason: the handler binds
    # the instance.
    monkeypatch.setattr(lifecycle, "register_embedded_client", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "owner_socket_of", lambda *a, **k: None)
    monkeypatch.setattr(atexit, "register", lambda *a, **k: None)
    return _build_guarded_falkordb(_RecordingBase)


def test_a_missing_data_dir_is_created_before_the_server_reads_its_config(guarded, tmp_path):
    """The #2204 signature: a fresh machine has no `~/.tortoise` yet.

    RED when the `os.makedirs(data_dir or ".", exist_ok=True)` line in
    `tortoise/__init__.py` is removed or moved after `super().__init__`.
    """
    db_path = tmp_path / "fresh-home" / ".tortoise" / "tortoise.db"
    assert not db_path.parent.exists()

    guarded(str(db_path))

    assert db_path.parent.is_dir(), (
        "the embedded choke point no longer creates its data dir — a fresh "
        "machine's first `tortoise init` will die with redislite's "
        "'FATAL CONFIG FILE ERROR ... No such file or directory' (#2204/#12363)"
    )


def test_the_data_dir_is_created_before_the_base_class_runs(guarded, tmp_path, monkeypatch):
    """Order is the fix. If the dir is created AFTER `super().__init__`, the
    server config is already read and the failure is back — so assert the
    directory exists AT THE MOMENT the base class is entered."""
    db_path = tmp_path / "nested" / "deeper" / "tortoise.db"
    seen = {}

    class _OrderBase(_RecordingBase):
        def __init__(self, *args, **kwargs):
            seen["parent_exists"] = os.path.isdir(os.path.dirname(args[0]))
            super().__init__(*args, **kwargs)

    class_probe = _build_guarded_falkordb(_OrderBase)
    # Reuse the fixture's monkeypatched seams: build again through `guarded`'s
    # module so the fork-safety stub applies.
    class_probe(str(db_path))

    assert seen["parent_exists"] is True, (
        "the data dir was created only after the server config was read — the "
        "#2204 ordering regression"
    )


def test_an_existing_data_dir_is_not_an_error(guarded, tmp_path):
    """Idempotent (`exist_ok=True`): `tortoise doctor` opens an existing graph."""
    db_path = tmp_path / "home" / ".tortoise" / "tortoise.db"
    db_path.parent.mkdir(parents=True)

    guarded(str(db_path))

    assert db_path.parent.is_dir()


def test_a_tilde_path_is_expanded_and_still_creates_its_dir(guarded, tmp_path, monkeypatch):
    """redislite derives its config `dir` verbatim from the path and never
    expands `~` (the #2204 review finding), so the guard must."""
    monkeypatch.setenv("HOME", str(tmp_path))
    guarded("~/tilde-graph/tortoise.db")

    assert (tmp_path / "tilde-graph").is_dir()


def test_a_relative_path_is_still_rejected(guarded, tmp_path, monkeypatch):
    """The #2204 fix must not weaken the Category-3 leak guard: a per-CWD
    relative db is refused, and no directory is created for it."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):
        guarded("relative.db")
    assert not (tmp_path / "relative.db").exists()


def test_memory_is_exempt(guarded, tmp_path):
    """`:memory:` is not a path — no directory is invented for it."""
    before = set(os.listdir(tmp_path))
    guarded(":memory:")
    assert set(os.listdir(tmp_path)) == before


def test_the_fixture_does_not_leak_a_client_into_the_lifecycle_registry(guarded, tmp_path):
    """The choke-point tests construct the guard 6 times in the `core` shard; a live
    registration (and its atexit handler) would outlive them and be read as live embedded
    clients by anything that reaps those — a detector measuring the test suite's own
    leftovers (#12363 round 4)."""
    from tortoise import embedded_lifecycle

    # A DEFAULT here would make the pin vacuous: if the registry is renamed or removed,
    # `before == after == 0` and the test passes while the leak it claims to catch lives
    # on. The attribute must EXIST for the assertion to mean anything (#12363 round 5).
    assert hasattr(embedded_lifecycle, "_embedded_clients"), (
        "embedded_lifecycle no longer exposes _embedded_clients — this pin's subject moved"
    )
    before = len(embedded_lifecycle._embedded_clients)
    # HOLD the instances: the registry is WEAK, so a dropped reference would collect the
    # client and hide a live registration behind refcounting — which is exactly how this
    # test first passed under a mutation (#12363 round 4).
    kept = [
        guarded(str(tmp_path / f"home{index}" / ".tortoise" / "tortoise.db")) for index in range(3)
    ]
    after = len(embedded_lifecycle._embedded_clients)
    assert len(kept) == 3
    assert after == before, (
        f"{after - before} stand-in client(s) registered in embedded_lifecycle — the "
        f"fixture must stub register_embedded_client/atexit alongside fork safety"
    )
