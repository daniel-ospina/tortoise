"""`.env.example` must declare every env name the committed Supabase config reads (#4030).

`supabase/config.toml` is TRACKED and resolves its providers/keys by
`env(NAME)` substitution, so each of those names is required by a file in this
repo. `.env.example` is the repo's declared env contract — the place an
operator looks for the name to set. A name the committed config requires but
the contract never mentions is the #4030 inventory's finding for the Google
external-auth pair: a fresh clone has no documented way to configure the local
stack, and the gap is invisible because the config is not read as documentation.

The predicate is TOTAL — every `env(NAME)` under every section, enabled or not.
Parsing `enabled = false` instead would be a trap in the direction that matters:
flipping a provider on is exactly the change that makes an undocumented name
required, and a predicate that excused the disabled block would go green on it.
So `[auth.external.apple]`, `[auth.sms.twilio]` and the `[experimental]` S3 keys
are asserted too, and the contract marks which of them a default local stack
does not need.

Declared means the assignment form the contract uses (``NAME=`` or a commented
``# NAME=`` placeholder) — a bare mention in prose does not give an operator
anything to copy, so it does not count.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "supabase" / "config.toml"
ENV_EXAMPLE = ROOT / ".env.example"

# A declaration line: optional `#`, optional indent, the NAME, then `=`.
_DECLARED = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
_ENV_REF = re.compile(r"^env\(([A-Z][A-Z0-9_]*)\)$")


def _config_env_names() -> set[str]:
    """Every env name `supabase/config.toml` resolves, at any nesting depth."""
    names: set[str] = set()

    def walk(node: dict) -> None:
        for value in node.values():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, str):
                match = _ENV_REF.match(value)
                if match:
                    names.add(match.group(1))

    walk(tomllib.loads(CONFIG.read_text()))
    return names


def _declared_names() -> set[str]:
    return set(_DECLARED.findall(ENV_EXAMPLE.read_text()))


def test_config_env_extractor_is_not_vacuous():
    """The pin below is only worth its green if the walk really reaches the config.

    A parser that returned nothing (a TOML shape change, a `walk` that stops at
    the first level) would make the coverage assertion pass over any input.
    """
    names = _config_env_names()
    assert len(names) >= 10, f"extractor found too few names: {sorted(names)}"
    # Reached through `[auth.external.google]` — the pair #4030 found missing.
    assert "SUPABASE_AUTH_EXTERNAL_GOOGLE_SECRET" in names
    assert "OPENAI_API_KEY" in names


def test_every_config_env_name_is_declared_in_the_env_contract():
    missing = sorted(_config_env_names() - _declared_names())
    assert not missing, (
        "these env names are required by supabase/config.toml's `env(...)` "
        "substitution but are not declared in .env.example, so a fresh clone "
        "has no documented way to set them (#4030): "
        + ", ".join(missing)
    )
