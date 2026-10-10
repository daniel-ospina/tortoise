"""#6869 — ``commit_session(base_url=..., api_key=...)`` does NOT select the
extraction provider.

The defect (measured on ``origin/main@5198af3a7``): ``base_url``/``api_key``
reach exactly one call site — ``_post_commit``, the **Tortoise API** POST —
while the LLM that reads the conversation is built on a different credential
plane: ``extractor_model`` when given, and the ambient environment
(``_default_byok_model`` → ``build_extractor_model``, env-routed) otherwise.
A caller that reads those parameter names as BYOK controls therefore still
extracts — and bills — on the deployment's provider account, and egresses the
conversation to a provider it never named.

Reproduction before the fix::

    sdk.commit_session(summary=..., mode="warn",
                       base_url="http://unused", api_key="k")

issued ``POST https://openrouter.ai/api/v1/chat/completions`` (401) — the
supplied endpoint and key never entered the extraction path, and the call
was made on whatever key the shell held. The in-tree test
``test_value_extractor.py::TestClosedVocab::test_commit_session_warn_mode_
reaches_payload`` made exactly that wrong inference, which is why it is
repaired alongside this module.

Remedy = option 2 of the issue (document + warn): the public SDK surface is
NOT changed. Option 1 (new ``extractor_base_url``/``extractor_api_key``
parameters) is an SDK-surface change and needs owner approval before it is
written (AGENTS.md "MCP/SDK Surface Approval"), so it is deliberately out of
scope here — see the issue's surface note.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tortoise.sdk as sdk_mod
from tests.test_value_extractor import MockModel
from tortoise.sdk import TortoiseSDK

# A non-vocab summary window so the v1 direct-``summary=`` path is reached
# without any extraction call being needed for the payload itself.
_SUMMARY = {
    "session": {"summary": "S"},
    "state": [{"name": "artifact", "objectKind": "core:goal"}],
    "decisions": [], "logic": [], "issues": [],
}


@pytest.fixture()
def ambient_model(monkeypatch):
    """Record every build of the AMBIENT extractor adapter, and stub the POST.

    The recorder is the measurement: if ``base_url``/``api_key`` really
    selected the extraction provider, ``_default_byok_model`` would never be
    reached. Stubbing ``_post_commit`` keeps the test off the network.
    """
    built: list[bool] = []

    def _spy():
        built.append(True)
        return MockModel()

    monkeypatch.setattr(sdk_mod, "_default_byok_model", _spy)
    monkeypatch.setattr(
        sdk_mod, "_post_commit", lambda *a, **k: {"ok": True, "warnings": []})
    return built


def _commit(**kwargs):
    sdk = object.__new__(TortoiseSDK)  # no graph init needed (summary path)
    return sdk.commit_session(summary=dict(_SUMMARY), **kwargs)


def test_base_url_and_api_key_never_select_the_extraction_provider(ambient_model):
    """The measured defect: POST credentials are supplied, ambient model built."""
    out = _commit(base_url="http://unused", api_key="k")

    assert ambient_model, (
        "base_url/api_key must NOT suppress the ambient extractor build — "
        "they govern only the Tortoise API POST")
    assert out["ok"] is True, out


@pytest.mark.parametrize("kwargs", [
    {"base_url": "http://unused"},
    {"api_key": "k"},
    {"base_url": "http://unused", "api_key": "k"},
])
def test_warns_when_post_credentials_meet_ambient_extraction(ambient_model, kwargs):
    """Either credential alone is enough to trigger the divergence notice."""
    with pytest.warns(UserWarning, match=r"does NOT select the extraction provider"):
        _commit(**kwargs)


def test_no_warning_when_extractor_model_is_supplied(ambient_model):
    """An explicit adapter IS the control — nothing is silent about it."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = _commit(extractor_model=MockModel(),
                      base_url="http://unused", api_key="k")

    assert out["ok"] is True, out
    assert not ambient_model, "an explicit extractor_model must win over ambient"


def test_no_warning_when_no_post_credentials_are_supplied(ambient_model):
    """A bare ambient commit has no misleading signal to warn about."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = _commit()

    assert out["ok"] is True, out


def test_no_warning_when_capture_consent_declines(ambient_model, monkeypatch):
    """The notice sits AFTER the consent gate — a declined call spends nothing."""
    import tortoise.capture_consent as cc

    monkeypatch.setattr(cc, "capture_declined_reason",
                        lambda: "declined (#3615)")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = _commit(base_url="http://unused", api_key="k")

    assert out["ok"] is False, out
    assert out["errors"] == ["declined (#3615)"]
    assert not ambient_model, "a declined call must not build a model at all"


def test_warning_message_documents_scope_and_remedy(ambient_model):
    """The message must name what the parameters DO and how to control extraction."""
    with pytest.warns(UserWarning) as record:
        _commit(base_url="http://unused")

    msg = str(record[0].message)
    assert "POST credentials only" in msg
    assert "does NOT select the extraction provider" in msg
    assert "extractor_model" in msg
    assert "#6869" in msg


def test_docstring_documents_post_only_scope():
    """The docstring is the discoverable half of the remedy (option 2)."""
    doc = " ".join((TortoiseSDK.commit_session.__doc__ or "").split())

    assert "Tortoise API POST" in doc
    assert "do NOT select the extraction provider" in doc
    assert "TORTOISE_EXTRACTOR_PROVIDER" in doc
    assert "#6869" in doc
