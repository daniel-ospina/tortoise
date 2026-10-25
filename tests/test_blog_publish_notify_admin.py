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
  * V6 `save.ts` notifies ALWAYS on the create path (a row that did not exist
    cannot have been live), and on the UPDATE path ONLY when the save makes the
    URL live — `consoleSaveMakesUrlLive` in `lib/publish-transition.ts`, the same
    rule the agent API applies. Saving an already-published post, including via
    the editor's "Save & Republish" button, notifies nothing (#9680 item 3);
    clearing a hold on a published post does. `clearHoldPost` fires it when the
    row it just wrote is `published` (a held DRAFT's clear-hold notifies
    nothing — that bound is in the function, not in the button), and
    `publishPost` fires it on the drafts-queue one-click Publish, which is the
    console's other publish entry point and never passes through the editor
    form's save.
  * #9680's console half decides by EXECUTION, not by source text: the two
    predicates are driven case by case (`_run_predicate`), and the derivation of
    the console's two `VisibilityState`s lives in the import-free module for that
    reason — composing them inline in `save.ts` left the whole composition pinned
    by regex, where swapping the two arguments stayed green while disabling the
    publish submission.

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

import pytest

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE = REPO_ROOT / "website"
NOTIFY_FUNCTION = WEBSITE / "functions" / "blog" / "api" / "notify-publish.ts"
INDEXNOW_MODULE = WEBSITE / "functions" / "blog" / "_shared" / "indexnow.ts"
SAVE_UNIT = WEBSITE / "apps" / "blog-admin" / "src" / "lib" / "save.ts"
BLOG_API = WEBSITE / "apps" / "blog-admin" / "src" / "lib" / "blog-api.ts"
PUBLISH_TRANSITION = WEBSITE / "apps" / "blog-admin" / "src" / "lib" / "publish-transition.ts"
POSTS_LIST = WEBSITE / "apps" / "blog-admin" / "src" / "pages" / "PostsList.tsx"
POST_EDITOR = WEBSITE / "apps" / "blog-admin" / "src" / "pages" / "PostEditor.tsx"

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


_DRIVER_DIR: Path | None = None


def _driver_path() -> Path:
    """Write the driver ONCE, not once per case, and tolerate its removal.

    A temp dir per call cost the same node start PLUS mkdir/rmtree, and the file is
    constant. It is re-created if it disappeared (a cleaner, or a sibling test
    module tidying a shared tmp root), so a missing file can never present as a
    node loader error.
    """
    global _DRIVER_DIR
    if _DRIVER_DIR is None or not (_DRIVER_DIR / "driver.mjs").is_file():
        if _DRIVER_DIR is not None:
            shutil.rmtree(_DRIVER_DIR, ignore_errors=True)
        _DRIVER_DIR = Path(tempfile.mkdtemp(prefix="notify-publish-"))
        (_DRIVER_DIR / "driver.mjs").write_text(_DRIVER)
    return _DRIVER_DIR / "driver.mjs"


@pytest.fixture(scope="module", autouse=True)
def _node_floor():
    """The floor probe spawns node too; once per module is enough, and it must run
    BEFORE the first case rather than 17 times alongside them."""
    require_node_floor(NODE_FLOOR_STRIP_TYPES, what="the admin-editor publish notification guard", absent="fail")
    yield


def _run(case: dict) -> dict:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(_driver_path()), str(NOTIFY_FUNCTION), str(INDEXNOW_MODULE), json.dumps(case)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
    )
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


