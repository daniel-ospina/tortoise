"""Canonical pin for the LLM **provider-routing** environment (#3699).

The session-extraction routing gates read the process environment, and pytest
runs one process: a developer shell that exports a routing value decides the
result of any test that does not neutralize it. That is the #3699 class — five
``test_capture_session.py`` gate/failover tests red under an ambient
``TORTOISE_EXTRACTOR_PROVIDER`` / ``VENICE_API_KEY`` while green in CI.

The per-test ``monkeypatch.delenv`` lists those tests carry are HAND-LISTS, and
they drifted from the gates: the pinned pair (``OPENROUTER_API_KEY`` +
``DEEPSEEK_API_KEY``) is only part of what
``model_adapters.resolve_extractor_provider()`` reads — an ambient
``TORTOISE_EXTRACTOR_PROVIDER=openrouter`` flips the primary, and an ambient
``VENICE_API_KEY`` flips "no routing-usable provider" into "route to venice",
so the gate raises (or does not raise) for a reason that has nothing to do with
the test's subject.

This module derives the name set from the REGISTRIES the gates themselves read,
so it cannot drift the way a literal list does:

- ``tortoise.hosted_api._LLM_PROVIDER_KEYS`` — the key envs of every ingest /
  analyze provider the hosted availability check reports (the broad gate).
- ``tortoise.model_adapters._PROVIDER_KEY_ENV`` — the key envs of the v2
  adapter routing pool (``deepseek-direct`` / ``openrouter`` / ``venice``) used
  by ``resolve_extractor_provider()`` (the inner gate).
- the provider/lane SELECTOR envs, which are literals in ``sdk`` /
  ``model_adapters`` and have no registry to derive from.

⚠️ State of the two sets, so this docstring cannot be read as a claim about
``VENICE`` that the tree has outgrown: when #3695 was filed, ``VENICE_API_KEY``
was in ``_PROVIDER_KEY_ENV`` and NOT in the hosted set — that asymmetry IS how
the hand-lists drifted, and it is why the union below (not either set alone) is
the thing a test must scrub. Since #8133 registered ``venice`` in
``tortoise.ingest._PROVIDERS``, the hosted set carries ``VENICE_API_KEY`` too,
so the two derivations agree today; the union stays because a future
single-registry registration must not be able to hide behind the agreement.
``provider_key_envs()`` here is the ONE copy of that union: ``conftest`` and the
keyless-capture guard both call it rather than re-deriving it.

``TORTOISE_SESSION_LLM_MOCK`` is deliberately NOT pinned: it is the offline
extraction seam, not a routing input, and a module that pins the mock seam
(``test_capture_session.py``'s ``llm_extraction_provider``) would have its own
granularity undone by clearing it here. Tests that need the keyless path clear
the seam themselves, as they already do.

Usage — opt in per module (NOT suite-wide: files such as
``tests/test_ask_regression_llm.py`` deliberately read ambient live keys, so a
global clear would change what they test)::

    import pytest
    from tests._provider_env import pin_provider_routing_env

    @pytest.fixture(autouse=True)
    def _pinned_provider_routing_env(monkeypatch):
        pin_provider_routing_env(monkeypatch)
"""
from __future__ import annotations

#: Provider/lane SELECTOR envs — literals at their read sites
#: (``model_adapters.resolve_extractor_provider``, ``sdk._build_session_llm_extractor``,
#: ``sdk._extract_session_v2``'s V2-lane selector). No registry to derive from;
#: add here when a new selector is introduced.
_PROVIDER_SELECTOR_ENVS: tuple[str, ...] = (
    "TORTOISE_EXTRACTOR_PROVIDER",   # primary/fallback pool selector
    "TORTOISE_SESSION_LLM_MODEL",    # model override for the configured provider
    "TORTOISE_EXTRACT_MODEL",        # V2-lane model override
    "TORTOISE_SESSION_EXTRACTOR",    # M2-lane selector
)


def provider_key_envs() -> tuple[str, ...]:
    """Every provider API-KEY env var, derived from the two registries.

    The keys-only half of ``provider_routing_env_names()``, exported because a
    caller that pins the whole routing env set would also clear the four
    *selector* envs — and a keyless-capture guard that clears
    ``TORTOISE_SESSION_LLM_MOCK`` fails for the wrong reason. One copy: both
    ``tests/conftest.py::provider_key_envs`` and the keyless-capture guard in
    ``tests/test_session_extraction_modes.py`` call THIS function.
    """
    from tortoise.hosted_api import _llm_provider_keys
    from tortoise.model_adapters import _PROVIDER_KEY_ENV

    return tuple(sorted(
        set(_llm_provider_keys()) | set(_PROVIDER_KEY_ENV.values())))


def provider_routing_env_names() -> tuple[str, ...]:
    """Every env name that decides WHICH provider/lane an extraction routes to.

    Derived from the registries the gates read — never a hand-list. Sorted and
    de-duplicated so a test can iterate it deterministically.
    """
    return tuple(sorted(set(provider_key_envs()) | set(_PROVIDER_SELECTOR_ENVS)))


def pin_provider_routing_env(monkeypatch) -> tuple[str, ...]:
    """Remove every provider-routing env value for the calling test.

    ``monkeypatch`` (not a raw ``os.environ.pop``) so the removal is undone at
    teardown and cannot leak into the next test — the #4883 class. Returns the
    pinned names so a caller can assert on them.
    """
    names = provider_routing_env_names()
    for name in names:
        monkeypatch.delenv(name, raising=False)
    return names
