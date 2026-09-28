"""#4316 — the agent DELETE surface's authorization + ownership predicates.

WHY THIS EXISTS
---------------
#4220 added ``DELETE /blog/api/posts/:slug`` to the agent API so the write E2E
suite could clean up after itself. The P1 review finding on that PR: the
destructive predicate only excluded ``archived``, so a **published** post could
be deleted. ``created_by`` is the CREATOR, not the publisher — an operator
publishes with ``published_by`` while ``created_by`` stays the creating agent
(``website/apps/blog-admin/src/lib/blog-api.ts::publishPost``) — so the
production write key held by CI could **irreversibly destroy an
operator-approved, already-published article**. The recorded lifecycle is
``draft → published → archived (terminal)``
(``docs/epics/2026-08-27-tortoise-blog-cms/03-plan.md`` §W4): there is no
published→deleted transition.

The second half of the finding: the DELETE had NO authorization test beyond
``anonymous → 401`` (``tests/e2e/test_blog.py``). The E2E environment cannot
express the remaining cases — it holds a single agent key (no second identity
for the cross-agent 403) and the agent API cannot set ``status=archived`` (the
operator console owns archive) — so the cases are driven HERE, by executing the
real exported handler under Node's type-stripping loader with a stubbed
``fetch`` (the convention of ``tests/test_admin_origin_redirect.py``). A source
substring check would pass on a handler whose guard is dead code; executing the
handler cannot.

Each assertion is falsifiable:
  * published / archived / other-owner / unknown → the destructive statement is
    never issued (asserted on the recorded calls, not just the status code);
  * the owned-draft DELETE URL carries ``created_by=eq.<agent>`` AND
    ``status=eq.draft`` — remove either predicate from the URL builder and
    these fail;
  * the PATCH URL carries ``created_by=eq.<agent>`` — the ownership predicate
    the DELETE comment claims is statement-scoped must be true for PATCH too.

All cases run in ONE node process (a module-scoped fixture): each node start
costs ~10s under the type-stripping loader, and the cases are independent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_FUNCTION = REPO_ROOT / "website" / "functions" / "blog" / "api" / "posts" / "[[path]].ts"

AGENT = "blog-e2e"

# Drives the REAL exported onRequestDelete / onRequestPatch. `globalThis.fetch`
# is stubbed per case and every call is recorded, so the test can assert what
# the handler did NOT send (the destructive half) — not merely the status it
# returned. Auth is stubbed at the Supabase read: the case's `agent` is the
# identity the key resolves to.
_DRIVER = """
const mod = await import(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
const out = [];
for (const c of cases) {
  const calls = [];
  const slug = c.slug || 'guard-slug';
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    const method = String((init && init.method) || 'GET').toUpperCase();
    calls.push({ url: u, method, body: (init && init.body) || null });
    if (u.includes('/rest/v1/blog_agent_keys')) {
      return Response.json([{ agent_name: c.agent, active: true }]);
    }
    if (u.includes('/rest/v1/blog_posts')) {
      if (method === 'GET') return Response.json(c.row ? [c.row] : []);
      if (method === 'PATCH' || method === 'DELETE') {
        return Response.json(c.empty_write ? [] : [{ id: c.row.id, slug }]);
      }
    }
    // Edge-cache purge / anything unmodelled — benign success.
    return Response.json({ success: true });
  };
  const handler = c.method === 'PATCH' ? mod.onRequestPatch : mod.onRequestDelete;
  const req = new Request('https://tortoise.premiselabs.co/blog/api/posts/' + slug, {
    method: c.method || 'DELETE',
    headers: { 'X-Agent-Key': c.key || 'test-agent-key', 'Content-Type': 'application/json' },
    body: c.body ? JSON.stringify(c.body) : undefined,
  });
  const res = await handler({
    request: req,
    env: { SUPABASE_URL: 'https://db.test', SUPABASE_SERVICE_ROLE_KEY: 'svc' },
    params: { path: [slug] },
    waitUntil: () => {},
  });
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  out.push({ status: res.status, body, calls });
}
console.log(JSON.stringify(out));
"""


def _row(status: str, created_by: str = AGENT, slug: str = "guard-slug") -> dict:
    return {"id": "row-1", "status": status, "created_by": created_by}


# Case name → the handler invocation. Driven once, in this order, by `results`.
CASES: dict[str, dict] = {
    "published_delete": {"method": "DELETE", "agent": AGENT, "row": _row("published")},
    "archived_delete": {"method": "DELETE", "agent": AGENT, "row": _row("archived")},
    "unknown_slug": {"method": "DELETE", "agent": AGENT, "row": None},
    "second_agent": {"method": "DELETE", "agent": "blog-e2e-2", "row": _row("draft", created_by=AGENT)},
    "other_owner_draft": {"method": "DELETE", "agent": "blog-e2e-2", "row": _row("draft", created_by="some-other-agent")},
    "owned_draft": {"method": "DELETE", "agent": AGENT, "row": _row("draft")},
    "empty_write": {"method": "DELETE", "agent": AGENT, "row": _row("draft"), "empty_write": True},
    "owned_patch": {"method": "PATCH", "agent": AGENT, "row": _row("draft"), "body": {"title": "renamed"}},
}


@pytest.fixture(scope="module")
def results() -> dict[str, dict]:
    node = shutil.which("node")
    if not node:
        pytest.fail(
            "no node runtime for the blog agent DELETE guard — need node >= 22.6 "
            "(--experimental-strip-types); refusing to skip, because a skipped "
            "guard looks like a passing one"
        )
    import tempfile

    order = list(CASES)
    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [node, "--experimental-strip-types", str(driver), str(POSTS_FUNCTION),
             json.dumps([CASES[k] for k in order])],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    # Node prints its experimental-feature warning to stderr; stdout is the JSON.
    out = json.loads(proc.stdout)
    assert len(out) == len(order), f"driver returned {len(out)} results for {len(order)} cases"
    return dict(zip(order, out, strict=True))


def _write_calls(res: dict) -> list[dict]:
    return [c for c in res["calls"] if c["method"] in ("DELETE", "PATCH")]


# ── P1: the destructive statement never reaches a live published post ──────

def test_delete_refuses_a_published_post_and_never_issues_the_delete(results) -> None:
    """The review's P1: a published post is not deletable through the agent API.

    Falsifiable: with the pre-fix predicate (``status=not.eq.archived`` only)
    the handler returns 200 and the recorded calls contain a DELETE — both
    assertions fail.
    """
    res = results["published_delete"]
    assert res["status"] == 409, f"DELETE published → {res['status']} (want 409): {res}"
    assert res["body"]["error"] == "published", res["body"]
    assert _write_calls(res) == [], (
        "a destructive statement was issued against a PUBLISHED post: "
        f"{_write_calls(res)}"
    )


def test_delete_refuses_an_archived_post_and_never_issues_the_delete(results) -> None:
    """Archived is terminal — the DELETE must refuse it (409), not touch it."""
    res = results["archived_delete"]
    assert res["status"] == 409, f"DELETE archived → {res['status']} (want 409): {res}"
    assert res["body"]["error"] == "archived", res["body"]
    assert _write_calls(res) == [], f"a destructive statement reached an archived post: {_write_calls(res)}"


def test_delete_refuses_unknown_slug(results) -> None:
    res = results["unknown_slug"]
    assert res["status"] == 404, res
    assert _write_calls(res) == [], f"a destructive statement was issued for an absent row: {_write_calls(res)}"


# ── P2: cross-agent authorization (the second-agent 403) ───────────────────

def test_second_agent_cannot_delete_another_agents_post(results) -> None:
    """A second agent key deleting another agent's post → 403, nothing deleted.

    This is the case the E2E environment cannot express (it holds one key):
    the handler is driven with ``agent='blog-e2e-2'`` against a ``blog-e2e``
    row, which is exactly what a second provisioned key would do.
    """
    res = results["second_agent"]
    assert res["status"] == 403, f"cross-agent DELETE → {res['status']} (want 403): {res}"
    assert res["body"]["error"] == "forbidden", res["body"]
    assert _write_calls(res) == [], f"a non-owner issued a destructive statement: {_write_calls(res)}"


def test_a_draft_owned_by_another_agent_is_also_refused(results) -> None:
    """The 403 is ownership, not status — a draft the caller did not create too."""
    res = results["other_owner_draft"]
    assert res["status"] == 403, res
    assert _write_calls(res) == [], f"a non-owner issued a destructive statement: {_write_calls(res)}"


# ── P2: the destructive statement carries the ownership + draft predicates ──

def test_owned_draft_delete_url_carries_created_by_and_draft_predicates(results) -> None:
    """The DELETE URL must scope by ``created_by`` AND ``status=eq.draft``.

    This fails if ``&created_by=eq.`` is removed from the URL builder (the
    finding's explicit requirement), and equally if the status predicate is
    dropped. The predicates — not the preceding SELECT — are what stop a row
    that changed hands, or was published, between the read and the delete.
    """
    res = results["owned_draft"]
    assert res["status"] == 200, f"owned-draft DELETE → {res['status']}: {res}"
    assert res["body"] == {"deleted": True, "slug": "guard-slug"}, res["body"]

    (call,) = _write_calls(res)
    assert call["method"] == "DELETE", call
    url = call["url"]
    assert "slug=eq.guard-slug" in url, url
    assert "&created_by=eq.blog-e2e" in url, f"DELETE URL lost the ownership predicate: {url}"
    assert "status=eq.draft" in url, f"DELETE URL lost the draft-only predicate: {url}"
    assert "status=not.eq.archived" not in url, (
        f"DELETE URL still uses the pre-fix status predicate: {url}"
    )


def test_owned_draft_delete_with_empty_write_is_a_honest_404(results) -> None:
    """A row that changed status/hands between read and delete must not be claimed.

    ``return=representation`` + an empty array means the statement matched
    nothing; the handler reports 404 rather than a phantom 200.
    """
    res = results["empty_write"]
    assert res["status"] == 404, f"empty write → {res['status']} (want an honest 404): {res}"


# ── P2: PATCH ownership must be statement-scoped too ──────────────────────

def test_patch_url_carries_the_created_by_predicate(results) -> None:
    """PATCH was read-then-trust while the DELETE comment claimed statement scope.

    Falsifiable: drop ``&created_by=eq.`` from the PATCH URL builder and this
    fails.
    """
    res = results["owned_patch"]
    assert res["status"] == 200, f"owned-draft PATCH → {res['status']}: {res}"

    (call,) = _write_calls(res)
    assert call["method"] == "PATCH", call
    url = call["url"]
    assert "&created_by=eq.blog-e2e" in url, f"PATCH URL lost the ownership predicate: {url}"
    assert "status=not.eq.archived" in url, f"PATCH URL lost the terminal-state predicate: {url}"
