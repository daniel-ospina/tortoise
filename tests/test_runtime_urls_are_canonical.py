"""#11767 — the runtime URL surface: one canonical origin per emitted link.

Why this file exists, in the form of the defect it prevents.

`email_link_base()`'s default was `https://tortoise.premiselabs.co` — a host that
has answered `301` for both of its consumers since #4054 moved `/welcome.html`
and `/invite-accept.html` to the app origin. The invite link is the ONLY
token-delivery path in the product: a hop on it is a hop that can drop the token
from `Location`. And a `grep` could not see the defect, because **four fixtures
SET `EMAIL_LINK_BASE_URL` to the retired host, and a fifth asserted the same value
through the recorded cassette** — so every email suite was green while asserting
the retired value, and exactly ONE test pinned the default.

So the assertions here are on the ARTIFACT (the invite email's own body, the
`302`'s `Location`), not on the constant that feeds it. A test that reads
`email_link_base()` alone would have passed for the whole life of the bug.

The siblings named on #11767 are pinned too: the CORS allowlist that must KEEP
the retired origin with a recorded removal trigger, the two Supabase edge
functions whose public allowlists already carry both hosts, and the appeal URL
whose host moved while its dead `#appeal` fragment did not (#11939).

⛔ #11767's items 4 and 5 are IN THIS FILE'S SURFACE but NOT re-aimed by the PR
that added it: the dashboard landing pages (`invite-accept`, `welcome`, `signup`),
the blog-origin pair, and the dashboard's docs links still spell the retired
origin. Each is in `RETIRED_HOST_ALLOWLIST` with an EXACT occurrence count, the
measured 301 that makes it a hop rather than a break, and the child that owns the
re-aim — so this guard cannot report GREEN over a file it never opened, and a
sweep that removes one of those literals must remove its recorded reason too.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

APP_ORIGIN = "https://app.premiselabs.co"
CANONICAL_ORIGIN = "https://premiselabs.co"
RETIRED_HOST = "tortoise.premiselabs.co"
RETIRED_ORIGIN = f"https://{RETIRED_HOST}"

#: Every origin a runtime-emitted absolute URL may name (#11767 §10 A3).
CANONICAL_ORIGINS = {
    CANONICAL_ORIGIN,
    APP_ORIGIN,
    "https://api.premiselabs.co",
    "https://tortoise-y4mjjq.fly.dev",
    RETIRED_ORIGIN,  # permanent 301 host — allowed ONLY where recorded below
}


# ── A1 — the EMAIL BASE, proved on the emitted artifacts ─────────────────────


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload or {"id": "msg_11767"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _send_one_invite(monkeypatch, *, token: str) -> list[dict]:
    """Drive the REAL schedule → POST path; return the Resend payload(s).

    Deliberately not ``_build_invite_link``: the link is built inside
    ``_send_invite_attempt`` and then baked into the HTML and text bodies, so
    this asserts what a recipient's mail client receives.
    """
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_11767")
    monkeypatch.delenv("EMAIL_LINK_BASE_URL", raising=False)
    from tortoise import email_notify

    email_notify._skip_logged.clear()
    email_notify._send_counts_day = 0
    email_notify._send_counts_month = 0
    email_notify._send_counts_day_period = ""
    email_notify._send_counts_month_period = ""

    calls: list[dict] = []

    async def fake_post(self, url, **kwargs):
        calls.append(kwargs["json"])
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)

    async def _main():
        email_notify.send_invite_email(
            "Acme Labs", "invitee@example.com", "member", token, "inv_11767")
        await asyncio.sleep(0.05)
        if email_notify._pending_email_tasks:
            await asyncio.wait(
                list(email_notify._pending_email_tasks), timeout=1.0)
        await email_notify.drain_pending_sends(0.01)

    asyncio.run(_main())
    return calls


def test_invite_email_default_base_is_the_app_origin(monkeypatch):
    """With EMAIL_LINK_BASE_URL unset (production, measured 2026-11-11), the
    INVITE EMAIL itself carries the app origin — the one path a token takes to a
    human, and the path a hop can break."""
    payloads = _send_one_invite(monkeypatch, token="tok_11767_default")
    assert payloads, "the invite send must reach the provider"
    body = payloads[0]
    link = f"{APP_ORIGIN}/invite-accept?token=tok_11767_default"
    assert link in body["html"], (
        "the invite email must carry the app origin — /invite-accept.html has "
        f"lived there since #4054. body: {body['html'][:400]!r}")
    assert link in body["text"]
    assert RETIRED_HOST not in body["html"]
    assert RETIRED_HOST not in body["text"]


def test_both_consumers_of_email_link_base_emit_the_same_terminal_origin(
        monkeypatch):
    """One base, two consumers, zero hops: `/invite-accept.html` (the email) and
    `/welcome.html` (the github_callback `302`). They diverge only by path, so a
    future "fix" to one host cannot leave the other on a redirect."""
    from fastapi.testclient import TestClient

    from tortoise import email_notify
    from tortoise.hosted_api import app

    monkeypatch.delenv("EMAIL_LINK_BASE_URL", raising=False)
    assert email_notify._build_invite_link("t0k") == (
        f"{APP_ORIGIN}/invite-accept?token=t0k")
    with TestClient(app) as client:
        r = client.get("/v1/onboarding/github/callback?error=access_denied",
                       follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == f"{APP_ORIGIN}/welcome?github=denied"


def test_email_link_base_follows_an_explicit_override(monkeypatch):
    """The flip must not have closed the seam: one env var still moves it."""
    from tortoise import email_notify

    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://links.example.com")
    assert email_notify._build_invite_link("t") == (
        "https://links.example.com/invite-accept?token=t")


# ── A3 — the retired host survives ONLY where a reason is recorded ───────────
#
# The counted unit is the SCHEME-BEARING origin, because that is the only form
# that can be *emitted* or dropped into an allowlist — bare-host prose in a
# comment that explains the history is documentation, and counting it would make
# this test red on its own rationale (measured: it did, before this split).
#
# Each entry: path -> (occurrence count, why the retired origin is deliberately
# still spelled there). Exact counts on purpose: an ADDED literal fails, and so
# does a sweep that DELETES one of these without deleting its reason — which is
# the point, because (b) below is a live allowlist and (c) is dashboard-adjacent
# local-dev state, not leftovers.
RETIRED_HOST_ALLOWLIST = {
    "tortoise/hosted_api.py": (
        1,
        "CORS/Host allowlist KEEP — removal trigger recorded inline (#11767 §2). "
        "The host still serves website/404.html forever (#11762)."),
    "supabase/functions/waitlist-subscribe/handle.ts": (
        1,
        "public CORS allowlist on a Deno edge function; both hosts intentional"),
    "supabase/functions/deck-view/handle.ts": (
        1,
        "public CORS allowlist on a Deno edge function; both hosts intentional"),
    "supabase/config.toml": (
        1,
        "LOCAL-DEV OAuth redirect allowlist only; the hosted project's Auth URL "
        "config is dashboard state nothing in this repo can read"),
    # ⛔ #11767 items 4 AND 5 — NOT re-aimed by this PR, and recorded here so the
    # guard COVERS these files instead of silently skipping them (fresh-context
    # review, P1: with a hand-list, the sweep could not fail for the ONE page the
    # emailed invite token lands on). Every URL built from these answers a
    # PERMANENT 301 on the retired host (each measured 2026-11-12 below), so each
    # is a hop, not a break — which is why deferring is safe and why it must stay
    # VISIBLE: a sweep that deletes one of these must delete its reason too, and
    # the deferred re-aim is tracked on #12057 rather than lost behind a green run.
    "website/apps/dashboard/public/invite-accept.html": (
        3,
        "#11767 item 5 — the page the emailed INVITE TOKEN lands on still links "
        "`/docs`, `/tos` and `/privacy` on the retired host (301 -> the canonical "
        "origin, measured 2026-11-12). Re-aiming it belongs in the same commit as "
        "#11764's canonical sweep, so the invite link and its landing page move "
        "together instead of the link racing ahead of the page."),
    "website/apps/dashboard/public/welcome.html": (
        1,
        "#11767 item 5 — the Product link is the retired host "
        "(301 -> premiselabs.co/tortoise). Deferred with invite-accept.html."),
    "website/apps/dashboard/public/signup.html": (
        2,
        "#11767 item 5 — `rel=canonical` names the retired `/auth` "
        "(301 -> app.premiselabs.co/auth) and the Product link is the retired "
        "host. Deferred with invite-accept.html (#11764)."),
    "website/apps/dashboard/functions/blog/api/[[path]].ts": (
        1,
        "#11767 item 4 — DEFAULT_BLOG_ORIGIN is still the retired host. ⛔ The "
        "hop lands on premiselabs.co/blog (301, measured 2026-11-12) — the blog's "
        "OWN canonical origin, NOT the premiselabs.co/tortoise product page an "
        "earlier revision of this note named. Moving it changes what the blog API "
        "reads THROUGH, not just a link: the retired host's `/blog*` 301s with "
        "`/blog/api/*` deliberately excluded from the map, so a base flip that "
        "misses the exclusion turns a POST into a GET. The sweep is #12057's."),
    "website/apps/blog-admin/src/lib/blog-api.ts": (
        1,
        "#11767 item 4 — the admin client's blog origin; same deferral as the "
        "blog Function above (301 measured on the retired host)."),
    # ⛔ The blog origin's ACTUAL source, missed by the first pass at this map: it
    # named the admin client and the dashboard Function but not `SITE_URL` in the
    # blog Function's own `_lib.ts`, which is what its canonicals, OG tags,
    # JSON-LD, sitemap and feed are built from. A sweep of item 4 that moved every
    # file BELOW while missing this one would leave the blog emitting the retired
    # origin everywhere it matters, with this guard green.
    "website/functions/blog/_lib.ts": (
        4,
        "#11767 item 4 — `SITE_URL`: the ONE constant the blog's canonical/OG/"
        "JSON-LD/sitemap output and three nav links (`/docs`, `/#pricing`, `/auth`) "
        "are built from; imported by `blog/[[path]].ts`, `blog/feed.xml.ts` and "
        "`blog/sitemap.xml.ts`. `/blog` on the retired host 301s to "
        "premiselabs.co/blog (measured 2026-11-12). Re-aiming item 4 is not "
        "complete without this file."),
    "website/apps/dashboard/functions/admin/[[path]].ts": (
        1,
        "#11767 item 4 — blog-admin API origin; deferred with the blog sweep "
        "(#11764). Measured 2026-11-12."),
    "website/apps/blog-admin/src/App.tsx": (
        1,
        "#11767 item 4 — blog-admin UI origin; deferred with the blog sweep "
        "(#11764). Measured 2026-11-12."),
    "website/apps/blog-admin/src/pages/PostEditor.tsx": (
        3,
        "#11767 item 4 — blog-admin author-image preview URLs; deferred with the "
        "blog sweep (#11764). Measured 2026-11-12."),
    "website/apps/blog-admin/src/pages/PostsList.tsx": (
        1,
        "#11767 item 4 — blog-admin author-image thumbnail; deferred with the blog "
        "sweep (#11764). Measured 2026-11-12."),
    "website/apps/dashboard/src/wizardPrompts.js": (
        3,
        "#11767 item 5-adjacent — the copy-pasteable wizard prompts still hand the "
        "user `https://tortoise.premiselabs.co/docs` "
        "(301 -> premiselabs.co/tortoise/docs). Deferred with the dashboard sweep; "
        "the docs target is the one #11764 settles."),
    "website/apps/dashboard/src/harnesses.js": (
        1,
        "DOCS_ORIGIN constant (301 -> premiselabs.co/tortoise/docs). Same "
        "deferral (#11764). ⚠️ This file ALSO carries a BARE-host mention at :277 "
        "that this counter does not see, because the guard counts the full "
        "origin — a bare-host sweep is a different guard."),
    "website/apps/dashboard/src/overviewEmptyAction.js": (
        2,
        "SDK_DOCS_HREF and ONBOARDING_FUNNEL_HREF (both 301). Same deferral "
        "(#11764)."),
    "website/apps/dashboard/src/billingCta.js": (
        1,
        "COMPARE_PLANS_URL `/#pricing-section` (301 -> premiselabs.co/tortoise). "
        "Same deferral (#11764)."),}

#: Every runtime file THIS PR touched. An emitted occurrence of the retired
#: origin in these that is not in the allowlist above fails.
#:
#: ⛔ NOT "every runtime file #11767 owns" — an earlier revision of this comment
#: said exactly that and it was false. The issue also enumerates five app pages
#: and API modules that still emit the retired origin and are DEFERRED here, not
#: swept. Counts measured at this head, so the deferral cannot be read as a clean
#: sweep. BASIS: EMITTING occurrences (an `href`/const a browser or server would
#: follow) — a PROSE comment that merely names the host is not counted, which is
#: why `[[path]].ts` is 1 and not 2 (line 12 is a comment, line 49 emits):
#:   website/apps/dashboard/functions/blog/api/[[path]].ts   1  DEFAULT_BLOG_ORIGIN
#:   website/functions/blog/_lib.ts                          4  SITE_URL (canonical/OG/JSON-LD/feed + nav)
#:   website/apps/blog-admin/src/lib/blog-api.ts             1  author-image URL
#:   website/apps/dashboard/functions/admin/[[path]].ts      1  blog-admin API origin
#:   website/apps/blog-admin/src/App.tsx                     1  blog-admin UI origin
#:   website/apps/blog-admin/src/pages/PostEditor.tsx        3  author-image preview
#:   website/apps/blog-admin/src/pages/PostsList.tsx          1  author-image thumbnail
#:   website/apps/dashboard/public/invite-accept.html        3  nav /docs + footer /tos + /privacy
#:   website/apps/dashboard/public/welcome.html              1  nav /
#:   website/apps/dashboard/public/signup.html               2  canonical /auth + nav /
#:   website/apps/dashboard/src/wizardPrompts.js             3  copy-pasteable prompts
#:   website/apps/dashboard/src/harnesses.js                 1  DOCS_ORIGIN
#:   website/apps/dashboard/src/overviewEmptyAction.js       2  SDK_DOCS_HREF + ONBOARDING_FUNNEL_HREF
#:   website/apps/dashboard/src/billingCta.js                1  COMPARE_PLANS_URL
#: None is a broken link (every one still 301s onto the canonical host); they are
#: a SCOPE deferral, and ALL FOURTEEN files listed above ARE in
#: `RETIRED_HOST_ALLOWLIST` with the same kind of reason — recording a count is not
#: blessing the literal, it is what makes the deferral visible and what forces a
#: sweep to update the record in the same commit.
#:
#: ⛔ An earlier revision of this paragraph said the OPPOSITE — that they were
#: "deliberately NOT added", because blessing them "would turn a deferral into a
#: decision" — while the map eleven lines up already carried them. Left standing,
#: that is an instruction to delete the very entries that make this guard able to
#: fail, leaving a red count check nobody can explain.
#:
#: ⛔ OWNERSHIP — one owner, **#12057** (filed from review). An earlier revision
#: said the sweep "belongs to #11764/#12049" and that the deferred re-aim was
#: "tracked on #11767": all three are false. `#12049` covers only the pasted
#: onboarding copy, `#11764`'s scoping is the product-page move, and `#11767`
#: CLOSES with this PR, so a note deferring to it defers nowhere. Where a row above
#: cites "#11764"/"#11765" it names the work the deferral RIDES.
RUNTIME_FILES = (
    "tortoise/email_notify.py",
    "tortoise/abuse.py",
    "tortoise/hosted_api.py",
    "tortoise/onboarding/SKILL.md",
    "website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md",
    ".env.example",
    "supabase/functions/waitlist-subscribe/handle.ts",
    "supabase/functions/deck-view/handle.ts",
    "supabase/config.toml",
    # #11767 items 4 and 5 — dashboard/blog surfaces whose retired-origin literals
    # are RECORDED above rather than re-aimed here. In the surface so the guard
    # can fail on them; every entry carries a measured 301 and a deferral note.
    "website/apps/dashboard/public/invite-accept.html",
    "website/apps/dashboard/public/welcome.html",
    "website/apps/dashboard/public/signup.html",
    "website/apps/dashboard/functions/blog/api/[[path]].ts",
    "website/apps/blog-admin/src/lib/blog-api.ts",
    "website/apps/dashboard/src/wizardPrompts.js",
    "website/apps/dashboard/src/harnesses.js",
    "website/apps/dashboard/src/overviewEmptyAction.js",
    "website/apps/dashboard/src/billingCta.js",
    "website/functions/blog/_lib.ts",
    "website/apps/dashboard/functions/admin/[[path]].ts",
    "website/apps/blog-admin/src/App.tsx",
    "website/apps/blog-admin/src/pages/PostEditor.tsx",
    "website/apps/blog-admin/src/pages/PostsList.tsx",
)


def test_retired_origin_literals_match_the_recorded_allowlist():
    offenders = {}
    for rel in RUNTIME_FILES:
        path = REPO / rel
        assert path.is_file(), f"{rel} is in the runtime surface but missing"
        hits = path.read_text(encoding="utf-8").count(RETIRED_ORIGIN)
        expected = RETIRED_HOST_ALLOWLIST.get(rel, (0, ""))[0]
        if hits != expected:
            offenders[rel] = (hits, expected)
    assert not offenders, (
        "the retired origin's occurrences do not match the recorded allowlist "
        f"(file: (found, expected)): {offenders}. {RETIRED_ORIGIN} is a permanent "
        "301 host (#11762) — runtime code must EMIT a canonical origin. To keep "
        "an occurrence, add it to RETIRED_HOST_ALLOWLIST with its reason.")


def test_documents_humans_copy_do_not_name_the_retired_host():
    """The SKILL.md is pasted into a customer's agent, and `.env.example` is what
    an operator copies — neither may teach the retired host at all, in any form.
    A 301 cannot reach a value already copied into someone's config (#11767 §5)."""
    for rel in ("tortoise/onboarding/SKILL.md",
                "website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md",
                ".env.example"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert RETIRED_HOST not in text, (
            f"{rel} still names the retired host; the served/pasted document "
            "must name the canonical origin — a hop cannot follow a copy")


def test_onboarding_skill_points_at_the_reference_copy_on_the_canonical_origin():
    """#11762 decided the docs page lives on the company origin; the served
    document must name it there, and keep the `#chatgpt` anchor the reference
    copy carries (measured live 2026-11-12: `id="chatgpt"` is present on the
    page both spellings end on).

    ⛔ BOTH spellings are accepted, on purpose. `/docs` is a permanent SAME-HOST
    301 onto `/tortoise/docs` (#11763's map — one hop, both on the canonical
    origin), and `/tortoise/docs` is the 200 they end on; measured live
    2026-11-12 the chain is `301` then `200`, one redirect. Pinning either
    spelling alone REDS the other, and the flip between them is #12049's sweep
    rather than a defect — a pin that forbids the better URL is how a guard
    blocks the follow-up it exists to make safe. (#12049, not #11764: #11764's
    scoping assigns this file to #11767 and lists it under "deliberately NOT
    do", so it needed an owner of its own.) What must not come
    back is the `.html` file form (itself a 301) or the retired host, which
    `test_documents_humans_copy_do_not_name_the_retired_host` refuses.
    """
    skill = (REPO / "tortoise/onboarding/SKILL.md").read_text(encoding="utf-8")
    assert (f"{CANONICAL_ORIGIN}/docs#chatgpt" in skill
            or f"{CANONICAL_ORIGIN}/tortoise/docs#chatgpt" in skill), (
        "the served document must point at the reference copy on the canonical "
        f"origin, at one of the two accepted spellings ({CANONICAL_ORIGIN}"
        "/docs#chatgpt or /tortoise/docs#chatgpt)")
    assert f"{CANONICAL_ORIGIN}/docs.html#chatgpt" not in skill, (
        "docs.html is itself a 301; the document names the canonical path")


def test_every_allowlisted_occurrence_has_a_reason():
    """A number without a reason is a sweep waiting to happen."""
    for rel, (count, why) in RETIRED_HOST_ALLOWLIST.items():
        assert count, f"{rel}: a zero-count entry belongs out of the allowlist"
        assert why.strip(), f"{rel}: allowlisted with no stated reason"


def test_canonical_origin_set_is_what_the_runtime_may_emit():
    """The allowlist cannot quietly grow an origin nobody decided on.

    ⛔ Compares the RUNTIME's own allowlist (`hosted_api._ALLOWED_ORIGINS`) with the
    decided set (`CANONICAL_ORIGINS`) — never a second literal copy of that set.
    An earlier form asserted `{…the same five literals…} == CANONICAL_ORIGINS`, which
    is this file compared with itself: mutation-measured, it stayed GREEN under every
    product revert its siblings caught (including the PR's own headline default-origin
    revert) and could only fail by desyncing its own duplicate literal. A pin that
    cannot fail on its regression is not a pin — the same defect class this PR fixes
    in `tests/test_onboarding_variants.py`.
    """
    from tortoise.hosted_api import _ALLOWED_ORIGINS

    assert set(_ALLOWED_ORIGINS) == CANONICAL_ORIGINS, (
        "the runtime CORS allowlist must be exactly the decided origin set — an "
        "origin that appears here without a decision recorded in `CANONICAL_ORIGINS` "
        "is the regression this pins.")


# ── A5 — the CORS allowlist has a test at all ────────────────────────────────


def test_mcp_cors_allowlist_keeps_both_origins_with_a_recorded_trigger():
    from tortoise.hosted_api import _ALLOWED_HOSTS, _ALLOWED_ORIGINS

    assert _ALLOWED_ORIGINS == [
        "https://premiselabs.co",
        "https://app.premiselabs.co",
        "https://api.premiselabs.co",
        "https://tortoise.premiselabs.co",
        "https://tortoise-y4mjjq.fly.dev",
    ], (
        "the CORS allowlist is the MCP transport's single source of truth "
        "(#1002). The canonical origin is entry 0 — measured live 2026-11-11: "
        "OPTIONS /mcp with Origin: https://premiselabs.co → 200 + echo. The "
        "retired entry is a KEEP (#11767 §2), not an oversight.")
    assert _ALLOWED_ORIGINS[0] == CANONICAL_ORIGIN
    # _ALLOWED_HOSTS is DERIVED (host guard, DNS-rebinding allowlist): dropping
    # the retired entry would silently tighten a second surface.
    assert _ALLOWED_HOSTS == [
        "premiselabs.co", "app.premiselabs.co", "api.premiselabs.co",
        "tortoise.premiselabs.co", "tortoise-y4mjjq.fly.dev",
    ]
    assert "https://evil.example" not in _ALLOWED_ORIGINS


def test_mcp_cors_allowlist_records_why_the_retired_origin_stays():
    """The trigger must live NEXT TO the entry — a ledger-only record is
    invisible to the sweep that deletes it (#11767 §2)."""
    src = (REPO / "tortoise/hosted_api.py").read_text(encoding="utf-8")
    start = src.index("_ALLOWED_ORIGINS = [")
    block = src[start:src.index("]", start)]
    assert "#11767" in block, "the keep must name its issue"
    for cond in ("#11763", "30 days", "404.html", "_ALLOWED_HOSTS"):
        assert cond in block, (
            f"the removal trigger must name {cond!r} — all four conditions were "
            "measured on #11767 and none of them is a judgement call")
    # ⛔ THE TRIGGER'S PREMISE IS PINNED TOO (#11767 review, P2 — measured). The
    # first version of this block argued the entry was safe to leave because
    # `website/404.html` was the ONE document left on the host. It was
    # false: `tortoise.premiselabs.co/contact` answers **200** with a `<script>`
    # (`_middleware.ts`'s product-host block does not intercept it and
    # `website/_redirects` maps `/contact.html` host-agnostically), so a sweep
    # reading it could have satisfied its own condition (c) from a premise that
    # never held. The substring asserts above could not catch that — they pin
    # TOKENS, and `404.html` appears in the true and the false version alike.
    #
    # What follows is a STRONGER pin than those tokens, and deliberately not a
    # claim that it is a complete one: a later review showed a paraphrase which
    # merely NAMED `/contact` stayed green over a false argument, so the measured
    # pair — the `200` and the `<script` — has to appear on the SAME line as the
    # path. That binds the assertion to the fact the argument rests on instead of
    # to a word the argument can borrow.
    contact_lines = [ln for ln in block.splitlines() if "/contact" in ln]
    assert contact_lines, (
        "the trigger must name the path that keeps this host live — `/contact` "
        "answers 200 there, so 'a scriptless 404 and nothing else' is not the reason "
        "the entry is safe to keep")
    assert any("200" in ln and "<script" in ln for ln in contact_lines), (
        "the `/contact` line must carry the MEASUREMENT that makes it matter (it "
        "answers 200 and serves a page with a <script>). Naming the path alone is "
        "the same token-level pin that stayed green over the retracted premise")
    # `"script" in "no script"` is True, so a NEGATED line — the shape of the
    # retracted premise itself, and the shape `/404 -> 200 (no script)` in the
    # same block already uses — would satisfy a bare token pin while INVERTING the
    # fact. A fixed list of phrasings was not enough: the block BACKTICKS its own
    # identifiers, so `no \`<script>\`` and `\`<script>\`-free` walked straight
    # through it (measured). So normalise the line (drop backticks, fold
    # whitespace, lowercase) and refuse any negation token.
    #
    # ⛔ SCOPED TO THE MEASUREMENT, and the scope is the whole point: a first cut
    # refused a negation token ANYWHERE on a `/contact` line and false-redded an
    # honest sentence in this very block — `_middleware.ts`'s product-host `block
    # does not intercept /contact` (measured: 1 failed, its own author's line).
    # A pin that reds true prose gets deleted or weakened by whoever meets it
    # next, which is a worse outcome than the paraphrase it was guarding against.
    # So the refusal is bound to the claim under attack: a line that mentions the
    # `<script` AND carries a negation is refused, in any spelling.
    negations = ("no ", "not ", "never", "without", "absent", "-free",
                 "scriptless", "none")
    for ln in contact_lines:
        flat = " ".join(ln.replace("`", "").lower().split())
        if "script" not in flat:
            continue
        hit = next((n for n in negations if n in flat), None)
        assert hit is None, (
            f"the `/contact` line negates its own measurement ({hit!r} in "
            f"{flat!r}) — it answers 200 and serves a page WITH a <script>, so a "
            "negated line is the retracted argument wearing the tokens this pin "
            "looks for")
    for retracted in ("only remaining document", "only non-redirect HTML"):
        assert retracted not in block, (
            f"the retracted premise is back ({retracted!r}): the host serves more "
            "than `website/404.html` (measured: `/contact` -> 200 with a <script>), "
            "so an argument built on that premise is an argument from a false one")


# ── A5b — the Supabase edge-function allowlists, pinned ──────────────────────


def _ts_allowed_origins(rel: str) -> list[str]:
    text = (REPO / rel).read_text(encoding="utf-8")
    m = re.search(r"const ALLOWED_ORIGINS = \[(.*?)\];", text, re.S)
    assert m, f"{rel}: no ALLOWED_ORIGINS array — the pin cannot be vacuous"
    return re.findall(r'"([^"]+)"', m.group(1))


@pytest.mark.parametrize("rel", [
    "supabase/functions/waitlist-subscribe/handle.ts",
    "supabase/functions/deck-view/handle.ts",
])
def test_supabase_edge_function_origins_keep_both_hosts(rel):
    """These are PUBLIC CORS allowlists on Deno edge functions. Both hosts are
    intentional: the marketing pages post from the retired host until #11763's
    map ships, and the canonical one takes over after. Pinned so a later sweep
    cannot delete the old entry blind — a browser is the only other detector."""
    origins = _ts_allowed_origins(rel)
    assert CANONICAL_ORIGIN in origins
    assert RETIRED_ORIGIN in origins
    assert APP_ORIGIN in origins
    assert all(o.startswith("https://") for o in origins)


def test_supabase_edge_function_allowlists_agree():
    a = _ts_allowed_origins("supabase/functions/waitlist-subscribe/handle.ts")
    b = _ts_allowed_origins("supabase/functions/deck-view/handle.ts")
    assert a == b, (
        "the two public edge functions' origin allowlists drifted apart; they "
        "front the same marketing pages")


# ── The appeal URL — host moved, fragment did NOT (that is #11939) ────────────


def test_appeal_url_default_is_the_canonical_origin(monkeypatch):
    from tortoise import abuse

    monkeypatch.delenv("TORTOISE_ABUSE_APPEAL_URL", raising=False)
    url = abuse.appeal_url()
    assert url == f"{CANONICAL_ORIGIN}/docs#appeal"
    assert RETIRED_HOST not in url
    assert "/docs.html" not in url, (
        "docs.html is itself a 301 to /docs; the emitted URL names the "
        "canonical path, never the file form")
    assert abuse.suspended_message().endswith(url)


def test_appeal_url_still_follows_the_override(monkeypatch):
    from tortoise import abuse

    monkeypatch.setenv("TORTOISE_ABUSE_APPEAL_URL", "https://x.example/appeal")
    assert abuse.appeal_url() == "https://x.example/appeal"


def test_appeal_url_documents_the_dead_fragment_rather_than_implying_it_works():
    """#11767 §6.1: `#appeal` has no `id="appeal"` on the docs page — it is a
    pre-existing defect filed as #11939, deliberately NOT fixed here. The
    docstring must say so, so a host flip is never read as a working appeal
    link."""
    import inspect

    from tortoise import abuse

    doc = inspect.getdoc(abuse.appeal_url) or ""
    assert "11939" in doc
    assert "DEAD" in doc.upper()
