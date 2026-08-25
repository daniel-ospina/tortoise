"""Supplementary tests to close remaining coverage gaps in projection + models."""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tortoise.log import EventLog  # noqa: E402, I001, RUF100
from tortoise.projection import FalkorProjection  # noqa: E402, RUF100
from tortoise.models import OpenAICompatModel, OllamaModel  # noqa: E402, RUF100


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _skip_if_no_falkor():
    try:
        from redislite.falkordb_client import FalkorDB  # noqa: F401
        return False
    except ImportError:
        return True


# ── projection.py line 194-196: PointsMerged in rebuild_all pass 1 ──

def test_rebuild_all_with_merges():
    """rebuild_all correctly handles PointsMerged events."""
    if _skip_if_no_falkor():
        pytest.skip("embedded FalkorDBLite unavailable")
    d = tempfile.mkdtemp(prefix="tortoise_merge_")
    try:
        # Create a JSONL with PointAdded, then PointsMerged
        log_path = os.path.join(d, "events.jsonl")
        log = EventLog(log_path)
        from tortoise.ids import ulid, now_iso  # noqa: I001

        pid1 = ulid()
        pid2 = ulid()
        events = [
            {"event_id": ulid(), "ts": now_iso(), "type": "PointAdded",
             "initiated_by": "user", "agent_id": "test",
             "point": {"id": pid1, "content": "A", "context": "ctx",
                       "provenance": {}, "created_at": now_iso()}},
            {"event_id": ulid(), "ts": now_iso(), "type": "PointAdded",
             "initiated_by": "user", "agent_id": "test",
             "point": {"id": pid2, "content": "B", "context": "ctx",
                       "provenance": {}, "created_at": now_iso()}},
            {"event_id": ulid(), "ts": now_iso(), "type": "PointsMerged",
             "initiated_by": "user", "agent_id": "test",
             "keep_id": pid1, "merge_ids": [pid2]},
        ]
        for ev in events:
            log.append(ev)

        proj = FalkorProjection(_tmp("g_merge.db"), graph_name="test")
        try:
            result = proj.rebuild_all(d)
            assert result["nodes"] == 1  # pid2 merged away
            assert result["edges"] == 0
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    print("PASS test_rebuild_all_with_merges")


# ── projection.py line 251-252: scipy ImportError in compute_grounding ──

def test_compute_grounding_no_scipy():
    """compute_grounding raises ImportError when scipy is missing."""
    if _skip_if_no_falkor():
        pytest.skip("embedded FalkorDBLite unavailable")
    proj = FalkorProjection(_tmp("g_noscipy.db"), graph_name="test")
    try:
        proj._upsert({"id": "p1", "content": "hello", "context": "ctx"})
        import builtins
        _orig = builtins.__import__

        def _fake(name, *args, **kwargs):
            if "scipy" in name:
                raise ImportError("no scipy")
            return _orig(name, *args, **kwargs)

        builtins.__import__ = _fake
        try:
            proj.compute_grounding()
            assert False, "should have raised"  # noqa: B011
        except ImportError as e:
            assert "scipy" in str(e)
        finally:
            builtins.__import__ = _orig
    finally:
        proj.close()
    print("PASS test_compute_grounding_no_scipy")


# ── models.py lines 62-66, 99-104: complete() methods ──

def test_openai_complete_mocked():
    """OpenAICompatModel.complete with mocked HTTP."""
    import urllib.request  # noqa: I001
    import io  # noqa: F401

    model = OpenAICompatModel(id="test", base_url="http://localhost:9999",
                              api_key_env=None)

    # Build the request but mock the HTTP call
    body = json.dumps(model.build_request("sys", "usr")).encode()  # noqa: F841

    class FakeResponse:
        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": '{"result": "ok"}'}}]
            }).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    _orig_urlopen = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: FakeResponse()
    try:
        result = model.complete(system="sys", user="usr")
        assert result == '{"result": "ok"}'
    finally:
        urllib.request.urlopen = _orig_urlopen
    print("PASS test_openai_complete_mocked")


def test_ollama_complete_mocked():
    """OllamaModel.complete with mocked HTTP."""
    import urllib.request

    model = OllamaModel(id="test", think=False)

    class FakeResponse:
        def read(self):
            return json.dumps({
                "message": {"content": '{"result": "ok"}'}
            }).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    _orig_urlopen = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: FakeResponse()
    try:
        result = model.complete(system="sys", user="usr")
        assert result == '{"result": "ok"}'
    finally:
        urllib.request.urlopen = _orig_urlopen
    print("PASS test_ollama_complete_mocked")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall supplementary tests passed")


if __name__ == "__main__":
    _run_all()
