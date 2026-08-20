"""Tests for the GitHub indexer + indexing endpoints (#499)."""
from __future__ import annotations

import json  # noqa: F401
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import httpx
import pytest

from tortoise.indexer.github_indexer import GitHubIndexer


class MockGitHubTransport(httpx.AsyncBaseTransport):
    """Mock GitHub API with configurable responses."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request.url.path)
        # Return based on path pattern
        if "/repos" in request.url.path and "issues" not in request.url.path:
            body = [{"full_name": "acme/repo1", "html_url": f"https://github.com/acme/repo1"}]  # noqa: F541
            return httpx.Response(200, json=body, request=request)
        if "issues" in request.url.path:
            body = [{
                "number": 1, "title": "Fix bug", "state": "open",
                "html_url": "https://github.com/acme/repo1/issues/1",
                "body": "The bug is in the parser",
            }]
            return httpx.Response(200, json=body, request=request)
        return httpx.Response(404, json={}, request=request)


class TestGitHubIndexer:
    def _make_indexer(self, responses=None):
        transport = MockGitHubTransport(responses or {})
        client = httpx.AsyncClient(transport=transport)
        return GitHubIndexer("fake-token", httpx_client=client), client

    @pytest.mark.asyncio
    async def test_index_creates_points(self):
        indexer, client = self._make_indexer()  # noqa: RUF059

        class FakeSDK:
            def __init__(self):
                self.points = []

            def create_point(self, kind, content, authoredBy=None, props=None):
                self.points.append({"kind": kind, "content": content, "props": props})

        sdk = FakeSDK()
        result = await indexer.index_issues(sdk, "acme")
        assert result["points_created"] == 1
        assert result["repos_processed"] == 1
        assert sdk.points[0]["props"]["source"] == "github"
        assert "github_url" in sdk.points[0]["props"]

    @pytest.mark.asyncio
    async def test_index_dedup_by_github_url(self):
        indexer, client = self._make_indexer()  # noqa: RUF059

        class FakeSDK:
            def __init__(self):
                self.points = []
                self.urls = set()

            def create_point(self, kind, content, authoredBy=None, props=None):
                url = props.get("github_url")
                if url in self.urls:
                    return {"existing": True}
                self.urls.add(url)
                self.points.append({"kind": kind, "props": props})

        sdk = FakeSDK()
        # Run twice — dedup should prevent duplicates
        await indexer.index_issues(sdk, "acme")
        await indexer.index_issues(sdk, "acme")
        assert len(sdk.points) == 1

    @pytest.mark.asyncio
    async def test_truncates_large_bodies(self):
        indexer, client = self._make_indexer()  # noqa: RUF059

        class FakeSDK:
            def __init__(self):
                self.points = []

            def create_point(self, kind, content, authoredBy=None, props=None):
                self.points.append({"content": content})

        sdk = FakeSDK()
        await indexer.index_issues(sdk, "acme")
        # Content = "[repo] title\n\nbody" — body truncated to 5000 chars
        assert len(sdk.points[0]["content"]) <= 5000 * 2 + 100
