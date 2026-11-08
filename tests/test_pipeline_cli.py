"""Pipeline CLI tests — #715 P2 conf 75: TORTOISE_DB_URI routing.

`tortoise pipeline run` must route a supported TORTOISE_DB_URI (docker:// /
redis:// / rediss://) through FalkorProjection.from_uri() — never silently
resolve the embedded default while the URI points elsewhere (split graph).
"""
from __future__ import annotations

import os
import sys
import threading
import types
from unittest import mock  # noqa: F401

import pytest  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import pipeline_cli


def _write_config(tmp_path, name):
    cfg = tmp_path / "pipelines.yaml"
    cfg.write_text(
        "version: 1\npipelines:\n"
        f"  {name}:\n"
        "    enabled: true\n"
        "    connector:\n"
        f"      module: {name}\n"
        "      class: FakeConn\n"
        "      config: {}\n",
        encoding="utf-8",
    )
    return cfg


def _register_connector(monkeypatch, module_name, ingested=1):
    fake_mod = types.ModuleType(module_name)

    class FakeConn:
        def __init__(self, config=None):
            pass

        def poll(self):
            return ["e1"]

        def ingest(self, proj):
            return ingested

    fake_mod.FakeConn = FakeConn
    monkeypatch.setitem(sys.modules, module_name, fake_mod)


def _patch_projection(monkeypatch, seen):
    """Record whether cmd_run uses from_uri (URI) or __init__ (path).

    ⛔ RECORD ONLY THIS THREAD'S CONSTRUCTIONS — the patch is on the CLASS, so
    it is process-wide, while the verdict is about ONE test's ``cmd_run``. The
    suite runs beside threads it does not own: a hosted-api retention sweep
    leaked from an earlier ``TestClient`` test in the same xdist worker reaches
    ``_make_sdk`` → ``_resolve_embedded_db_path()``, whose unwritable-``/data``
    fallback is ``tempfile.gettempdir()/tortoise.db``, and constructs a
    ``FalkorProjection`` with it. Unfiltered, that foreign construction lands
    in ``seen`` and the assertion is made about ANOTHER thread's path.

    Measured 2026-11-08 (PR #11495, required check `test (f)`): ``seen["path"]``
    read ``/tmp/tt_nmrzy6yn/tortoise.db`` — TMPDIR, not ``~/.tortoise`` — while
    ``resolve_db_path()`` was correct; the captured log carried
    ``event retention sweep skipped for …: 'FalkorProjection' object has no
    attribute 'g'``. That attribute error is the sweep tripping over THIS
    patch, i.e. the proof that the write came from the sweep thread.
    """
    from tortoise.projection import FalkorProjection

    owner = threading.get_ident()

    class _FakeProj:
        def close(self):
            pass

    def _from_uri(cls, u, graph_name=None):
        if threading.get_ident() == owner:
            seen["uri"] = u
        return _FakeProj()

    def _init_path(self, path, *a, **k):
        if threading.get_ident() == owner:
            seen["path"] = path
        return None

    monkeypatch.setattr(FalkorProjection, "from_uri", classmethod(_from_uri))
    monkeypatch.setattr(FalkorProjection, "__init__", _init_path)


def test_run_routes_supported_uri_through_from_uri(monkeypatch, tmp_path, capsys):
    """TORTOISE_DB_URI=redis:// → FalkorProjection.from_uri(uri), never the
    embedded default."""
    uri = "redis://:pw@db.example.com:6379/tortoise"
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

    monkeypatch.setattr(pipeline_cli, "CONFIG_PATH", _write_config(tmp_path, "t1"))
    _register_connector(monkeypatch, "t1")
    seen: dict = {}
    _patch_projection(monkeypatch, seen)

    pipeline_cli.cmd_run("t1")

    assert seen.get("uri") == uri
    assert "path" not in seen  # embedded path never used
    assert "Ingested 1 entities" in capsys.readouterr().out


