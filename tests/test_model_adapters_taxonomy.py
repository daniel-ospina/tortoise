"""Error-class taxonomy unit tests (#1530 P2 — the M2/M3 export contract).

Pins the classification table from the #1530 plan exhaustively: every status
code in each frozenset → its class, unknown 4xx/5xx fallbacks, the transport
exception classes, and the is_transient/is_fatal boolean contract. M2 (pre-
flight) and M3 (retry/backoff) import from tortoise.model_adapters — do not
fork; these tests freeze the contract.
"""
from __future__ import annotations

from urllib import error as urllib_error

import pytest
import requests

from tortoise.model_adapters import (
    FATAL_CONFIG_STATUS_CODES,
    FATAL_STATUS_CODES,
    TRANSIENT_STATUS_CODES,
    LlmErrorClass,
    classify_llm_error,
    is_fatal,
    is_transient,
)


def _http_error(status: int) -> requests.HTTPError:
    """requests.HTTPError carrying a response with the given status."""
    class _FakeResp:
        status_code = status

    err = requests.HTTPError(f"HTTP {status}")
    err.response = _FakeResp()
    return err


def _urllib_http_error(status: int) -> urllib_error.HTTPError:
    return urllib_error.HTTPError(url="https://x", code=status, msg="err",
                                   hdrs={}, fp=None)


# ── every status code in each frozenset → its class (parameterized) ─────────

@pytest.mark.parametrize("status", sorted(FATAL_STATUS_CODES))
def test_fatal_status_codes(status):
    assert classify_llm_error(_http_error(status)) is LlmErrorClass.FATAL
    assert classify_llm_error(_urllib_http_error(status)) is LlmErrorClass.FATAL


@pytest.mark.parametrize("status", sorted(FATAL_CONFIG_STATUS_CODES))
def test_fatal_config_status_codes(status):
    assert classify_llm_error(_http_error(status)) is LlmErrorClass.FATAL_CONFIG
    assert classify_llm_error(_urllib_http_error(status)) is LlmErrorClass.FATAL_CONFIG


@pytest.mark.parametrize("status", sorted(TRANSIENT_STATUS_CODES))
def test_transient_status_codes(status):
    assert classify_llm_error(_http_error(status)) is LlmErrorClass.TRANSIENT
    assert classify_llm_error(_urllib_http_error(status)) is LlmErrorClass.TRANSIENT


# ── fallbacks: unknown 4xx → FATAL_CONFIG, unknown 5xx → TRANSIENT ─────────

@pytest.mark.parametrize("status", [409, 413, 415, 422, 451])
def test_unknown_4xx_is_fatal_config(status):
    """Unknown 4xx = deterministic client error — never retry (the "no
    flip-flop on 4xx" rule)."""
    assert classify_llm_error(_http_error(status)) is LlmErrorClass.FATAL_CONFIG


@pytest.mark.parametrize("status", [501, 505, 507, 599])
def test_unknown_5xx_is_transient(status):
    """Unknown 5xx = server-side, may clear — retry/failover eligible."""
    assert classify_llm_error(_http_error(status)) is LlmErrorClass.TRANSIENT


# ── transport classes ───────────────────────────────────────────────────────

def test_requests_connection_error_transient():
    assert classify_llm_error(requests.ConnectionError("boom")) is LlmErrorClass.TRANSIENT


def test_requests_timeout_transient():
    assert classify_llm_error(requests.Timeout("slow")) is LlmErrorClass.TRANSIENT


def test_urllib_urlerror_transient():
    assert classify_llm_error(urllib_error.URLError("dns")) is LlmErrorClass.TRANSIENT


def test_socket_timeout_transient():
    assert classify_llm_error(TimeoutError("t")) is LlmErrorClass.TRANSIENT


def test_plain_timeout_error_transient():
    """TimeoutError from extractor_v2._complete's thread deadline (#1530)."""
    assert classify_llm_error(TimeoutError("deadline")) is LlmErrorClass.TRANSIENT


def test_network_oserror_transient():
    """OSError with a network errno (ECONNRESET/ECONNREFUSED/…) — the #1350
    collapse class (errno constants, platform-independent)."""
    import errno as errno_mod
    for err in (errno_mod.ECONNRESET, errno_mod.ECONNREFUSED,
                errno_mod.ETIMEDOUT):
        assert classify_llm_error(
            OSError(err, "conn")) is LlmErrorClass.TRANSIENT


def test_non_network_oserror_unknown():
    """A non-network OSError (e.g. ENOENT) is NOT auto-transient."""
    import errno as errno_mod
    exc = OSError(errno_mod.ENOENT, "no such file")
    assert classify_llm_error(exc) is LlmErrorClass.UNKNOWN


def test_generic_exception_unknown():
    """Non-HTTP anything else (parse errors, KeyError on body) → UNKNOWN."""
    for exc in (ValueError("no JSON block"), KeyError("choices"),
                RuntimeError("boom"), Exception("generic")):
        assert classify_llm_error(exc) is LlmErrorClass.UNKNOWN


# ── boolean contract ────────────────────────────────────────────────────────

def test_is_transient_boolean_contract():
    """is_transient True → M3 may retry, P2 may fail over. TRANSIENT and
    UNKNOWN (transient-safe) are True; FATAL / FATAL_CONFIG are False."""
    assert is_transient(_http_error(429)) is True
    assert is_transient(_http_error(503)) is True
    assert is_transient(requests.ConnectionError("x")) is True
    assert is_transient(Exception("unknown")) is True      # UNKNOWN → safe
    assert is_transient(_http_error(401)) is False
    assert is_transient(_http_error(400)) is False
    assert is_transient(_http_error(422)) is False


def test_is_fatal_boolean_contract():
    """is_fatal True → M3 aborts immediately, P2 never fails over. FATAL and
    FATAL_CONFIG are True; TRANSIENT / UNKNOWN are False."""
    assert is_fatal(_http_error(401)) is True
    assert is_fatal(_http_error(402)) is True
    assert is_fatal(_http_error(403)) is True
    assert is_fatal(_http_error(400)) is True
    assert is_fatal(_http_error(404)) is True
    assert is_fatal(_http_error(422)) is True             # unknown 4xx
    assert is_fatal(_http_error(429)) is False
    assert is_fatal(requests.ConnectionError("x")) is False
    assert is_fatal(Exception("unknown")) is False


def test_classification_is_exhaustive_for_status_ranges():
    """Every status 400-599 lands in exactly one of the three classes."""
    for status in range(400, 600):
        cls = classify_llm_error(_http_error(status))
        assert cls in (LlmErrorClass.FATAL, LlmErrorClass.FATAL_CONFIG,
                       LlmErrorClass.TRANSIENT), status
