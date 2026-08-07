"""Tests for connector secrets at rest — env-var pattern (#324).

Validates that all three connectors read secrets from environment variables,
degrade gracefully when env vars are absent, and that the connector loader
strips and warns about plaintext secrets in YAML config.
"""
from __future__ import annotations

import logging
import os
import tempfile

import pytest

from tortoise.connectors.slack import SlackConnector
from tortoise.connectors.github import GitHubConnector
from tortoise.connectors.linear import LinearConnector
import tortoise.connector_loader as cl


# ── Test 1: Slack init with env var ──────────────────────────────────

def test_slack_env_var_token_and_signing_secret(monkeypatch):
    """SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET env vars should be read."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env-token")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-signing-secret-abc123")

    sc = SlackConnector(config={"channel_id": "C01"})
    assert sc.token == "xoxb-env-token"
    assert sc.signing_secret == "env-signing-secret-abc123"

    # Cleanup
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)


def test_slack_env_var_wins_over_config(monkeypatch):
    """Env var takes precedence over config dict value."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env-token")

    sc = SlackConnector(config={
        "token": "xoxb-config-token",
        "channel_id": "C01",
    })
    assert sc.token == "xoxb-env-token"

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)


# ── Test 2: Slack without env var → graceful empty ───────────────────

def test_slack_no_env_var_graceful_empty(monkeypatch):
    """When env vars are absent and config is empty, secrets stay empty."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)

    sc = SlackConnector(config={"channel_id": "C01"})
    assert sc.token == ""
    assert sc.signing_secret == ""
    # Connector should degrade gracefully — poll returns empty, no crash
    assert sc.poll() == []


# ── Test 3: GitHub webhook secret from env ───────────────────────────

def test_github_webhook_secret_env_var(monkeypatch):
    """GITHUB_WEBHOOK_SECRET env var should be read for webhook_secret."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-webhook-secret-xyz")

    gh = GitHubConnector(config={"repo": "test/r"})
    assert gh.webhook_secret == "env-webhook-secret-xyz"

    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)


def test_github_webhook_secret_env_wins_over_config(monkeypatch):
    """Env var takes precedence over config dict value."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")

    gh = GitHubConnector(config={
        "repo": "test/r",
        "webhook_secret": "config-secret",
    })
    assert gh.webhook_secret == "env-secret"

    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)


# ── Test 4: Linear env var already works ─────────────────────────────

def test_linear_env_var_already_works(monkeypatch):
    """LINEAR_API_KEY env var pattern was already implemented — verify."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_env_key_123")

    lc = LinearConnector(config={})
    assert lc.api_key == "lin_api_env_key_123"

    # Without env var, falls back to config
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    lc2 = LinearConnector(config={"api_key": "lin_api_config_key"})
    assert lc2.api_key == "lin_api_config_key"

    # Without either, empty string
    lc3 = LinearConnector(config={})
    assert lc3.api_key == ""


# ── Test 5: connector_loader strips secrets + warns ───────────────────

def test_connector_loader_warns_on_plaintext_secrets(caplog, monkeypatch):
    """Connector loader should log a warning when YAML contains plaintext secrets."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
version: 1
connectors:
  slack:
    name: "Slack"
    module: "tortoise.connectors.slack"
    class: "SlackConnector"
    active: true
    config:
      token: "xoxb-plaintext-in-yaml"
      signing_secret: "plaintext-signing-secret"
      channel_id: "C01"
""")
        f.flush()
        path = f.name

    try:
        with caplog.at_level(logging.WARNING, logger="tortoise.connector_loader"):
            result = cl.load_connectors(manifest_path=path)

        # Connector should still be loaded (backward compat)
        assert "slack" in result
        sc = result["slack"]

        # Secrets should have been stripped from config — fall back to env (none set)
        assert sc.token == ""
        assert sc.signing_secret == ""

        # Warning should mention plaintext secrets
        log_text = " ".join(r.message for r in caplog.records)
        assert "plaintext" in log_text.lower() or "secret" in log_text.lower()
    finally:
        os.unlink(path)


def test_connector_loader_does_not_warn_when_no_plaintext_secrets(caplog):
    """Connector loader should NOT warn when YAML has no plaintext secrets."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
version: 1
connectors:
  github:
    name: "GitHub"
    module: "tortoise.connectors.github"
    class: "GitHubConnector"
    active: true
    config:
      repo: "test/repo"
      webhook_secret: ""
""")
        f.flush()
        path = f.name

    try:
        with caplog.at_level(logging.WARNING, logger="tortoise.connector_loader"):
            result = cl.load_connectors(manifest_path=path)

        assert "github" in result
        log_text = " ".join(r.message for r in caplog.records)
        # No warning about plaintext secrets
        assert "plaintext" not in log_text.lower()
    finally:
        os.unlink(path)
