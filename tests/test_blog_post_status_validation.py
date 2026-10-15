"""#4311 — the blog agent API's CREATE status validation, executed.

WHY THIS EXISTS
---------------
#4311 recorded that ``website/functions/**`` is not typechecked in CI, and that
``validateCreate`` in ``website/functions/blog/api/posts/[[path]].ts`` did not
pass ``tsc --strict``::

    status: status ?? "draft",   // string | null → "draft" | "published"  (TS2322)

The fix moved the invariant into the TYPE (``PostStatus``, narrowed once where
the raw input is parsed) rather than casting at the assignment. A type makes the
*shape* right; it says nothing about the *behaviour*, and the behaviour is what
this handler's callers depend on: an absent ``status`` defaults to ``draft``, an
invalid one is REFUSED (400) rather than silently coerced, and a
``published`` request must still carry a body and no ``hold_for_review``.

The failure a source-substring check would miss is the one this revision could
have introduced: a default that turns an explicitly-invalid status into a
successful ``draft`` INSERT. So the REAL exported ``onRequestPost`` is driven
under Node's type-stripping loader (the convention of
``tests/test_blog_agent_delete_guard.py`` / ``tests/test_admin_origin_redirect.py``)
with ``globalThis.fetch`` stubbed, and each case asserts on the ROW the handler
attempted to INSERT — not merely on the status code.

Each assertion is falsifiable:
  * omitted / explicit status → the INSERT row carries ``draft`` (the default) or
    the requested member — not a constant;
  * ``status="archived"`` → 400 with the ``status must be draft | published``
    error AND NO INSERT (a coercion to ``draft`` would return 201);
  * ``status=42`` → 400 with ``must be a string`` (the non-string branch);
  * ``status="published"`` with an empty body → 400 ``body required``;
  * ``status="published"`` + ``hold_for_review`` → 400 on ``hold_for_review``.

All cases run in ONE node process (a module-scoped fixture): each node start
costs ~10s under the type-stripping loader, and the cases are independent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_FUNCTION = REPO_ROOT / "website" / "functions" / "blog" / "api" / "posts" / "[[path]].ts"

AGENT = "blog-status-test"

# Drives the REAL exported onRequestPost. Auth resolves through the stubbed
# Supabase read; the INSERT is recorded rather than issued, so the test can
# assert on the row the handler tried to write (and on the absence of one).
_DRIVER = """
const mod = await import(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
const out = [];
for (const c of cases) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    const method = String((init && init.method) || 'GET').toUpperCase();
    if (u.includes('/rest/v1/blog_agent_keys')) {
      return Response.json([{ agent_name: c.agent || 'blog-status-test', active: true }]);
    }
    if (u.includes('/rest/v1/blog_posts') && method === 'POST') {
      calls.push(JSON.parse(String(init.body)));
      return Response.json([{ id: 'row-1', slug: JSON.parse(String(init.body)).slug }]);
    }
    return Response.json({ success: true });
  };
  const req = new Request('https://tortoise.premiselabs.co/blog/api/posts', {
    method: 'POST',
    headers: { 'X-Agent-Key': 'test-agent-key', 'Content-Type': 'application/json' },
    body: JSON.stringify(c.body),
  });
  const res = await mod.onRequestPost({
    request: req,
    env: { SUPABASE_URL: 'https://db.test', SUPABASE_SERVICE_ROLE_KEY: 'svc' },
    params: {},
    waitUntil: () => {},
  });
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  out.push({ status: res.status, body, rows: calls });
}
console.log(JSON.stringify(out));
"""

#: Case name → the POST body. `slug` is explicit so the auto-slug path (a
#: separate concern) cannot mask a status failure.
CASES: dict[str, dict] = {
    "omitted": {"title": "T", "slug": "status-omitted"},
    "draft": {"title": "T", "slug": "status-draft", "status": "draft"},
    "published": {"title": "T", "slug": "status-published", "status": "published", "body": "hello"},
    "invalid_string": {"title": "T", "slug": "status-archived", "status": "archived"},
    "non_string": {"title": "T", "slug": "status-number", "status": 42},
    "published_empty_body": {"title": "T", "slug": "status-empty-body", "status": "published", "body": ""},
    "published_hold_for_review": {
        "title": "T",
        "slug": "status-hold",
        "status": "published",
        "body": "hello",
        "hold_for_review": True,
    },
}


@pytest.fixture(scope="module")
def results() -> dict[str, dict]:
    # A *missing* Node FAILs (the guard under test must not look like a passing
    # one); a PRESENT node below the type-stripping floor is a host-capability
    # gap and skips — the same polarity as test_blog_agent_delete_guard.py.
    require_node_floor(
        NODE_FLOOR_STRIP_TYPES,
        what="the blog API status validation",
        absent="fail",
    )
    node = shutil.which("node")

    order = list(CASES)
    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [node, "--experimental-strip-types", str(driver), str(POSTS_FUNCTION),
             json.dumps([{"body": CASES[k]} for k in order])],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    # Node prints its experimental-feature warning on stderr; stdout is the JSON.
    out = json.loads(proc.stdout)
    assert len(out) == len(order), f"driver returned {len(out)} results for {len(order)} cases"
    return dict(zip(order, out, strict=True))


# ── the default and the explicit members reach the INSERT unchanged ─────────

@pytest.mark.parametrize("case,want", [("omitted", "draft"), ("draft", "draft"), ("published", "published")])
def test_a_valid_status_reaches_the_insert(results, case: str, want: str) -> None:
    """Falsifiable: a hardcoded ``status`` in the INSERT row fails two of three."""
    res = results[case]
    assert res["status"] == 201, f"{case} → {res['status']} (want 201): {res}"
    assert len(res["rows"]) == 1, f"{case} did not issue exactly one INSERT: {res['rows']}"
    assert res["rows"][0]["status"] == want, res["rows"][0]


# ── an invalid status is REFUSED, never coerced ──────────────────────────────

def test_an_unknown_status_string_is_rejected_and_never_inserted(results) -> None:
    """The pre-#4311 cast would have made this a 201 with status='archived'."""
    res = results["invalid_string"]
    assert res["status"] == 400, f"invalid status → {res['status']} (want 400): {res}"
    assert res["body"]["status"] == "status must be draft | published", res["body"]
    assert res["rows"] == [], f"an invalid status reached the INSERT: {res['rows']}"


def test_a_non_string_status_keeps_the_non_string_error(results) -> None:
    """The two status errors are distinct contracts; the type change merged the
    branches, so pin that ``42`` still reads ``must be a string``."""
    res = results["non_string"]
    assert res["status"] == 400, res
    assert res["body"]["status"] == "must be a string", res["body"]
    assert res["rows"] == [], f"a non-string status reached the INSERT: {res['rows']}"


# ── the published-only constraints still fire on the narrowed value ─────────

def test_published_requires_a_body(results) -> None:
    res = results["published_empty_body"]
    assert res["status"] == 400, res
    assert "body" in res["body"], res["body"]
    assert res["rows"] == [], f"a body-less published post reached the INSERT: {res['rows']}"


def test_published_rejects_hold_for_review(results) -> None:
    res = results["published_hold_for_review"]
    assert res["status"] == 400, res
    assert "hold_for_review" in res["body"], res["body"]
    assert res["rows"] == [], f"a contradictory published row reached the INSERT: {res['rows']}"
