"""Tests for connector_loader — manifest parsing, env var overrides."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import tortoise.connector_loader as cl


def test_load_connectors_empty_when_no_manifest():
    result = cl.load_connectors(manifest_path="/nonexistent/manifest.yaml")
    assert result == {}


def test_load_connectors_skips_inactive():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
version: 1
connectors:
  github:
    name: "GitHub"
    module: "tortoise.connectors.github"
    class: "GitHubConnector"
    active: false
    config:
      repo: "test/repo"
""")
        f.flush()
        path = f.name

    try:
        result = cl.load_connectors(manifest_path=path)
        assert result == {}
    finally:
        os.unlink(path)


def test_load_connectors_creates_instance():
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
      state: "closed"
      limit: 10
""")
        f.flush()
        path = f.name

    try:
        result = cl.load_connectors(manifest_path=path)
        assert "github" in result
        gh = result["github"]
        assert gh.repo == "test/repo"
        assert gh.state == "closed"
        assert gh.limit == 10
    finally:
        os.unlink(path)


def test_env_var_overrides_repo(monkeypatch):
    """GITHUB_REPO env var overrides manifest config."""
    monkeypatch.setenv("GITHUB_REPO", "env-org/env-repo")

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
      repo: "manifest/repo"
""")
        f.flush()
        path = f.name

    try:
        result = cl.load_connectors(manifest_path=path)
        assert result["github"].repo == "env-org/env-repo"
    finally:
        os.unlink(path)
        monkeypatch.delenv("GITHUB_REPO", raising=False)
