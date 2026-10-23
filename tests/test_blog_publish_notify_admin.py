"""#9803 — IndexNow on the ADMIN EDITOR publish path.

WHY THIS EXISTS
---------------
#9680 put the submission on the AGENT API's publish transition. The editor does
not go through that API: `apps/blog-admin/src/lib/blog-api.ts` writes status to
Supabase with supabase-js, so the owner publishing from the UI submitted nothing.
The capability existed and was unreachable by the human who publishes.

`POST /blog/api/notify-publish` is the editor's seam onto the SAME submission
path (`_shared/indexnow.ts`), so the window and the unreadable-state skip are not
re-implemented here.

WHAT IS PINNED, AND WHY EACH CASE CAN FAIL
------------------------------------------
  * V1 admin gate — a non-admin gets 401 and nothing is submitted. Removing the
    `requireAdmin` call reds it.
  * V2 an unpublished slug is refused 409 and NOTHING is submitted. Dropping the
    `status !== 'published'` guard reds it. This is the case that must never be
    softened: IndexNow has no retraction, and the slug comes from the browser.
    V2c is the SECOND half of that predicate: `status='published'` with
    `hold_for_review=true` is filtered out of every public surface, so its URL
    answers 404 — the endpoint must answer 409 and submit nothing. A guard that
    reads only `status` passes V2 and reds V2c.
  * V3 a published slug submits exactly one URL. Deleting the `notifyPublished`
    call reds it.
  * V3b the vendor's REFUSAL is reported as `submit-failed`, not `submitted`.
    Discarding `submitUrl`'s `SubmitResult` (the pre-fix shape) reds it: the
    attempt was made and did not land, and the caller must be able to tell.
  * V4 a published slug inside the 24h window submits NOTHING and reports
    `skipped-window`. Bypassing the shared rule (calling `submitUrl` directly)
    reds it.
  * V5 the row read that fails (500 / unreachable) answers 503 and reports
    `skipped-unreadable` — a store blip is not a publish failure and must not
    look like one to the editor.
  * V5b the OTHER unreadable arm: the submission STAMP read failing is a 200 with
    `skipped-unreadable` and nothing submitted. Returning `skipped-window` there
    (a plausible "just skip it" edit) reds it — the two arms mean different
    things and the editor reads them.
  * V2d a NULL `hold_for_review` is refused, because the renderer serves only
    `= false`. The pre-fix `!== true` predicate passes V2c and reds this: a NULL
    row 404s on the site, so submitting it is the same failure as a held row.
  * V6 `save.ts` fires the notification on `mode === 'publish'` for BOTH the
    create path and the update path, and NOT on a draft save; and
    `clearHoldPost` fires it when clearing a hold makes a published post live —
    the mirror transition, without which the hold fix would leave a real
    publish un-notified; and `publishPost` fires it on the drafts-queue one-click
    Publish, which is the console's other publish entry point and never passes
    through the editor form's save.

The real exported `onRequestPost` runs under Node's type-stripping loader (the
convention of `tests/test_blog_indexnow.py`), with `globalThis.fetch` stubbed as
a dumb store. The 24h decision itself is the real TS in `_shared/indexnow.ts`.

⛔ Google does not consume IndexNow. Nothing here claims otherwise.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE = REPO_ROOT / "website"
NOTIFY_FUNCTION = WEBSITE / "functions" / "blog" / "api" / "notify-publish.ts"
INDEXNOW_MODULE = WEBSITE / "functions" / "blog" / "_shared" / "indexnow.ts"
SAVE_UNIT = WEBSITE / "apps" / "blog-admin" / "src" / "lib" / "save.ts"
BLOG_API = WEBSITE / "apps" / "blog-admin" / "src" / "lib" / "blog-api.ts"

SITE = "https://tortoise.premiselabs.co"

_DRIVER = """
const handler = await import(process.argv[2]);
const indexnow = await import(process.argv[3]);
const c = JSON.parse(process.argv[4]);

