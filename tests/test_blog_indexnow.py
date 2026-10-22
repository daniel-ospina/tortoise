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

import datetime
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

# Drives the REAL handler for a SEQUENCE of PATCHes against one row (or ONE
# POST create, for the `post` case).
# The `fetch` stub is a dumb store + a recorded vendor endpoint:
#   * `/rest/v1/blog_agent_keys`      → the calling identity
#   * `GET  ?select=indexnow_submitted_at` → what the store holds (or a 400 when
#     `stateUnreadable`, which is the shape of "the migration is not applied")
#   * `GET  blog_posts`               → the row
#   * `POST blog_posts`               → the created row (the create path)
#   * `PATCH` carrying indexnow_submitted_at → persist the stamp
#   * `PATCH` (status/title)          → apply the body, answer the row
#   * `api.indexnow.org`              → record the submission (`signal` included,
#                                       so a removed timeout is visible), answer
#                                       with `indexnowStatus` or throw when
#                                       `indexnowThrows`
# `waitUntil` promises are collected and awaited between steps: the handler
# deliberately answers BEFORE the submission settles, so a test that dropped them
# would observe zero submissions and prove nothing. The count at the moment each
# handler call RESOLVES is recorded too (`atResolve`) — that is what distinguishes
# `waitUntil(p)` from `await p`, which is otherwise invisible to a test whose stub
# answers instantly.
#
# The driver also returns `windowMs` and the results of `shouldSubmit` on fixed
# probes, so the window's VALUE and its untaken branches are pinned rather than
# merely exercised once by luck.
_DRIVER = """
const posts = await import(process.argv[2]);
const indexnow = await import(process.argv[3]);
const cases = JSON.parse(process.argv[4]);
const probes = JSON.parse(process.argv[5] || '[]').map((p) => ({
  lastSubmittedAt: p.lastSubmittedAt === undefined ? undefined : p.lastSubmittedAt,
  nowMs: p.nowMs,
}));
const probeResults = probes.map((p) => indexnow.shouldSubmit(p));
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
  const stateCalls = [];
  const logs = [];
  const realLog = console.log, realWarn = console.warn;
  console.log = (...a) => logs.push(a.map(String).join(' '));
  console.warn = (...a) => logs.push(a.map(String).join(' '));
  const pending = [];
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    const method = String((init && init.method) || 'GET').toUpperCase();
    if (u.startsWith('https://api.indexnow.org')) {
      submissions.push({
        url: u,
        body: JSON.parse(String(init.body)),
        hasSignal: !!(init && init.signal),
      });
      if (c.indexnowThrows) throw new Error('socket hang up');
      return new Response('', { status: c.indexnowStatus || 200 });
    }
    if (u.includes('/rest/v1/blog_agent_keys')) {
      return Response.json([{ agent_name: row.created_by, active: true }]);
    }
    if (u.includes('/rest/v1/blog_posts')) {
      if (method === 'GET' && u.includes('select=indexnow_submitted_at')) {
        stateCalls.push({ kind: 'read', hasSignal: !!(init && init.signal) });
        if (c.stateUnreadable) {
          return new Response('{"message":"column does not exist"}', { status: 400 });
        }
        if (c.stateNoRow) return Response.json([]);
        return Response.json([{ indexnow_submitted_at: row.indexnow_submitted_at }]);
      }
      if (method === 'GET') return Response.json([{ ...row }]);
      if (method === 'POST') {
        const body = JSON.parse(String(init.body));
        Object.assign(row, body);
        return Response.json([{ id: row.id, slug: row.slug || slug }]);
      }
      if (method === 'PATCH') {
        const body = JSON.parse(String(init.body));
        if (Object.prototype.hasOwnProperty.call(body, 'indexnow_submitted_at')) {
          stateCalls.push({ kind: 'stamp', hasSignal: !!(init && init.signal) });
          if (c.stampFails) return new Response('{"message":"boom"}', { status: 500 });
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
  const atResolve = [];
  const flush = async () => { await Promise.all(pending.splice(0)); };
  if (c.post) {
    const req = new Request('https://tortoise.premiselabs.co/blog/api/posts', {
      method: 'POST',
      headers: { 'X-Agent-Key': 'test-agent-key', 'Content-Type': 'application/json' },
      body: JSON.stringify(c.post),
    });
    const res = await posts.onRequestPost({
      request: req,
      env: { SUPABASE_URL: 'https://db.test', SUPABASE_SERVICE_ROLE_KEY: 'svc' },
      params: { path: [] },
      waitUntil: (p) => { pending.push(p); },
    });
    statuses.push(res.status);
    await res.text();
    atResolve.push(submissions.length);
    await flush();
  }
  for (const step of c.steps || []) {
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
    await res.text();
    atResolve.push(submissions.length);
    await flush();
  }
  console.log = realLog;
  console.warn = realWarn;
  out.push({ statuses, atResolve, submissions, stateCalls, logs, key: indexnow.INDEXNOW_KEY, stamp: row.indexnow_submitted_at });
}
console.log(JSON.stringify({
  cases: out,
  windowMs: indexnow.INDEXNOW_WINDOW_MS,
  submitTimeoutMs: indexnow.INDEXNOW_TIMEOUT_MS,
  stateTimeoutMs: indexnow.INDEXNOW_STATE_TIMEOUT_MS,
  probeResults,
}));
"""

