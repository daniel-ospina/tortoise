"""M7 error-census taxonomy tests (#1527, epic #1509).

The eval error vocabulary (``tools.longmem_eval.errors``) aligns to the P2
contract (``tortoise.model_adapters.classify_llm_error`` — live) for the
coarse class and adds the eval's own site dimension + eval-only classes
(parse / retries_exhausted / ingest).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402, RUF100

from tools.longmem_eval.errors import (  # noqa: E402, RUF100
    EVAL_ERROR_CLASSES,
    census_classes,
    class_for_ingest_error_text,
    classify_eval_error,
    eval_failure_class,
)


class _HttpErr(Exception):
    """An exception carrying an HTTP status (requests/urllib shape)."""

    def __init__(self, status):
        super().__init__(f"http {status}")
        self.response = type("R", (), {"status_code": status})()


class _UrllibErr(Exception):
    """urllib.error.HTTPError shape (code attribute, no response)."""

    def __init__(self, status):
        super().__init__(f"http {status}")
        self.code = status


def test_classify_eval_error_table():
    """D6 (M7 #1527): the P2 status-code semantics + the parse pre-branch,
    site-prefixed."""
    # 401/402/403 → fatal
    assert classify_eval_error(_HttpErr(401), site="reader") == "reader:fatal"
    assert classify_eval_error(_HttpErr(402), site="judge") == "judge:fatal"
    assert classify_eval_error(_HttpErr(403), site="reader") == "reader:fatal"
    # 400/404/422 (other 4xx) → fatal_config
    assert classify_eval_error(_HttpErr(400), site="reader") == \
        "reader:fatal_config"
    assert classify_eval_error(_HttpErr(404), site="reader") == \
        "reader:fatal_config"
    assert classify_eval_error(_HttpErr(422), site="judge") == \
        "judge:fatal_config"
    # 408/425/429/500/502/503/504 + other 5xx → transient
    for status in (408, 425, 429, 500, 502, 503, 504, 507):
        assert classify_eval_error(_HttpErr(status), site="reader") == \
            "reader:transient", status
    # urllib HTTPError shape (code attribute) classifies identically
    assert classify_eval_error(_UrllibErr(429), site="judge") == \
        "judge:transient"
    # connection/timeout/URLError → transient
    import requests
    assert classify_eval_error(requests.ConnectionError("down"),
                               site="reader") == "reader:transient"
    assert classify_eval_error(requests.Timeout("slow"),
                               site="judge") == "judge:transient"
    assert classify_eval_error(TimeoutError("t"), site="reader") == \
        "reader:transient"
    import urllib.error
    assert classify_eval_error(urllib.error.URLError("dns"),
                               site="reader") == "reader:transient"
    # non-HTTP body-shape errors → parse
    assert classify_eval_error(KeyError("answer"), site="reader") == \
        "reader:parse"
    import json
    assert classify_eval_error(json.JSONDecodeError("x", "doc", 0),
                               site="judge") == "judge:parse"
    assert classify_eval_error(TypeError("NoneType"), site="reader") == \
        "reader:parse"
    # everything else → unknown (transient-safe per P2)
    assert classify_eval_error(RuntimeError("mystery"), site="reader") == \
        "reader:unknown"
    # every produced class is in the documented vocabulary
    for exc, site in ((_HttpErr(401), "reader"), (_HttpErr(422), "reader"),
                      (_HttpErr(429), "reader"), (KeyError("k"), "reader"),
                      (RuntimeError("x"), "reader")):
        assert classify_eval_error(exc, site=site).split(":", 1)[1] in \
            EVAL_ERROR_CLASSES


def test_classify_eval_error_bad_site():
    with pytest.raises(ValueError, match="site"):
        classify_eval_error(RuntimeError("x"), site="nope")


def test_eval_failure_class_semantics():
    """D6 (M7 #1527) + #1776: the run-loop's final failure class —
    ingest-stage exceptions classify through the SAME taxonomy (transient /
    unknown → ``ingest:retries_exhausted`` — recoverable; fatal / parse →
    bare ``ingest`` — hard veto); transient-safe reader/judge errors →
    retries_exhausted; fatal/config/parse pass through."""
    # #1776: a transient/unknown at ingest grades retries_exhausted (a
    # single FalkorDB/network blip during ingest is recoverable, like the
    # identical reader/judge transients) — never a run-wide veto.
    assert eval_failure_class(_HttpErr(429), site="ingest") == \
        "ingest:retries_exhausted"
    import requests
    assert eval_failure_class(requests.ConnectionError("down"),
                              site="ingest") == "ingest:retries_exhausted"
    assert eval_failure_class(RuntimeError("extractor boom"),
                              site="ingest") == "ingest:retries_exhausted"
    # structurally-fatal / parse at ingest stay bare ``ingest`` (unchanged
    # hard veto — the extractor-internal failure is permanent by
    # construction): no loosening beyond the transient class.
    assert eval_failure_class(_HttpErr(401), site="ingest") == "ingest"
    assert eval_failure_class(KeyError("x"), site="ingest") == "ingest"
    # reader/judge transient (incl. P2-unknown = transient-safe) → exhausted
    assert eval_failure_class(_HttpErr(429), site="reader") == \
        "reader:retries_exhausted"
    assert eval_failure_class(RuntimeError("boom"), site="judge") == \
        "judge:retries_exhausted"
    # fatal / fatal_config / parse pass through unchanged
    assert eval_failure_class(_HttpErr(401), site="reader") == "reader:fatal"
    assert eval_failure_class(_HttpErr(400), site="judge") == \
        "judge:fatal_config"
    assert eval_failure_class(KeyError("body"), site="reader") == "reader:parse"
    # the eval-abort path: a fatal reader error is NOT retries_exhausted — it
    # is permanent (the M2 run-abort classification must stay distinguishable)
    assert eval_failure_class(_HttpErr(402), site="judge") == "judge:fatal"


def test_ingest_retries_exhausted_recoverable_allowlist():
    """#1776: ``ingest:retries_exhausted`` is in
    RECOVERABLE_EVAL_FAILURE_CLASSES (recoverable, rate-limited); bare
    ``ingest`` stays EXCLUDED (fail-closed — a structurally-fatal ingest
    failure still hard-vetoes)."""
    from tools.longmem_eval.report import RECOVERABLE_EVAL_FAILURE_CLASSES
    assert "ingest:retries_exhausted" in RECOVERABLE_EVAL_FAILURE_CLASSES
    assert "ingest" not in RECOVERABLE_EVAL_FAILURE_CLASSES
    # the exact-string match also fails a tampered suffix closed.
    assert "evil:retries_exhausted" not in RECOVERABLE_EVAL_FAILURE_CLASSES


def test_ingest_error_text_class():
    assert class_for_ingest_error_text("extractor timeout") == "ingest"


def test_census_classes():
    entries = ["reader:retries_exhausted", "judge:fatal", "ingest",
               "reader:retries_exhausted", "", None]
    assert census_classes(entries) == {
        "ingest": 1, "judge:fatal": 1, "reader:retries_exhausted": 2}
    assert census_classes([]) == {}