def test_v2g_archived_is_not_live():
    """Archived is terminal and never public. Without a case here, relaxing the
    guard to `status !== "draft"` leaves the suite green while a terminal row's
    URL is submitted."""
    out = _run({
        "row": {"slug": "agent-memory", "status": "archived"},
        "body": {"slug": "agent-memory"},
    })
    assert out["status"] == 409
    assert out["body"]["error"] == "not_live"
    assert out["submissions"] == []


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
    """KEPT for its two MIRROR arms; its UPDATE-path arm was REVERSED in #9680.

    It used to require exactly
    `if (mode === 'publish' && ctx.form.slug) { void notifyPublished(...) }` and
    described it as "covers 'publish again'". That form is the defect: on an
    already-published post, `mode === 'publish'` is the editor's "Save &
    Republish" button, so it notified on an EDIT THAT LEAVES THE ROW PUBLISHED —
    the case #9680 forbids twice (item 3, indicator (c)). The 24h window could
    not cover for it, because a window bounds REPEATS and cannot classify a
    transition: an edit saved 25 hours later passed it and submitted a URL whose
    content had not changed.

    So the update-path arm now asserts the opposite direction — the call is
    guarded by the shared transition rule — and the rule itself is driven case by
    case by `test_the_transition_predicate_admits_only_a_write_that_makes_the_url_live`
    below rather than pinned as source text here.
    """
    src = SAVE_UNIT.read_text()
    # create path: a row that did not exist before cannot have been live, so a
    # create always transitions — no predicate needed, and none used.
    assert re.search(r"if \(mode === 'publish' && created\.slug\) void notifyPublished\(created\.slug\)", src), (
        "the create path must notify on publish"
    )
    # update path: guarded by the ONE transition rule, whose DERIVATION lives in
    # the import-free module so the tests below EXECUTE the composition. This is
    # the only thing `save.ts` keeps — the call itself. (Drop it and nothing
    # submits at all; the executed arm cannot see that, which is why this one
    # regex exists.)
    assert re.search(
        r"const makesUrlLive = consoleSaveMakesUrlLive\(ctx, ctx\.form, record\.status \?\? 'draft'\);", src
    ), (
        "the update path must decide through consoleSaveMakesUrlLive (lib/publish-transition.ts), "
        "not on `mode` alone"
    )
    assert re.search(r"if \(makesUrlLive && ctx\.form\.slug\) \{\s*\n\s*void notifyPublished\(ctx\.form\.slug\);", src), (
        "the notify call must be guarded by the transition decision, not by `mode`"
    )
    # THE REGRESSION THIS PINS (#9680): the pre-fix condition. Its absence is the
    # point — restore it and an edit that leaves the row published submits again.
    assert not re.search(r"if \(mode === 'publish' && ctx\.form\.slug\) \{", src), (
        "the update path must NOT notify on `mode === 'publish'` alone — that is the "
        "'Save & Republish' button, i.e. an edit that leaves the row published"
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


# ── #9680 item 3 / indicator (c): the console must not submit on an EDIT ──────
#
# WHY THE RULE IS DRIVEN HERE AND NOT ONLY PINNED AS SOURCE TEXT. The defect this
# closes is a guard that was *present and green*: `save.ts` notified on
# `mode === 'publish'`, every test asserting "one submission" used a fresh row,
# and the 24h window in `_shared/indexnow.ts` made an edit-only save look
# identical to a publish. A regex can pin the SHAPE of the fix and can never say
# what it decides, so both predicates are executed, case by case — over every
# visibility transition `becomesLive` answers, and over every CONTEXT shape the
# console's save can hand `consoleSaveMakesUrlLive`.
#
# That second arm is not decoration (review finding on the first cut): with only
# `becomesLive` executed, `save.ts` composed the two `VisibilityState` literals
# inline and every assertion about that composition was text. Swapping the two
# arguments kept all of them green — same expressions, other object literal, and
# TypeScript cannot object because both parameters are the same type — while
# `becomesLive(after, before)` made publishing a DRAFT submit nothing. The
# derivation therefore lives in the import-free module, and what `save.ts` keeps
# is one regex: that it calls it.
#
# The rule has ONE home (`lib/publish-transition.ts`). It mirrors the agent API's
# `becameLive` (`website/functions/blog/api/posts/[[path]].ts`), whose own comment
# already states it — "Everything else submits nothing: a draft write, an
# unpublish, an edit that leaves a live row live." The console was the one path
# out of step.

_TRANSITION_DRIVER = """
const mod = await import(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
console.log(JSON.stringify(cases.map((c) => mod.becomesLive(c.before, c.after))));
"""

# The console's save decision, driven through the SAME import-free module. See the
# header block above: composing these two states inside `save.ts` left this whole
# composition pinned by text, and swapping the arguments then stayed green.
_CONSOLE_SAVE_DRIVER = """
const mod = await import(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
console.log(JSON.stringify(cases.map((c) => mod.consoleSaveMakesUrlLive(c.ctx, c.form, c.statusAfter))));
"""

# (before, after, expected, what the case IS)
TRANSITION_CASES: list[tuple[dict, dict, bool, str]] = [
    # ── the writes that MUST notify: the URL starts being served ─────────────
    (
        {"status": "draft", "hold_for_review": False},
        {"status": "published", "hold_for_review": False},
        True,
        "publish a draft",
    ),
    (
        {"status": "draft", "hold_for_review": True},
        {"status": "published", "hold_for_review": False},
        True,
        "publish a held draft and clear the hold in the same save",
    ),
    (
        {"status": "published", "hold_for_review": True},
        {"status": "published", "hold_for_review": False},
        True,
        "clear the hold on an already-published post (#9830) — status unchanged, URL goes 404 → 200",
    ),
    (
        {"status": "published", "hold_for_review": None},
        {"status": "published", "hold_for_review": False},
        True,
        "clear a NULL hold — NULL counts as held, so this is the same transition",
    ),
    # ── the writes that must submit NOTHING — #9680 item 3 / indicator (c) ───
    (
        {"status": "published", "hold_for_review": False},
        {"status": "published", "hold_for_review": False},
        False,
        "AN EDIT THAT LEAVES STATUS PUBLISHED — the editor's 'Save & Republish' on a live post. "
        "This is the row the pre-fix form submitted once the 24h window had passed.",
    ),
    (
        {"status": "published", "hold_for_review": True},
        {"status": "published", "hold_for_review": True},
        False,
        "a save that leaves a held post held — its URL was 404 before and is 404 after",
    ),
    (
        {"status": "published", "hold_for_review": False},
        {"status": "published", "hold_for_review": True},
        False,
        "a save that HIDES a live article — nothing to notify, and IndexNow has no retraction",
    ),
    (
        {"status": "draft", "hold_for_review": True},
        {"status": "published", "hold_for_review": True},
        True,
        "publish a held draft with the hold still on — arm 1 answers TRUE while the URL is 404 on "
        "both sides. BOUNDED, not harmless-by-accident: `api/notify-publish.ts` re-reads the row and "
        "answers 409 not_live, so nothing is submitted. Kept as-is because tightening arm 1 would "
        "change the AGENT API's behaviour; recorded here so the space is complete rather than assumed.",
    ),
    (
        {"status": "published", "hold_for_review": False},
        {"status": "draft", "hold_for_review": False},
        False,
        "unpublish",
    ),
    (
        {"status": "draft", "hold_for_review": False},
        {"status": "draft", "hold_for_review": False},
        False,
        "draft save",
    ),
    (
        {"status": "published", "hold_for_review": False},
        {"status": "archived", "hold_for_review": False},
        False,
        "archive — terminal, and not a publish",
    ),
]


def _run_predicate(driver_src: str, driver_name: str, payload: list) -> list[bool]:
    """Execute the SHIPPED predicates once for every case (one node, not ten)."""
    driver = _driver_path().parent / driver_name
    driver.write_text(driver_src)
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            str(driver),
            str(PUBLISH_TRANSITION),
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _run_transition(cases: list[tuple[dict, dict, bool, str]]) -> list[bool]:
    return _run_predicate(
        _TRANSITION_DRIVER,
        "transition.mjs",
        [{"before": c[0], "after": c[1]} for c in cases],
    )


# (ctx, form, statusAfter, expected, what the console SAVE state IS)
CONSOLE_SAVE_CASES: list[tuple[dict, dict, str, bool, str]] = [
    (
        {"wasPublished": False, "wasHeld": False},
        {"hold_for_review": False},
        "published",
        True,
        "publishing a draft from the editor MUST submit — the capability, not a guard. "
        "SWAPPING `becomesLive`'s two arguments reds exactly this case.",
    ),
    (
        {"wasPublished": True, "wasHeld": False},
        {"hold_for_review": False},
        "published",
        False,
        "'Save & Republish' on a live post — an edit that leaves status published (#9680 item 3)",
    ),
    (
        {"wasPublished": True, "wasHeld": True},
        {"hold_for_review": False},
        "published",
        True,
        "clearing the hold on a published post (#9830) — status unchanged, URL goes 404 → 200. "
        "Making the prior hold always false reds this case.",
    ),
    (
        {"wasPublished": True, "wasHeld": True},
        {"hold_for_review": True},
        "published",
        False,
        "a save that leaves it held — 404 before, 404 after. Hard-coding the AFTER hold to false "
        "reds this case.",
    ),
    (
        {"wasPublished": True},
        {"hold_for_review": False},
        "published",
        False,
        "`wasHeld` ABSENT — the forgotten-input default. It must skip the notification, never "
        "submit on an ordinary edit.",
    ),
    (
        {"wasPublished": True, "wasHeld": False},
        {"hold_for_review": False},
        "draft",
        False,
        "unpublish (the editor's 'Save as draft' on a live post)",
    ),
    (
        {"wasPublished": False, "wasHeld": False},
        {"hold_for_review": False},
        "draft",
        False,
        "draft save",
    ),
]


def test_the_console_save_decision_is_executed_over_every_context_shape():
    """The composition `save.ts` performs, decided by running it, not read as text.

    MUTATION THIS CATCHES (the review's P2, and the reason this test exists): with
    only `becomesLive` executed, `save.ts` built the two states inline and every
    assertion about them was a regex. Swapping the two arguments kept all six
    green — both parameters are `VisibilityState`, so TypeScript cannot object —
    while publishing a DRAFT from the editor submitted nothing, i.e. the suite
    reported OK over a disabled capability. The first case reds on that swap; the
    third on a prior-hold hard-coded to false; the fourth on an after-hold
    hard-coded to false; the fifth on the forgotten-input default flipping.
    """
    actual = _run_predicate(
        _CONSOLE_SAVE_DRIVER,
        "console-save.mjs",
        [
            {"ctx": c[0], "form": c[1], "statusAfter": c[2]}
            for c in CONSOLE_SAVE_CASES
        ],
    )
    mismatches = [
        f"{case[4]}\n    ctx={case[0]} form={case[1]} statusAfter={case[2]!r}\n"
        f"    expected {case[3]}, console decision said {got}"
        for case, got in zip(CONSOLE_SAVE_CASES, actual, strict=True)
        if got != case[3]
    ]
    assert not mismatches, "the console save decision is wrong for:\n  " + "\n  ".join(mismatches)


def test_the_transition_predicate_admits_only_a_write_that_makes_the_url_live():
    """#9680 item 3 / indicator (c), decided — not described.

    MUTATION THIS CATCHES: replacing the rule with `return after.status ===
    'published'` (the pre-fix intent: "publish save ⇒ notify") reds the
    `published → published` case, which is the one the 24h window could not
    protect. Dropping the hold arm reds the three hold cases. Reading NULL as
    clear reds the NULL-hold case.
    """
    actual = _run_transition(TRANSITION_CASES)
    mismatches = [
        f"{case[3]}\n    before={case[0]} after={case[1]}\n    expected {case[2]}, predicate said {got}"
        for case, got in zip(TRANSITION_CASES, actual, strict=True)
        if got != case[2]
    ]
    assert not mismatches, "the publish-transition rule is wrong for:\n  " + "\n  ".join(mismatches)


def test_the_editor_supplies_the_hold_state_the_rule_needs():
    """The rule is only as good as its inputs, and both come from the ROW.

    MUTATION THIS CATCHES: dropping `wasHeld` from `PostEditor`'s save context
    makes `ctx.wasHeld` undefined, the rule reads the row as never-held, and
    clearing a hold on a published post stops being submitted — a real publish
    transition going silently un-notified, which is the #7751 failure shape. And
    `existingPost.hold_for_review` must come from the row select (`POST_SELECT`
    carries it), or every publish save reads the row as held and the edit case
    above submits again.
    """
    editor = POST_EDITOR.read_text()
    assert re.search(
        r"wasHeld: existingPost \? existingPost\.hold_for_review !== false : false", editor
    ), "PostEditor must pass the stored hold state (`!== false`: NULL counts as held)"
    assert re.search(r"\bhold_for_review\b", (BLOG_API.read_text().split("POST_SELECT")[1])[:900]), (
        "POST_SELECT must select hold_for_review, or the editor's `existingPost` cannot carry it"
    )


def test_the_console_call_sites_that_keep_the_coarse_guard_are_transition_only_by_construction():
    """Why `publishPost` / `clearHoldPost` may fire on every call while the editor
    save may not — and the mechanism is NOT the same for both.

    `test_v6_...` pins that those two functions notify unconditionally. What bounds
    them differs, and the first cut of this test named only the render guards:

    * `publishPost` — bounded by its BUTTON, rendered only for `status === 'draft'`.
    * `clearHoldPost` — bounded by the FUNCTION, which notifies only when the row it
      just wrote is `published` (`updated.status === 'published' &&`). The render
      guard alone would not do it: Clear-hold also renders for a held DRAFT, where
      clearing the hold changes no status and the URL stays 404. A render state
      cannot classify a transition — the same reasoning this PR applies to the
      editor's button, one button over.

    MUTATION THIS CATCHES: rendering Publish on a published row, or dropping the
    `status === 'published'` half of `clearHoldPost`'s condition, turns that call
    site into a non-transition submitter — the defect this PR fixed in `save.ts`.
    """
    src = POSTS_LIST.read_text()
    assert re.search(r"\{post\.status === 'draft' && \(\s*\n\s*<Button[^>]*onClick=\{\(\) => doPublish\.mutate\(\)\}", src), (
        "the drafts-queue Publish must be rendered only for a draft row"
    )
    assert re.search(r"\{post\.hold_for_review && post\.status !== 'archived' && \(", src), (
        "Clear-hold must be rendered only for a held row"
    )
    # …but the render guard is NOT what bounds clearHoldPost: the function's own
    # status check is, and it is the half that survives a held draft.
    assert re.search(
        r"export async function clearHoldPost[\s\S]{0,900}?"
        r"if \(updated\.status === 'published' && updated\.slug\) void notifyPublished\(updated\.slug\);",
        BLOG_API.read_text(),
    ), (
        "clearHoldPost's binding guard is `updated.status === 'published'` — without it a clear on a "
        "held DRAFT notifies (and the URL is 404 before and after)"
    )