# Stamps for the through-the-handler window cases are built WHEN THE CASES ARE
# SERIALISED, not at import (see ``run_driver``): the handler stamps
# ``Date.now()``, so a 23 h stamp built at collection time would be a fixed
# 3600 s budget consumed by everything else pytest does before this module's
# first test. The sentinels below stand in for the real ISO strings.
def _stamp_hours_ago(hours: float) -> str:
    return (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")


STAMP_SENTINEL_HOURS = {"@expired": 25, "@fresh": 23}


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
    # A FAILED attempt still burns the window: 500 on the publish, then
    # unpublish → re-publish is ONE submission, not two. This is the case that
    # reds if the stamp is written only on a SUCCESSFUL submission (which is the
    # change that turns a vendor outage into a flood of re-attempts).
    "failed_submit_then_republish": {
        "status": "draft",
        "indexnowStatus": 500,
        "steps": [{"status": "published"}, {"status": "draft"}, {"status": "published"}],
    },
    # A stamp OLDER than the window submits again — THROUGH THE HANDLER, not only
    # through the pure function. The probes below pin `shouldSubmit` itself; this
    # pins the CALLER, because "never re-submit once stamped" re-implemented one
    # level up (`if (!shouldSubmit(...) || lastSubmittedAt)`) leaves the pure
    # probes green. The sentinel stamps below are materialised by the `payload`
    # fixture, milliseconds before node runs.
    "expired_stamp_resubmits": {"status": "draft", "lastSubmittedAt": "@expired", "steps": [{"status": "published"}]},
    "fresh_stamp_does_not_resubmit": {"status": "draft", "lastSubmittedAt": "@fresh", "steps": [{"status": "published"}]},
    # The stamp write fails (our own control plane 5xx). The submission still
    # happens and the publish still 200s, but the window was NOT recorded — so the
    # operator must be told, or the next re-publish silently submits again with
    # nothing saying why.
    "stamp_fails": {"status": "draft", "stampFails": True, "steps": [{"status": "published"}]},
    # ── V4 ────────────────────────────────────────────────────────────────
    "endpoint_500": {"status": "draft", "indexnowStatus": 500, "steps": [{"status": "published"}]},
    "endpoint_unreachable": {"status": "draft", "indexnowThrows": True, "steps": [{"status": "published"}]},
    # ── accepted-with-pending-validation ──────────────────────────────────
    "endpoint_202": {"status": "draft", "indexnowStatus": 202, "steps": [{"status": "published"}]},
    # ── dedupe state unreadable (migration not applied) ───────────────────
    "state_unreadable": {"status": "draft", "stateUnreadable": True, "steps": [{"status": "published"}]},
    # A read that answers with zero rows is ALSO "unreadable", not "never
    # submitted": it must skip, and it must log (the module's "every read is
    # logged" contract, and the only way the runbook's grep can see it).
    "state_no_row": {"status": "draft", "stateNoRow": True, "steps": [{"status": "published"}]},
    # ── the create path (deliberately NOT instrumented) ───────────────────
    # `POST /blog/api/posts` with status=published publishes immediately, and it
    # does NOT notify: #9680 names the PATCH transition as the trigger. Pinned so
    # the disclosed gap cannot silently become an instrumented path (or silently
    # stay one) without a test changing alongside it.
    "direct_publish_create": {
        "post": {"title": "Created published", "body": "body text", "slug": "indexnow-slug", "status": "published"},
    },
}

