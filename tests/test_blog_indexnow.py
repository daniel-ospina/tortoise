"""#9680 — IndexNow on the publish transition, executed.

WHY THIS EXISTS
---------------
#7751 was CLOSED / COMPLETED while the IndexNow capability was never built: its own
closing comment said the submitter was not implemented, and `git grep -il indexnow`
found three planning documents and zero implementation. The tracker said the
capability existed; nothing did. So the tests here are written to be *falsifiable*
in the two directions the issue names (#9680 V5):

  * delete the submission call            → ``test_v2_*`` must red;
  * fire the trigger on every PATCH       → ``test_v3_*`` must red;
  * drop the once-per-24h window          → ``test_v3_*`` must red as well.

That is why the V2 and V3 cases are separated the way they are. A single
"publish → unpublish → publish" sequence cannot see a fire-on-every-PATCH bug on
its own, because the 24h window masks the extra attempts — so the "edit that
leaves the post published" case is driven against a row that was NEVER submitted
(``lastSubmittedAt: null``), where the window cannot apply and only the TRANSITION
guard can suppress it. The two guards are therefore each isolated by a case that
fails when it is removed.

The REAL exported ``onRequestPatch`` is driven under Node's type-stripping loader
(the convention of ``tests/test_blog_agent_delete_guard.py`` /
``tests/test_blog_post_status_validation.py``) with ``globalThis.fetch`` stubbed.
The stub is a DUMB store: it applies whatever a PATCH carries and answers reads
from what it holds. It contains no dedupe rule of its own — the decision under
test is the real TS in ``_shared/indexnow.ts``, so a test cannot pass by
re-implementing the rule in the mock.

⛔ WHAT THIS DOES NOT CLAIM. IndexNow does not reach Google (Google does not
consume the protocol). Nothing here asserts, names or implies otherwise.

V1 — the key file — is asserted from the repo, because that is where the fact
lives: the deployed 200 cannot be observed before the deploy. The post-deploy
check is the ``curl`` line in the IndexNow section of ``docs/blog-authoring.md``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE = REPO_ROOT / "website"
POSTS_FUNCTION = WEBSITE / "functions" / "blog" / "api" / "posts" / "[[path]].ts"
INDEXNOW_MODULE = WEBSITE / "functions" / "blog" / "_shared" / "indexnow.ts"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml"

SITE = "https://tortoise.premiselabs.co"
AGENT = "blog-indexnow-test"
ENDPOINT = "https://api.indexnow.org/indexnow"

# Drives the REAL handler for a SEQUENCE of PATCHes against one row.
# The `fetch` stub is a dumb store + a recorded vendor endpoint:
#   * `/rest/v1/blog_agent_keys`      → the calling identity
#   * `GET  ?select=indexnow_submitted_at` → what the store holds (or a 400 when
#     `stateUnreadable`, which is the shape of "the migration is not applied")
#   * `GET  blog_posts`               → the row
#   * `PATCH` carrying indexnow_submitted_at → persist the stamp
#   * `PATCH` (status/title)          → apply the body, answer the row
#   * `api.indexnow.org`              → record the submission, answer with
#                                       `indexnowStatus` or throw when
#                                       `indexnowThrows`
# `waitUntil` promises are collected and awaited between steps: the handler
# deliberately answers BEFORE the submission settles, so a test that dropped them
# would observe zero submissions and prove nothing.
_DRIVER = """
const posts = await import(process.argv[2]);
const indexnow = await import(process.argv[3]);
const cases = JSON.parse(process.argv[4]);
const out = [];
for (const c of cases) {
  const slug = c.slug || 'indexnow-slug';
  const row = {
    id: 'row-1',
    status: c.status,
    created_by: c.agent || 'blog-indexnow-test',
    body: 'body text',
    indexnow_submitted_at: c.lastSubmittedAt === undefined ? null : c.lastSubmittedAt,
  };
  const submissions = [];
  const logs = [];
  const realLog = console.log, realWarn = console.warn;
  console.log = (...a) => logs.push(a.map(String).join(' '));
  console.warn = (...a) => logs.push(a.map(String).join(' '));
  const pending = [];
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    const method = String((init && init.method) || 'GET').toUpperCase();
    if (u.startsWith('https://api.indexnow.org')) {
      submissions.push({ url: u, body: JSON.parse(String(init.body)) });
      if (c.indexnowThrows) throw new Error('socket hang up');
      return new Response('', { status: c.indexnowStatus || 200 });
    }
    if (u.includes('/rest/v1/blog_agent_keys')) {
      return Response.json([{ agent_name: row.created_by, active: true }]);
    }
    if (u.includes('/rest/v1/blog_posts')) {
      if (method === 'GET' && u.includes('select=indexnow_submitted_at')) {
        if (c.stateUnreadable) {
          return new Response('{"message":"column does not exist"}', { status: 400 });
        }
        return Response.json([{ indexnow_submitted_at: row.indexnow_submitted_at }]);
      }
      if (method === 'GET') return Response.json([{ ...row }]);
      if (method === 'PATCH') {
        const body = JSON.parse(String(init.body));
        if (Object.prototype.hasOwnProperty.call(body, 'indexnow_submitted_at')) {
          row.indexnow_submitted_at = body.indexnow_submitted_at;
          return Response.json([{ ...row }]);
        }
        Object.assign(row, body);
        return Response.json([{ id: row.id, slug }]);
      }
    }
    return Response.json({ success: true });
  };
  const statuses = [];
  for (const step of c.steps) {
    const req = new Request('https://tortoise.premiselabs.co/blog/api/posts/' + slug, {
      method: 'PATCH',
      headers: { 'X-Agent-Key': 'test-agent-key', 'Content-Type': 'application/json' },
      body: JSON.stringify(step),
    });
    const res = await posts.onRequestPatch({
      request: req,
      env: { SUPABASE_URL: 'https://db.test', SUPABASE_SERVICE_ROLE_KEY: 'svc' },
      params: { path: [slug] },
      waitUntil: (p) => { pending.push(p); },
    });
    statuses.push(res.status);
    await Promise.all(pending.splice(0));
  }
  console.log = realLog;
  console.warn = realWarn;
  out.push({ statuses, submissions, logs, key: indexnow.INDEXNOW_KEY });
}
console.log(JSON.stringify(out));
"""

CASES: dict[str, dict] = {
    # ── V2 ────────────────────────────────────────────────────────────────
    # The publish transition: exactly one submission, of the canonical URL.
    "publish_transition": {"status": "draft", "steps": [{"status": "published"}]},
    # ── V3 ────────────────────────────────────────────────────────────────
    # Published → published. The row has NEVER been submitted, so the 24h window
    # cannot explain a zero: only the transition guard can. This is the case that
    # reds when the submit path is made to fire on every PATCH.
    "edit_while_published_legacy": {
        "status": "published",
        "lastSubmittedAt": None,
        "steps": [{"title": "renamed"}],
    },
    # Published → draft. Same isolation.
    "unpublish_legacy": {"status": "published", "steps": [{"status": "draft"}]},
    # Draft → draft.
    "draft_edit": {"status": "draft", "steps": [{"title": "still drafting"}]},
    # Draft → published → edit → unpublish → published, all within one day.
    "republish_same_day": {
        "status": "draft",
        "steps": [{"status": "published"}, {"title": "tweak"}, {"status": "draft"}, {"status": "published"}],
    },
    # ── V4 ────────────────────────────────────────────────────────────────
    "endpoint_500": {"status": "draft", "indexnowStatus": 500, "steps": [{"status": "published"}]},
    "endpoint_unreachable": {"status": "draft", "indexnowThrows": True, "steps": [{"status": "published"}]},
    # ── accepted-with-pending-validation ──────────────────────────────────
    "endpoint_202": {"status": "draft", "indexnowStatus": 202, "steps": [{"status": "published"}]},
    # ── dedupe state unreadable (migration not applied) ───────────────────
    "state_unreadable": {"status": "draft", "stateUnreadable": True, "steps": [{"status": "published"}]},
}


@pytest.fixture(scope="module")
def results() -> dict[str, dict]:
    # #4916: a missing node FAILs here rather than skipping — V2/V3 are the only
    # evidence that the publish path submits exactly once, and a skipped guard
    # looks like a passing one. A present-but-too-old node is a host gap and skips.
    require_node_floor(NODE_FLOOR_STRIP_TYPES, what="the IndexNow publish-transition guard", absent="fail")
    node = shutil.which("node")

    order = list(CASES)
    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [
                node,
                "--experimental-strip-types",
                str(driver),
                str(POSTS_FUNCTION),
                str(INDEXNOW_MODULE),
                json.dumps([CASES[k] for k in order]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    out = json.loads(proc.stdout)
    assert len(out) == len(order), f"driver returned {len(out)} results for {len(order)} cases"
    return dict(zip(order, out, strict=True))


def _submitted_urls(res: dict) -> list[str]:
    return [s["body"]["urlList"][0] for s in res["submissions"]]


def _logs_matching(res: dict, needle: str) -> list[str]:
    return [line for line in res["logs"] if needle in line]


# ── V1: the key file is served from the site root ──────────────────────────
def test_v1_the_key_file_is_served_at_the_site_root(results) -> None:
    """The key file exists, is named after the key, and carries ONLY the key.

    IndexNow proves host ownership by fetching ``${site}/${key}.txt``; the
    endpoint answers 403 for a key it cannot fetch, so a submission without this
    file is silently rejected. Falsifiable three ways: rename the file, change the
    constant, or add anything to the body (a trailing newline included) — the
    issue's contract is "the key and nothing else", and each of those breaks it.

    ⚠️ The file must live directly under ``website/`` because that directory IS
    the deploy upload root (``.github/workflows/deploy-pages.yml`` rsyncs it to
    the Pages stage), which is what makes it answer at ``/<key>.txt`` rather than
    under ``/blog``.
    """
    key = results["publish_transition"]["key"]
    assert re.fullmatch(r"[a-f0-9]{32}", key), (
        f"the IndexNow key is {key!r}; the issue pins 32 lowercase hex characters"
    )

    key_file = WEBSITE / f"{key}.txt"
    assert key_file.is_file(), (
        f"the IndexNow key file is not served: {key_file} is absent. IndexNow fetches "
        f"{SITE}/{key}.txt to prove host ownership and answers 403 when it is missing."
    )
    # Byte-exact: no trailing newline, no BOM, no comment. The device serves the
    # file as-is, and the protocol says the file "must contain the key".
    assert key_file.read_bytes() == key.encode("utf-8"), (
        f"{key_file.name} must contain the key and nothing else — got "
        f"{key_file.read_bytes()!r}, want {key.encode()!r}"
    )

    # The deploy stage rsync is where a file can silently stop shipping (#3620).
    staging = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"--exclude='?\*\.txt", staging), (
        "the Pages deploy stage now excludes *.txt, so the IndexNow key file would "
        "never reach the site root and every submission would answer 403"
    )


# ── V2: the publish transition submits the canonical URL, once ─────────────
def test_v2_the_publish_transition_submits_the_canonical_url_exactly_once(results) -> None:
    """One outbound request, to the documented endpoint, carrying ONE url.

    Falsifiable in the direction the issue names: delete the ``notifyPublished``
    call from the handler and ``submissions`` is empty.
    """
    res = results["publish_transition"]
    assert res["statuses"] == [200], f"the publish itself must succeed: {res['statuses']}"
    assert len(res["submissions"]) == 1, (
        f"a draft→published transition must submit exactly once — got "
        f"{len(res['submissions'])}: {_submitted_urls(res)}"
    )

    submission = res["submissions"][0]
    assert submission["url"] == ENDPOINT, (
        f"the submission went to {submission['url']!r}, not the documented global "
        f"endpoint {ENDPOINT!r}"
    )
    body = submission["body"]
    canonical = f"{SITE}/blog/indexnow-slug"
    assert body["urlList"] == [canonical], (
        f"the payload must carry exactly the canonical URL {canonical!r} — got {body['urlList']!r}"
    )
    assert body["host"] == "tortoise.premiselabs.co", body["host"]
    assert body["key"] == res["key"], body["key"]
    assert body["keyLocation"] == f"{SITE}/{res['key']}.txt", (
        f"keyLocation must name the file V1 asserts is served — got {body['keyLocation']!r}"
    )
    assert _logs_matching(res, "submitted (status 200)"), (
        f"the submission's result must be logged: {res['logs']}"
    )


# ── V3: nothing is submitted without a publish transition, or twice a day ──
def test_v3_no_submission_without_a_transition_and_never_twice_in_a_day(results) -> None:
    """Three zero cases plus one counted sequence — each isolates a distinct guard.

    * ``edit_while_published_legacy`` / ``unpublish_legacy`` — the row has NEVER
      been submitted, so the 24h window cannot produce a zero here. Only the
      transition guard can, which is why a submit path that fires on every PATCH
      reds this test.
    * ``draft_edit`` — a draft write never submits.
    * ``republish_same_day`` — publish → edit → unpublish → re-publish inside one
      day is ONE submission, not three. Drop the window and this reds.

    The issue's V3 expectation is "1 submission total, not 3" for that sequence;
    the zero cases are what make the same test discriminate on both axes.
    """
    for name in ("edit_while_published_legacy", "unpublish_legacy", "draft_edit"):
        res = results[name]
        assert res["statuses"] and all(s < 400 for s in res["statuses"]), (
            f"{name}: the PATCHes themselves must still succeed: {res['statuses']}"
        )
        assert res["submissions"] == [], (
            f"{name}: submitted {_submitted_urls(res)} — a non-publish-transition PATCH "
            "must submit nothing (IndexNow is a notification, not a spam channel, and "
            "the vendor's own FAQ asks for it to be used on content changes only)"
        )

    sequence = results["republish_same_day"]
    assert sequence["statuses"] == [200, 200, 200, 200], sequence["statuses"]
    assert len(sequence["submissions"]) == 1, (
        "publish → edit → unpublish → re-publish within one day must submit ONCE — "
        f"got {len(sequence['submissions'])}: {_submitted_urls(sequence)}"
    )


# ── V4: a submission failure never fails the publish, and is logged ────────
@pytest.mark.parametrize(
    ("case", "needle"),
    [
        ("endpoint_500", "status 500"),
        ("endpoint_unreachable", "no status — timeout/network"),
    ],
)
def test_v4_a_failed_submission_does_not_fail_the_publish(results, case: str, needle: str) -> None:
    """The post is live either way; the failure must be visible, with its status.

    Both arms are falsifiable: propagating the error from the submission would
    turn the step's status into a 5xx (the ``waitUntil`` promise is awaited here,
    so an exception would surface), and dropping the ``console.warn`` would leave
    nothing in the log — the silent failure this issue exists to prevent.
    """
    res = results[case]
    assert res["statuses"] == [200], (
        f"{case}: a failed IndexNow submission failed the publish ({res['statuses']}) — "
        "the post is live either way"
    )
    assert len(res["submissions"]) == 1, f"{case}: the attempt itself must happen: {res}"
    matching = _logs_matching(res, needle)
    assert matching, f"{case}: no log line carries {needle!r}: {res['logs']}"
    assert any("tortoise.premiselabs.co/blog/indexnow-slug" in line for line in matching), (
        f"{case}: the failure log must name the URL — got {matching}"
    )


def test_a_202_is_accepted_not_logged_as_a_failure(results) -> None:
    """202 = "received, key validation pending" — the FIRST submission's normal answer.

    Pinned because the natural ``status === 200`` success test would log a false
    failure on the very submission that worked — and a log that cries wolf on the
    happy path is a log the operator learns to ignore.
    """
    res = results["endpoint_202"]
    assert res["statuses"] == [200], res["statuses"]
    assert len(res["submissions"]) == 1, res
    assert _logs_matching(res, "submitted (status 202)"), (
        f"a 202 must be logged as accepted: {res['logs']}"
    )
    assert _logs_matching(res, "submit failed") == [], (
        f"a 202 must not be logged as a failure: {res['logs']}"
    )


def test_unreadable_dedupe_state_skips_the_submission_and_says_so(results) -> None:
    """No migration → no proof the URL was not submitted today → submit nothing.

    The direction is deliberate: a SKIPPED submission is recoverable (the
    operator's backfill, or the next publish), a flood is not. The skip is logged,
    so a deploy that ran ahead of its migration is visible instead of turning the
    capability silently dead.
    """
    res = results["state_unreadable"]
    assert res["statuses"] == [200], res["statuses"]
    assert res["submissions"] == [], (
        f"an unreadable stamp must not submit — got {_submitted_urls(res)}"
    )
    assert _logs_matching(res, "state unreadable"), (
        f"the skip must be logged: {res['logs']}"
    )
