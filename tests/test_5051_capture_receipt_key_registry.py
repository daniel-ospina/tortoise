"""#5051: pin ``hosted_api``'s literal capture-key registry to the ONE key-format
definition in ``tortoise/capture_receipts.py``.

``tortoise/capture_receipts.py`` owns the receipt / last-error key FORMAT.
``tortoise/hosted_api.py`` additionally spells every per-harness key name out
literally in its two onboarding default-state dicts — ``DEFAULT_ONBOARDING_STATE``
(the live provisioning default) and ``_ONBOARDING_DEFAULT_STATE`` (the read-time
merge default). That enumeration is legitimate and must stay: the dashboard
needs a REGISTERED row per harness even before the first capture arrives, and
``capture_receipts.py`` owns only the format, not the harness list. But it is a
second source of truth for the format, and nothing else stops the two from
drifting — change the format and the literal registry keys silently stop
matching what the write path produces (and an unregistered key is dropped by the
``_update_onboarding_state`` allowlist filter, so the dashboard row just goes
blank).

These dicts store the per-harness keys in STATE-key spelling, hyphens included
(``session_capture_receipt_claude-desktop``) — they do NOT apply the
hyphen→underscore JSON-field transform. The underscore form
(``session_capture_receipt_claude_desktop``) lives only in the PATCH model /
``_PATCH_FIELD_TO_STATE_KEY`` / ``test_onboarding_endpoints._STATE_KEY_TABLE``.
The exact-set assertions below pin that too.
"""
from __future__ import annotations

import pytest

import tortoise.hosted_api as hosted_api
from tortoise.capture_receipts import capture_last_error_key, capture_receipt_key

# The two registries, by their real module-level names.
_REGISTRY_NAMES = ("DEFAULT_ONBOARDING_STATE", "_ONBOARDING_DEFAULT_STATE")

# Key-name FAMILIES, as static selectors. Deliberately NOT derived from the
# formatters under test: a selector that moves with the format could select
# nothing and pass vacuously.
_RECEIPT_FAMILY = "session_capture_receipt"
_LAST_ERROR_FAMILY = "session_capture_last_error"
_LAST_ERROR_PREFIX = _LAST_ERROR_FAMILY + "_"


def _family_keys(registry: dict) -> set[str]:
    """Every capture receipt / last-error key name the registry spells out."""
    return {
        key for key in registry
        if key.startswith(_RECEIPT_FAMILY) or key.startswith(_LAST_ERROR_FAMILY)
    }


def _registry_harnesses(registry: dict) -> set[str]:
    """Harness names the registry literally enumerates.

    Derived from the LAST-ERROR family, which has no bare variant, so every
    member carries exactly one harness suffix (``session_capture_last_error_`` +
    harness) and the suffix IS the harness. Hyphens are preserved — this is the
    state-key spelling, not the JSON-field spelling.
    """
    return {
        key[len(_LAST_ERROR_PREFIX):] for key in registry
        if key.startswith(_LAST_ERROR_PREFIX)
    }


@pytest.mark.parametrize("registry_name", _REGISTRY_NAMES)
def test_registry_harness_set_is_the_session_vocabulary(registry_name: str) -> None:
    """No harness extra, none missing: the literal registry enumerates exactly
    the cross-surface harness vocabulary — a harness added to
    ``_SESSION_HARNESS_VALUES`` without registering its rows (or a row left
    behind for a harness that no longer exists) fails here."""
    registry = getattr(hosted_api, registry_name)
    assert _registry_harnesses(registry) == set(hosted_api._SESSION_HARNESS_VALUES)


@pytest.mark.parametrize("registry_name", _REGISTRY_NAMES)
def test_registry_keys_match_the_capture_receipts_formatters(
        registry_name: str) -> None:
    """Every literal key name in the registry equals the formatter output for
    its harness — and the registry has neither an extra nor a missing key."""
    registry = getattr(hosted_api, registry_name)
    harnesses = _registry_harnesses(registry)
    assert harnesses, f"{registry_name} enumerates no harnesses"

    expected = (
        # The bare legacy no-harness receipt is a registered row like any other.
        {capture_receipt_key(None)}
        | {capture_receipt_key(h) for h in harnesses}
        # No bare last-error variant is registered.
        | {capture_last_error_key(h) for h in harnesses}
    )
    actual = _family_keys(registry)
    assert actual == expected, (
        f"{registry_name} drifted from tortoise.capture_receipts: "
        f"only-in-registry={sorted(actual - expected)}, "
        f"only-in-formatters={sorted(expected - actual)}"
    )


def test_both_registries_agree_and_hold_the_state_key_spelling() -> None:
    """The two default-state dicts are kept in lockstep, and both spell the
    hyphenated STATE key — the underscore JSON-field form belongs to the PATCH
    surface, never to the registry."""
    provision = hosted_api.DEFAULT_ONBOARDING_STATE
    read_merge = hosted_api._ONBOARDING_DEFAULT_STATE

    assert _family_keys(provision) == _family_keys(read_merge)
    assert _registry_harnesses(provision) == _registry_harnesses(read_merge)

    # The hyphenated harnesses really do keep their hyphen in the registry; the
    # underscored variant is NOT registered (and so has no dashboard row).
    for key in sorted(_family_keys(provision)):
        assert "claude_desktop" not in key and "claude_web" not in key, key
    assert "session_capture_receipt_claude-desktop" in provision
    assert "session_capture_receipt_claude_desktop" not in provision
