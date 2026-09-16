"""Unit tests for tools/check_pages_bindings.py — the #3616 deploy gate.

Everything here is offline: `evaluate()` is a pure function over the Pages API's
`deployment_configs` shape. The point of these tests is that the GATE CAN FAIL —
a checker that always returns [] would have let #3616 ship again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_pages_bindings as cpb  # noqa: E402

MANIFEST_PATH = REPO / "website" / "required-bindings.yml"


def _manifest() -> dict:
    return cpb.load_manifest(MANIFEST_PATH)


def test_the_repo_manifest_parses_and_declares_sessions() -> None:
    """`SESSIONS` is the binding whose absence caused the #3616 outage."""
    m = _manifest()
    names = {b["name"] for b in m["bindings"]}
    assert "SESSIONS" in names
    sessions = next(b for b in m["bindings"] if b["name"] == "SESSIONS")
    assert sessions["kind"] == "required", (
        "SESSIONS must be `required` — without it /auth/* answers 503"
    )
    assert sessions["type"] == "d1_databases"
    assert "production" in sessions["envs"]


def test_a_complete_configuration_passes() -> None:
    configs = {
        "production": {
            "d1_databases": {"SESSIONS": {"id": "x"}},
            "env_vars": {
                "SUPABASE_URL": {"type": "plain_text"},
                "SUPABASE_ANON_KEY": {"type": "plain_text"},
            },
        },
        "preview": {"d1_databases": {"SESSIONS": {"id": "x"}}},
    }
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert missing_required == []


def test_absent_sessions_is_reported_as_missing_required() -> None:
    """The exact production state at the #3616 outage: env vars present, no D1.

    This is the assertion that would have blocked the deploy.
    """
    configs = {
        "production": {
            "env_vars": {
                "SUPABASE_URL": {"type": "plain_text"},
                "SUPABASE_ANON_KEY": {"type": "plain_text"},
            }
            # no d1_databases at all
        },
        "preview": {"env_vars": {}},
    }
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:d1_databases:SESSIONS" in missing_required
    assert "preview:d1_databases:SESSIONS" in missing_required


def test_required_env_var_absence_is_reported() -> None:
    configs = {
        "production": {"d1_databases": {"SESSIONS": {"id": "x"}}, "env_vars": {}},
        "preview": {"d1_databases": {"SESSIONS": {"id": "x"}}},
    }
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:env_vars:SUPABASE_URL" in missing_required
    assert "production:env_vars:SUPABASE_ANON_KEY" in missing_required


def test_recommended_absence_warns_but_does_not_block() -> None:
    """API_ORIGIN / APP_ORIGIN / AUTH_CALLBACK_URL have correct code defaults.

    A gate that failed the deploy over these would be a gate people disable.
    """
    configs = {
        "production": {
            "d1_databases": {"SESSIONS": {"id": "x"}},
            "env_vars": {
                "SUPABASE_URL": {"type": "plain_text"},
                "SUPABASE_ANON_KEY": {"type": "plain_text"},
            },
        },
        "preview": {"d1_databases": {"SESSIONS": {"id": "x"}}},
    }
    missing_required, missing_recommended = cpb.evaluate(_manifest(), configs)
    assert missing_required == [], "recommended absence must not block the deploy"
    assert "production:env_vars:API_ORIGIN" in missing_recommended


def test_a_binding_of_the_wrong_TYPE_does_not_count() -> None:
    """A KV namespace named SESSIONS is not the D1 database the code expects.

    Presence-by-name alone would pass this — which is why the check is keyed on
    (env, type, name), not name alone.
    """
    configs = {
        "production": {
            "kv_namespaces": {"SESSIONS": {"id": "x"}},
            "env_vars": {
                "SUPABASE_URL": {"type": "plain_text"},
                "SUPABASE_ANON_KEY": {"type": "plain_text"},
            },
        },
        "preview": {"kv_namespaces": {"SESSIONS": {"id": "x"}}},
    }
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:d1_databases:SESSIONS" in missing_required


def test_evaluate_is_not_vacuous_over_the_real_manifest() -> None:
    """Non-vacuity: the manifest must declare at least one required binding with
    at least one env, so an empty config cannot silently pass."""
    m = _manifest()
    required = [b for b in m["bindings"] if b.get("kind", "required") == "required"]
    assert required, "no required bindings declared — the gate asserts nothing"
    assert all(b.get("envs") for b in required), "a required binding has no envs"
    missing, _ = cpb.evaluate(m, {})
    assert missing, "an EMPTY config must report missing required bindings"


def test_cli_fails_closed_when_the_api_is_unreachable(monkeypatch) -> None:
    """Not knowing is not the same as knowing it is fine."""
    def boom(*_a, **_k):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(cpb, "fetch_configs", boom)
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 2, "an unreachable API must fail CLOSED (exit 2), not pass"


def test_cli_returns_1_when_required_binding_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: {"production": {}, "preview": {}})
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 1


def test_cli_returns_0_when_everything_is_present(monkeypatch) -> None:
    complete = {
        "production": {
            "d1_databases": {"SESSIONS": {"id": "x"}},
            "env_vars": {
                "SUPABASE_URL": {"type": "plain_text"},
                "SUPABASE_ANON_KEY": {"type": "plain_text"},
                "APP_ORIGIN": {"type": "plain_text"},
                "AUTH_CALLBACK_URL": {"type": "plain_text"},
                "API_ORIGIN": {"type": "plain_text"},
            },
        },
        "preview": {"d1_databases": {"SESSIONS": {"id": "x"}}},
    }
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: complete)
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 0


@pytest.mark.parametrize("bad", ["bindings: []\n", "project: x\n", ""])
def test_malformed_manifest_is_rejected(tmp_path, bad: str) -> None:
    p = tmp_path / "m.yml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises((ValueError, AttributeError, TypeError)):
        cpb.load_manifest(p)
