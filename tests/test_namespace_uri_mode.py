"""Tests for namespace isolation in URI mode (#221 R1, #7886).

Verifies that TortoiseSDK resolves the correct graph name in every
namespace configuration, and that the regression from #7886 (no-namespace
SDK clobbering the URI's own graph with a hardcoded 'tortoise') is fixed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def uri_mode(monkeypatch):
    """Force URI mode with a test-prefixed session graph (like conftest)."""
    monkeypatch.setenv(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_221_namespace",
    )
    yield


def _graph_name(sdk: TortoiseSDK) -> str:
    proj = sdk._get_proj()
    return getattr(proj, "graph_name", None) or getattr(proj.g, "name", "unknown")


class TestNamespaceInURIMode:
    def test_no_namespace_uses_uri_graph(self, uri_mode):
        """#221: no-namespace SDK honors the URI's own graph (conftest session
        graph), NOT a hardcoded 'tortoise' — regression from #7886."""
        sdk = TortoiseSDK()
        try:
            assert _graph_name(sdk) == "tortoise_test_221_namespace"
        finally:
            sdk.close()

    def test_test_namespace_is_test_prefixed(self, uri_mode):
        """Test namespaces stay guard-compatible (start with test_/tortoise_test)
        so _assert_test_graph passes for DETACH DELETE."""
        sdk = TortoiseSDK(namespace="test_ep_src_xyz")
        try:
            name = _graph_name(sdk)
            assert name.startswith(("test_", "tortoise_test")), name
        finally:
            sdk.close()

    def test_team_namespace_maps_to_team_graph(self, uri_mode):
        """Production team namespaces map to team_<id> (matches provision)."""
        sdk = TortoiseSDK(namespace="team-abc123")
        try:
            assert _graph_name(sdk) == "team_team-abc123"
        finally:
            sdk.close()

    def test_registry_namespace_maps_to_registry_graph(self, uri_mode):
        sdk = TortoiseSDK(namespace="registry")
        try:
            assert _graph_name(sdk) == "registry_tortoise"
        finally:
            sdk.close()

    def test_uri_without_path_defaults_to_tortoise(self, monkeypatch):
        """A URI without a graph path still resolves (no crash)."""
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
        sdk = TortoiseSDK()
        try:
            assert _graph_name(sdk) == "tortoise"
        finally:
            sdk.close()
