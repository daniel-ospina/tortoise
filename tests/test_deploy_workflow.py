"""Deploy-workflow ↔ runtime-registry parity guard (#1197, PR #1220 review P2 c70).

The provider gate and Fly secrets-propagation in
``.github/workflows/deploy-hosted.yml`` hardcode the LLM provider key names
(``OPENROUTER/DEEPSEEK/OPENAI/GEMINI_API_KEY``). The runtime registry
(``hosted_api._LLM_PROVIDER_KEYS``, derived from ``ingest._PROVIDERS`` /
``analyze._LLM_PROVIDERS``) is the source of truth the 503 gate actually
consumes. A rename in the registry that is not mirrored in the workflow drifts
SILENTLY: the deploy gate keeps passing (the GH secret name still matches) but
the key never propagates to Fly — every capture 503s with zero failing tests.

These tests read the workflow file and assert BOTH the verify-secrets gate and
the secrets-set propagation use EXACTLY the runtime registry key set (both
directions: no missing key, no extra key), AND that each key is actually
fail-closed in the gate and actually appended to the Fly secrets ARGS — a key
merely *referenced* (echoed, commented, gated on with wrong semantics) passes
name parity but still ships a 503-on-every-capture deploy. Anchored to the
#1197 marker comments so a moved block fails loudly instead of silently
passing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy-hosted.yml"

# Anchors inside the workflow (the #1197 blocks). Slicing between markers keeps
# the scan scoped: RESEND_API_KEY etc. live in other blocks and must not leak in.
_GATE_START = "# Session capture LLM provider (#1197, #1346)"
_GATE_END = 'if [ -z "${{ secrets.STRIPE_PRICE_IDS }}" ]; then'
_PROP_START = "# Session-capture LLM provider (#1197)"
_PROP_END = "# Optional (env-gated at runtime)"


def _region(text: str, start: str, end: str) -> str:
    """Slice ``text`` between two unique anchor substrings (exclusive end)."""
    s = text.index(start)  # ValueError → anchor moved; the test must fail loudly
    e = text.index(end, s)
    return text[s:e]


def _key_names(region: str) -> set[str]:
    """Every ``secrets.<NAME>`` referenced inside a workflow region."""
    return set(re.findall(r"secrets\.([A-Z0-9_]+)", region))


@pytest.fixture(scope="module")
def registry_keys() -> frozenset[str]:
    """The runtime registry — imported lazily so merely collecting this test
    module never imports the FastAPI app (review P2: keep collection cheap)."""
    from tortoise.hosted_api import _LLM_PROVIDER_KEYS

    return frozenset(_LLM_PROVIDER_KEYS)


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert _WORKFLOW.is_file(), f"deploy workflow not found: {_WORKFLOW}"
    return _WORKFLOW.read_text(encoding="utf-8")


def test_verify_secrets_gate_matches_runtime_registry(workflow_text, registry_keys):
    """The deploy gate's key set == _LLM_PROVIDER_KEYS, with the
    warn-only shape (::warning::, no exit 1) intact — the #1346 decision:
    session capture is optional and degrades to a loud 503 server-side, so
    a missing key must warn, NOT block the whole API deploy.

    A rename in the registry must force an update here, else the gate checks
    a stale name while the app 503s (gate passes, key never consumed)."""
    gate = _region(workflow_text, _GATE_START, _GATE_END)
    gate_keys = _key_names(gate)
    assert gate_keys, "no secrets.<KEY> found in the verify-secrets provider block — marker drift"
    assert gate_keys == set(registry_keys), (
        f"deploy gate keys {sorted(gate_keys)} != runtime registry "
        f"{sorted(registry_keys)} — a registry rename not mirrored here lets "
        f"the deploy gate pass while the app 503s every capture "
        f"(docs/infra-runbook.md §4.6)"
    )
    # Semantics (#1346/#1347): the LLM provider gate is WARN-ONLY — a missing
    # key must NOT block the API deploy (session capture is optional, degrades
    # to a loud 503 server-side). Assert the ::warning:: shape, not the old
    # fail-closed exit-1 shape.
    assert "::warning::" in gate and "::error::" not in gate, (
        "verify-secrets LLM gate must be warn-only (::warning::, no ::error::) "
        "— a fail-closed gate here blocks ALL API deploys (#1346)"
    )


def test_secrets_set_propagation_matches_runtime_registry(workflow_text, registry_keys):
    """The secrets-set step propagates EXACTLY the runtime registry key set.

    The gate and propagation must agree with the registry — a key gated on but
    never propagated ships a deploy that 503s despite a passing gate."""
    prop = _region(workflow_text, _PROP_START, _PROP_END)
    prop_keys = _key_names(prop)
    assert prop_keys, "no secrets.<KEY> found in the secrets-set provider block — marker drift"
    assert prop_keys == set(registry_keys), (
        f"Fly secrets propagation keys {sorted(prop_keys)} != runtime registry "
        f"{sorted(registry_keys)} — a registry rename not mirrored here ships "
        f"the gate passing while the key never reaches Fly (503-on-every-capture)"
    )
    # Semantics: each registry key must be APPENDED to the flyctl ARGS — a key
    # merely referenced (echoed or commented) passes name parity but still never
    # reaches Fly. Matches the actual propagation line shape:
    #   [ -n "${{ secrets.KEY }}" ] && ARGS="$ARGS KEY=${{ secrets.KEY }}"
    for key in registry_keys:
        assert re.search(rf"ARGS=\"\$ARGS\s+{re.escape(key)}=", prop), (
            f"{key} is referenced in the secrets-set block but never appended to "
            f"the flyctl ARGS — the key would stay on GitHub secrets and never "
            f"reach Fly (503-on-every-capture with a passing deploy)"
        )


def test_gate_and_propagation_agree_with_each_other(workflow_text, registry_keys):
    """Gate ⊆ propagation ⊆ gate — the two blocks can never diverge."""
    gate = _region(workflow_text, _GATE_START, _GATE_END)
    prop = _region(workflow_text, _PROP_START, _PROP_END)
    assert _key_names(gate) == _key_names(prop) == set(registry_keys), (
        "verify-secrets gate and secrets-set propagation reference DIFFERENT key "
        "sets — a key gated on but not propagated (or vice versa) is a deploy hazard"
    )