const held = c.row && c.row.hold_for_review;
const fullRow = () => ({ id: 'row-1', slug: c.row.slug, status: c.row.status, hold_for_review: held === 'null' ? null : !!held });
// HONOUR `select=`. PostgREST returns ONLY the columns named, so a stub that
// always returns every column hides the fact that the guard and the SELECT are
// ONE change: drop `hold_for_review` from the select and the endpoint reads
// `undefined`, which the `=== false` predicate refuses — every live post answers
// 409 while a permissive stub keeps the suite green (measured: the reviewer
// removed the column and all 14 cases stayed green).
const projectRow = (select) => {
  if (!select) return fullRow();
  const fields = new Set(select.split(',').map((f) => f.trim()).filter(Boolean));
  return Object.fromEntries(Object.entries(fullRow()).filter(([k]) => fields.has(k)));
};
const rows = c.row ? [fullRow()] : [];
const submissions = [];
const logs = [];
const realLog = console.log, realWarn = console.warn;
console.log = (...a) => logs.push(a.map(String).join(' '));
console.warn = (...a) => logs.push(a.map(String).join(' '));

globalThis.fetch = async (url, init) => {
  const u = String(url);
  const method = String((init && init.method) || 'GET').toUpperCase();
  if (u.startsWith('https://api.indexnow.org')) {
    submissions.push({ url: u, body: JSON.parse(String(init.body)) });
    return new Response('', { status: c.indexnowStatus || 200 });
  }
  if (u.includes('/auth/v1/user')) return Response.json({ id: 'user-1' });
  if (u.includes('/rest/v1/blog_admins')) {
    return Response.json(c.isAdmin === false ? [] : [{ user_id: 'user-1' }]);
  }
  if (u.includes('/rest/v1/blog_posts')) {
    if (method === 'GET' && u.includes('select=indexnow_submitted_at')) {
      if (c.stampReadFails) return new Response('{"message":"boom"}', { status: 400 });
      return Response.json([{ indexnow_submitted_at: c.lastSubmittedAt ?? null }]);
    }
    if (method === 'GET' && c.readFails) {
      return new Response('{"message":"boom"}', { status: 500 });
    }
    if (method === 'GET') {
      const m = u.match(/[?&]select=([^&]*)/);
      // HONOUR `slug=eq.` too: the stub must not answer for a slug the endpoint
      // did not ask about, or "the status is read from the store FOR THIS SLUG"
      // is asserted by nothing and a mis-targeted read passes.
      const s = u.match(/[?&]slug=eq\.([^&]*)/);
      const want = s ? decodeURIComponent(s[1]) : null;
      if (want === null || !c.row || want !== c.row.slug) return Response.json([]);
      return Response.json([projectRow(m ? decodeURIComponent(m[1]) : '')]);
    }
    if (method === 'PATCH') return Response.json([{ ...rows[0] }]);
  }
  return Response.json({});
};