def test_run_uses_embedded_path_when_no_uri(monkeypatch, tmp_path):
    """No TORTOISE_DB_URI → embedded path via resolve_db_path() (unchanged)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

    monkeypatch.setattr(pipeline_cli, "CONFIG_PATH", _write_config(tmp_path, "t2"))
    _register_connector(monkeypatch, "t2", ingested=2)
    from tortoise.config import DEFAULT_DB_PATH
    seen: dict = {}
    _patch_projection(monkeypatch, seen)

    pipeline_cli.cmd_run("t2")

    assert "uri" not in seen
    assert seen.get("path") == DEFAULT_DB_PATH


def test_a_foreign_threads_construction_never_decides_this_verdict(
        monkeypatch, tmp_path):
    """The pin for the hold above: a `FalkorProjection` built — or routed — by a
    thread this test does not own must not enter `seen`.

    Driving it, not asserting a shape: the foreign constructions are made for
    real, on worker threads, with the same SHAPE a leaked hosted-api sweep
    produces — a temp-rooted non-canonical file for the `__init__` arm (**not**
    `tempfile.gettempdir()/tortoise.db` itself: that is what
    `_resolve_embedded_db_path()` falls back to when /data is unwritable
    (`tortoise/hosted_api.py` — cited by symbol, because that file's fallback
    moved under this comment once already), and the CI `TMPDIR` is the session root,
    so this pin uses its own `tmp_path` file — the assertion is about WHOSE
    construction is recorded, never about the path's prefix), and a `redis://`
    URI for the `from_uri` arm. Before the thread guards these overwrote `seen`
    and each sibling test's assertion was made about them; with the guards the
    recorded values are the ones `cmd_run` resolved. Deliberately constructed
    BEFORE the assertions, so the pin needs no sleep and no scheduling luck to
    fire.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
    monkeypatch.setattr(pipeline_cli, "CONFIG_PATH", _write_config(tmp_path, "t3"))
    _register_connector(monkeypatch, "t3", ingested=1)
    from tortoise.config import DEFAULT_DB_PATH
    from tortoise.projection import FalkorProjection
    seen: dict = {}
    _patch_projection(monkeypatch, seen)

    pipeline_cli.cmd_run("t3")

    foreign = str(tmp_path / "foreign-thread-store.db")
    foreign_uri = "redis://:pw@foreign.example.com:6379/foreign"
    errors: list[BaseException] = []

    def _construct_elsewhere():
        try:
            FalkorProjection(foreign)
        except BaseException as exc:  # pragma: no cover - reported, not raised
            errors.append(exc)

    def _route_elsewhere():
        try:
            FalkorProjection.from_uri(foreign_uri)
        except BaseException as exc:  # pragma: no cover - reported, not raised
            errors.append(exc)

    for target in (_construct_elsewhere, _route_elsewhere):
        t = threading.Thread(target=target, name=getattr(target, "__name__", "foreign"),
                             daemon=True)
        t.start()
        t.join(30)
        assert not t.is_alive(), f"the foreign {target.__name__} thread did not finish"

    assert not errors, errors
    # ⛔ BOTH arms, because the leak is symmetric: a stray thread reaching
    # `_make_sdk` while `TORTOISE_DB_URI` is set constructs through `from_uri`,
    # which would false-red `test_run_routes_supported_uri_through_from_uri`'s
    # `assert seen.get("uri") == uri`; a path-arm construction false-reds
    # `test_run_uses_embedded_path_when_no_uri`'s
    # `assert seen.get("path") == DEFAULT_DB_PATH` and its
    # `assert "uri" not in seen`. Neither sibling can see a foreign write on the
    # OTHER arm — that is why this test drives both. Naming the tests, not line
    # numbers, on purpose: a line number here re-stales on the next edit, which
    # is the `[pin-defensive-claim]` defect this file's own review removed from
    # `tests/test_runner_ports_6673.py`. Measured: deleting EITHER guard reds
    # THIS test and nothing else.
    assert seen.get("path") == DEFAULT_DB_PATH, (
        "a construction this test did not make was recorded as its verdict: "
        f"{seen!r}")
    assert "uri" not in seen, (
        "a from_uri this test did not call was recorded as its verdict: "
        f"{seen!r}")
