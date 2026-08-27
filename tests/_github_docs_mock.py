"""Shared mock GitHub Contents-API transport for #1726 (docs fetcher) tests.

Simulates the tree + blob surface the docs fetcher walks:
- ``GET /repos/{org}/{repo}/git/trees/{branch}?recursive=1`` →
  ``{sha, tree: [entries], truncated}``
- ``GET /repos/{org}/{repo}/git/blobs/{sha}`` →
  ``{content: <base64>, encoding: "base64", size}``
- repo resolution (``/orgs/{org}/repos`` + ``/users/{org}/repos``)

Failure injection: ``fail_blobs`` (blob fetch → 503) + ``tree_404_branches``
((repo, branch) pairs that 404 — drives the master-fallback probe).
"""
from __future__ import annotations

import base64
import hashlib

import httpx


def gh_docs_entry(path: str, *, data: bytes | None = None,
                  text: str | None = None,
                  sha: str | None = None,
                  size: int | None = None) -> dict:
    """A recursive-tree blob entry (git object sha = sha1 of content).

    ``data`` wins over ``text``; an explicit ``size`` lets tests simulate an
    oversized entry WITHOUT materializing the content.
    """
    if data is None:
        data = (text if text is not None else f"# {path}\n").encode("utf-8")
    blob_sha = sha or hashlib.sha1(data).hexdigest()
    return {
        "path": path,
        "mode": "100644",
        "type": "blob",
        "sha": blob_sha,
        "size": len(data) if size is None else size,
        "url": f"https://api.github.com/repos/acme/repo1/git/blobs/{blob_sha}",
    }


class MockGitHubDocsTransport(httpx.AsyncBaseTransport):
    """Configurable GitHub tree/blob mock.

    ``trees``: ``{repo_full: {branch: {"sha": str, "entries": [entry, ...]}}}``
    ``blobs``: ``{blob_sha: bytes}`` (auto-populated from entry data is NOT
    assumed — tests register the blobs they want served).
    ``repos``: full_names returned by repo resolution.
    """

    def __init__(self, *, repos: list[str] | None = None,
                 trees: dict | None = None,
                 blobs: dict | None = None,
                 fail_blobs: set[str] | None = None,
                 tree_404_branches: set[tuple[str, str]] | None = None):
        self.repos = repos or ["acme/repo1"]
        self.trees = trees or {}
        self.blobs = dict(blobs or {})
        self.fail_blobs = set(fail_blobs or [])
        self.tree_404_branches = set(tree_404_branches or [])
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if "/git/trees/" in url:
            # /repos/{owner}/{repo}/git/trees/{branch}?recursive=1 — the
            # branch may itself contain slashes, so take everything after
            # "git/trees/".
            repo = url.split("/repos/")[1].split("/git/trees/")[0]
            branch = url.split("git/trees/")[1].split("?")[0]
            if (repo, branch) in self.tree_404_branches:
                return httpx.Response(404, json={}, request=request)
            t = (self.trees.get(repo) or {}).get(branch)
            if t is None:
                return httpx.Response(404, json={}, request=request)
            return httpx.Response(200, json={
                "sha": t["sha"],
                "tree": t["entries"],
                "truncated": False,
            }, request=request)
        if "/git/blobs/" in url:
            sha = url.split("git/blobs/")[1].split("?")[0]
            if sha in self.fail_blobs:
                return httpx.Response(503, json={"message": "injected 503"},
                                      request=request)
            data = self.blobs.get(sha)
            if data is None:
                return httpx.Response(404, json={}, request=request)
            return httpx.Response(200, json={
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
                "size": len(data),
            }, request=request)
        if "/repos" in url:
            return httpx.Response(
                200, json=[{"full_name": r} for r in self.repos],
                request=request)
        return httpx.Response(404, json={}, request=request)