const headers = {};
if (c.token !== null) headers['Authorization'] = 'Bearer ' + (c.token || 'test-token');
if (c.body !== undefined) headers['Content-Type'] = 'application/json';
const req = new Request('https://tortoise.premiselabs.co/blog/api/notify-publish', {
  method: 'POST',
  headers,
  body: c.body === undefined ? undefined : (typeof c.body === 'string' ? c.body : JSON.stringify(c.body)),
});
const res = await handler.onRequestPost({
  request: req,
  env: {
    SUPABASE_URL: 'https://db.test',
    SUPABASE_ANON_KEY: 'anon',
    SUPABASE_SERVICE_ROLE_KEY: 'svc',
  },
  params: {},
  waitUntil: () => {},
});
const text = await res.text();
console.log = realLog;
console.warn = realWarn;
console.log(JSON.stringify({
  status: res.status,
  body: text ? JSON.parse(text) : null,
  submissions,
  logs,
  windowMs: indexnow.INDEXNOW_WINDOW_MS,
  indexnowKey: indexnow.INDEXNOW_KEY,
}));
"""


def _run(case: dict) -> dict:
    require_node_floor(NODE_FLOOR_STRIP_TYPES, what="the admin-editor publish notification guard", absent="fail")
    driver = Path(tempfile.mkdtemp(prefix="notify-publish-")) / "driver.mjs"
    driver.write_text(_DRIVER)
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(driver), str(NOTIFY_FUNCTION), str(INDEXNOW_MODULE), json.dumps(case)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
    finally:
        shutil.rmtree(driver.parent, ignore_errors=True)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_v1_non_admin_is_refused_and_nothing_is_submitted():
    out = _run({"isAdmin": False, "row": {"slug": "agent-memory", "status": "published"}, "body": {"slug": "agent-memory"}})
    assert out["status"] == 401
    assert out["submissions"] == []


def test_v1b_no_token_is_refused():
    out = _run({"token": None, "row": {"slug": "agent-memory", "status": "published"}, "body": {"slug": "agent-memory"}})
    assert out["status"] == 401
    assert out["submissions"] == []


def test_v2_unpublished_slug_is_refused_and_never_submitted():
    out = _run({"row": {"slug": "agent-memory", "status": "draft"}, "body": {"slug": "agent-memory"}})
    assert out["status"] == 409
    assert out["body"]["error"] == "not_live"
    assert out["submissions"] == []


def test_v2c_published_but_held_is_refused_and_never_submitted():
    """A held post has status='published' but is filtered out of every public
    surface, so its URL answers 404. Submitting it would hand six engines a dead
    URL — the failure the module header forbids. This case reds if the guard
    checks only `status`."""
    out = _run({
        "row": {"slug": "agent-memory", "status": "published", "hold_for_review": True},
        "body": {"slug": "agent-memory"},
    })
    assert out["status"] == 409
    assert out["body"]["error"] == "not_live"
    assert out["body"]["hold_for_review"] is True
    assert out["submissions"] == []


def test_v2d_null_hold_is_refused_like_held():
    """The renderer serves only `hold_for_review = false`, so a NULL row is not
    public. `!== true` would call it live and submit a URL that 404s."""
    out = _run({
        "row": {"slug": "agent-memory", "status": "published", "hold_for_review": "null"},
        "body": {"slug": "agent-memory"},
    })
    assert out["status"] == 409
    assert out["body"]["error"] == "not_live"
    assert out["submissions"] == []


def test_v2e_the_row_select_actually_asks_for_the_hold_column():
    """The guard and the SELECT are ONE change. PostgREST returns only the named
    columns, so a select without `hold_for_review` reads `undefined`, which the
    `=== false` predicate refuses — every live post answers 409, a total outage.

    The behaviour cases catch it because the driver honours `select=`, but this
    pins the pair directly, so a future edit that relaxes the predicate back to
    `!== true` cannot re-open the hole by also dropping the column."""
    src = NOTIFY_FUNCTION.read_text()
    m = re.search(r"select=id,slug,status[^&`]*", src)
    assert m, "the endpoint must name its columns explicitly in the PostgREST query"
    assert "hold_for_review" in m.group(0), (
        "the row read must select hold_for_review — the live predicate is two columns"
    )


def test_v2b_unknown_slug_is_404_and_never_submitted():
    out = _run({"row": None, "body": {"slug": "nope"}})
    assert out["status"] == 404
    assert out["submissions"] == []


def test_v2f_refusal_is_logged_for_the_operator():
    """A 409 with no log line leaves "why was my publish not submitted?"
    unanswerable from the only place the docs tell the operator to look."""
    out = _run({
        "row": {"slug": "agent-memory", "status": "published", "hold_for_review": True},
        "body": {"slug": "agent-memory"},
    })
    assert out["status"] == 409
    assert any("[indexnow] skipped (not live" in line for line in out["logs"]), out["logs"]


def test_v3_published_slug_submits_exactly_one_canonical_url():
    out = _run({"row": {"slug": "agent-memory", "status": "published"}, "body": {"slug": "agent-memory"}})
    assert out["status"] == 200
    assert out["body"]["result"] == "submitted"
    assert len(out["submissions"]) == 1
    payload = out["submissions"][0]["body"]
    assert payload["urlList"] == [f"{SITE}/blog/agent-memory"]
    assert out["body"]["url"] == f"{SITE}/blog/agent-memory"
    # The protocol's key must be the DEPLOYED one, and the file it points at must
    # be the one the repo ships — a submission with an unserved key is rejected
    # by every engine.
    assert payload["key"] == out["indexnowKey"]
    assert payload["keyLocation"] == f"{SITE}/{out['indexnowKey']}.txt"
    # The key must be SERVED at that path, not merely named: an unserved key is
    # rejected by every engine, so the assertion that matters is that the repo
    # ships the file `keyLocation` points at.
    key_file = WEBSITE / f"{out['indexnowKey']}.txt"
    assert key_file.is_file(), f"missing key file {key_file}"
    assert key_file.read_text().strip() == out["indexnowKey"]


def test_v3b_vendor_rejection_is_reported_as_submit_failed():
    out = _run({
        "row": {"slug": "agent-memory", "status": "published"},
        "body": {"slug": "agent-memory"},
        "indexnowStatus": 403,
    })
    assert out["status"] == 200
    assert out["body"]["result"] == "submit-failed"
    assert len(out["submissions"]) == 1


def test_v4_inside_the_window_nothing_is_submitted():
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    out = _run({
        "row": {"slug": "agent-memory", "status": "published"},
        "body": {"slug": "agent-memory"},
        "lastSubmittedAt": recent,
    })
    assert out["status"] == 200
    assert out["body"]["result"] == "skipped-window"
    assert out["submissions"] == []
    assert any("[indexnow] skipped" in line for line in out["logs"])


def test_v5_unreadable_row_is_503_not_a_publish_failure():
    out = _run({"readFails": True, "body": {"slug": "agent-memory"}})
    assert out["status"] == 503
    assert out["body"]["result"] == "skipped-unreadable"
    assert out["submissions"] == []


def test_v5d_malformed_body_is_400():
    out = _run({"body": "{not json"})
    assert out["status"] == 400
    assert out["submissions"] == []


def test_v5e_json_null_body_is_400_not_500():
    """A body of literally `null` parses successfully and then threw on `.slug` —
    a 500 out of a handler whose documented answer is 400."""
    out = _run({"row": {"slug": "agent-memory", "status": "published"}, "body": "null"})
    assert out["status"] == 400
    assert out["body"]["error"] == "invalid_slug"
    assert out["submissions"] == []


def test_v5c_invalid_slug_is_400():
    out = _run({"body": {"slug": "../etc/passwd"}})
    assert out["status"] == 400
    assert out["submissions"] == []


def test_v5b_stamp_read_failure_is_a_200_skipped_unreadable():
    """The row is live and readable, but the submission STAMP cannot be read: the
    endpoint answers 200 with `skipped-unreadable` and submits nothing. Reporting
    `skipped-window` instead would tell the editor "already submitted" about a
    post that was never submitted."""
    out = _run({
        "row": {"slug": "agent-memory", "status": "published"},
        "body": {"slug": "agent-memory"},
        "stampReadFails": True,
    })
    assert out["status"] == 200
    assert out["body"]["result"] == "skipped-unreadable"
    assert out["submissions"] == []


def test_v6_editor_fires_on_publish_only_for_both_paths():
    src = SAVE_UNIT.read_text()
    # create path
    assert re.search(r"if \(mode === 'publish' && created\.slug\) void notifyPublished\(created\.slug\)", src), (
        "the create path must notify on publish"
    )
    # update path
    assert re.search(r"if \(mode === 'publish' && ctx\.form\.slug\) \{\s*\n\s*void notifyPublished\(ctx\.form\.slug\);", src), (
        "the update path must notify on publish (covers 'publish again')"
    )
    # and nothing outside those two guarded sites (the import carries no paren)
    assert src.count("notifyPublished(") == 2, "expected exactly two call sites"
    # the MIRROR transition: clearing a hold on a published post makes its URL
    # live, so that save is the event worth submitting
    api = BLOG_API.read_text()
    assert re.search(r"export async function clearHoldPost[\s\S]{0,900}?void notifyPublished\(updated\.slug\)", api), (
        "clearHoldPost must notify when it makes a published post live"
    )
    assert re.search(r"export async function publishPost[\s\S]{0,900}?void notifyPublished\(updated\.slug\)", api), (
        "publishPost (the drafts-queue one-click Publish) must notify"
    )
    assert api.count("void notifyPublished(") == 2, (
        "expected exactly two console call sites: publishPost and clearHoldPost"
    )
