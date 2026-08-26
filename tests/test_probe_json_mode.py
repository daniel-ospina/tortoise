"""#1746 (D6): JSON-mode honor probe — verdict logic unit tests.

Covers the D6 verdict rules (honored / ignored / rejected / inconclusive)
via scripted adapters — no network. The @slow live test is gated on the
direct-path key/provider env and skipped when absent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import probe_json_mode as probe  # noqa: E402, RUF100


class _Scripted:
    """Adapter scripted per scenario. ``on_text`` / ``off_text`` are the
    responses under TORTOISE_JSON_MODE=1 / =0; ``raise_http`` optionally
    raises an HTTP-400-shaped error for every call."""

    provider = "scripted"
    last_finish_reason = "stop"

    def __init__(self, on_text=None, off_text=None, raise_http: bool = False):
        self.on_text = on_text or "this is not json"
        self.off_text = off_text or "this is not json"
        self.raise_http = raise_http

    def complete(self, *, system, user, max_tokens=None):
        if self.raise_http:
            raise _HTTPError(400)
        if os.environ.get("TORTOISE_JSON_MODE", "1") == "1":
            return self.on_text
        return self.off_text


class _HTTPError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = type("R", (), {"status_code": status_code})()


_GOOD = ('{"entities": [], "events": [], "operators": [], '
         '"points": [{"content": "x", "pointKind": "statement"}]}')


def _run(adapter, n: int = 5) -> dict:
    return probe.probe_json_mode(adapter, n=n)


def test_probe_verdict_honored(monkeypatch):
    """Mode-on parses, mode-off doesn't → honored / mode_delta better; the
    JSON report shape carries both per-mode blocks."""
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result = _run(_Scripted(on_text=_GOOD))
    assert result["verdict"] == "honored"
    assert result["mode_delta"] == "better"
    assert result["mode_on"]["malformed"] == 0
    assert result["mode_off"]["malformed"] == 5
    assert result["mode_on"]["finish_reason"] == {"stop": 5}
    # the report serializes cleanly (the closing-run record consumes it)
    json.dumps(result)


def test_probe_verdict_ignored_identical(monkeypatch):
    """Both modes identically malformed → ignored (indistinguishable)."""
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result = _run(_Scripted(on_text="not json", off_text="not json"))
    assert result["verdict"] == "ignored"
    assert result["mode_delta"] == "same"


def test_probe_verdict_ignored_worse(monkeypatch):
    """A strictly WORSE mode-on rate is still ``ignored`` but records
    mode_delta: 'worse' — the harmful-direction signal is not lost."""
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result = _run(_Scripted(on_text="bad", off_text=_GOOD))
    assert result["verdict"] == "ignored"
    assert result["mode_delta"] == "worse"


def test_probe_verdict_rejected(monkeypatch):
    """Any HTTP 400/404 → rejected (the provider refuses the field; the
    run must not ship into wholesale-400s)."""
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result = _run(_Scripted(raise_http=True))
    assert result["verdict"] == "rejected"
    assert result["mode_on"]["http_400_404"] >= 1


def test_probe_verdict_inconclusive_both_zero(monkeypatch):
    """Both modes fully clean → inconclusive (n too small to distinguish an
    inert mode from a clean model — a false-honored would mislabel H1)."""
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result = _run(_Scripted(on_text=_GOOD, off_text=_GOOD))
    assert result["verdict"] == "inconclusive"


def test_probe_verdict_inconclusive_transient(monkeypatch):
    """A transient (non-400/404) error contaminates the signal →
    inconclusive."""

    class _Flaky(_Scripted):
        def complete(self, *, system, user, max_tokens=None):
            if os.environ.get("TORTOISE_JSON_MODE", "1") == "1" \
                    and getattr(self, "first", True):
                self.first = False
                raise ConnectionError("boom")
            return _GOOD

    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result = _run(_Flaky(on_text=_GOOD, off_text=_GOOD))
    assert result["verdict"] == "inconclusive"
    assert result["mode_on"]["transient"] >= 1


def test_probe_dry_run_scripted_honored(monkeypatch):
    """--dry-run uses the scripted adapter end-to-end (no network) and
    restores the TORTOISE_JSON_MODE env after the run."""
    monkeypatch.setenv("TORTOISE_JSON_MODE", "0")
    result = probe.probe_json_mode(None, n=3, dry_run=True)
    assert result["verdict"] == "honored"
    assert result["adapter"] == "dry-run-scripted"
    assert os.environ.get("TORTOISE_JSON_MODE") == "0"  # restored
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    result2 = probe.probe_json_mode(None, n=3, dry_run=True)
    assert result2["verdict"] == "honored"
    assert "TORTOISE_JSON_MODE" not in os.environ  # restored (was absent)


def test_probe_cli_dry_run(tmp_path, monkeypatch):
    """The CLI --dry-run path writes the verdict JSON to --out."""
    out = tmp_path / "probe.json"
    rc = probe.main(["--n", "3", "--dry-run", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verdict"] == "honored"
    assert data["probe_date"]


def test_probe_cli_rejects_n_zero():
    with pytest.raises(SystemExit):
        probe.main(["--n", "0", "--dry-run"])


@pytest.mark.slow
def test_probe_live_direct_path():
    """Real keys, gated: exercises the PILOT's path (the direct DeepSeek
    adapter per the D6 selection rule). Skips when the direct-path key or
    provider env is absent."""
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY absent — live probe gated")
    if os.environ.get("TORTOISE_EXTRACTOR_PROVIDER", "").strip().lower() \
            == "openrouter":
        pytest.skip("provider is openrouter — the probe targets the direct path")
    adapter = probe.resolve_probe_adapter(
        os.environ.get("TORTOISE_EXTRACT_MODEL"))
    result = probe.probe_json_mode(adapter, n=2)
    assert result["verdict"] in ("honored", "ignored", "inconclusive")
    assert "mode_on" in result and "mode_off" in result