# Fixed `shouldSubmit` probes: driven through the REAL exported function, so the
# window's VALUE and its untaken branches are pinned rather than merely exercised
# once by luck. Each of these reds a different mutant that the sequence cases
# cannot see: `shouldSubmit` that always says yes (the window is gone — the V3
# sequence reds too, but this names it), `shouldSubmit` that always says NO (never
# re-submit an expired stamp: every sequence case stays green, which is why this
# probe set exists), a window shortened to seconds, and the unparseable-stamp
# branch flipped.
NOW_ISO = "2027-01-15T00:00:00.000Z"
NOW_MS = int(datetime.datetime.fromisoformat(NOW_ISO.replace("Z", "+00:00")).timestamp() * 1000)


def _hours_before(hours: float) -> str:
    return (
        datetime.datetime.fromtimestamp(NOW_MS / 1000, datetime.UTC)
        - datetime.timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")


#: `lastSubmittedAt` is one of: None (never), an ISO string, or NOT_SUBMITTED_STATE
#: — the `undefined` / "could not read the column" state, which JSON cannot carry.
NOT_SUBMITTED_STATE = object()



PROBES: list[tuple[str, str | object | None, bool]] = [
    # (label, lastSubmittedAt, expected verdict)
    ("never-submitted", None, True),
    ("unreadable-state", NOT_SUBMITTED_STATE, False),
    ("inside-window-1h", _hours_before(1), False),
    ("inside-window-23h", _hours_before(23), False),
    ("outside-window-25h", _hours_before(25), True),
    ("outside-window-7d", _hours_before(24 * 7), True),
    ("unparseable-stamp", "not-a-date", True),
]


def _probe_payload() -> list[dict]:
    return [
        {"nowMs": NOW_MS, "undefinedState": True}
        if last is NOT_SUBMITTED_STATE
        else {"nowMs": NOW_MS, "lastSubmittedAt": last}
        for _, last, _expected in PROBES
    ]


@pytest.fixture(scope="module")
def payload() -> dict:
    """One node process for every case (each start costs ~2 s under the loader)."""
    # #4916: a missing node FAILs here rather than skipping — V2/V3 are the only
    # evidence that the publish path submits exactly once, and a skipped guard
    # looks like a passing one. A present-but-too-old node is a host gap and skips.
    require_node_floor(NODE_FLOOR_STRIP_TYPES, what="the IndexNow publish-transition guard", absent="fail")
    node = shutil.which("node")

    order = list(CASES)
    # The window stamps are materialised HERE, milliseconds before node runs, not
    # at import: the handler stamps `Date.now()`, so an import-time 23 h stamp
    # would give the "still inside the window" case a fixed 3600 s budget spent on
    # everything else pytest does before this module's first test.
    stamp_now = {
        sentinel: _stamp_hours_ago(hours) for sentinel, hours in STAMP_SENTINEL_HOURS.items()
    }
    cases = [
        {
            **CASES[k],
            **(
                {"lastSubmittedAt": stamp_now[CASES[k]["lastSubmittedAt"]]}
                if CASES[k].get("lastSubmittedAt") in stamp_now
                else {}
            ),
        }
        for k in order
    ]
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
                json.dumps(cases),
                json.dumps(_probe_payload()),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    out = json.loads(proc.stdout)
    assert len(out["cases"]) == len(order), (
        f"driver returned {len(out['cases'])} results for {len(order)} cases"
    )
    return {"cases": dict(zip(order, out["cases"], strict=True)),
            "windowMs": out["windowMs"],
            "submitTimeoutMs": out["submitTimeoutMs"],
            "stateTimeoutMs": out["stateTimeoutMs"],
            "probeResults": out["probeResults"]}


@pytest.fixture(scope="module")
def results(payload) -> dict[str, dict]:
    return payload["cases"]


def _submitted_urls(res: dict) -> list[str]:
    return [s["body"]["urlList"][0] for s in res["submissions"]]


def _logs_matching(res: dict, needle: str) -> list[str]:
    return [line for line in res["logs"] if needle in line]


# ── The operator doc must not advertise a log surface it has lost a row of ──
#: The status-level rows the runbook PROMISES to explain, one per distinct
#: operator action. A template like `submit failed (status ${res.status})` can emit
#: any code, so coverage of the template is not coverage of the statuses: each of
#: these is a row an operator reads, and each one's remedy is different. Deleting
#: any of them reds the test below.
DOC_ROWS = (
    "submitted (status 200)",
    "submitted (status 202)",
    "submit failed (status 400)",
    "submit failed (status 403)",
    "submit failed (status 422)",
    "submit failed (status 429)",
    "submit failed (status 5xx)",
    "submit failed (no status — timeout/network)",
    "submit failed (unusable url)",
    "stamp failed (supabase <n>)",
    "stamp failed (upstream unreachable)",
    "skipped (submitted <ts>)",
    "skipped (state unreadable, supabase 400)",
    "skipped (state unreadable, supabase 401|403|5xx)",
    "skipped (state unreadable, upstream unreachable)",
    "skipped (state unreadable, no row)",
    "unexpected failure",
)


def test_the_operator_table_documents_every_shape_and_loses_no_row() -> None:
    """The doc table is the runbook's only discovery mechanism — keep it honest.

    Two directions, both read from the SOURCE rather than from a copy of its names:

    * **Emitted ⊆ documented** — EVERY `console.<method>(` in the module must be an
      `[indexnow]` log whose shape is in the table. The denominator is every console
      call of any method (so `console.debug`, or a call re-wrapped across lines,
      cannot hide from it) and the numerator is the parsed `[indexnow]` shapes: the
      two must be equal, so a log line written in a form this test cannot read reds
      instead of silently escaping the pin.
    * **the promised rows still exist** — ``DOC_ROWS`` names each status/qualifier
      the table promises to explain, because one template can emit many statuses
      with different remedies. Delete a row an operator relies on and this reds.

    This is the same shape as the V1 key-file assertion: it reads the committed
    artifact, not a restatement of it.
    """
    source = INDEXNOW_MODULE.read_text(encoding="utf-8")
    calls = len(re.findall(r"console\.\w+\(", source))
    shapes = set()
    for match in re.finditer(r"console\.\w+\(\s*`\[indexnow\] ([^`]+)`", source):
        # Everything before the first interpolation, minus the trailing separator.
        head = match.group(1).split("${", 1)[0].rstrip(": ")
        if head:
            shapes.add(head)
    assert len(shapes) == calls, (
        f"{calls} console calls but {len(shapes)} parsed [indexnow] shapes — a log line "
        "was written in a form this test cannot see, so it is not actually pinned:"
        f" {sorted(shapes)}"
    )

    # Backslash-escaped pipes (`401\|403`) are markdown table syntax for one
    # cell — unescape before comparing so the row key reads as an operator sees it.
    doc = (REPO_ROOT / "docs" / "blog-authoring.md").read_text(encoding="utf-8").replace("\\|", "|")
    missing = sorted(s for s in shapes if s not in doc)
    assert missing == [], (
        "docs/blog-authoring.md's [indexnow] table omits log shapes the module emits: "
        f"{missing} — an operator cannot act on a line the runbook does not describe"
    )
    lost = [row for row in DOC_ROWS if f"| `{row}`" not in doc]
    assert lost == [], (
        f"the [indexnow] table lost rows an operator acts on: {lost} — a status row is "
        "not covered by its template's row, because each status implies a different action"
    )


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
    # ... and the suppression must be VISIBLE: a re-publish that is silently
    # skipped is indistinguishable from a notification that never fired, which is
    # the #7751 shape one layer down.
    assert _logs_matching(sequence, "skipped (submitted "), (
        f"a window-suppressed re-publish must log why: {sequence['logs']}"
    )


# ── V3, through the handler: an EXPIRED stamp submits again ───────────────
def test_an_expired_stamp_resubmits_through_the_handler(results) -> None:
    """The 24 h rule must EXPIRE, and it must be the CALLER that honours it.

    The probes above pin the pure function, but re-implementing "never re-submit
    once stamped" one level up — ``if (!shouldSubmit(...) || lastSubmittedAt)`` —
    leaves every probe green, because the probes call ``shouldSubmit`` directly.
    These two cases drive a real age through ``notifyPublished`` instead, so that
    caller-side mutant reds: 25 h submits, 23 h does not.

    The stamps are materialised in the ``payload`` fixture — at serialisation,
    milliseconds before node runs — from ``STAMP_SENTINEL_HOURS``, so the "still
    inside the window" case's margin is the subprocess, not the whole pytest
    session (an import-time 23 h stamp would spend an hour of budget on unrelated
    collection and would false-red after 3600 s).
    """
    expired = results["expired_stamp_resubmits"]
    assert len(expired["submissions"]) == 1, (
        "a stamp 25 h old must submit again — a post edited and re-published a day "
        f"later is otherwise never announced, silently; got {len(expired['submissions'])}"
    )
    fresh = results["fresh_stamp_does_not_resubmit"]
    assert fresh["submissions"] == [], (
        f"a stamp 23 h old must NOT submit — got {_submitted_urls(fresh)}"
    )


# ── A zero-row read is "unreadable", not "never", and it is logged ────────
def test_an_empty_state_read_skips_and_says_so(results) -> None:
    """Zero rows from the stamp SELECT must skip the submission AND log a line.

    Treating it as "never submitted" would submit — a guess about a state we could
    not read. Treating it as unreadable and logging nothing would put this path
    outside the module's "every read is logged" contract, and the runbook's only
    discovery mechanism is ``grep [indexnow]``. Both halves are pinned here.
    """
    res = results["state_no_row"]
    assert res["submissions"] == [], (
        f"an empty state read must not submit (the state is unknown, not clean) — "
        f"got {_submitted_urls(res)}"
    )
    # The SUFFIX, not just the shared "state unreadable" prefix: the doc gives the
    # three causes three different remedies, so a branch that logs the wrong one
    # sends the operator to the wrong action ("the row was gone — nothing to do"
    # on a deploy that ran ahead of its migration is how the capability stays
    # silently dead).
    assert _logs_matching(res, "state unreadable, no row"), (
        f"an empty state read must log its OWN shape, not another cause's: {res['logs']}"
    )


def test_a_failed_stamp_still_notifies_and_says_the_window_was_lost(results) -> None:
    """The stamp write failing must not be silent: the window was NOT recorded.

    The publish still succeeds and the submission still happens — but the next
    re-publish will submit again, which the doc tells the operator to expect. The
    stub answered every stamp with 200 until round 3, so this shape was reachable
    in production and unexercised in the suite.
    """
    res = results["stamp_fails"]
    assert res["statuses"] == [200], f"a failed stamp must not fail the publish: {res['statuses']}"
    assert len(res["submissions"]) == 1, f"the notification itself must still happen: {res}"
    assert _logs_matching(res, "stamp failed (supabase 500)"), (
        f"the lost window must be logged, with its status: {res['logs']}"
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
    # The SUFFIX pins WHICH cause was logged — the doc gives `supabase 400`
    # ("apply the migration") and `upstream unreachable` ("not a migration
    # problem") different remedies, so logging the wrong one sends the operator to
    # an action that cannot fix it. The stub answers 400 here.
    assert _logs_matching(res, "state unreadable, supabase 400"), (
        f"the skip must log its own shape: {res['logs']}"
    )


# ── The window's VALUE and its untaken branches ────────────────────────────
def test_the_window_is_a_day_and_expires(payload) -> None:
    """24 h exactly, and an expired stamp re-submits.

    Every other case sits inside one millisecond, so ANY positive window (or a
    ``shouldSubmit`` that always says no) satisfies them — measured: shortening
    ``INDEXNOW_WINDOW_MS`` to one second, or making ``shouldSubmit`` return false
    once a stamp exists, both left the rest of this suite green. That mutant
    permanently disables the capability the issue exists for, which is the
    "tracker says shipped, nothing works" shape (#7751) this file was written
    against. These probes are driven through the REAL exported function with a
    fixed ``nowMs``, so they cannot go wall-clock dependent.
    """
    assert payload["windowMs"] == 86_400_000, (
        f"the de-duplication window is {payload['windowMs']} ms; #9680 item 3 asks "
        "for at most one submission per URL per day"
    )
    mismatches = [
        (label, expected, actual)
        for (label, _last, expected), actual in zip(PROBES, payload["probeResults"], strict=True)
        if expected != actual
    ]
    assert mismatches == [], f"shouldSubmit disagreed with the rule, (label, want, got): {mismatches}"

    # Name the two branches the sequence cases cannot reach, so a failure says
    # WHICH rule broke rather than "a probe disagreed".
    by_label = {label: actual for (label, _l, _e), actual in zip(PROBES, payload["probeResults"], strict=True)}
    assert by_label["outside-window-25h"] is True, (
        "a stamp older than the window must submit again — otherwise a post edited "
        "and re-published a week later is never announced, silently"
    )
    assert by_label["inside-window-23h"] is False, "a stamp 23 h old must NOT submit"
    assert by_label["unparseable-stamp"] is True, (
        "a garbage stamp must cost ONE submission, not disable the capability forever"
    )
    assert by_label["unreadable-state"] is False, "an unreadable stamp must not submit"


# ── The publish never waits on the vendor ─────────────────────────────────
def test_the_publish_resolves_before_the_submission_starts(results) -> None:
    """``waitUntil(p)``, not ``await p`` — the difference is invisible otherwise.

    The stub answers instantly, so replacing ``waitUntil(notifyPublished(...))``
    with ``await notifyPublished(...)`` leaves every other assertion in this file
    unchanged — while in production every publish would block on a vendor call for
    up to ``INDEXNOW_TIMEOUT_MS``. The driver snapshots the submission count at the
    moment each handler call RESOLVES, so the awaited form is observable here (it
    would report 1) and the backgrounded form reports 0.
    """
    for name, res in results.items():
        if not res["atResolve"]:
            continue
        assert res["atResolve"][0] == 0, (
            f"{name}: the vendor call had already run when the response resolved — "
            "the submission is inside the request path, so a hung endpoint would "
            f"block the publish (atResolve={res['atResolve']})"
        )
    # ... and it did run afterwards, for the case that submits.
    assert results["publish_transition"]["submissions"], (
        "nothing was submitted at all — the count above would be a vacuous zero"
    )


def test_the_vendor_call_is_bounded(results, payload) -> None:
    """A 5 s bound on the outbound call, so a hung endpoint cannot hold the task.

    Falsifiable: delete the ``signal`` from ``submitUrl``'s fetch and this reds.
    The ``indexnowThrows`` case simulates a rejection, not an abort — without this
    assertion nothing in the suite can see the timeout go away.

    The two Supabase round-trips carry the sharper bound, so they are pinned too:
    a STALLED state read produces no ``[indexnow]`` line at all (the task is
    abandoned at the runtime's post-response grace boundary rather than
    rejecting), which is precisely the silent failure the whole module exists to
    avoid. Deleting either ``signal`` — or raising either constant above the
    grace boundary — reds here.
    """
    for name in ("publish_transition", "endpoint_unreachable"):
        res = results[name]
        assert res["submissions"], f"{name}: expected an attempt to observe: {res}"
        for submission in res["submissions"]:
            assert submission["hasSignal"] is True, (
                f"{name}: the IndexNow fetch carries no AbortSignal — a hung endpoint "
                "would hold the background task for as long as the runtime allows"
            )

    res = results["publish_transition"]
    assert {c["kind"] for c in res["stateCalls"]} == {"read", "stamp"}, (
        f"expected one state read and one stamp on the happy path: {res['stateCalls']}"
    )
    for call in res["stateCalls"]:
        assert call["hasSignal"] is True, (
            f"the {call['kind']} call to Supabase carries no AbortSignal — a stalled "
            "state read means nothing is notified AND no [indexnow] line is written"
        )
    assert payload["submitTimeoutMs"] <= 10_000, (
        f"the vendor bound is {payload['submitTimeoutMs']} ms"
    )
    assert payload["stateTimeoutMs"] <= 5_000, (
        f"the state bound is {payload['stateTimeoutMs']} ms — it must stay "
        "well inside the runtime's post-response grace boundary, or a stalled read "
        "goes silent again"
    )


# ── The stamp records the ATTEMPT, not the outcome ────────────────────────
def test_a_failed_submission_still_burns_the_window(results) -> None:
    """Vendor down → re-publish the same day is ONE attempt, not one per publish.

    The stamp is written whatever the endpoint answered, precisely so a vendor
    outage cannot turn every re-publish into another attempt. Falsifiable: write
    the stamp only on success and this case submits twice.
    """
    res = results["failed_submit_then_republish"]
    assert res["statuses"] == [200, 200, 200], res["statuses"]
    assert len(res["submissions"]) == 1, (
        "publish (endpoint 500) → unpublish → re-publish must attempt ONCE — the "
        f"failed attempt is stamped too; got {len(res['submissions'])}: "
        f"{_submitted_urls(res)}"
    )
    assert res["stamp"], "the attempt must be recorded even when it failed"


# ── The create path is NOT instrumented (a disclosed gap, pinned) ─────────
def test_a_direct_publish_create_does_not_notify(results) -> None:
    """``POST`` with ``status: "published"`` publishes but submits nothing.

    #9680 names the PATCH transition as the trigger, so this is deliberate — and
    it is disclosed in ``docs/blog-authoring.md`` rather than left implicit. The
    case exists so the gap cannot change in EITHER direction without a test
    changing with it: instrument the create path and this reds; drop the doc line
    and the doc test reds.
    """
    res = results["direct_publish_create"]
    assert res["statuses"] == [201], f"the create itself must still succeed: {res['statuses']}"
    assert res["submissions"] == [], (
        f"the create path must not notify (#9680 names the PATCH transition) — got "
        f"{_submitted_urls(res)}. If this is now intended, wire it deliberately and "
        "update this test and the doc together"
    )
