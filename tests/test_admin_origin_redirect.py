"""#4171 — the retired admin origin must redirect to the app origin, in ONE hop.

WHY THIS EXISTS
---------------
#4054 moved the session (`__Host-session`, host-only by construction) to
app.premiselabs.co; #4171 moved the blog admin console there with it. The old
URL must keep working, but the redirect must be a **single-hop 301 straight to
the app origin** — `SCOPE.md` §4 W2 / F12 forbid a chained permanent redirect
(the next hop could change and the first link would keep asserting the old
target forever).

The routing lives in `website/functions/_middleware.ts` (the `premise-labs`
project), which is the one file every request passes through. It is executed
HERE, not string-matched: an `if` that never runs, or one whose Location omits
the origin, passes a substring check.

A SECOND, LOAD-BEARING PROPERTY: `/blog/api/*` must NOT be redirected. The blog
Functions stayed on the marketing origin, and a redirect would turn a POST into
a GET and drop the body (the middleware's own blog-prefix rule documents this).
#4171 adds a same-origin proxy on the app origin instead, so `/blog/api/*` must
still reach `premise-labs` untouched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIDDLEWARE = REPO_ROOT / "website" / "functions" / "_middleware.ts"
APP_ORIGIN = "https://app.premiselabs.co"
TORTOISE_HOST = "tortoise.premiselabs.co"
COMPANY_HOST = "premiselabs.co"

# Drives the REAL exported onRequest under Node's type-stripping loader. The
# middleware's `/` branch would fetch ASSETS, but every case here returns before
# that, and a stub keeps the harness from touching the filesystem either way.
_DRIVER = """
const mod = await import(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
const out = [];
for (const c of cases) {
  const req = new Request(c.url, { method: c.method || 'GET', headers: { host: c.host } });
  const ctx = {
    request: req,
    env: { ASSETS: { fetch: async () => new Response('asset', { status: 404 }) } },
    next: async () => new Response('passthrough', { status: 200, headers: { 'x-mw': 'next' } }),
  };
  const res = await mod.onRequest(ctx);
  out.push({ status: res.status, location: res.headers.get('Location'), next: res.headers.get('x-mw'),
             hsts: res.headers.get('Strict-Transport-Security') });
}
console.log(JSON.stringify(out));
"""


def _run(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [node, "--experimental-strip-types", str(driver), str(MIDDLEWARE), json.dumps(cases)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    # Node 22 prints an experimental-feature warning to stderr, not stdout, so
    # the JSON payload is the whole of stdout.
    return json.loads(proc.stdout)


def _case(path: str, host: str = TORTOISE_HOST, method: str = "GET") -> dict:
    return {"url": f"https://{host}{path}", "host": host, "method": method}


@pytest.mark.parametrize(
    "path",
    ["/admin", "/admin/", "/admin/blog", "/admin/blog/edit", "/admin#/edit/1"],
)
def test_admin_redirects_to_the_app_origin_in_one_hop(path: str) -> None:
    (res,) = _run([_case(path)])
    assert res["status"] == 301, f"{path} -> {res['status']}, want a single-hop 301"
    assert res["location"] == f"{APP_ORIGIN}/admin", (
        f"{path} -> {res['location']!r}; want exactly {APP_ORIGIN + '/admin'!r} "
        "(a chained or off-origin target is the failure this guards)"
    )
    assert res["hsts"], f"{path}: the redirect lost the HSTS header (#1003)"


def test_company_host_admin_also_redirects_to_the_app_origin() -> None:
    """The console was never on premiselabs.co; both marketing hosts must 301."""
    (res,) = _run([_case("/admin", host=COMPANY_HOST)])
    assert res["status"] == 301 and res["location"] == f"{APP_ORIGIN}/admin", res


def test_blog_api_is_not_redirected() -> None:
    """A redirect here would drop the POST body (the blog-prefix rule's exclusion).

    The status is whatever the blog Function answers (200 from the stub `next`
    here); the assertion is that it is NOT the /admin redirect.
    """
    (res,) = _run([_case("/blog/api/purge", method="POST")])
    assert res["status"] != 301, f"/blog/api/purge was 301'd: {res}"
    assert res["location"] is None, f"/blog/api/purge carried a Location: {res}"
    assert res["next"] == "next", "/blog/api/purge did not fall through to the next handler"


def test_admin_is_not_a_prefix_false_positive() -> None:
    """`/administer` must NOT redirect — the guard is a path-prefix, not a string one."""
    (res,) = _run([_case("/administrator")])
    assert res["status"] != 301, f"/administrator was treated as /admin: {res}"
