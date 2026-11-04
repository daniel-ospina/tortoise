"""#4171 — the retired admin origin must redirect to the app origin, in ONE hop.

WHY THIS EXISTS
---------------
#4054 moved the session (`__Host-session`, host-only by construction) to
app.premiselabs.co; #4171 moved the blog admin console there with it. The old
URL must keep working, but the redirect must be a **single-hop** one straight to
the app origin — `SCOPE.md` §4 W2 forbids a chained redirect (the next hop could
change and the first link would keep asserting a stale target), and §12/F12
require the redirect itself be non-permanent (a 301 is browser-persistent and
deploy-unreachable).

**302** — the status for a NEW branch (#4409). `SCOPE.md` §12: “302, never a new
301”. A 301 is browser-persistent and cannot be reclaimed by a later deploy,
which is the trade-off §12 records; the `OVERRIDES` marker carrying it is on
#3501, #3521 and #4409.

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

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

REPO_ROOT = Path(__file__).resolve().parent.parent
MIDDLEWARE = REPO_ROOT / "website" / "functions" / "_middleware.ts"
APP_ORIGIN = "https://app.premiselabs.co"
TORTOISE_HOST = "tortoise.premiselabs.co"
COMPANY_HOST = "premiselabs.co"
#: The repo's record of the Supabase redirect allow-list the `/auth/*` comment
#: names as the guard (docs/ops/auth-email-flows.md §3), and the OPEN audit of
#: that property.
ALLOWLIST_RECORD = REPO_ROOT / "docs" / "ops" / "auth-email-flows.md"
AUDIT_ISSUE = "#8680"

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
    # #4916: a present-but-too-old Node must SKIP, not RED. The driver passes
    # `--experimental-strip-types`, so the recorded flag floor (22.7) applies.
    require_node_floor(
        NODE_FLOOR_STRIP_TYPES,
        what="the admin-origin redirect middleware driver",
    )
    node = shutil.which("node")
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
    """302, not 301 — resolved in #4409.

    This branch was added UNDER a rule that already existed (§12 was written with
    the #3501 scoping), so it is a NEW branch and the ruling fixes its status:
    §12 “302, never a new 301” — with no “chained” qualifier — plus §1's in-scope
    list naming `/admin` among the surfaces moving to `app.*`. The earlier 301
    here read W2's “never a chained new 301” as forbidding only chained ones,
    which does not survive §12's plain text. The OVERRIDES marker on
    #3501/#3521 states it plainly ("New branches: 302"); it post-dates this
    branch, so the misreading was available at the time — recorded here so the
    next lane does not re-make it. `/admin` is an authenticated operator surface
    with no SEO stake, so a permanent signal buys nothing.
    """
    (res,) = _run([_case(path)])
    assert res["status"] == 302, (
        f"{path} -> {res['status']}, want a single-hop 302 (§12/F12: a NEW branch "
        "for the moved surface is 302)"
    )
    assert res["location"] == f"{APP_ORIGIN}/admin", (
        f"{path} -> {res['location']!r}; want exactly {APP_ORIGIN + '/admin'!r} "
        "(a chained or off-origin target is the failure this guards)"
    )
    assert res["hsts"], f"{path}: the redirect lost the HSTS header (#1003)"


def test_company_host_admin_also_redirects_to_the_app_origin() -> None:
    """The console was never on premiselabs.co; both marketing hosts must redirect."""
    (res,) = _run([_case("/admin", host=COMPANY_HOST)])
    assert res["status"] == 302 and res["location"] == f"{APP_ORIGIN}/admin", res


def test_blog_api_is_not_redirected() -> None:
    """A redirect here would drop the POST body (the blog-prefix rule's exclusion).

    The status is whatever the blog Function answers (200 from the stub `next`
    here); the assertion is that it is NOT the /admin redirect.
    """
    (res,) = _run([_case("/blog/api/purge", method="POST")])
    assert res["status"] not in (301, 302), f"/blog/api/purge was redirected: {res}"
    assert res["location"] is None, f"/blog/api/purge carried a Location: {res}"
    assert res["next"] == "next", "/blog/api/purge did not fall through to the next handler"


def test_admin_is_not_a_prefix_false_positive() -> None:
    """`/administer` must NOT redirect — the guard is a path-prefix, not a string one."""
    (res,) = _run([_case("/administrator")])
    assert res["location"] is None, f"/administrator was treated as /admin: {res}"


# ---------------------------------------------------------------------------
# #4346 — the BFF's OWN endpoints (`/auth/*`) also moved, and must 302.
#
# The exact-path rule above covers `/auth` and `/auth.html` only, so
# `/auth/start`, `/auth/callback`, `/auth/confirm` … fell through to a DELETED
# asset and answered 404 on the marketing host — verified live:
#   tortoise.premiselabs.co/auth/start -> 404
#   app.premiselabs.co/auth/start      -> 302 (correct)
#
# 302 and NOT 301, a deliberate departure from the ordinary "moved ⇒ 301"
# practice: a 301 is browser-persistent and cannot be reclaimed by a later
# deploy, so a NEW branch for the moved surface is 302 (`SCOPE.md` §12 "302,
# never a new 301"; OVERRIDES marker on #3501/#3521/#4409).
#
# Both moved subtrees answer 302: `/auth/*` (#4346) and `/admin` (#4409).
#
# The `/auth` exact-path 301 is pinned AS-IS — it is the one redirect here that
# #4054 already shipped, so it is in browsers' caches and changing it is its own
# decision rather than a side effect of another. The pin below makes a change to
# it a visible edit instead of a silent drift.
# ---------------------------------------------------------------------------

AUTH_ENDPOINTS = [
    "/auth/start",
    "/auth/callback",
    "/auth/confirm",
    "/auth/update-password",
    "/auth/reset",
    "/auth/resend",
    "/auth/link",
    "/auth/api-key",
    "/auth/set-email",
]


@pytest.mark.parametrize("path", AUTH_ENDPOINTS)
def test_every_auth_endpoint_302s_to_the_app_origin(path: str) -> None:
    """Before #4346 these were 404s on the marketing host.

    A 301 fails here deliberately: it is browser-persistent, so it cannot be
    reclaimed by a later deploy (`SCOPE.md` §12/F12).
    """
    (res,) = _run([_case(path)])
    assert res["status"] == 302, (
        f"{path} -> {res['status']}, want 302 (§12/F12: a NEW branch for the moved "
        "auth surface is 302 — never a 301, never a 404)"
    )
    assert res["location"] == f"{APP_ORIGIN}{path}", (
        f"{path} -> {res['location']!r}; want exactly {APP_ORIGIN + path!r}"
    )
    assert res["hsts"], f"{path}: the redirect lost the HSTS header (#1003)"


def test_auth_subtree_preserves_the_query_string() -> None:
    """`next=`/`provider=`/`token=`/`type=` must survive the hop.

    A dropped query on `/auth/start` loses the provider; on `/auth/confirm` it
    loses the token — both are user-visible dead ends, and neither shows up in a
    status-code assertion.
    """
    (res,) = _run([_case("/auth/start?provider=github&next=%2Fwelcome%3Fclaim%3D1")])
    assert res["status"] == 302 and res["location"] == (
        f"{APP_ORIGIN}/auth/start?provider=github&next=%2Fwelcome%3Fclaim%3D1"
    ), res


def test_bare_auth_slash_collapses_to_one_hop() -> None:
    """`/auth/` must reach the app origin in ONE hop.

    It used to be a two-hop chain (`/auth/` -> `/auth` on the marketing host ->
    app). W2 asks for the app origin directly; a chain is what F12 forbids
    because the intermediate link is the one that sticks.
    """
    (res,) = _run([_case("/auth/")])
    assert res["status"] == 302, f"/auth/ -> {res['status']}, want a single-hop 302"
    assert res["location"] == f"{APP_ORIGIN}/auth", (
        f"/auth/ -> {res['location']!r}; want {APP_ORIGIN + '/auth'!r} — a "
        "same-host intermediate hop is the chained redirect F12 forbids"
    )


def test_company_host_auth_endpoints_also_302_to_the_app_origin() -> None:
    """#4054 moved the surface off BOTH marketing hosts."""
    (res,) = _run([_case("/auth/start", host=COMPANY_HOST)])
    assert res["status"] == 302 and res["location"] == f"{APP_ORIGIN}/auth/start", res


def test_the_exact_auth_rule_is_grandfathered_at_301() -> None:
    """Pinned AS-IS, on purpose.

    Shipped by #4054, so it is already in browsers' caches; changing it is its
    own decision. The pin makes any change a visible edit, not a silent drift.
    """
    (res,) = _run([_case("/auth")])
    assert res["status"] == 301 and res["location"] == f"{APP_ORIGIN}/auth", (
        "the grandfathered /auth 301 changed — if that is deliberate, change this "
        "pin in the same commit so it is not a silent drift"
    )


@pytest.mark.parametrize("path", ["/authorize", "/authentication", "/author", "/authx"])
def test_auth_subtree_is_not_a_prefix_false_positive(path: str) -> None:
    """Only the `/auth/` SUBTREE moves — the guard is a path-segment prefix.

    `/authorize` is an OAuth-standard-looking path a reader might expect to be
    swept up; it must fall through untouched.
    """
    (res,) = _run([_case(path)])
    assert res["location"] is None, f"{path} was swept into the /auth redirect: {res}"
    assert res["status"] != 302 or res["next"] == "next", f"{path} was redirected: {res}"


# ── The safety claim in the `/auth/*` rationale must match the record ───────
# `docs/ops/auth-email-flows.md` §3 is the repo's record of the Supabase redirect
# allow-list. The `/auth/*` comment explains the 302 by naming that allow-list as
# the guard that keeps the redirect safe (#4328) — a load-bearing claim, because a
# lane reading this file is meant to be able to rely on it. #8680 is the OPEN audit
# of that same property ("is https://tortoise.premiselabs.co/auth/callback still in
# the allow-list?"), and it states the property is UNVERIFIED and conditional.
# An earlier revision of the comment asserted it as settled ("only the app origin
# may be an authorized target") while §3 requires entries on
# `tortoise.premiselabs.co` and `api.premiselabs.co` — the very host this branch
# serves. The claim and the record must not be free to drift apart.


def _auth_branch_comment() -> str:
    """The contiguous `//` block immediately above the `/auth/` 302 branch.

    Scoped to that block rather than the whole file so an unrelated comment
    elsewhere cannot satisfy — or break — the claim this guard pins.
    """
    lines = MIDDLEWARE.read_text(encoding="utf-8").splitlines()
    branch = next(
        (i for i, line in enumerate(lines) if 'url.pathname.startsWith("/auth/")' in line),
        None,
    )
    assert branch is not None, (
        "the `/auth/` branch is gone from website/functions/_middleware.ts — the routing this "
        "rationale justifies no longer exists, so this guard has no subject"
    )
    start = branch
    while start > 0 and lines[start - 1].lstrip().startswith("//"):
        start -= 1
    assert start < branch, "the `/auth/` branch lost its rationale comment entirely"
    return "\n".join(lines[start:branch])


def test_the_auth_comment_names_the_allowlist_record_as_the_open_audit() -> None:
    """The guard the `/auth/*` comment names must match `docs/ops/auth-email-flows.md` §3.

    #8680 (the open audit of the allow-list) records the property as unverified
    and conditional; §3 records required entries outside the app origin. A comment
    that asserts the property as present-tense fact therefore contradicts both, and
    a lane reading the routing decision could close the audit as already done.
    The fix is to state the property that must hold against the record.
    """
    record = ALLOWLIST_RECORD.read_text(encoding="utf-8")
    section = record.split("## 3. Redirect allowlist", 1)
    assert len(section) == 2, (
        "docs/ops/auth-email-flows.md no longer has a '## 3. Redirect allowlist' section — the "
        "record this guard checks the comment against has moved"
    )
    table = section[1].split("## 4.", 1)[0]
    # Guard the guard: the refuting fact must still be in the record, or this test
    # could pass for the wrong reason once the allow-list itself changes.
    assert TORTOISE_HOST in table, (
        f"§3 of the allow-list record no longer names {TORTOISE_HOST} — the fact that the list "
        "is wider than the app origin is gone, so re-derive this guard before trusting it"
    )

    comment = _auth_branch_comment()
    assert "auth-email-flows.md" in comment, (
        "the `/auth/*` rationale names the redirect allow-list as the guard keeping the 302 "
        f"safe but does not cite the repo's record of that list (docs/ops/auth-email-flows.md "
        f"§3) and the OPEN audit ({AUDIT_ISSUE}) of the same property — an uncited claim "
        "reads as settled"
    )
    assert AUDIT_ISSUE in comment, (
        f"the `/auth/*` rationale no longer names {AUDIT_ISSUE}, the open audit of the "
        "allow-list property it relies on — the comment then states a security control as a "
        "fact with nothing recording that it is unverified"
    )
    assert "only the app origin" not in comment, (
        "the `/auth/*` rationale asserts that only the app origin may be an authorized "
        f"redirect target, but docs/ops/auth-email-flows.md §3 requires entries on "
        f"{TORTOISE_HOST} and api.premiselabs.co. State the property that MUST hold and the "
        f"open audit ({AUDIT_ISSUE}); do not assert a control the record contradicts "
        "(#8682 review, P1)"
    )
