"""#13998 — the two working-set capacity notices (80 % / 100 %).

The copy is the OWNER'S (approved 2026-11-20). These tests are the mechanism that
keeps it from drifting, so they are deliberately of three kinds:

1. **Golden files** — the rendered subject/HTML/text for both thresholds, byte for
   byte. Any wording edit reds CI and must be the owner's edit, in the issue,
   with these fixtures regenerated beside it.
2. **A vocabulary guard** — the phrases the pre-destination drafts used, and any
   number that is not `{n}`, red the suite. #13998 requirement 3: "a test, not a
   style guide".
3. **The ledger** — exactly one send per threshold per account, ever, including
   the jump, retry, out-of-order and budget-skip cases.

Nothing here touches the network: the provider POST is always a fake.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import functools
import json
import logging
import pathlib
import re
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests.fake_control_plane import FakeControlPlane
from tortoise import allowance_gate as ag
from tortoise import capacity_notice as cn
from tortoise import email_notify, quota
from tortoise import supabase_control as sc
from tortoise.supabase_control import (
    org_storage_notice_pct,
    set_org_storage_notice_pct,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ROOT = pathlib.Path(__file__).parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"
#: The migration that RENAMED `public.teams` → `public.organizations`
#: (`ALTER TABLE public.teams RENAME TO organizations`, line 50). Every
#: migration sorting after this one must speak the post-rename name; a migration
#: written against `public.teams` fails with `relation "public.teams" does not
#: exist` and its column never exists.
RENAME_MIGRATION = "20260915000001_tenancy_team_to_org.sql"

#: The lines an earlier draft of this email carried, and that the destination
#: rewrite exists to keep out (#13989 records the discarded 80 % wording, which
#: promised "exact-word search may start missing older items").
FORBIDDEN = (
    "allowance reached",
    "upgrade your plan",
    "exact-word",
    "may miss",
)

ORG_ID = "team-solo-001"

#: A bare-URI span in the PLAIN-TEXT half — `<mailto:…>` / `<https://…>`.
#:
#: ⛔ The number rule below is about what a reader READS, and a URL is not copy:
#: the CTA's `?subject=Add%20capacity…` is percent-encoding, not the "figure the
#: architecture can move" that guard exists to keep out of a customer email.
#: The comment is here, at the exemption, so the reason travels with the code.
_TEXT_URL_SPAN = re.compile(r"<(?:mailto:|https?://)[^<>\s]*>")


def _org_row(**overrides) -> dict:
    row = {"id": ORG_ID, "name": "Acme", "tier": "solo",
           "email": "dana@acme.example", "storage_notice_pct": None}
    row.update(overrides)
    return row


def _fake_cp(**overrides) -> FakeControlPlane:
    return FakeControlPlane({"organizations": [_org_row(**overrides)]})


def _limits(allowance_mb: int | None = 102) -> dict:
    limits = {"org_id": ORG_ID, "tier": "solo", "max_points": 10_000}
    if allowance_mb is not None:
        limits["max_graph_storage_mb"] = allowance_mb
    return limits


class _ColumnBlindCP:
    """A control plane whose orgs read rejects ONE column, like PostgREST 400.

    This is the shape a schema one migration behind produces: the statement names
    a column that does not exist, and the error does not say which one.
    """

    def __init__(self, missing: str):
        self.missing = missing
        self.calls: list[list[str]] = []

    def query(self, table: str, *, select: list[str] | None = None,
              filters=None, **_kwargs):
        self.calls.append(list(select or []))
        if select and self.missing in select:
            raise RuntimeError(
                f'column organizations.{self.missing} does not exist (PGRST204)')
        return [_org_row()]


class _FlakyCP(_ColumnBlindCP):
    """A control plane whose FIRST orgs read fails for a reason that is NOT drift.

    The shape of a connection reset / a 5xx / a timeout. The retry WOULD succeed —
    that is the whole point: the second read is fine, so nothing but the
    CLASSIFICATION of the first error decides whether the account is read as
    un-suspended. It projects the row to the requested `select`, like the real
    PostgREST does (a fake that returns the whole row masks this class).
    """

    def __init__(self, first_error: BaseException, **row):
        super().__init__(missing="")
        self.first_error = first_error
        self.row = _org_row(**row)

    def query(self, table: str, *, select: list[str] | None = None,
              filters=None, **_kwargs):
        self.calls.append(list(select or []))
        if len(self.calls) == 1:
            raise self.first_error
        if select:
            return [{k: v for k, v in self.row.items() if k in select}]
        return [dict(self.row)]


class _StatusResponse:
    """One scripted PostgREST answer: a status and the body PostgREST sends.

    A 4xx from PostgREST is NOT an empty body — it is its own error DOCUMENT
    (`code` / `message`). Scripting that document is what makes the premise test
    falsifiable: with a real body on the wire, a seam that starts CARRYING it
    renders `PGRST204` / `does not exist` in the message, and the test that
    forbids those markers then has something to catch. An empty-body fixture
    would leave that test green over the very change it names.
    """

    def __init__(self, status: int, payload: list[dict] | None = None,
                 rejected_column: str | None = None):
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._payload = payload
        self._error: dict | None = None
        if rejected_column is not None:
            self._error = {
                "code": "PGRST204",
                "message": (f"Could not find the '{rejected_column}' column "
                            f"of 'organizations' in the schema cache"),
            }
            self.content = json.dumps(self._error).encode()
        else:
            # ``content`` and ``json()`` are two views of ONE body — a double
            # whose halves disagree would lie to a seam that ever reads either.
            self.content = b"" if payload is None else json.dumps(payload).encode()

    def json(self):
        if self._error is not None:
            return self._error
        return [] if self._payload is None else list(self._payload)


class _RealSeamControlPlane:
    """The REAL ``SupabaseControlPlane`` over a scripted transport (no network).

    ⛔ THE POINT IS THE ERROR TEXT, NOT THE ROW. What licenses
    `read_account`'s degrade is what the FAILURE SAYS, and the production seam
    renders a non-2xx as ``…: HTTP <status>`` while DISCARDING the PostgREST
    body (`supabase_control.query_with_total`). So `PGRST204` / `42703` /
    `column … does not exist` are text this seam can never emit, and a fake that
    raises them pins a shape production does not produce. Measured at
    `1ad388b5f6`: deleting the one marker that CAN fire in production
    (`http 400`) left this whole file green (93 passed).

    The rejection is scripted WITH PostgREST's own error document on the wire (a
    bare ``HTTP 400`` with no body would make that measurable only in one
    direction — see `_StatusResponse`).
    """

    def __init__(self, missing: str, status: int = 400):
        self.missing = missing
        self.status = status
        self.calls: list[list[str]] = []
        self.plane = sc.SupabaseControlPlane(
            url="https://proj.supabase.co", service_key="svc")
        # Close the real client before replacing it: this helper never uses it,
        # and an orphaned `httpx.Client` per instance is a leak with no upside.
        self.plane._http.close()
        self.plane._http = self

    def get(self, url, params=None, headers=None, **_kw):
        cols = [c for c in (params or {}).get("select", "").split(",") if c]
        self.calls.append(cols)
        if self.missing in cols:
            return _StatusResponse(
                self.status,
                rejected_column=self.missing if self.status == 400 else None)
        row = _org_row()
        return _StatusResponse(200, [{k: row[k] for k in cols if k in row}])

    def query(self, *args, **kwargs):
        return self.plane.query(*args, **kwargs)


class _Reading:
    """The shape `allowance_gate.read_dimension` returns (a MeterReading)."""

    def __init__(self, used: float):
        self.used = used
        self.estimated = True


class _Sender:
    """A fake send that records every call and returns a scripted status."""

    def __init__(self, *statuses: str):
        self.calls: list[cn.Copy] = []
        self.statuses = list(statuses)

    async def __call__(self, copy: cn.Copy) -> dict:
        self.calls.append(copy)
        status = self.statuses.pop(0) if self.statuses else "sent"
        if status == "sent":
            return {"status": "sent", "message_id": f"msg_{len(self.calls)}"}
        return {"status": status}


def _pass(**kwargs) -> dict:
    return asyncio.run(cn.sweep_pass(**kwargs))


# ── 1. Golden files ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("threshold", cn.THRESHOLDS)
def test_rendered_copy_matches_its_golden_file(threshold):
    """The approved copy, byte for byte, in both bodies.

    ⛔ If this fails, the copy changed. That is allowed only as the OWNER's
    change (the issue carries it): update the fixture in the same commit and say
    so in the PR. Do not "fix" the fixture to match a lane's preference.
    """
    copy = cn.render(threshold, "Dana")
    assert copy.subject == (FIXTURES / f"13998_notice_{threshold}_subject.txt"
                            ).read_text().strip()
    assert copy.text == (FIXTURES / f"13998_notice_{threshold}.txt").read_text()
    assert copy.html == (FIXTURES / f"13998_notice_{threshold}.html").read_text()


def test_greeting_falls_back_rather_than_reading_hi_comma():
    for empty in (None, "", "   "):
        assert cn.render(80, empty).text.startswith("Hi there,\n")


def test_the_greeting_constant_is_what_both_renderings_emit():
    """`GREETING` is a fix only while something RENDERS it.

    MEASURED at `5a8c7edda0c` (#14038 review round 4): putting both render
    sites back to the literal `Hi {name},` — the exact "a greeting constant
    nothing rendered" state the P2 named — left the whole file green
    (103 passed). The constant's value equals the old literal, so the goldens
    cannot see the difference, and the fallback row uses `Hi there`. Rebinding
    the constant is therefore the only mutation that reaches it.
    """
    original = cn.GREETING
    try:
        cn.GREETING = "Howdy {name},"
        copy = cn.render(80, "Dana")
    finally:
        cn.GREETING = original
    assert copy.text.startswith("Howdy Dana,\n"), copy.text[:40]
    assert "Howdy Dana," in copy.html, copy.html[:200]


# ── 2. The vocabulary guard ─────────────────────────────────────────────────

@pytest.mark.parametrize("threshold", cn.THRESHOLDS)
def test_forbidden_vocabulary_never_renders(threshold):
    """#13998 requirement 3 — the guard is the mechanism, not a style guide."""
    copy = cn.render(threshold, "Dana")
    for rendered in (copy.subject, copy.text, copy.html):
        lowered = rendered.lower()
        for phrase in FORBIDDEN:
            assert phrase not in lowered, (
                f"the approved copy's vocabulary guard forbids {phrase!r}; "
                f"it appeared in the {threshold}% rendering")


@pytest.mark.parametrize("threshold", cn.THRESHOLDS)
def test_no_number_other_than_the_threshold_renders(threshold):
    """`{n}` is the ONLY number the copy may carry.

    A "102 MB" or a "1.5x" or a "0.80" in a customer email is a figure the
    architecture can move without the copy following (the allowance is a
    per-tier derivation, `docs/architecture/SEARCH-ARCHITECTURE.md` §8) — so the
    rendered text may contain no digits at all once `{n}%` is removed.
    """
    copy = cn.render(threshold, "Dana")
    # The plain-text half carries its two DESTINATIONS (see the span note above):
    # exactly two, so this exemption cannot silently grow to cover prose.
    assert len(_TEXT_URL_SPAN.findall(copy.text)) == 2, copy.text[-400:]
    for rendered in (copy.subject, copy.text):
        stripped = _TEXT_URL_SPAN.sub("", rendered).replace(f"{threshold}%", "")
        digits = re.findall(r"\d", stripped)
        assert not digits, (
            f"the {threshold}% copy may carry no number but the threshold "
            f"itself; found {digits!r} in: {stripped[:200]!r}")
    # The HTML half carries hex colours and a viewport width in its TAGS — the
    # rule is about what a reader READS, so strip the markup and check the text
    # nodes (a number spliced into a sentence is the drift this catches).
    visible = re.sub(r"<[^>]+>", " ", copy.html).replace(f"{threshold}%", "")
    assert not re.findall(r"\d", visible), (
        f"the {threshold}% email may show no number but the threshold itself; "
        f"visible text: {visible[:400]!r}")


def test_the_approved_decisions_are_pinned():
    """Postgres named, "graph database" used, CTA is capacity, tier is absent.

    These are the four copy decisions the owner approved on 2026-11-20 (the
    issue's "Approved decisions behind this copy") — pinned so a future lane
    cannot quietly revert one while keeping the vocabulary guard green.
    """
    copy = cn.render(100, "Dana")
    body = copy.text
    assert "Postgres database" in body          # Postgres is NAMED (approved)
    assert "graph database" in body             # approved "fine for now"
    assert "[ Add capacity ]" in body           # capacity FIRST
    assert "upgrade" not in body.lower()        # never a tier remedy
    assert "$" not in copy.text and "$" not in copy.html


@pytest.mark.parametrize("threshold", cn.THRESHOLDS)
def test_links_point_at_capacity_first_and_the_docs_page(threshold):
    copy = cn.render(threshold, "Dana")
    assert cn.add_capacity_url() in copy.html
    assert cn.STORAGE_DOCS_URL in copy.html
    # The CTA precedes the docs link: capacity first, explanation second.
    assert copy.html.index(cn.add_capacity_url()) < copy.html.index(
        cn.STORAGE_DOCS_URL)
    assert copy.text.rstrip().endswith(f"<{cn.STORAGE_DOCS_URL}>"), (
        "the docs link stays the LAST thing in the body, and it now carries its "
        "destination (pre-fix this assertion pinned the label alone, i.e. the "
        "dead end)")


@pytest.mark.parametrize("threshold", cn.THRESHOLDS)
def test_the_plain_text_half_carries_both_destinations(threshold):
    """⛔ A TEXT-ONLY READER MUST BE ABLE TO ACT ON THE EMAIL.

    #13998 requirement 2 is that the CTA **resolves**; requirement 4 that the
    docs page is **linked**. The HTML half does both through `<a href>`, and the
    pre-fix text half rendered the two labels with NO destination at all — so a
    plain-text client, a screen reader reading the text alternative, or any
    gateway that strips HTML received an email whose only action was a dead
    label. That is the one failure this email exists to avoid: the send is once
    ever, so a reader who cannot act has no second chance.

    The module's own precedent is the sibling emails: `email_notify._invite_text`
    renders `Accept: {link}` and `_onboarding_text` renders `book a call {link}` —
    the URL is in the text part there too.

    ⛔ THE WORDS ARE UNCHANGED. The labels are the owner's (2026-11-20) and are
    still verbatim; what this pins is the DESTINATION, and it is the same
    function the HTML half uses, so the two halves cannot point at two pages.
    """
    copy = cn.render(threshold, "Dana")
    assert cn.add_capacity_url() in copy.text, (
        "the text half's [ Add capacity ] label has no destination — a reader on a "
        "text-only client cannot act on a once-ever notice")
    assert cn.STORAGE_DOCS_URL in copy.text, (
        "the text half's docs label has no destination (#13998 requirement 4)")
    assert copy.text.index(cn.add_capacity_url()) < copy.text.index(
        cn.STORAGE_DOCS_URL), "capacity first, explanation second"
    # Both destinations exist — nothing in the text half may carry one that the
    # HTML half does not, which would be a second place to keep in sync.
    for url in (cn.add_capacity_url(), cn.STORAGE_DOCS_URL):
        assert url in copy.html, url


def test_the_docs_link_is_the_zero_hop_canonical_origin():
    """`#14013` publishes the page; this pins WHERE the email points at it.

    ⛔ MEASURED 2026-11-20 (curl -L): `https://premiselabs.co/tortoise/docs`
    answers 200 with 0 hops, while `premiselabs.co/docs` and
    `tortoise.premiselabs.co/docs` each answer 301 onto it. Pinning the 0-hop
    spelling is the #11767 rule these emails' sibling links already follow — a
    redirect is a hop that can drop a reader, and on a link that carries no
    token it buys nothing.

    ⛔ #13998 review P3 (agent-infra#3442 rule 3): the retired-spelling check
    used to be `assert not STORAGE_DOCS_URL.startswith(retired)` — UNREACHABLE,
    because both retired prefixes are non-prefixes of the canonical literal
    asserted two lines earlier, so no mutation of ``STORAGE_DOCS_URL`` could
    ever make it fail while the earlier asserts passed. A comment explaining why
    it cannot fail is not a fix, so the spurious assertion is gone and the pin
    is now the EXACT url (which every mutation reds), with the retired spellings
    checked where they CAN appear: in the rendered copy.
    """
    assert cn.STORAGE_DOCS_URL == "https://premiselabs.co/tortoise/docs#storage"
    retired_spellings = (
        "https://premiselabs.co/docs",
        "https://tortoise.premiselabs.co/docs",
    )
    for threshold in cn.THRESHOLDS:
        copy = cn.render(threshold, "Dana")
        for retired in retired_spellings:
            # Reachable: a template hardcoding a one-hop link reds here even when
            # the module constant stays canonical.
            assert retired not in copy.html, (threshold, retired)
            assert retired not in copy.text, (threshold, retired)


def test_the_cta_offers_capacity_and_never_a_tier():
    """#13998 requirement 2 / #13989 policy 4, as a guard that reads the DESTINATION.

    A URL string can satisfy any guard while the page it opens sells the one thing
    the email must not offer: the first cut pointed at the dashboard's `#/billing`
    tab, which is a real deep link AND a `<h3>Plans</h3>` grid of upgrade buttons
    (#14038 review, P1). There is no capacity-purchase route in the product yet,
    so the honest destination is a capacity REQUEST — and the guard's job is to
    red if a later lane quietly points the CTA back at an app page that offers
    tiers.
    """
    url = cn.add_capacity_url()
    assert url.startswith("mailto:"), (
        f"the CTA now points at {url!r} — an app route that offers a tier change "
        "is exactly what #13998 forbids; if a capacity/add-on surface has landed, "
        "pin THIS test to it deliberately")
    for forbidden in ("#/billing", "pricing", "upgrade", "plan"):
        assert forbidden not in url.lower()
    assert "subject=" in url, "the request must arrive already classified"


def test_the_capacity_request_address_is_a_published_support_contact():
    """The mailto must be an address the product already publishes.

    Pinned against its published home rather than a bare string, so an address
    that gets retired reds here instead of mailing a black hole.
    """
    doc = (pathlib.Path(__file__).resolve().parents[1]
           / "docs/quickstart-cloud.md").read_text()
    assert cn.CAPACITY_REQUEST_EMAIL in doc, (
        f"{cn.CAPACITY_REQUEST_EMAIL!r} is no longer in docs/quickstart-cloud.md "
        "— re-derive the published support address before mailing it")


# ── the tense rule (the only latitude this module takes with the copy) ──────

def test_the_two_thresholds_differ_only_in_n_and_tense():
    """Exactly three lines differ in the body (the status line and the two status
    sentences), plus the subject's `{n}`. Everything else is the same approved
    copy."""
    eighty, full = cn.render(80, "Dana"), cn.render(100, "Dana")
    assert eighty.subject != full.subject
    assert eighty.subject.replace("80%", "100%") == full.subject
    eighty_lines = eighty.text.splitlines()
    full_lines = full.text.splitlines()
    assert len(eighty_lines) == len(full_lines)
    differing = [(a, b) for a, b in zip(eighty_lines, full_lines, strict=True)
                 if a != b]
    assert len(differing) == 3, differing
    for a, b in differing:
        # `{n}` and tense are the ONLY permitted differences: normalise the
        # percentage, then the three tense rewrites, and the lines must match.
        normalised = (a.replace("80%", "100%")
                       .replace("What will change", "What changes")
                       .replace("will be fetched", "are fetched")
                       .replace("they may take", "they can take"))
        assert normalised == b, (a, b)


def test_at_80_the_demotion_sentences_are_future_tense():
    """At 80 % nothing has been demoted, so the copy may not describe it as done.

    The issue's rule: "only `{n}` and the tense of the status line change (at
    80 % the items have not yet been demoted)". Telling an account at 80 % that
    "what changes is how quickly some older items come back" would declare a
    degraded state that has not happened — the gloss #2952 forbids.
    """
    at_80 = cn.render(80, "Dana").text
    at_100 = cn.render(100, "Dana").text
    assert "What will change is how quickly" in at_80
    assert "What changes is how quickly" in at_100
    assert "will be fetched when you search for them, so they may take" in at_80
    assert "are fetched when you search for them, so they can take" in at_100
    # and the future wording is only at 80 %
    assert "What will change" not in at_100


def test_an_unapproved_threshold_has_no_copy():
    with pytest.raises(ValueError):
        cn.render(50, "Dana")
    with pytest.raises(ValueError):
        cn.demotion_status(99)


# ── threshold arithmetic ────────────────────────────────────────────────────

@pytest.mark.parametrize("used,allowance,expected", [
    (0, 102, 0),
    (40.8, 102, 40),
    (81.6, 102, 80),      # 80 % exactly, floored
    (101.9, 102, 99),     # 99.9 % is NOT 100 % — below the allowance floors to 99
    (102, 102, 100),
    (153, 102, 100),
    (5, 0, None),         # unarmed tier: nothing to be a percentage OF
    (-1, 102, None),
    (float("nan"), 102, None),
    (10, float("inf"), None),
    (True, 102, None),    # a bool is not a measurement
])
def test_capacity_pct(used, allowance, expected):
    assert cn.capacity_pct(used, allowance) == expected


@pytest.mark.parametrize("pct,expected", [
    (None, None), (0, None), (79, None), (80, 80), (99, 80), (100, 100),
])
def test_threshold_reached_picks_the_highest(pct, expected):
    assert cn.threshold_reached(pct) == expected


# ── evaluate: the gate's figures, never a guess ─────────────────────────────

def test_evaluate_reads_the_gate_instruments(monkeypatch):
    """The production default path is `quota.resolve_org_limits` +
    `allowance_gate.read_dimension` — the gate's own two instruments, so the
    email cannot disagree with what happens to the account."""
    calls = {}

    def fake_limits(org_id):
        calls["limits"] = org_id
        return _limits(102)

    def fake_read(dimension, org_id):
        calls["read"] = (dimension, org_id)
        return _Reading(110.0)

    monkeypatch.setattr("tortoise.quota.resolve_org_limits", fake_limits)
    monkeypatch.setattr("tortoise.allowance_gate.read_dimension", fake_read)
    notice, reason = cn.evaluate(ORG_ID)
    assert reason is None
    assert notice.threshold == 100
    assert notice.used_mb == 110.0 and notice.allowance_mb == 102.0
    assert calls == {"limits": ORG_ID, "read": ("graph_storage", ORG_ID)}


def test_evaluate_sends_nothing_when_the_gauge_cannot_be_read(monkeypatch):
    """A METER FAULT resolves to no email — injected at the INSTRUMENT.

    ⛔ #13998 review ("also worth a line"): this row used to pass
    ``reading=None``, but in ``evaluate`` ``None`` means *read it yourself*, not
    *unreadable* — so it exercised the REAL ``allowance_gate.read_dimension``
    against the ambient environment and stayed green because that call returns
    ``None`` for an unknown org, not because a fault was injected. A caller that
    DID attempt the read and got nothing could not express that through that
    seam. Patching the instrument controls the fault; the call assertion keeps
    it from going vacuous.
    """
    calls: list[tuple[str, str]] = []

    def unreadable(dimension, org_id):
        calls.append((dimension, org_id))
        return None

    monkeypatch.setattr("tortoise.allowance_gate.read_dimension", unreadable)
    notice, reason = cn.evaluate(ORG_ID, limits=_limits(102))
    assert (notice, reason) == (None, "no-reading")
    assert calls == [(cn.DIMENSION, ORG_ID)], (
        "the gauge was never consulted — this row would be vacuous")


@pytest.mark.parametrize("exc", [
    # The TRANSPORT class: the PostgREST read failed.
    RuntimeError("the allowance read failed: HTTP 500"),
    # A SECOND class the guard exists for: `resolve_org_limits` raises
    # `OrgNotFoundError` (a `QuotaCheckError`) when the org row is missing
    # (`tortoise/quota.py:804`), which is NOT a `RuntimeError`. ⛔ This pair pins
    # BREADTH, not "the realistic one" — a transport failure and a terminal non-2xx
    # raise `RuntimeError`, so neither of the two is hypothetical. Pinned because a
    # guard narrowed to `except RuntimeError` leaves every OTHER row in this file
    # green while it regresses exactly this path (#14038 review cycle 10, P3).
    quota.OrgNotFoundError("Team 'team-solo-001' not found in control plane"),
    # ⛔ A THIRD CLASS IN NEITHER of the two above, because those two NAMED classes
    # pin two names, not the guard's BREADTH: a narrowing to
    # `except (RuntimeError, quota.OrgNotFoundError)` leaves the two rows above
    # green while `QuotaCheckError` — a real raise of this same instrument
    # (`tortoise/quota.py:727`, an unreadable `pricing.json` tier figure reached
    # through `_arm_graph_storage` from `resolve_org_limits`) — escapes `evaluate`
    # and reaches `sweep_pass` as the generic `error` this row exists to prevent
    # (#14038 review cycle 11, P3). ⛔ NOT the empty-`org_id` raise at `:773`: that
    # one cannot reach `evaluate` in production at all (`sweep_pass` skips a falsy
    # id at `capacity_notice.py:1333-1334`, and `notify_account` answers
    # `unknown-account` before the gauge) — the class is what pins breadth, the
    # instance must be a reachable one (#14038 review cycle 13, P3).
    quota.QuotaCheckError("resolve_org_limits requires a org_id"),
], ids=["transport", "org-not-found", "quota-check-error"])
def test_a_raising_allowance_is_named_in_the_closed_list_not_left_to_escape(
        monkeypatch, exc):
    """A gauge that RAISES must answer with the documented status, not escape.

    `notify_account` publishes a CLOSED list of statuses a caller aggregates on,
    and `unreadable-allowance`/`unreadable-reading` are defined there as "the
    gauge pair could not be read". `sweep_pass` turns ANY exception into its
    generic `error`, so a raise left unguarded inside `evaluate` would make both
    names unreachable for the very failure they name — the caller would see
    `error` and the cause would live only in a log line nothing aggregates.

    MEASURED before the guard existed: each of these raises escaped `evaluate`,
    which is why they are the pins and not a restatement.

    ⛔ THREE EXCEPTION CLASSES, because the guard's BREADTH is what is pinned:
    `OrgNotFoundError` is a `QuotaCheckError` (not a `RuntimeError`), and the third
    row is a `QuotaCheckError` that is neither — so a single-class row could not tell
    `except Exception` from `except RuntimeError`, and the two named classes alone
    could not tell it from `except (RuntimeError, OrgNotFoundError)`.
    ⛔ This paragraph deliberately does NOT rank the classes by "which one
    production raises": an earlier cut did, twice, and both times it was wrong (a
    transport failure and a terminal non-2xx raise `RuntimeError`; a missing org row
    raises `OrgNotFoundError`; an unreadable `pricing.json` tier figure raises
    `QuotaCheckError`). ALL THREE
    params exist to pin breadth, nothing else.
    """
    def raise_on_read(org_id):
        raise exc

    monkeypatch.setattr("tortoise.quota.resolve_org_limits", raise_on_read)
    # No `limits=` argument: the production path resolves them, which is exactly
    # the instrument that is broken here. Passing limits would make this vacuous.
    assert cn.evaluate(ORG_ID) == (None, "unreadable-allowance")

    # And the CONSEQUENCE through the caller that aggregates the closed list: a
    # status, not an escaped exception, and nothing sent.
    send = _Sender()
    out = asyncio.run(cn.notify_account(ORG_ID, cp=_fake_cp(), send=send))
    assert out["status"] == "unreadable-allowance", out
    assert send.calls == [], "a broken gauge mailed a customer"


@pytest.mark.parametrize("exc", [
    # The synthetic transport-ish class.
    RuntimeError("read_dimension failed before the meter ran"),
    # ⛔ THE CLASS THE DOCSTRING NAMES, and the one a real wiring fault raises:
    # `allowance_gate.read_dimension` does `declared[dimension]`
    # (`tortoise/allowance_gate.py:2042`), so an undeclared dimension is a
    # `KeyError`, not a `RuntimeError`.
    KeyError("definitely_not_a_declared_dimension"),
], ids=["runtime-error", "undeclared-dimension"])
def test_a_read_dimension_that_raises_is_named_in_the_closed_list(
        monkeypatch, exc):
    """`read_dimension` ITSELF failing is named `unreadable-reading`.

    ⛔ NOT the meter-fault pin — an earlier cut of this docstring said it graded
    "a reading that RAISES" at "the instrument a real caller would hit", and both
    halves were false. `allowance_gate.read_dimension` catches the METER's own
    exception and answers `None`, so a meter outage never reaches this guard; the
    row below pins that path. What THIS row grades is the wiring fault the guard
    can catch: `read_dimension` raising before it ever calls the meter.

    ⛔ AND IT GRADES BREADTH, not one class (#14038 review cycle 13, P2). The
    wiring fault this docstring names is a **`KeyError`** — an undeclared
    dimension makes `declared[dimension]` raise — which is NOT a `RuntimeError`,
    so a single `RuntimeError` row left the same narrowing this file pins TWICE
    for the allowance guard (`except RuntimeError`) invisible here: measured, it
    kept the whole file green. Two rows, one class each, for the same reason as
    the sibling above.
    """
    def raise_on_read(dimension, org_id):
        raise exc

    monkeypatch.setattr("tortoise.allowance_gate.read_dimension", raise_on_read)
    assert cn.evaluate(ORG_ID, limits=_limits(102)) == (None, "unreadable-reading")


def test_a_raising_METER_answers_no_reading_through_the_real_seam(monkeypatch):
    """A meter that RAISES reaches `evaluate` as a MISSING reading — `no-reading`.

    This is the production path, and it is why `unreadable-reading` must not be
    documented as covering a meter fault: `allowance_gate.read_dimension` is a
    documented NON-RAISING seam ("it never refuses, never raises for a meter
    failure … a caller that must not guess gets ``None`` and says so"), so the
    raise is converted to `None` one layer below this module and the status an
    operator sees is `no-reading` — the same status as "never measured".

    ⛔ THIS PIN NEEDS A POSITIVE CONTROL, and an earlier cut of it lacked one: the
    asserted value is what the NO-RAISE state also produces, so a patch that
    silently stopped applying would leave the row green and vacuous. It therefore
    records the meter's calls and asserts the patched meter RAN, and it patches
    the dimension named by `cn.DIMENSION` (not a literal) so a rename there cannot
    silently revert it to vacuity.
    """
    calls = []

    def boom(org_id):
        calls.append(org_id)
        raise RuntimeError("the meter read failed: HTTP 503")

    patched = tuple(
        dataclasses.replace(dim, meter=boom)
        if dim.dimension == cn.DIMENSION else dim
        for dim in ag.DIMENSIONS)
    monkeypatch.setattr(ag, "DIMENSIONS", patched)
    assert cn.evaluate(ORG_ID, limits=_limits(102)) == (None, "no-reading")
    assert calls == [ORG_ID], (
        "the patched meter was never consulted — this row would be vacuous"
    )


def test_evaluate_sends_nothing_without_an_armed_allowance():
    """`free`/`anon` carry no hot window (`quota._arm_graph_storage` leaves them
    unarmed), so the copy's "capacity included in your plan" has no figure."""
    notice, reason = cn.evaluate(ORG_ID, limits=_limits(None), reading=_Reading(99))
    assert notice is None and reason == "no-allowance"
    assert cn.evaluate(ORG_ID, limits=_limits(0), reading=_Reading(99)) == (
        None, "no-allowance")


def test_evaluate_is_quiet_below_the_threshold():
    notice, reason = cn.evaluate(ORG_ID, limits=_limits(102), reading=_Reading(80.0))
    assert notice is None and reason == "below-threshold"


# ── 3. The ledger: exactly one send per threshold, ever ─────────────────────

def test_one_send_per_threshold_across_repeated_passes():
    """The headline requirement: 80 % once, 100 % once — no repeats, ever."""
    cp = _fake_cp()
    reading = {"value": _Reading(90.0)}          # 88 % of 102 MB
    send = _Sender()

    async def run_pass():
        # The two gate instruments are patched so the test drives the THRESHOLD,
        # not the database; everything else (marker, CAS stamp, templates) is the
        # production path.
        return await cn.sweep_pass(
            cp=cp, send=send,
            enumerate_orgs=lambda limit=None: [_org_row()])

    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: reading["value"]):
        assert asyncio.run(run_pass())["sent"] == 1
        assert asyncio.run(run_pass())["sent"] == 0        # mark holds
        assert len(send.calls) == 1
        assert send.calls[0].subject.startswith("Your search layer is at 80%")
        assert cp.tables["organizations"][0]["storage_notice_pct"] == 80

        reading["value"] = _Reading(103.0)                  # crosses 100 %
        assert asyncio.run(run_pass())["sent"] == 1
        assert asyncio.run(run_pass())["sent"] == 0
        assert len(send.calls) == 2
        assert send.calls[1].subject.startswith("Your search layer is at 100%")
        assert cp.tables["organizations"][0]["storage_notice_pct"] == 100


def test_a_jump_past_100_sends_only_the_100_notice():
    """60 % → 105 % between passes: ONE email (the 100 % one), never two."""
    cp = _fake_cp()
    send = _Sender()
    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(140.0)):
        summary = asyncio.run(cn.sweep_pass(
            cp=cp, send=send,
            enumerate_orgs=lambda limit=None: [_org_row()]))
    assert summary["sent"] == 1
    assert len(send.calls) == 1
    assert send.calls[0].subject == "Your search layer is at 100% — nothing is lost"
    assert cp.tables["organizations"][0]["storage_notice_pct"] == 100


def test_an_already_notified_account_is_never_emailed_again():
    """The high-water mark answers before the gauge is even consulted twice."""
    cp = _fake_cp(storage_notice_pct=100)
    send = _Sender()
    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(103.0)):
        summary = asyncio.run(cn.sweep_pass(
            cp=cp, send=send,
            enumerate_orgs=lambda limit=None: [_org_row(storage_notice_pct=100)]))
    assert summary["sent"] == 0
    assert summary["statuses"]["already-notified"] == 1
    assert send.calls == []


def test_a_finished_80_pass_pays_no_render_and_no_pre_send_reread(monkeypatch):
    """The ledger gate answers BEFORE the render and the pre-send mark re-read.

    ⛔ THIS ROW EXISTS BECAUSE NOTHING ATTRIBUTED THAT GATE (#14038 review,
    mutation pass). Mark 80, gauge still at 80: the gate returns
    `already-notified` — but with its comparison weakened to `<`, or with the
    whole block deleted, EVERY other row in this file stayed green (measured),
    because the pre-send re-read re-decides the same threshold and there is no
    send either way. What differs is what a finished pass COSTS: a render and one
    control-plane round trip. So the row watches those two effects directly,
    which is what makes it red when the gate goes rather than a restatement of
    the status it returns.
    """
    cp = _fake_cp(storage_notice_pct=80)
    send = _Sender()
    rendered: list[int] = []
    rereads: list[str] = []
    real_render = cn.render

    def spy_render(threshold, first_name):
        rendered.append(threshold)
        return real_render(threshold, first_name)

    monkeypatch.setattr(cn, "render", spy_render)
    monkeypatch.setattr("tortoise.supabase_control.org_storage_notice_pct",
                        lambda cp_, org_id: rereads.append(org_id) or 80)
    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(81.6)):
        out = _pass(cp=cp, send=send,
                    enumerate_orgs=lambda limit=None: [
                        _org_row(storage_notice_pct=80)])
    assert out["statuses"]["already-notified"] == 1
    assert send.calls == []
    assert rendered == [], (
        "the ledger gate rendered copy for a threshold the mark already covers — "
        "`render` here is work a re-run of a finished pass must not pay")
    assert rereads == [], (
        "the ledger gate fell through to the pre-send mark re-read for a threshold "
        "the mark already covers — that re-read is the extra round trip the gate "
        "exists to skip")


def test_a_failed_send_does_not_stamp_and_is_retried():
    """Send-then-stamp: a provider failure must not swallow the notice."""
    cp = _fake_cp()
    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)):
        failing = _Sender("failed")
        out = asyncio.run(cn.sweep_pass(
            cp=cp, send=failing,
            enumerate_orgs=lambda limit=None: [_org_row()]))
        assert out["statuses"]["failed"] == 1
        assert cp.tables["organizations"][0]["storage_notice_pct"] is None

        retrying = _Sender()
        out = asyncio.run(cn.sweep_pass(
            cp=cp, send=retrying,
            enumerate_orgs=lambda limit=None: [_org_row()]))
        assert out["sent"] == 1
        assert len(retrying.calls) == 1
        assert cp.tables["organizations"][0]["storage_notice_pct"] == 80


def test_a_budget_skipped_send_does_not_stamp():
    """The shared Resend budget (#1138) is a RAMP, not a drop: a skipped send
    leaves the mark unset so a later pass retries it."""
    cp = _fake_cp()
    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)):
        out = asyncio.run(cn.sweep_pass(
            cp=cp, send=_Sender("skipped"),
            enumerate_orgs=lambda limit=None: [_org_row()]))
    assert out["statuses"]["skipped"] == 1
    assert cp.tables["organizations"][0]["storage_notice_pct"] is None


def test_an_account_without_an_address_is_not_emailed():
    cp = _fake_cp(email=None)
    send = _Sender()
    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)):
        out = asyncio.run(cn.sweep_pass(
            cp=cp, send=send,
            enumerate_orgs=lambda limit=None: [_org_row(email=None)]))
    assert out["statuses"]["no-email"] == 1 and send.calls == []


def test_one_broken_account_does_not_cost_the_pass():
    """Per-account isolation: a raising account costs its own notice only."""
    cp = FakeControlPlane({"organizations": [
        _org_row(),
        _org_row(id="team-broken-002", email="b@example.test"),
    ]})
    seen = []

    async def fake_notify(org_id, *, cp=None, **kwargs):
        seen.append(org_id)
        if org_id == "team-broken-002":
            raise RuntimeError("boom")
        return {"status": "sent"}

    from unittest.mock import patch
    with patch.object(cn, "notify_account", fake_notify):
        out = asyncio.run(cn.sweep_pass(
            cp=cp, send=_Sender(),
            enumerate_orgs=lambda limit=None: [_org_row(),
                                              _org_row(id="team-broken-002")]))
    assert seen == [ORG_ID, "team-broken-002"]
    assert out["sent"] == 1 and out["statuses"]["error"] == 1


def test_an_uncertifiable_enumeration_sends_nothing():
    """A truncated fleet read is not an error in the store — it is a shorter
    list, and a pass over half the fleet looks exactly like a pass with nothing
    to do. The seam that certifies completeness RAISES, and we send nothing."""
    def broken(limit=None):
        raise RuntimeError("enumeration could not be certified complete")

    out = _pass(cp=_fake_cp(), send=_Sender(), enumerate_orgs=broken)
    assert out.get("error") == "enumeration-failed"
    assert out["sent"] == 0


# ── the control-plane ledger itself ─────────────────────────────────────────

class TestDriftAndStaleness:
    """The review round's four behavioural fixes, each pinned by its own case."""

    def test_a_schema_older_than_0015_still_reads_the_account(self):
        """#1096: `suspended_at` is ADDITIVE (0015) — not a base column — so its
        absence must degrade to "not suspended", never take the feature down.

        A flat select addition cannot do that: the 400 is the same whether the
        missing column is the additive one or the LEDGER, so a schema one
        migration behind would report `notice-mark-unavailable` for the whole
        fleet and silently never send. The retry without the soft column is what
        separates the two cases.
        """
        cp = _ColumnBlindCP(missing="suspended_at")
        account = cn.read_account(cp, ORG_ID)
        assert account is not None
        assert account.suspended is False
        assert len(cp.calls) == 2, "exactly one drift-soft retry"
        assert "suspended_at" in cp.calls[0]
        assert "suspended_at" not in cp.calls[1]
        assert "storage_notice_pct" in cp.calls[1], (
            "the retry must never drop the ledger column — that is the "
            "fail-closed half of the split")

    def test_the_drift_warning_names_both_degradations(self, caplog):
        """⛔ THE DEGRADE HAS TWO CONSEQUENCES, and the line named one.

        The retry drops every `_ACCOUNT_DRIFT_SOFT` column, so an account read on
        a schema behind 0012/0015 is BOTH "not suspended" AND without the
        billing-contact rung of the recipient chain. The line claimed only the
        suspension half, so an operator reading it could not tell that the notice
        may have gone — or may go — to a different mailbox than the account
        recorded. The column LIST was `%s`-formatted from the tuple and stayed
        right; the prose did not.

        Non-vacuous: the pre-fix line contains no "billing-contact rung" text at
        all (only the column names), so this row reds on the wording it grades.
        """
        cp = _ColumnBlindCP(missing="customer_email")
        with caplog.at_level(logging.WARNING, logger="tortoise.capacity_notice"):
            cn.read_account(cp, ORG_ID)
        lines = [r.getMessage() for r in caplog.records]
        assert any(
            "billing-contact rung" in line and "not suspended" in line.lower()
            for line in lines), lines

    def test_a_missing_ledger_column_still_refuses_to_read_unset(self):
        """The other half: dropping the soft column does not rescue a missing
        ledger — it re-raises, so the account reports `notice-mark-unavailable`
        instead of "never notified" and re-sending every pass.
        """
        cp = _ColumnBlindCP(missing="storage_notice_pct")
        with pytest.raises(RuntimeError, match="storage_notice_pct"):
            cn.read_account(cp, ORG_ID)
        assert len(cp.calls) == 2, "one retry, then fail closed"

    def test_a_missing_deletion_column_still_refuses_to_read_alive(self):
        """The OTHER fail-closed column, pinned in the direction nobody pinned.

        `deleted_at` must never join `_ACCOUNT_DRIFT_SOFT`: a drift ladder that
        dropped it would read a soft-deleted org as alive and mail it copy saying
        "nothing is lost or deleted" while its data is on its way out — the
        finding two earlier reviewers raised. The ledger half was pinned above;
        this is the mirror, so moving `deleted_at` into the degrade tuple (which
        left the whole file green before, #14038 review P2) now reds.
        """
        cp = _ColumnBlindCP(missing="deleted_at")
        with pytest.raises(RuntimeError, match="deleted_at"):
            cn.read_account(cp, ORG_ID)
        assert len(cp.calls) == 2, (
            "one drift-soft retry, which still asks for deleted_at — the retry "
            f"must never drop a fail-closed column; calls={cp.calls}")
        assert "deleted_at" in cp.calls[1]

    @pytest.mark.parametrize("first_error", [
        # The shapes the real seam produces for a non-schema failure:
        # `query_with_total` re-raises a transport error VERBATIM, and renders an
        # HTTP failure as `… : HTTP <status>` (the body is discarded).
        RuntimeError("Supabase control-plane query failed (organizations): "
                     "Connection reset by peer"),
        RuntimeError("Supabase control-plane query failed (organizations): "
                     "HTTP 502"),
        RuntimeError("Supabase control-plane query failed (organizations): "
                     "timed out"),
        TimeoutError("read timeout"),
    ])
    def test_a_transient_first_read_failure_is_not_read_as_schema_drift(
            self, first_error):
        """⛔ A timeout/5xx/reset is NOT "the schema is older than 0015".

        #14038 review P1, measured: the pre-fix retry fired on ANY first failure
        and the retry succeeded, so a suspended org read as un-suspended, was
        mailed a capacity purchase and had its once-ever stamp burnt — the stamp
        names a threshold, so the mistake is not retryable. The retry is licensed
        only by a REJECTED PROJECTION (PGRST204 / 42703 / `does not exist` /
        HTTP 400); everything else propagates to
        `notice-mark-unavailable` and is retried on the next pass.
        """
        cp = _FlakyCP(first_error, suspended_at="2026-11-19T00:00:00Z")
        with pytest.raises((RuntimeError, TimeoutError)):
            cn.read_account(cp, ORG_ID)
        assert len(cp.calls) == 1, (
            "a transient failure must not trigger the drift-soft retry: that "
            "retry SUCCEEDS here, and its success is what reads a suspended org "
            f"as un-suspended; calls={cp.calls}")

    def test_a_transient_read_failure_mails_nothing_and_burns_no_stamp(self):
        """The consequence, not the mechanism: no mail, so no stamp.

        The stamp is written only after a successful send, and it is once-EVER, so
        the pass must not reach the sender at all when the account row could not
        be read (#14038 review P1). Pre-fix this sent a 100 % notice to a
        SUSPENDED account on a 502.
        """
        cp = _FlakyCP(
            RuntimeError("Supabase control-plane query failed (organizations): "
                         "HTTP 502"),
            suspended_at="2026-11-19T00:00:00Z")
        send = _Sender()
        out = asyncio.run(cn.notify_account(
            ORG_ID, cp=cp, send=send, limits=_limits(102),
            reading=_Reading(120.0)))
        assert out["status"] == "notice-mark-unavailable", out
        assert send.calls == [], "a suspended org was mailed on a transient error"

    def test_the_unreadable_account_log_line_names_WHICH_failure(self, caplog):
        """The docstring's promise, graded: the line carries the MESSAGE, not the type alone.

        Both a REJECTED COLUMN and a TRANSPORT failure reach this seam as
        `RuntimeError` (`supabase_control` raises the same class for `HTTP 400` and
        for a connection error), and `_rejected_the_projection` decides between
        them on the MESSAGE text. A line printing only `type(e).__name__` therefore
        told an operator "RuntimeError" for both "apply the migration" and "wait and
        retry" — the distinction the account's retry decision is built on
        (#14038 review, P2). Measured before the fix: the `HTTP 502` / transport
        text never reached the line and nothing red.
        """
        cp = _FlakyCP(RuntimeError(
            "Supabase control-plane RPC failed (organizations): "
            "HTTP 502: upstream connect error or disconnect/reset before headers"),
            suspended_at=None)
        with caplog.at_level(logging.ERROR, logger="tortoise.capacity_notice"):
            out = asyncio.run(cn.notify_account(
                ORG_ID, cp=cp, limits=_limits(102), reading=_Reading(120.0)))
        assert out["status"] == "notice-mark-unavailable", out
        line = " ".join(r.getMessage() for r in caplog.records)
        assert "HTTP 502" in line and "upstream connect error" in line, (
            "the unreadable-account log line does not name WHICH failure this is — "
            "`_rejected_the_projection` decides on this text, so an operator cannot "
            f"tell 'apply the migration' from 'retry'; line={line[:300]!r}")

    def test_a_fully_drifted_schema_raises_the_ORIGINAL_error(self):
        """When the retry fails too, the FIRST error is what the operator sees.

        The retry exists to separate "the additive column is missing" from "the
        ledger is missing". If it also swallowed the case where nothing works, the
        logged cause would be the RETRY's error — which names the ledger column
        even when a base column is what the schema is actually missing
        (#14038 review cycle 2's misdiagnosis class, one direction further out).
        """
        cp = _ColumnBlindCP(missing="suspended_at")

        def failing_retry(table, *, select=None, filters=None, **_kw):
            cp.calls.append(list(select or []))
            raise RuntimeError("column organizations.deleted_at does not exist")

        original_query = cp.query

        def query(table, *, select=None, filters=None, **kw):
            if len(cp.calls) == 0:
                return original_query(table, select=select, filters=filters, **kw)
            return failing_retry(table, select=select, filters=filters, **kw)

        cp.query = query
        with pytest.raises(RuntimeError, match="suspended_at"):
            cn.read_account(cp, ORG_ID)
        assert len(cp.calls) == 2

    def test_the_real_seams_bare_400_is_what_licenses_the_degrade(self):
        """⛔ The marker that fires in PRODUCTION, pinned against the seam itself.

        `query_with_total` turns a non-2xx into ``…: HTTP <status>`` and throws
        the PostgREST body away, so a rejected projection reaches `read_account`
        as a bare **HTTP 400** — never as `PGRST204` / `42703` / `does not
        exist` (#14038 review, P1: "classify the rejection before degrading").

        The other drift cases in this file raise the body text, so on their own
        they pin a shape the production seam cannot emit: measured at
        `1ad388b5f6`, deleting `http 400` from `_SCHEMA_REJECTION_MARKERS` left
        the whole file green while production would lose the degrade entirely —
        every account on a schema one migration behind reporting
        `notice-mark-unavailable` on every pass, forever.
        """
        cp = _RealSeamControlPlane(missing="suspended_at")
        account = cn.read_account(cp, ORG_ID)
        assert account is not None
        assert account.suspended is False
        assert len(cp.calls) == 2, f"exactly one drift-soft retry: {cp.calls}"
        assert "suspended_at" in cp.calls[0]
        assert "suspended_at" not in cp.calls[1]
        assert "storage_notice_pct" in cp.calls[1], (
            "the retry must never drop the ledger column — that is the "
            "fail-closed half of the split")

    def test_the_seam_renders_a_rejected_projection_as_a_status_only_error(self):
        """The PREMISE the marker list rests on, pinned where it can change.

        `_SCHEMA_REJECTION_MARKERS` accepts `http 400` *because* the seam
        discards the body. That is a property of `query_with_total`, not of
        `capacity_notice`, so it is asserted here: if a future seam carries the
        PostgREST body (a strictly better error), this reds and the marker list
        is re-read rather than silently depending on a text that disappeared.
        The fixture therefore puts PostgREST's OWN error document on the wire —
        the bodyless version of this test was green over a seam mutated to carry
        it (#14038 review), which is the same "a claim the code does not make"
        class the record's finding 3 is about.
        """
        cp = _RealSeamControlPlane(missing="suspended_at")
        with pytest.raises(RuntimeError) as caught:
            cp.query("organizations", select=list(cn._ACCOUNT_SELECT))
        text = str(caught.value)
        assert text.endswith("HTTP 400"), text
        for body_marker in ("pgrst204", "42703", "does not exist", "column"):
            assert body_marker not in text.lower(), (
                f"the seam now carries {body_marker!r}, so the marker list's "
                f"premise changed — re-read `_SCHEMA_REJECTION_MARKERS` "
                f"instead of leaving the degrade to depend on the status "
                f"alone: {text}")
        assert cn._rejected_the_projection(caught.value) is True

    def test_the_body_code_behind_the_drift_400_is_not_a_claim_this_code_makes(
            self):
        """⛔ The justification names BOTH vendor shapes and picks NEITHER.

        Three blocks describe the SAME read shape — a `select=` of an absent
        column — and an earlier cut had them disagree: the
        `_SCHEMA_REJECTION_MARKERS` block presented `42703` as what a rejected
        `select=` produces and `PGRST204` as belonging to a `columns=`
        parameter; `org_storage_notice_pct` drew the conclusion out loud
        (*"so ``42703`` is the shape it would see"*); and
        `_orgs_row_fail_soft` said the opposite — `PGRST204` — for that same
        read. Neither code is a claim this code can make: the
        PostgREST reference pins `PGRST204` to the **`columns=` parameter**,
        `42703` is PostgreSQL's own code forwarded under PostgREST's catch-all
        400, and `query_with_total` DISCARDS the body, so neither reaches this
        seam (the test above asserts exactly that). So this pins the DOCTRINE,
        not a wording: every block must name both documented shapes, and none
        may bind one of them to what this reader sees.
        """
        docstrings = {
            "capacity_notice._SCHEMA_REJECTION_MARKERS": _source_block(
                pathlib.Path(cn.__file__),
                "What a REJECTED PROJECTION looks like",
                "_SCHEMA_REJECTION_MARKERS = ("),
            "supabase_control.org_storage_notice_pct": (
                sc.org_storage_notice_pct.__doc__ or ""),
            "supabase_control._orgs_row_fail_soft": (
                sc._orgs_row_fail_soft.__doc__ or ""),
        }
        for where, block in docstrings.items():
            low = block.lower()
            for code in ("pgrst204", "42703"):
                assert code in low, (
                    f"{where} no longer names {code!r}. PostgREST documents BOTH "
                    f"shapes behind HTTP 400 — `PGRST204` for a column missing "
                    f"from its schema cache and PostgreSQL's own `42703` "
                    f"forwarded as a 400 — so dropping one leaves "
                    f"`_SCHEMA_REJECTION_MARKERS` justified by half its "
                    f"evidence: {block}")
            picked = re.search(
                r"[`\"']*(pgrst204|42703)[`\"']*\s*(?:is|as)\s+the\s+shape", low)
            assert picked is None, (
                f"{where} binds {picked.group(1)!r} to the shape this reader "
                f"sees. `SupabaseControlPlane.query` renders only the STATUS "
                f"and discards the body, so which code a rejected `select=` "
                f"carries is UNOBSERVABLE here and the vendor reference does "
                f"not pin it for `select=` (it documents `PGRST204` for "
                f"`columns=`): {block}")

    @pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 500, 502, 503])
    def test_a_non_400_status_from_the_real_seam_never_licenses_the_degrade(
            self, status):
        """The other direction, at the same seam: only a REJECTED PROJECTION is
        drift. A 401/404/422/5xx must propagate to `notice-mark-unavailable`,
        never be read as "the schema is older than 0015"."""
        cp = _RealSeamControlPlane(missing="suspended_at", status=status)
        with pytest.raises(RuntimeError):
            cn.read_account(cp, ORG_ID)
        assert len(cp.calls) == 1, (
            f"only a rejected projection (HTTP 400) licenses the retry; "
            f"HTTP {status} must propagate at once: {cp.calls}")

    def test_a_stale_gauge_is_not_a_reading(self, monkeypatch):
        """The gate treats a stale gauge as UNARMED; the notice must not state a
        figure from one (#14038 review, P3 — a 5-day-old 110 MB reading against a
        102 MB allowance produced a 100 % notice while the gate's own `_stale`
        verdict was True).

        One staleness rule, not two: `read_dimension` applies the dimension's own
        `stale_after_seconds` through the SAME `_stale` the gating path uses.
        """
        from tortoise import allowance_gate as ag

        def declare(measured_at):
            """Point the graph_storage dimension at a fake meter with this stamp."""
            meter = lambda org_id: ag.MeterReading(  # noqa: E731
                used=110.0, measured_at=measured_at, estimated=True)
            monkeypatch.setattr(
                ag, "DIMENSIONS",
                tuple(
                    ag.AllowanceDimension(
                        dimension=d.dimension, limit_key=d.limit_key,
                        unit=d.unit, stale_after_seconds=86_400,
                        meter=meter if d.dimension == cn.DIMENSION else d.meter)
                    for d in ag.DIMENSIONS))

        five_days_ago = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        for measured_at, expected in ((five_days_ago, None),
                                      (datetime.now(UTC).isoformat(), 110.0),
                                      (None, None)):
            declare(measured_at)
            reading = ag.read_dimension(cn.DIMENSION, ORG_ID)
            if expected is None:
                assert reading is None, measured_at
            else:
                assert reading is not None and reading.used == expected

    def test_a_stale_reading_sends_nothing(self):
        """End to end: a gauge the gate would not act on produces no email.

        The notice reads `no-reading`, so nothing is stated and — because every
        exit but `sent` leaves the mark alone — nothing is stamped.
        """
        cp = _fake_cp()
        send = _Sender()
        with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
             patch("tortoise.allowance_gate.read_dimension",
                   lambda dimension, org_id: None):
            out = asyncio.run(cn.sweep_pass(
                cp=cp, send=send, enumerate_orgs=lambda: [_org_row()]))
        assert out["statuses"].get("no-reading") == 1
        assert send.calls == []
        assert cp.tables["organizations"][0]["storage_notice_pct"] is None

    def test_an_unmigrated_ledger_column_sends_nothing(self):
        """Code deployed before the migration: the mark read raises, so the pass
        reports `notice-mark-unavailable` and sends NOTHING (#14038 review, P3).

        Sending without a ledger would re-send the same one-shot notice on every
        6-hourly pass; the provider key cannot make that safe (it names a
        threshold, not a pass).
        """
        cp = _fake_cp()
        cp.tables["organizations"][0]["storage_notice_pct"] = ""
        send = _Sender()

        def exploding_read(cp_, org_id):
            raise RuntimeError("column storage_notice_pct does not exist")

        from unittest.mock import patch
        with patch.object(cn, "read_account", exploding_read):
            out = asyncio.run(cn.sweep_pass(
                cp=cp, send=send, enumerate_orgs=lambda: [_org_row()]))
        assert out["statuses"].get("notice-mark-unavailable") == 1
        assert send.calls == []

    def test_a_failed_pre_send_re_read_is_recorded_and_the_send_still_goes(
            self, caplog):
        """⛔ The re-read is the only thing that narrows the cross-process window,
        and a failure used to be SILENT (#14038 fix lane).

        The `except` assigned the stale value and emitted nothing, so the send
        went out at the WIDER window while the comment above it said the window
        was narrowed to one round trip — a reader (and an operator reading the
        logs) could not tell the two apart. This is the same class as the
        `no-reading` line the pass logs when the gauge cannot be read, and the
        fix is a record, not a refusal: the send PROCEEDS on the value this pass
        already gated on, which is exactly the CAS guard the stamp uses, so the
        LEDGER stays monotone either way.

        Measured non-vacuous at the pre-fix head: with the `logger.warning`
        removed this row fails on its log assertion (no record of the failure)
        and the two status assertions still pass — i.e. it grades the RECORD, not
        the send.
        """
        cp = _fake_cp()                       # mark unset; the read would work
        send = _Sender()
        account = cn.Account(org_id=ORG_ID, email="dana@acme.example",
                             name="Acme", notice_pct=None)
        with patch.object(sc, "org_storage_notice_pct",
                          side_effect=RuntimeError("control plane 503")), \
                caplog.at_level(logging.WARNING,
                                logger="tortoise.capacity_notice"):
            out = asyncio.run(cn.notify_account(
                ORG_ID, cp=cp, account=account, send=send,
                limits=_limits(102), reading=_Reading(90.0)))
        assert out["status"] == "sent", out
        assert len(send.calls) == 1
        lines = [r.getMessage() for r in caplog.records]
        assert any("pre-send mark re-read" in line and ORG_ID in line
                   for line in lines), lines
        assert any("NOT narrowed" in line for line in lines), lines

    def test_the_mark_is_re_read_immediately_before_sending(self):
        """A concurrent stamp between the gauge read and the send must suppress
        the send (#14038 review, P2: two processes could otherwise deliver 100 %
        then 80 %).
        """
        cp = _fake_cp(storage_notice_pct=100)      # stamped by a peer already
        send = _Sender()
        account = cn.Account(org_id=ORG_ID, email="dana@acme.example",
                             name="Acme", notice_pct=None)  # …but we read stale
        out = asyncio.run(cn.notify_account(
            ORG_ID, cp=cp, account=account, send=send,
            limits=_limits(102), reading=_Reading(90.0)))
        assert out["status"] == "already-notified"
        assert out["notified"] == 100
        assert send.calls == []

    def test_a_SAME_threshold_stamp_is_the_duplicate_the_re_read_exists_for(self):
        """The EQUALITY case — the one a duplicate notice actually comes from.

        A peer stamped THIS threshold between this pass's row read and its send:
        the account row says ``None``, the ledger says ``80``, and this pass is
        about to send ``80``. ``<=`` suppresses it; ``<`` sends a SECOND 80 %
        notice, which is the defect the pre-send re-read exists to prevent.

        ⛔ Added because the sibling row above pins only ``fresh > threshold``
        (mark 100 / notice 80), so inverting the comparison to ``<`` left the
        whole file GREEN — measured, 134 passed (#14038 mutation pass). A guard
        that stays green under the inversion it claims to protect is not a
        guard; this row is what makes the equality reachable.
        """
        cp = _fake_cp(storage_notice_pct=80)       # stamped AT this threshold
        send = _Sender()
        account = cn.Account(org_id=ORG_ID, email="dana@acme.example",
                             name="Acme", notice_pct=None)  # …read stale
        out = asyncio.run(cn.notify_account(
            ORG_ID, cp=cp, account=account, send=send,
            limits=_limits(102), reading=_Reading(90.0)))
        assert out["status"] == "already-notified", out
        assert out["notified"] == 80
        assert send.calls == []

    def test_a_fully_notified_account_costs_no_gauge_read(self):
        """The top mark is the whole ledger: skip the two sync gauge reads."""
        cp = _fake_cp(storage_notice_pct=100)
        send = _Sender()
        from unittest.mock import patch

        def must_not_be_called(*a, **k):
            raise AssertionError("the gauge was read for a fully-notified account")

        with patch.object(cn, "evaluate", must_not_be_called):
            out = asyncio.run(cn.notify_account(
                ORG_ID, cp=cp,
                account=cn.Account(org_id=ORG_ID, email="d@acme.example",
                                   name="Acme", notice_pct=100),
                send=send))
        assert out["status"] == "already-notified"
        assert send.calls == []


class TestSweepPass:
    """The pass itself: burst guard, liveness, and what it learns from one run."""

    def test_a_soft_deleted_org_is_neither_emailed_nor_stamped(self):
        """The enumeration deliberately includes soft-deleted orgs (they can still
        be BILLED); a customer email must not (#14038 review, P2 — and the copy
        would say "nothing is lost or deleted" to an org inside its grace window).
        """
        cp = _fake_cp(deleted_at="2026-11-19T00:00:00+00:00")
        send = _Sender()
        from unittest.mock import patch
        with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
             patch("tortoise.allowance_gate.read_dimension",
                   lambda dimension, org_id: _Reading(103.0)):
            summary = asyncio.run(cn.sweep_pass(
                cp=cp, send=send,
                enumerate_orgs=lambda: [_org_row(
                    deleted_at="2026-11-19T00:00:00+00:00")]))
        assert summary["statuses"].get("deleted-org") == 1
        assert send.calls == []
        assert cp.tables["organizations"][0]["storage_notice_pct"] is None

    def test_a_suspended_org_is_neither_emailed_nor_stamped(self):
        """#1853: a suspended org's calls 403 but its inbox is still open to us.

        The notice offers to ADD CAPACITY; offering it to an account that cannot
        use the capacity is noise at best, and the stamp is once-ever.
        """
        cp = _fake_cp(suspended_at="2026-11-19T00:00:00+00:00")
        send = _Sender()
        with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
             patch("tortoise.allowance_gate.read_dimension",
                   lambda dimension, org_id: _Reading(103.0)):
            summary = asyncio.run(cn.sweep_pass(
                cp=cp, send=send,
                enumerate_orgs=lambda: [_org_row(
                    suspended_at="2026-11-19T00:00:00+00:00")]))
        assert summary["statuses"].get("suspended-org") == 1
        assert send.calls == []
        assert cp.tables["organizations"][0]["storage_notice_pct"] is None

    def test_the_burst_guard_defers_and_the_frontier_still_moves(self):
        """A capped pass walks the WHOLE enumeration and stops SENDING — so the
        next pass starts with a smaller unstamped population.

        This is the property that makes the cap safe where the removed
        enumeration cap was not: a prefix re-processes, a send frontier moves
        (#14038 review, P2).
        """
        rows = [_org_row(id=f"team-{i:02d}") for i in range(5)]
        for row in rows:
            row["email"] = f"{row['id']}@acme.example"
        cp = FakeControlPlane({"organizations": rows})
        sent: list[str] = []

        async def send(copy):
            sent.append(copy.text[:0])
            return {"status": "sent", "message_id": f"m{len(sent)}"}

        from unittest.mock import patch
        with patch("tortoise.quota.resolve_org_limits",
                   lambda org_id: {**_limits(102), "org_id": org_id}), \
             patch("tortoise.allowance_gate.read_dimension",
                   lambda dimension, org_id: _Reading(103.0)):
            first = asyncio.run(cn.sweep_pass(
                cp=cp, send=send, max_sends=2,
                enumerate_orgs=lambda: [dict(r) for r in rows]))
            assert first["sent"] == 2
            assert first["checked"] == 5, "the cap must not shorten the walk"
            assert first["statuses"]["deferred"] == 3
            stamped = sum(1 for r in cp.tables["organizations"]
                          if r["storage_notice_pct"] == 100)
            assert stamped == 2
            second = asyncio.run(cn.sweep_pass(
                cp=cp, send=send, max_sends=2,
                enumerate_orgs=lambda: [dict(r) for r in rows]))
            assert second["sent"] == 2, (
                "a deferred account must be reached by a later pass — the "
                "frontier, not a stuck prefix")
        assert len(sent) == 4

    def test_a_send_whose_mark_write_fails_still_counts_against_the_cap(self):
        """An ACCEPTED notice counts against the burst guard even when the stamp
        raises (#14038 review, P2).

        The stamp is send-THEN-stamp, so the provider has already spent the
        shared Resend budget by the time `claim_notice` runs. `sweep_pass`
        decrements the cap on `status == "sent"`, so a marker write that raises
        must not be reported as `error`: a pass whose ledger WRITES fail while
        its READS still work would then send past `max_sends` — the overrun the
        cap exists to prevent.

        Asserted by the CAP, not by the label: with `max_sends=1` and two
        accounts whose every write fails, ONE email may go out, not two.
        """
        rows = [_org_row(id=f"team-{i:02d}") for i in range(2)]
        for row in rows:
            row["email"] = f"{row['id']}@acme.example"
        cp = FakeControlPlane({"organizations": rows})
        cp.fail_query(table="organizations", method="PATCH", times=10)
        sent: list[str] = []

        async def send(copy):
            sent.append(copy.text[:0])
            return {"status": "sent", "message_id": f"m{len(sent)}"}

        from unittest.mock import patch
        with patch("tortoise.quota.resolve_org_limits",
                   lambda org_id: {**_limits(102), "org_id": org_id}), \
             patch("tortoise.allowance_gate.read_dimension",
                   lambda dimension, org_id: _Reading(103.0)):
            out = asyncio.run(cn.sweep_pass(
                cp=cp, send=send, max_sends=1,
                enumerate_orgs=lambda: [dict(r) for r in rows]))
        assert out["sent"] == 1, (
            f"an accepted send must count against the cap: {out}")
        assert out["statuses"].get("sent") == 1, (
            f"a DELIVERED notice must not be reported as an error: {out}")
        assert out["statuses"].get("deferred") == 1
        assert len(sent) == 1, (
            f"max_sends=1 was exceeded because a failed mark write hid the "
            f"send: {len(sent)} emails went out")

    def test_a_gauge_that_was_never_written_is_named_in_the_summary(self):
        """`no-reading` for the whole fleet is the shape of a stopped gauge
        (BACKUP_SWEEP_ENABLED off), and it must not read as "nobody is near
        capacity" (#14038 review, P2).
        """
        cp = _fake_cp()
        send = _Sender()
        from unittest.mock import patch
        with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
             patch("tortoise.allowance_gate.read_dimension",
                   lambda dimension, org_id: None):
            summary = asyncio.run(cn.sweep_pass(
                cp=cp, send=send, enumerate_orgs=lambda: [_org_row()]))
        assert summary["statuses"]["no-reading"] == 1
        assert summary["checked"] == 1
        assert send.calls == []


class TestKnobs:
    """The env knobs, ASSERTED rather than described (#14038 review, P2 residue).

    `notices_enabled()` and `sweep_interval_s()` shipped with no test, and the
    enumeration cap this PR removed had none either — so an inverted default, a
    `sleep(0)` busy loop against every tenant's gauge, or a re-added cap would
    each have shipped green.
    """

    def test_the_pass_is_armed_by_default_and_garbage_cannot_disarm_it(
            self, monkeypatch):
        """Unset/blank/garbage -> the default (ON). Only an explicit falsy value
        disarms: a typo must never silently kill the only trigger these notices
        have.
        """
        for raw in (None, "", "   ", "maybe", "2"):
            if raw is None:
                monkeypatch.delenv(cn.NOTICE_ENABLED_ENV, raising=False)
            else:
                monkeypatch.setenv(cn.NOTICE_ENABLED_ENV, raw)
            assert cn.notices_enabled() is True, raw
        for raw in ("0", "false", "no", "off"):
            monkeypatch.setenv(cn.NOTICE_ENABLED_ENV, raw)
            assert cn.notices_enabled() is False, raw

    def test_the_interval_refuses_zero_and_garbage(self, monkeypatch):
        """A zero/negative interval is REFUSED, not accepted: `asyncio.sleep(0)`
        returns immediately and would turn the pass into a busy loop.
        """
        monkeypatch.delenv(cn.NOTICE_INTERVAL_ENV, raising=False)
        assert cn.sweep_interval_s() == cn.NOTICE_INTERVAL_DEFAULT_S
        monkeypatch.setenv(cn.NOTICE_INTERVAL_ENV, "60")
        assert cn.sweep_interval_s() == 60
        for raw in ("0", "-5", "abc", ""):
            monkeypatch.setenv(cn.NOTICE_INTERVAL_ENV, raw)
            assert cn.sweep_interval_s() == cn.NOTICE_INTERVAL_DEFAULT_S, raw

    def test_the_per_pass_send_cap_refuses_zero_and_garbage(self, monkeypatch):
        """`0` is not a cap, it is an off switch — and the off switch is
        `TORTOISE_CAPACITY_NOTICE=0`.
        """
        monkeypatch.delenv(cn.NOTICE_MAX_SENDS_ENV, raising=False)
        assert cn.max_sends_per_pass() == cn.NOTICE_MAX_SENDS_PER_PASS_DEFAULT
        monkeypatch.setenv(cn.NOTICE_MAX_SENDS_ENV, "3")
        assert cn.max_sends_per_pass() == 3
        for raw in ("0", "-1", "abc"):
            monkeypatch.setenv(cn.NOTICE_MAX_SENDS_ENV, raw)
            assert cn.max_sends_per_pass() == (
                cn.NOTICE_MAX_SENDS_PER_PASS_DEFAULT), raw

    def test_the_sweep_never_caps_the_enumeration(self):
        """P2-1's mutant guard: the removed knob can only come back by passing a
        cap to the enumerator, and this is the row that reds when it does.

        `billing._enumerate_billed_orgs(limit=N)` truncates a keyset walk to a
        deterministic lowest-id PREFIX, so the same first N accounts were
        re-processed forever and the fleet's tail was never notified — one layer
        up, the silent partial pass the certified walk exists to prevent.
        """
        calls: list[tuple[tuple, dict]] = []

        def enumerator(*args, **kwargs):
            calls.append((args, kwargs))
            return [_org_row()]

        with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(None)):
            summary = asyncio.run(cn.sweep_pass(
                cp=_fake_cp(), send=_Sender(), enumerate_orgs=enumerator,
                max_sends=1))
        assert calls == [((), {})], (
            "the enumeration must be called with NO cap — a limit here is the "
            "#14038 P2 defect returning")
        assert summary["checked"] == 1
        assert not hasattr(cn, "sweep_limit"), (
            "the enumeration-cap knob was removed (#14038 review, P2) and must "
            "not be reintroduced")


class TestHostedWiring:
    """The two server-side facts: the kill switch and the mode guard.

    #14038 review, P2: the wrapper's docstrings asserted both, and nothing
    executed them — an inverted `notices_enabled()` or a dropped mode guard
    would have shipped green.
    """

    def test_the_kill_switch_short_circuits_before_any_read(self, monkeypatch):
        import tortoise.hosted_api as api

        monkeypatch.setenv("TORTOISE_CAPACITY_NOTICE", "0")
        called = []
        monkeypatch.setattr(cn, "sweep_pass",
                            lambda **k: called.append(1))
        assert asyncio.run(api._capacity_notice_sweep_pass()) == {"enabled": False}
        assert called == []

    def test_registry_mode_is_skipped_not_swept(self, monkeypatch):
        import tortoise.hosted_api as api
        from tortoise import supabase_control

        monkeypatch.delenv("TORTOISE_CAPACITY_NOTICE", raising=False)
        monkeypatch.setattr(supabase_control, "is_supabase_enabled",
                            lambda: False)
        called = []
        monkeypatch.setattr(cn, "sweep_pass",
                            lambda **k: called.append(1))
        assert asyncio.run(api._capacity_notice_sweep_pass()) == {
            "enabled": True, "skipped": "registry-mode"}
        assert called == []

    def test_a_raising_pass_cannot_end_the_loop(self, monkeypatch):
        """#5381: the observable invariant is that a SUBSEQUENT iteration actually
        runs — the evidence `_event_retention_loop`'s own contract asks for.
        """
        import tortoise.hosted_api as api

        calls: list[int] = []

        async def flaky() -> dict:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return {"checked": 0, "sent": 0, "statuses": {}}

        monkeypatch.setattr(api, "_capacity_notice_sweep_pass", flaky)

        async def drive() -> None:
            task = asyncio.create_task(
                api._capacity_notice_sweep_loop(0.01, first_delay=0))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if len(calls) >= 3:
                    break
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        asyncio.run(drive())
        assert len(calls) >= 3, calls


class TestNoticeMark:
    def test_unset_then_cas_stamp(self):
        cp = _fake_cp()
        assert org_storage_notice_pct(cp, ORG_ID) is None
        assert set_org_storage_notice_pct(cp, ORG_ID, 80, previous=None) is True
        assert org_storage_notice_pct(cp, ORG_ID) == 80
        # a stale pass that read None can no longer claim the first send
        assert set_org_storage_notice_pct(cp, ORG_ID, 80, previous=None) is False
        # …nor can a lagging 80 % overwrite a 100 %
        assert set_org_storage_notice_pct(cp, ORG_ID, 100, previous=80) is True
        assert set_org_storage_notice_pct(cp, ORG_ID, 80, previous=80) is False
        assert org_storage_notice_pct(cp, ORG_ID) == 100

    def test_the_mark_never_moves_downwards(self):
        cp = _fake_cp(storage_notice_pct=100)
        assert set_org_storage_notice_pct(cp, ORG_ID, 80, previous=None) is False
        assert set_org_storage_notice_pct(cp, ORG_ID, 80, previous=100) is False
        assert org_storage_notice_pct(cp, ORG_ID) == 100

    def test_unknown_team_reads_unset_and_stamps_nothing(self):
        cp = _fake_cp()
        assert org_storage_notice_pct(cp, "no-such-team") is None
        assert set_org_storage_notice_pct(cp, "no-such-team", 80,
                                          previous=None) is False

    def test_an_unreadable_mark_reads_as_unset(self):
        """A corrupt value must not silently suppress every future notice."""
        cp = _fake_cp(storage_notice_pct="eighty")
        assert org_storage_notice_pct(cp, ORG_ID) is None


# ── the shared sender: one path, budget-guarded, idempotency-keyed ──────────

def test_send_notice_email_uses_the_shared_budget_and_idempotency_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    email_notify._send_counts_day = 0
    email_notify._send_counts_month = 0
    email_notify._send_counts_day_period = ""
    email_notify._send_counts_month_period = ""
    email_notify._skip_logged.clear()
    calls = []

    class _Resp:
        def raise_for_status(self): pass

        def json(self): return {"id": "msg_abc"}

    class _Client:
        def __init__(self, *a, **k): pass

        async def __aenter__(self): return self

        async def __aexit__(self, *a): return False

        async def post(self, url, **kwargs):
            calls.append(kwargs)
            return _Resp()

    monkeypatch.setattr(email_notify.httpx, "AsyncClient", _Client)
    copy = cn.render(80, "Dana")
    result = asyncio.run(email_notify.send_notice_email(
        "dana@acme.example", copy.subject, copy.html, copy.text,
        idempotency_key="capacity-notice:team-x:80", label="capacity notice 80%"))
    assert result["status"] == "sent"
    assert calls[0]["headers"]["Idempotency-Key"] == "capacity-notice:team-x:80"
    assert calls[0]["json"]["to"] == ["dana@acme.example"]
    assert email_notify._send_counts_day == 1


def test_send_notice_email_refunds_the_slot_on_failure(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    email_notify._send_counts_day = 0
    email_notify._send_counts_month = 0
    email_notify._send_counts_day_period = ""
    email_notify._send_counts_month_period = ""
    email_notify._skip_logged.clear()

    class _Client:
        def __init__(self, *a, **k): pass

        async def __aenter__(self): return self

        async def __aexit__(self, *a): return False

        async def post(self, url, **kwargs):
            raise RuntimeError("provider down")

    monkeypatch.setattr(email_notify.httpx, "AsyncClient", _Client)
    result = asyncio.run(email_notify.send_notice_email(
        "dana@acme.example", "s", "<p>h</p>", "t", idempotency_key="k"))
    assert result["status"] == "failed"
    assert email_notify._send_counts_day == 0


def _production_send(monkeypatch, *, stale: str | None = None) -> dict:
    """One real `notify_account` pass through the REAL sender (`send=None`).

    Only the provider POST is stubbed: the subject here is the LOG an operator
    reads, not delivery, so a fake `send` would bypass the lines under test.
    """
    email_notify._send_counts_day = 0
    email_notify._send_counts_month = 0
    email_notify._send_counts_day_period = ""
    email_notify._send_counts_month_period = ""
    email_notify._skip_logged.clear()
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    class _Resp:
        def raise_for_status(self): pass

        def json(self): return {"id": "msg_log"}

    class _Client:
        def __init__(self, *a, **k): pass

        async def __aenter__(self): return self

        async def __aexit__(self, *a): return False

        async def post(self, url, **kwargs): return _Resp()

    monkeypatch.setattr(email_notify.httpx, "AsyncClient", _Client)
    address = stale or "dana@acme.example"
    cp = _fake_cp(email=address)
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)):
        return _pass(cp=cp, enumerate_orgs=lambda limit=None: [
            _org_row(email=address)])


def test_the_send_log_names_the_team_and_never_the_mailbox(caplog, monkeypatch):
    """The success line identifies the ACCOUNT and not the ADDRESS.

    ⛔ BOTH HALVES ARE LOAD-BEARING, and neither was pinned before this row: the
    address came OUT of the log (a log is durable; the rung logs name the mailbox
    CLASS, never the value), and the team went IN, because once the address was
    gone the label was the only attribution a successful send had left — measured
    before the fix, the line read `capacity notice 80% email accepted by provider
    (msg msg_1)`, which names no account at all (#14038 review).
    """
    caplog.set_level(logging.INFO, logger="tortoise.email_notify")
    out = _production_send(monkeypatch)
    accepted = [ln for ln in caplog.text.splitlines()
                if "accepted by provider" in ln]
    assert out["statuses"]["sent"] == 1
    assert accepted, f"the success line is missing: {caplog.text!r}"
    assert f"team {ORG_ID}" in accepted[0], (
        f"the success line names no account: {accepted[0]!r}")
    assert "dana@acme.example" not in accepted[0], (
        f"the success line prints the recipient address: {accepted[0]!r}")


def test_the_skip_arm_logs_no_mailbox_either(caplog, monkeypatch):
    """The third arm of the same posture: budget-exhausted must not leak either.

    The accept and fail arms were stripped of the address first, and this one was
    left behind — it is reachable (any day the shared budget is spent), so the
    rule is either true on all three arms or it is not a rule (#14038 review).
    """
    caplog.set_level(logging.WARNING, logger="tortoise.email_notify")
    monkeypatch.setattr(email_notify, "reserve_send_slot", lambda: "day-cap")
    out = _production_send(monkeypatch)
    skipped = [ln for ln in caplog.text.splitlines() if "SKIPPED" in ln]
    assert out["statuses"]["skipped"] == 1
    assert skipped, f"the skip line is missing: {caplog.text!r}"
    assert f"team {ORG_ID}" in skipped[0], (
        f"the skip line names no account: {skipped[0]!r}")
    assert "dana@acme.example" not in skipped[0], (
        f"the skip line prints the recipient address: {skipped[0]!r}")


def test_greeting_reuses_the_onboarding_heuristic():
    """One heuristic for every email from this product (#2406)."""
    assert email_notify.recipient_first_name(None, "dana@acme.example",
                                             "Acme") == "Dana"
    assert email_notify.recipient_first_name(None, "info@acme.example",
                                             "Acme") == "Acme"
    assert email_notify.recipient_first_name("Dana Lopez", None, None) == "Dana"


# ── 5. The decision runs OFF the event loop ────────────────────────────────────

def test_the_decision_runs_off_the_event_loop():
    """⛔ #13998 review 4 (P1): `evaluate` is sync and does TWO blocking reads.

    `notify_account` already offloaded the reads BESIDE the decision
    (`read_account`, `claim_notice`) but called `evaluate` inline. `evaluate`
    performs two control-plane round-trips (`quota.resolve_org_limits` →
    `_orgs_row_fail_soft` → `cp.query`, and `allowance_gate.read_dimension` →
    `metering.get_graph_storage_reading` → `cp.query`), and this module's pass
    runs as an asyncio task INSIDE the API process
    (`hosted_api._capacity_notice_sweep_loop`, armed by default) — so every
    account stalled the whole event loop for the length of a fleet pass, the very
    thing `_cp_offload` exists for (#3498).

    The assertion is the THREAD the instrument runs on, measured: pre-fix it is
    the loop's own thread, post-fix a worker's.
    """
    cp = _fake_cp()
    loop_thread = threading.get_ident()
    instrument_threads: list[int] = []

    def observed_limits(org_id):
        instrument_threads.append(threading.get_ident())
        return _limits(102)

    from unittest.mock import patch
    with patch("tortoise.quota.resolve_org_limits", observed_limits), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)):
        out = asyncio.run(cn.notify_account(ORG_ID, cp=cp, send=_Sender()))
    assert out["status"] == "sent"
    assert instrument_threads, (
        "the allowance was never resolved — this row would be vacuous")
    assert instrument_threads[0] != loop_thread, (
        "the decision ran ON the event loop: `evaluate` issues two blocking "
        "control-plane reads, so a fleet pass freezes the API process")


# ── 6. The migration, and the #1096 ladder it depends on ────────────────────
#
# Both of these are the #13998 review's BLOCKING findings, and both were
# invisible to the behavioural tests above because neither finding is about the
# copy or the ledger — they are about whether the column the ledger lives in can
# exist at all, and whether every reader of it can DEGRADE when it does not.

def _ddl_only(sql: str) -> str:
    """The statement text, with `--` comment lines removed.

    The pins below are about what the migration DOES. The header deliberately
    NAMES the pre-rename table to explain why it is not used, so scanning raw
    text would red on the prose that prevents the bug.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _source_block(path: pathlib.Path, start_marker: str,
                  end_marker: str) -> str:
    """The source text from `start_marker` up to the next `end_marker`.

    A docstring pin has to read the text a reviewer reads. `inspect.getsource`
    cannot reach a `#:` comment block that justifies a CONSTANT, which is where
    `_SCHEMA_REJECTION_MARKERS`' evidence lives, so the file is sliced instead.
    A missing marker raises `ValueError` rather than returning `''` — an empty
    slice would make every assertion below vacuous.
    """
    text = path.read_text()
    start = text.index(start_marker)
    return text[start:text.index(end_marker, start)]


# --- the Idempotency-Key's bound: the PROVIDER's window, not ours ----------
# #14038 review (P2, tree) found FOUR passages stating that the provider
# Idempotency-Key "collapses" the crash-window duplicate without its bound. TWO
# fresh-context reviews of successive cuts of this fix then defeated the detector
# itself, three times over, and each defeat is pinned below:
#   * cut 1 matched the literal phrase "idempotency key" beside the stem
#     "collaps" — GREEN on four reworded sites ("dedupes only the SAME
#     threshold", "collapsed the duplicate send ... outcome is still
#     exactly-once");
#   * cut 2 read only docstrings and comment lines, so the runtime STRING an
#     operator reads in the log ("replaying this threshold is collapsed by the
#     provider key") was outside it entirely;
#   * cut 2 also searched the verb only AFTER the subject (a passive "is covered
#     by the provider Idempotency-Key" read as clean) and allowed the bound in
#     the NEXT sentence, where any unrelated "runs every 24 h" cleared a claim.
# Resend's page is the bound: "Resend checks whether an email with the same
# idempotency key has already been sent in the last 24 hours", so a replay older
# than that window sends AGAIN.
_SUBJECT = re.compile(r"idempotency[-\s]?key|provider\s+key", re.IGNORECASE)
#: The verbs that claim the key REMOVES a duplicate. Voice and order do not
#: matter: both operands are searched over the whole sentence, because a passive
#: construction puts the verb first and a rewrite can pick any of these. The set
#: is not a guess — a review defeated the previous cut with each of
#: "covered"/"prevents"/"avoids"/"removes"/"eliminates"/"blocks"/"makes a
#: replay safe"/"ensures ... the notice is sent at most once", so every one of
#: them is here, verbatim, as a case in the discrimination test below. A review
#: then found the CODE said all of them were covered while "makes a replay safe"
#: was not ("replay" was missing from `_SEND_OBJECT`) — so the case is the real
#: wording now, not a near-miss containing "send".
_COLLAPSE = re.compile(
    r"collaps|dedup|suppress|prevent|avoid|remov|eliminat|block|cover"
    r"|makes?.{0,24}(?:safe|forgiving)"
    r"|exactly[-\s]once|single\s+delivery|only\s+once|at\s+most\s+once"
    r"|ensures?.{0,40}once",
    re.IGNORECASE)
#: A DURATION, never a durationless "window" — "inside its window" is the wording
#: the original finding rejected. `<=24h` counts: `hosted_api` already writes the
#: onboarding key's bound that way.
_RETENTION_BOUND = re.compile(
    r"\**24\s*hours?\**"           # Resend's own wording
    r"|\b24\s?-?h(?:ours?)?\b"     # the short form these comments use
    r"|24[- ]hour",
    re.IGNORECASE)


#: The claim must also be ABOUT a delivered thing. `stamp`/`accept` are here
#: because the SITE THE REVIEW FOUND MISSED worded it as "the crash window
#: between accept and stamp is covered by the provider Idempotency-Key" — a
#: send-object-only rule read that sentence as not-a-claim, which is how the
#: guard stayed blind to the very site it was written for.
_SEND_OBJECT = re.compile(
    r"\bemail|\bnotice|\bsend|\bsent|\bdeliver|\bstamp|\baccept|\bduplicate"
    r"|\breplay",
    re.IGNORECASE)
#: ...but NOT the session-create idempotency key, which is a different promise
#: (one row per click) that no provider window governs. Both `hosted_api.py`
#: comments below are excluded by measurement, not by taste — they are cases in
#: the discrimination test, asserted NOT to be provider-send claims.
_NOT_PROVIDER_SEND = re.compile(
    r"session_id|one\s+click|non-episodic|\b402\b", re.IGNORECASE)
#: The veto above is whole-sentence and would otherwise hide a REAL claim that
#: happens to mention a session row — a review showed three such sentences
#: ("prevents the duplicate notice, so a replayed 402 report never sends twice",
#: "collapses the duplicate email even when one click is retried", "removes the
#: duplicate send on the non-episodic session path"). So it only applies when none of
#: `_DELIVERY_WORD`'s seven words appears — an ABSENCE test, not a reading of what the
#: sentence is about. ⛔ Measured, that test can still swallow a real claim: "the
#: provider Idempotency-Key collapses the duplicate a one click retry produces to the
#: customer" names no delivery word and grades nothing. The veto is therefore one more
#: enumeration limit, listed with the others below — not a scope that makes it safe.
_DELIVERY_WORD = re.compile(
    r"\bemail|\bnotice|\bsend|\bsent|\bdeliver|\bstamp|\baccept",
    re.IGNORECASE)


def _is_provider_send_claim(sentence: str) -> bool:
    if not (_SUBJECT.search(sentence)
            and _COLLAPSE.search(sentence)
            and _SEND_OBJECT.search(sentence)):
        return False
    return not (_NOT_PROVIDER_SEND.search(sentence)
                and not _DELIVERY_WORD.search(sentence))


def _prose_units(text: str) -> list[str]:
    """Every unit of human-readable text in `text`; source code excluded.

    `ast` answers this directly for a Python file — a docstring IS a string
    constant, and so is the log message a reader sees — while comment lines are
    what `ast` discards and a line filter keeps. The `.sql` file does not parse,
    which is fine: its claims live in `--` lines. A text with neither (the
    guard's own samples, handed in as bare sentences) is returned as-is, so those
    are graded rather than read as an empty file.
    """
    units: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                units.append(node.value)
    block: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(("#", "--")):
            block.append(re.sub(r"^\s*(#:?|--)\s?", "", line))
        elif block:
            units.append("\n".join(block))
            block = []
    if block:
        units.append("\n".join(block))
    return units or [text]


def _sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for unit in _prose_units(text):
        sentences.extend(re.split(r"(?<=[.!?])\s+", " ".join(unit.split())))
    return sentences


def _idempotency_claims(text: str) -> tuple[list[str], list[str]]:
    """(every claim that the key removes a duplicate, the unbounded ones).

    The bound must be in the SAME sentence as the claim. A one-sentence lookahead
    was tried and removed: it let an unrelated "the sweep runs every 24 h" clear a
    claim that named no window at all.
    """
    claims: list[str] = []
    unbounded: list[str] = []
    for sentence in _sentences(text):
        if not _is_provider_send_claim(sentence):
            continue
        claims.append(sentence)
        if not _RETENTION_BOUND.search(sentence):
            unbounded.append(sentence)
    return claims, unbounded


#: Every file that carries the claim. `capacity_notice` is where the notices
#: send (and where the operator-facing log line lives); `supabase_control` is
#: where every marker it stamps lives (the #2406 onboarding key is the same claim
#: one marker over); `hosted_api` stamps that marker on the send path and asserted
#: "still exactly-once"; the migration header is append-only once applied — the
#: artifact that can never be corrected later.
_IDEMPOTENCY_ARTIFACTS = (
    "tortoise/capacity_notice.py",
    "tortoise/supabase_control.py",
    "tortoise/hosted_api.py",
    "supabase/migrations/20261120000001_storage_notice_pct.sql",
)


def test_every_idempotency_key_claim_names_the_providers_retention_window():
    """⛔ #14038 review (P2, tree): the collapse was claimed UNBOUNDED.

    Nothing in this code expires an idempotency key — the window is the
    PROVIDER's — so the prose has to name it. Past 24 h the deterministic key
    `capacity-notice:{org_id}:{pct}` is replayed into a fresh window and the
    notice is sent a SECOND time, which is the path `_ACCOUNT_DRIFT_SOFT`
    describes ("an unledgered pass re-sends the one-shot notice every interval
    forever") and the path a process down past the window reaches too.

    This row grades the CLAIM, not a literal string, and it grades the file — not
    a selected region — so a new claim in a docstring, a `#:` block, a `--`
    header or a runtime log message is in scope. ⛔ WHAT IT DOES NOT GRADE, said
    so the row cannot overstate itself — all four were found by review, not
    guessed, and each is a limit of the TRIGGER rather than a claim this branch
    makes:
      * a claim that never names the key at all ("deduplication makes the send
        safe") — outside the subject match;
      * a claim that names the key but no delivery object (email/notice/send/
        sent/deliver/stamp/accept/duplicate/replay) — outside `_SEND_OBJECT`;
      * a claim phrased in session-row vocabulary (`session_id`, `one click`,
        `non-episodic`, `402`) with no delivery word — the session-create veto.
        Its test is the ABSENCE of seven words and nothing else, so it CAN swallow a
        real claim: measured, "the provider Idempotency-Key collapses the duplicate
        a one click retry produces to the customer" grades nothing. The three
        sentences it used to hide on this branch ARE graded, and remain cases below;
      * a real but UNRELATED `24 h` in the claim's own sentence — the bound is
        matched by shape, not by referent;
      * ⛔ THE DOMINANT LIMIT, and the one that hid for three cycles: the VERB SET
        (`_COLLAPSE`) is an enumeration. A claim phrased with a verb outside it
        — measured misses are "means the duplicate cannot be issued twice",
        "makes the concurrent duplicate harmless", "guarantees one email per
        org", "stops the duplicate reaching the customer", "neutralises the
        duplicate", "closes the crash window entirely" — names the key and a
        delivery object and is still not graded. That is why the four artifacts'
        prose is read in review as well as graded here.
    Every wording a review used to defeat a cut of this row is a case in
    `test_the_idempotency_bound_guard_grades_a_bound_and_an_unbounded_claim`.
    """
    graded = 0
    for rel in _IDEMPOTENCY_ARTIFACTS:
        claims, unbounded = _idempotency_claims(
            (ROOT / rel).read_text(encoding="utf-8"))
        graded += len(claims)
        assert not unbounded, (
            f"{rel}: {len(unbounded)} sentence(s) claim the provider "
            "Idempotency-Key collapses, dedupes or otherwise removes a duplicate "
            "WITHOUT naming a retention window in the same sentence. Resend keeps "
            "a key for the last 24 hours, so a replay after that window sends "
            "again and the bare claim is false:\n  "
            + "\n  ".join(s[:300] for s in unbounded))
    assert graded >= 18, (
        f"only {graded} sentence(s) were graded — this row is vacuous if the "
        "claim's homes stop being found. Eighteen is EXACTLY what the detector "
        "reads across the four files at this head, so the floor has no slack: a "
        "single claim dropping out of the scan is red, not absorbed")

def test_the_idempotency_bound_guard_grades_a_bound_and_an_unbounded_claim():
    """The detector's own discriminating power, pinned on what BEAT earlier cuts.

    A guard nobody has seen refuse is a guard that may be matching nothing — and
    this one has been defeated twice, so every defeat is a case here. The
    unbounded samples are wordings that were live on this branch or that a
    reviewer used to defeat a previous cut.
    """
    unbounded_samples = (
        # the pre-fix wording of the migration header
        "The provider Idempotency-Key `capacity-notice:{org_id}:{pct}` collapses "
        "the one duplicate a crash between the provider accept and the stamp can "
        "produce.",
        # the pre-fix wording of the key's own dedupe note
        "the Idempotency-Key below dedupes only the SAME threshold; closing the "
        "order window needs a pre-send reservation.",
        # the pre-fix wording of `set_org_storage_notice_pct`, which a previous
        # review found BYTE-IDENTICAL to the unfixed text: "covered" is not a
        # synonym any earlier cut's verb list carried
        "The crash window between accept and stamp is covered by the provider "
        "Idempotency-Key ``capacity-notice:{org_id}:{pct}``.",
        # the rest of the family a review measured as NO-CLAIM
        "the provider Idempotency-Key prevents the duplicate send",
        "the provider Idempotency-Key avoids a second email",
        "the provider Idempotency-Key removes the duplicate notice",
        "the provider Idempotency-Key eliminates the duplicate notice",
        "the provider Idempotency-Key blocks the duplicate notice",
        "the provider Idempotency-Key makes the duplicate send safe",
        # verbatim, so the code comment above cannot claim it while the sample is
        # a near-miss: "replay" is a delivery word in its own right
        "the provider Idempotency-Key makes a replay safe",
        # the "exactly-once" alternative had NO sample once a rewrite dropped it,
        # so the comment certifying full coverage was false for exactly this row
        "the provider Idempotency-Key collapsed the duplicate send; outcome is "
        "still exactly-once",
        "the provider Idempotency-Key ensures the notice is sent at most once",
        # the runtime log STRING that cut 2 never read (the full message, since a
        # claim is judged in its real sentence, not as a fragment)
        "capacity notice: sent to team %s at %s%% but the mark moved concurrently "
        "(replaying this threshold is collapsed by the provider key; a DIFFERENT "
        "threshold's notice is not)",
        # PASSIVE voice — the verb precedes the subject
        "a duplicate send is collapsed by the provider Idempotency-Key",
        # no "collapse" anywhere in the sentence
        "the provider key suppresses the duplicate send",
        "the provider key guarantees a single delivery per threshold",
        # a durationless "window" — the wording the original finding rejected
        "the provider Idempotency-Key collapses the duplicate inside its window",
        # an unrelated 24 h in the NEXT sentence must not clear the claim
        "the provider Idempotency-Key collapses the duplicate. The sweep runs "
        "every 24 h.",
    )
    for sample in unbounded_samples:
        claims, unbounded = _idempotency_claims(sample)
        assert claims and unbounded, (
            f"the guard read an over-claim as bounded: {sample!r}")
    for sample in (
        "the provider Idempotency-Key collapses the duplicate within its 24 h "
        "window.",
        "the duplicate send a crash between accept and stamp can produce is "
        "covered by the provider Idempotency-Key, inside its 24 h window.",
        # the artifact's own wording, `≤24h` included. A previous cut of this fix
        # reworded it to "the duplicate notice from cross-process replays", which
        # appears in NO artifact, and in doing so dropped the only bounded sample
        # that pinned the replay-only shape. DELIBERATELY a fragment, against the
        # rule stated above: measured with `|\breplay` removed from
        # `_SEND_OBJECT`, the artifact's FULL sentence still grades a claim
        # ("send"/"stamp" in its surrounding clause) while this fragment does not
        # — so quoting the whole sentence would mask a `replay` regression.
        "the provider Idempotency-Key onboarding:{org_id} collapses "
        "cross-process replays \u226424h.",
        # SYNTHESISED. Measured: the artifact's own sentence
        # (`capacity_notice.py`: "Resend keeps a key for \"the last 24 hours\" …")
        # IS graded a claim — but only by accident of its URL slug
        # (``idempotency-keys`` satisfies `_SUBJECT`) and its leading "THAT
        # COLLAPSE" clause (`_COLLAPSE`); the bare "Resend keeps a key for …"
        # fragment grades nothing, failing BOTH. So the artifact's wording is
        # graded, but by a path this guard would not credit, which is no help in
        # pinning that a plain sentence naming the key and the bound is read as
        # bounded. This sample states that plainly and independently. The
        # artifact's real prose is graded by the artifact row above, not copied
        # here, so the two cannot drift apart.
        "Resend keeps the Idempotency-Key for the last 24 hours, so it "
        "collapses a replayed notice inside that window.",
    ):
        claims, unbounded = _idempotency_claims(sample)
        # ⛔ #14038 review (P3, tree): the previous cut asserted only
        # `not unbounded`, so a sample the detector did not read as a CLAIM
        # satisfied it vacuously — TWO of these four did exactly that (`claims=0,
        # unbounded=0` for the "crash window … covered" and "Resend keeps that
        # key" lines; measured at `ca03e79c4eb`, the parent of the commit that
        # added this assertion — NOT at HEAD~1, where all four already graded).
        # A bounded sample that is
        # not a claim grades nothing, so both halves are asserted now.
        assert claims, (
            f"this sample is not read as a claim at all, so it grades no bound: "
            f"{sample!r}")
        assert not unbounded, (
            f"a bounded sentence was graded unbounded: {sample!r}")
    # ⛔ AND NOT A CLAIM AT ALL: the session-create idempotency key is a different
    # promise (one row per click) that no provider window governs. Grading it
    # would demand a provider window in a sentence about Postgres rows, so the
    # exclusion is measured on both live comments rather than assumed.
    for not_a_provider_send in (
        "#1727: resolve the idempotency key + probe EARLY so session_existed runs "
        "BEFORE the quota estimate — a re-POST of an existing session_id writes "
        "ZERO non-episodic points (the replay path below skips extraction) and "
        "must never be 402-blocked by an as-if-fresh estimate.",
        "Do NOT release its reservation — this request never took it, and "
        "dropping it lets a third request claim afresh and record a SECOND row "
        "for one click (the duplicate the idempotency key exists to prevent).",
    ):
        claims, _unbounded = _idempotency_claims(not_a_provider_send)
        assert not claims, (
            f"a session-create idempotency-key sentence was graded as a "
            f"provider-send claim: {not_a_provider_send!r}")
    # ⛔ AND THE VETO MUST NOT HIDE A REAL CLAIM that happens to mention a row:
    # a review showed the whole-sentence veto swallowing these three, so the veto
    # is scoped to sentences with no delivery word, and these are graded.
    for still_a_claim in (
        "the provider Idempotency-Key prevents the duplicate notice, so a "
        "replayed 402 report never sends twice",
        "the provider Idempotency-Key collapses the duplicate email even when "
        "one click is retried",
        "the provider Idempotency-Key removes the duplicate send on the "
        "non-episodic session path",
    ):
        claims, unbounded = _idempotency_claims(still_a_claim)
        assert claims and unbounded, (
            "the session-create veto swallowed a real provider-send claim: "
            f"{still_a_claim!r}")


def test_the_module_docstring_names_the_post_rename_table():
    """P3-1: the module a reader opens FIRST must not name the retired table.

    The ledger lives on `organizations.storage_notice_pct`. The pre-rename
    spelling `teams.storage_notice_pct` fails with `relation does not exist` —
    which the migration header records as the blocker this PR fixed — and it
    survived three review rounds in the module docstring while the SQL and the
    readers were already correct (#14038 review, P3).
    """
    doc = cn.__doc__ or ""
    assert "organizations.storage_notice_pct" in doc, (
        "the ledger's table is no longer named in the module docstring")
    assert "teams.storage_notice_pct" not in doc, (
        "the docstring still names the pre-rename table (`teams` was renamed by "
        "20260915000001_tenancy_team_to_org.sql) — the migration and the readers "
        "say `organizations`")


# --- the overlap-claim scan -------------------------------------------------
# Clause granularity, not line granularity (#14038 review cycle 6). A line can
# carry an unrelated negation AND the refuted claim — "a replacement cannot mount
# the held volume, yet a rolling replacement overlaps an old and a new process" —
# so an OR-across-the-line let the sentence three review cycles removed pass
# green. Every CLAUSE that mentions an overlap must negate it.
_OVERLAP_WORD = re.compile(r"(?<!non-)\boverlap", re.IGNORECASE)
_OVERLAP_NEGATED = re.compile(
    # `no` / `not` are negations too: "there is no overlap" and "the overlap is not
    # reachable" are TRUE statements here, and an earlier set of six verbs
    # false-REDded both — telling an author their truthful sentence "claim[s] an
    # OVERLAP without negating it", which invites a reword to satisfy the regex.
    # `not` is excluded when it opens "not only", which is an ADDITIVE and can sit
    # beside a real claim ("… not only N>1 replicas").
    r"\b(?:cannot|can ?not|does not|do not|nor|never|no|not(?!\s+only))\b",
    re.IGNORECASE)
_OVERLAP_CLAUSE_SPLIT = re.compile(
    # ⛔ THE SEPARATORS ARE THE GUARD'S WHOLE MECHANISM (#14038 review cycle 8).
    # Each widening was forced by a laundering this set let through: cycle 6 added
    # clause granularity, cycle 7 added `[,()—–]` after a comma/dash/paren kept
    # the claim in the same clause as an unrelated `cannot`, and cycle 8 added the
    # subordinators after `while`/`whereas`/`although`/`because`/`when`/`since`/
    # `though`/`as` did the same. A joiner OUTSIDE this list is not caught — see
    # the scan's docstring, which states that limit instead of claiming coverage.
    r"(?<=[.;:!?])\s+"
    r"|\s+(?:and|but|yet|so|while|whereas|although|though|because|when|since"
    r"|as|if|unless|which|that)\s+"
    r"|[,()\u2014\u2013]",
    re.IGNORECASE)


def _affirmative_overlap_claims(text: str) -> list[tuple[int, str]]:
    """Every ``(line, clause)`` that mentions an overlap WITHOUT negating it.

    Grading a CLAUSE rather than a line is the whole point: on this topology
    every true statement about overlap is a negative one, so a clause that NAMES
    an overlap and carries no negation is the refuted claim — regardless of what
    the rest of the line says.

    ⛔ THIS STATES ITS MECHANISM, NOT A COVERAGE CLAIM, because two earlier cuts
    of this docstring overstated what the scan can do and that overstatement was
    itself the finding. Concretely: it splits each line on the separators in
    `_OVERLAP_CLAUSE_SPLIT` — terminal punctuation, the coordinators and
    subordinators listed there, and commas/brackets/dashes — and flags any clause
    that names an overlap with no negating token. A joiner OUTSIDE that list, or
    a paraphrase that never names an overlap ("run side by side"), is NOT caught.
    That residue is why the artifacts' prose is also read in review; this row
    catches the forms it enumerates, not every way to say the thing.
    """
    claimed: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not _OVERLAP_WORD.search(line):
            continue
        for clause in _OVERLAP_CLAUSE_SPLIT.split(line):
            clause = clause.strip()
            if not clause or not _OVERLAP_WORD.search(clause):
                continue
            if _OVERLAP_NEGATED.search(clause):
                continue
            claimed.append((lineno, clause))
    return claimed


def test_the_overlap_guard_grades_each_CLAUSE_not_each_line():
    """The guard's own discriminating power, pinned (#14038 review cycle 6).

    Cycle 4 defeated the exact-string match with a PARAPHRASE, so cycle 5 made
    the rule semantic — but at LINE granularity, an OR over the whole line: "the
    line names an overlap AND the line also contains a negation". Measured on the
    cycle-5 guard, this shape passed GREEN:

        # a replacement cannot mount the held volume, yet a rolling replacement
        # overlaps an old and a new process in ordinary operation.

    which is precisely the "reachable in normal operation" claim the review
    removed three times. This row is what makes the clause-level rule testable
    instead of a claim in a commit message: a regression to line granularity
    reds here, and the honest spellings below stay green so the row cannot be
    satisfied by reddening prose.
    """
    refuted_on_one_line = (
        "# a replacement cannot mount the held volume, yet a rolling replacement "
        "overlaps an old and a new process in ordinary operation.\n")
    assert _affirmative_overlap_claims(refuted_on_one_line), (
        "one line carrying an unrelated negation ('a replacement cannot mount the "
        "held volume') let an affirmative OVERLAP claim through — the scan is "
        "grading LINES again instead of clauses: "
        f"{refuted_on_one_line!r}")

    # ⛔ `not` inside "not only" is an ADDITIVE, not a negation. The cycle-5
    # paraphrase ended "…, not only N>1 replicas", so counting every `not` as a
    # negation would re-open the exact sentence this guard exists to stop.
    not_only_trap = (
        "# which includes the old/new process overlap of a rolling replacement, "
        "not only N>1 replicas.\n")
    assert _affirmative_overlap_claims(not_only_trap), (
        "'not only' was read as a negation and licensed an affirmative OVERLAP "
        f"claim — it is an ADDITIVE: {not_only_trap!r}")

    # ⛔ PUNCTUATION IS A CLAUSE BOUNDARY (#14038 review cycle 7). The cycle-6
    # splitter knew only `[.;:!?]` and `and/but/yet/so`, so a comma, a dash or a
    # parenthesis kept a laundered claim in the SAME "clause" as an unrelated
    # `cannot` and the row passed GREEN. Each of these is the exact sentence the
    # review has removed six times, re-punctuated.
    for laundered in (
        "# a replacement cannot mount the held volume, a rolling replacement "
        "overlaps an old and a new process.\n",
        "# a replacement cannot mount the held volume \u2014 a rolling replacement "
        "overlaps an old and a new process.\n",
        "# a replacement cannot mount the held volume (in practice a rolling "
        "replacement overlaps an old and a new process).\n",
        # cycle 8: a subordinator is a clause boundary too. These eight all let the
        # refuted sentence through before `_OVERLAP_CLAUSE_SPLIT` carried them.
        *[
            "# a replacement cannot mount the held volume "
            f"{joiner} a rolling replacement overlaps an old and a new process.\n"
            for joiner in ("while", "whereas", "although", "because", "when",
                           "since", "though", "as")
        ],
    ):
        assert _affirmative_overlap_claims(laundered), (
            "a claim laundered past the scan by punctuation — the splitter is "
            "missing a clause boundary, so an unrelated negation elsewhere on the "
            f"line licensed it: {laundered!r}")

    for honest in (
        "# a rolling replacement cannot overlap an old and a new process.\n",
        # The two TRUE spellings an earlier negation set false-REDded: the failure
        # message accused an author's truthful sentence of "claim[ing] an OVERLAP
        # without negating it", which invites a reword to satisfy the regex.
        "# there is no overlap between the old and the new process.\n",
        "# the overlap is not reachable in ordinary operation.\n",
        "# Fly does not promise the overlap either: `bluegreen` cannot be "
        "used for Machines with attached volumes.\n",
        "# the volume is single-attach, so a replacement cannot overlap the old "
        "process; the old process exits first.\n",
        "# the topology is non-overlap: one machine, one single-attach volume.\n",
    ):
        assert not _affirmative_overlap_claims(honest), (
            f"an honest negation was graded as a claim: {honest!r} -> "
            f"{_affirmative_overlap_claims(honest)!r}")


def test_the_residuals_reachability_is_pinned_to_what_fly_toml_documents():
    """⛔ #14038 review cycle 3 (P2): "reachable in normal operation" was an
    INFERENCE about the platform, and `fly.toml` refutes it.

    The out-of-order residual needs two LIVE processes on the same account. The
    deployed app is ONE machine mounting a SINGLE-ATTACH volume, so a rolling
    replacement cannot bring a second process up while the first still holds that
    volume — the old process must release it first. Fly does not promise the
    overlap either: its config reference replaces the Machines one by one under
    `rolling` and puts the alongside-boot in `bluegreen`, which it also says
    cannot be used for Machines with attached volumes (this app sets
    `strategy = "rolling"`).

    Cycles 2 and 3 shipped the opposite claim into the module docstring AND into
    the migration header, which is append-only once applied — so the artifact that
    can never be corrected carried the least-verified sentence in the PR. This row
    is what stops the third copy — and it grades the CLAIM, not a literal phrase.
    Cycle 4 shipped a PARAPHRASE ("the old/new process overlap of a rolling
    replacement") that the previous exact-string check could not see, so a
    surviving copy passed it green — cycle 5 answered that with a semantic rule,
    but at LINE granularity. Cycle 6 closed the last way round it: the grading is
    per CLAUSE, because a single line could carry both the negation and the claim
    (see `test_the_overlap_guard_grades_each_CLAUSE_not_each_line`, which pins the
    guard's own discriminating power). Cycle 7 widened the SPLITTER itself: a
    comma, a dash or a parenthesis kept a laundered claim in the same "clause" as
    an unrelated `cannot`, so all three passed GREEN until punctuation became a
    boundary. Each widening is pinned by that same row.

    ⛔ WHAT THIS ROW DOES **NOT** GRADE, stated so the self-description cannot
    overstate its coverage the way earlier cuts did: it catches a clause that
    NAMES an overlap without negating it, where the splitter's separator list is
    the enumerable part and is described at `_OVERLAP_CLAUSE_SPLIT`. Two things
    are outside it and always will be — a joiner the list does not carry, and a
    paraphrase that never uses the word "overlap" ("the old and the new process
    run side by side"). Neither is shippable-by-construction; both are why the two
    artifacts' prose is also read in review. The row makes the enumerated forms
    un-shippable, which is what it can honestly promise.
    """
    fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
    mounts = fly.split("[[mounts]]")[1:]
    assert len(mounts) == 1, (
        f"fly.toml declares {len(mounts)} `[[mounts]]` blocks. The 'a rolling "
        "replacement cannot overlap an old and a new process' premise in "
        "`capacity_notice`'s residual paragraph and in the "
        "20261120000001_storage_notice_pct.sql header rests on exactly ONE "
        "single-attach volume — re-derive both before changing the topology")
    claimed: list[str] = []
    for path in (
        ROOT / "tortoise" / "capacity_notice.py",
        MIGRATIONS / "20261120000001_storage_notice_pct.sql",
    ):
        for lineno, clause in _affirmative_overlap_claims(
                path.read_text(encoding="utf-8")):
            claimed.append(
                f"{path.relative_to(ROOT).as_posix()}:{lineno}: {clause}")
    assert not claimed, (
        "these artifacts claim an OVERLAP without negating it, which fly.toml's "
        "single-attach volume makes impossible (a replacement cannot mount the "
        f"volume the old process still holds, so the old process exits first): {claimed}")


def test_the_mark_migration_targets_the_post_rename_table():
    """⛔ #13998 review 1 (BLOCKING): `public.teams` was RENAMED.

    `20260915000001_tenancy_team_to_org.sql:50` runs `ALTER TABLE public.teams
    RENAME TO organizations`, and no compatibility view named `teams` is created
    anywhere (`grep 'CREATE VIEW' supabase/migrations/*.sql` finds none). So the
    original `ALTER TABLE public.teams ADD COLUMN storage_notice_pct` failed
    with `relation "public.teams" does not exist`, the column never existed, and
    the whole ledger was inert — `read_account` selects it directly, so the
    sweep raised per account and sent nothing, ever. The migration was copied
    from 20260907000001, which predates the rename, and that is where the
    `teams` spelling came from.
    """
    migration = MIGRATIONS / "20261120000001_storage_notice_pct.sql"
    ddl = _ddl_only(migration.read_text())
    assert "ALTER TABLE public.organizations" in ddl
    assert "ADD COLUMN IF NOT EXISTS storage_notice_pct integer" in ddl
    assert "public.teams" not in ddl


def test_no_migration_after_the_tenancy_rename_writes_to_public_teams():
    """The same class, for every future additive migration.

    `schema-drill` (the REQUIRED leg running `supabase/tests/pglite/validate.mjs`,
    which enumerates the directory from disk) is what actually applies these; this
    pin is the cheap local arm that names the offending file instead of leaving a
    first-failure abort.
    """
    after_rename = sorted(
        p for p in MIGRATIONS.glob("*.sql") if p.name > RENAME_MIGRATION)
    assert after_rename, "the pin is vacuous — no migration sorts after the rename"
    offenders = [p.name for p in after_rename if "public.teams" in _ddl_only(p.read_text())]
    assert offenders == [], (
        f"migration(s) {offenders} reference `public.teams`, which "
        f"{RENAME_MIGRATION} renamed to `organizations` — they cannot apply")


@functools.cache
def _parse(path: pathlib.Path) -> ast.Module:
    """Parse each module ONCE — the guards in this file walk all of `tortoise/`."""
    return ast.parse(path.read_text(), filename=str(path))


def _additive_tiers() -> dict[str, list[str]]:
    """Every `_ORG_ADDITIVE_*_TIER` the ladder can drop, by name."""
    return {name: list(getattr(sc, name)) for name in dir(sc)
            if re.fullmatch(r"_ORG_ADDITIVE_[A-Z0-9_]*_TIER", name)}


def test_the_additive_tier_registry_covers_every_additive_column():
    """A column in `_ORG_ADDITIVE_SELECT` with no tier can never be dropped.

    This is the invariant that makes the ladder complete: the terminal rung is
    `[c for c in select if c not in dropped]`, so an unlisted column survives
    every rung, every attempt 400s (PGRST204), and `_orgs_row_fail_soft` ends at
    `raise last_exc` instead of degrading — the #1096/#1832 fail-OPEN-to-500
    class. Pinned as a SET EQUALITY so adding a column without a tier reds here.
    """
    tiers = _additive_tiers()
    assert tiers, "no additive tiers found — the scan is broken, not green"
    covered = {column for columns in tiers.values() for column in columns}
    assert covered == set(sc._ORG_ADDITIVE_SELECT)


def _call_sites() -> list[tuple[str, int, set[str], set[str]]]:
    """Every `_orgs_row_fail_soft(...)` call: (file, line, select tokens, tiers named).

    Parsed, not grepped: the two defective call sites sat inside `lambda:` under
    `_cp_offload`, and a text scan for "storage_notice_pct" cannot tell which
    call site a ladder belongs to.
    """
    sites = []
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_orgs_row_fail_soft"):
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            select_node, tiers_node = kwargs.get("select"), kwargs.get("additive_tiers")
            if select_node is None or tiers_node is None:
                continue
            select_tokens: set[str] = set()
            for sub in ast.walk(select_node):
                if isinstance(sub, ast.Name):
                    select_tokens.add(sub.id)
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    select_tokens.add(sub.value)
            tiers_named = {sub.id for sub in ast.walk(tiers_node)
                           if isinstance(sub, ast.Name)}
            sites.append((str(path.relative_to(ROOT)), node.lineno,
                          select_tokens, tiers_named))
    return sites


def test_every_quota_select_ladder_uses_the_one_shared_tuple():
    """⛔ #13998 review 3 (BLOCKING), re-stated against the shared ladder.

    `_QUOTA_SELECT` is `_ORG_BASE_SELECT + _ORG_ADDITIVE_SELECT`, and this PR
    added `storage_notice_pct` to the additive list. The ladder only drops a
    column that appears in a NAMED tier, so a caller that omits a tier keeps that
    column on its terminal base-only rung, 400s there, and raises —
    `hosted_api.py` records what that cost the last time (HTTP 500 on `/v1/team`,
    `/v1/team/keys`, `/v1/sessions`, `/v1/onboarding/state`), and
    `_agent_recover_flow` is the key-recovery emergency path.

    Measured at the pre-fix head: `_session_user_org` and `_agent_recover_flow`
    named NONE of the storage-notice tier, and no `_QUOTA_SELECT` caller outside
    `supabase_control.py` named `_ORG_ADDITIVE_ONBOARDING_TIER` — so
    `quota.resolve_org_limits` had the same hole for the #2406 marker. The first
    fix respelled the tier list at each site, which is the same defect one
    migration later; this asserts the ONE owner instead: a call site passes the
    shared tuple, and the tuple covers every declared tier
    (`test_the_shared_ladder_covers_every_declared_tier`).
    """
    wants_all = {"_QUOTA_SELECT", "_ORG_ADDITIVE_SELECT", "storage_notice_pct"}
    checked = 0
    wrong: list[str] = []
    for path, line, select_tokens, tiers_named in _call_sites():
        if not (select_tokens & wants_all):
            continue
        checked += 1
        if "_ORG_ADDITIVE_TIERS" not in tiers_named:
            wrong.append(
                f"{path}:{line} passes a hand-spelled ladder "
                f"({sorted(tiers_named) or 'empty'}) instead of "
                "list(_ORG_ADDITIVE_TIERS) — a site list goes stale the next "
                "time a column is added, which IS the #13998 blocker")
    assert checked >= 4, f"only {checked} ladder call sites found — the scan broke"
    assert wrong == [], "\n".join(wrong)


def test_the_shared_ladder_covers_every_declared_tier():
    """`_ORG_ADDITIVE_TIERS` must name every tier, newest-migration FIRST.

    The tuple is the one place the ladder is spelled, so a new additive column
    whose tier is declared but not appended here is dead the moment it lands: the
    ladder would never drop it, and every rung 400s. Pinned as an ordered
    equality against the tier constants themselves, newest-first, so an insertion
    at the wrong end (oldest dropped first) also reds.
    """
    members = [tuple(t) for t in sc._ORG_ADDITIVE_TIERS]
    assert members, "the shared ladder is empty — the scan is broken, not green"
    assert len(set(members)) == len(members), f"duplicate tier in {members}"
    # newest-first: the #13998 mark's tier is the newest additive migration.
    assert members[0] == tuple(sc._ORG_ADDITIVE_STORAGE_NOTICE_TIER), (
        f"the ladder must drop the NEWEST migration's tier first; it starts "
        f"with {members[0]} — {members}")
    assert members[-1] == tuple(sc._ORG_ADDITIVE_BILLING_TIER), members
    declared = {tuple(v) for v in _additive_tiers().values()}
    assert set(members) == declared, (
        "the shared ladder and the declared tiers disagree — a tier was "
        f"declared but not appended (or removed): {sorted(declared - set(members))}")


#: The one ordered ladder every drift-soft call site passes.
_LADDER_TUPLE = "_ORG_ADDITIVE_TIERS"


def _ladder_tuple_binding_problems(tree: ast.Module,
                                   label: str) -> tuple[list[str], int, int]:
    """Every load of the shared ladder tuple — resolved, counted, COVERED.

    Two questions, ONE resolver (#14038 review, P2). The CALL SITES that hand the
    tuple to `_orgs_row_fail_soft` are counted here, so a row that found none
    cannot read green; the BINDING question is delegated to
    `_ladder_import_problems` — the same resolver the tier arms use — with
    `names` fixed to the shared tuple.

    ⛔ Delegating is the fix for a real false PASS. The previous revision answered
    the binding question itself: it built `per_function` with `ast.walk(node)`
    (which descends INTO nested `def` bodies) and collapsed every line onto its
    OUTERMOST enclosing function. So an `ImportFrom` sitting only inside a helper
    nested in the endpoint counted as bound for the endpoint, and the row stayed
    green over a `NameError` on the drift path — measured by the reviewer, whose
    mutant kept the alias at nesting depth 1 and nothing at function scope.
    `_ladder_import_problems` maps a load to its OWN scope and inherits only
    lexical ancestors, so the nested import binds the helper and nothing else.

    ⛔ AND THE THIRD NUMBER IS THE ONE THAT KEEPS THIS HONEST: `covered`, the
    loads the resolver actually EXAMINED. `sites` counts `ast.Call` nodes, which a
    narrowing bug in the resolver cannot affect — so a resolver that examined ZERO
    loads still reported every site and zero problems. That was the state measured
    at the merged head (`checked=0` on `hosted_api`, `sites=2`), because both
    ladders are built inside a `lambda`. The caller asserts `covered` against an
    INDEPENDENT `ast.walk` count of the same name, so "examined nothing" cannot
    read green.
    """
    sites = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_orgs_row_fail_soft"):
            continue
        names = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
        if _LADDER_TUPLE in names:
            sites += 1
    problems, covered = _ladder_import_problems(
        tree, label, names=frozenset({_LADDER_TUPLE}))
    return problems, sites, covered


def test_every_ladder_call_site_can_name_the_shared_tuple():
    """The shared ladder must be BOUND AT THE CALL SITE, not merely somewhere.

    `hosted_api` builds two ladders in two different endpoint functions; an
    `ImportFrom` anywhere in the module satisfies a module-wide check while the
    OTHER endpoint raises `NameError` on a schema-drift read — the one path that
    is already degraded.

    ⛔ THE SCOPES MUST BE SEPARATE, or this guard is vacuous — measured, not
    assumed (#14038 review cycle 2): the first revision built `module_bound` with
    `ast.walk(tree)`, which descends INTO function bodies, so a function-local
    import counted as module-level and the per-function arm could never fire. The
    second revision fixed the module arm and left the NESTED one: `ast.walk`
    still credited an import inside a nested `def` to the feature above them. Both
    arms are gone — the binding verdict is `_ladder_import_problems`', the same
    resolver the tier rows use, so there is one narrowing rule in this file and a
    row cannot be blind to a shape its sibling catches (#14038 review, P2).

    ⛔ AND IT COUNTS ITS OWN COVERAGE (#14038 review, P2 — measured live). The
    binding verdict is only worth what the resolver EXAMINED. At the merged head
    this row reported `sites=2, problems=[]` while the resolver examined ZERO
    loads, because both ladders sit inside a `lambda: _orgs_row_fail_soft(...)`
    and `_iter_own_scope` refused to descend into a lambda body. `sites` could not
    catch it — it counts `ast.Call` nodes, which a narrowing bug cannot
    perturb. So the row now compares the resolver's own `covered` count with an
    INDEPENDENT `ast.walk` count of the same name: if the two ever disagree, this
    row reds instead of silently guarding nothing. That assertion is what makes
    the lambda descent a fix rather than a comment.
    """
    problems: list[str] = []
    checked = 0
    covered_total = 0
    expected = 0
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        tree = _parse(path)
        found, sites, covered = _ladder_tuple_binding_problems(
            tree, str(path.relative_to(ROOT)))
        problems.extend(found)
        checked += sites
        covered_total += covered
        expected += sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == _LADDER_TUPLE
            and isinstance(node.ctx, ast.Load))
    assert checked >= 4, f"only {checked} shared-ladder call sites found — the scan broke"
    assert expected >= 4, (
        f"only {expected} loads of {_LADDER_TUPLE} in tortoise/ — either the shape "
        "this row guards has vanished or the counter broke; neither is a pass")
    assert covered_total == expected, (
        f"the resolver EXAMINED {covered_total} of the {expected} loads of "
        f"{_LADDER_TUPLE} in tortoise/ — a load it never looked at cannot be "
        "reported unbound, so this guard would be green over the very NameError "
        "it names (#14038 review: `checked=0` with `sites=2` while both ladders "
        "sat inside a lambda)")
    assert problems == [], "\n".join(problems)


def _scope_bindings(body: list[ast.stmt]) -> set[str]:
    """Names bound DIRECTLY in ONE scope's body.

    Nested `def`/`class` bodies are NOT descended into: their bindings belong to
    their own scope, and inheriting them is exactly the blindness this guard was
    rewritten to remove.
    """
    bound: set[str] = set()
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)      # its NAME is bound here; its body is not
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.ImportFrom):
            bound |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            bound |= {(alias.asname or alias.name).split(".")[0]
                      for alias in node.names}
        elif isinstance(node, ast.Assign):
            bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif (isinstance(node, (ast.AnnAssign, ast.AugAssign))
              and isinstance(node.target, ast.Name)):
            bound.add(node.target.id)
        stack.extend(ast.iter_child_nodes(node))
    return bound


def _iter_own_scope(body: list[ast.stmt]):
    """Yield ``(node, shadowed)`` for THIS scope's nodes.

    Nested `def`/`class` bodies are their OWN scope and are not descended into
    here. A `LAMBDA` body is NOT: see the branch below. ``shadowed`` carries the
    lambda parameters in force at the node, so a parameter that hides a watched
    name is reported instead of credited.
    """
    stack: list[tuple[ast.AST, frozenset[str]]] = [
        (node, frozenset()) for node in body]
    while stack:
        node, shadowed = stack.pop()
        yield node, shadowed
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        if isinstance(node, ast.Lambda):
            # A `LAMBDA` body IS the enclosing scope (#14038 review, P2). It holds
            # an expression, so it can bind nothing but its own parameters; a load
            # inside it resolves against those and then the ENCLOSING scope. Both
            # of `hosted_api`'s ladders are built that way —
            # `_cp_offload(lambda: _orgs_row_fail_soft(..., additive_tiers=list(
            # _ORG_ADDITIVE_TIERS)))` — so refusing to descend here made this
            # resolver check ZERO loads on `hosted_api` while its call-site counter
            # reported two: a green guard over the exact `NameError` it names.
            # The parameters ride along so a lambda that shadows a watched name
            # still reports honestly.
            stack.append((node.body, shadowed | _lambda_params(node)))
            continue
        for child in ast.iter_child_nodes(node):
            stack.append((child, shadowed))


def _lambda_params(node: ast.Lambda) -> frozenset[str]:
    """Every name a lambda binds — its parameters, and nothing else."""
    a = node.args
    names = {arg.arg for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    for opt in (a.vararg, a.kwarg):
        if opt is not None:
            names.add(opt.arg)
    return frozenset(names)


def _ladder_import_problems(tree: ast.Module, label: str, *,
                            names: frozenset[str] | None = None
                            ) -> tuple[list[str], int]:
    """Every load of each watched NAME, resolved against ITS OWN scope bindings.

    The pre-fix guard built one ``bound`` set per MODULE, so a module that
    imported a tier in two functions stayed green when one of the two imports was
    deleted — the defect its docstring names ("a NameError on a schema-drift
    read") is per-CALL-SITE, and a module-wide set cannot see it (#14038 review,
    mutation-attribution rule 2/3).

    ``names`` is what the caller watches; it defaults to the ADDITIVE TIER
    constants, and the shared-ladder row passes the ladder TUPLE instead
    (`_ladder_tuple_binding_problems`) so both rows resolve through this one
    function. One narrowing rule, not two (#14038 review, P2).
    """
    tiers = set(names) if names is not None else set(_additive_tiers())
    problems: list[str] = []
    checked = 0

    def visit(scope_name: str, body: list[ast.stmt], inherited: set[str]) -> None:
        """Check THIS scope, then recurse with its bindings INHERITED.

        ⛔ A nested `def` resolves a name against its own bindings AND every
        ENCLOSING function's, so a legal closure read must not red (#14038 review
        cycle 3 measured the over-block: `outer`'s import did not reach `nested`).
        That is also why `inherited` accumulates rather than replacing.
        """
        nonlocal checked
        bound = inherited | _scope_bindings(body)
        for node, shadowed in _iter_own_scope(body):
            if not (isinstance(node, ast.Name) and node.id in tiers
                    and isinstance(node.ctx, ast.Load)):
                continue
            checked += 1
            if node.id not in bound or node.id in shadowed:
                problems.append(
                    f"{label}:{node.lineno} {scope_name} uses {node.id} "
                    "without binding it")
        for node in _own_child_scopes(body):
            name = node.name if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else "<lambda>"
            visit(name, node.body, bound)

    visit("<module>", tree.body, set())
    return problems, checked


def _own_child_scopes(body: list[ast.stmt]) -> list[ast.AST]:
    """The def/class nodes that belong DIRECTLY to this scope (not nested deeper)."""
    found: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                found.append(child)          # its body is ITS scope, stop here
                continue
            walk(child)

    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append(node)
            continue
        walk(node)
    return found


def test_the_call_site_binding_guard_is_not_vacuous(tmp_path):
    """The mutation that proved the FIRST guard vacuous must red THIS one.

    #14038 review cycle 2 measured it: `module_bound` was built by walking the
    whole tree, function bodies included, so deleting one of `hosted_api`'s two
    `_ORG_ADDITIVE_TIERS` imports left every ladder guard green — over a real
    `NameError` on the schema-drift path. A guard nobody can prove can fail is a
    comment with a `def`, so this plants that exact shape in a throwaway module
    and requires the resolver to NAME the unbound use and only that one.

    ⛔ AND IT PLANTS THE SHAPE THE ROW WAS STILL BLIND TO (#14038 review, P2):
    the alias exists only inside a `def` nested in `outer`, at nesting depth 1,
    with nothing at `outer`'s own scope. `ast.walk` credited that import to
    `outer` and the row stayed green; the resolver binds it to the helper alone.
    The previous version of this fixture planted `_ORG_ADDITIVE_0015_TIER`
    while the measured mutation deleted `_ORG_ADDITIVE_TIERS` — a fixture can
    only pin the shape it actually writes, so it writes the ladder tuple now.
    """
    fake = tmp_path / "fake_module.py"
    fake.write_text(
        "from somewhere import OTHER\n"
        "\n"
        "\n"
        "def outer():\n"
        "    def helper():\n"
        "        from somewhere import _ORG_ADDITIVE_TIERS\n"
        "    return _orgs_row_fail_soft(select=1, additive_tiers=list(\n"
        "        _ORG_ADDITIVE_TIERS))\n"
        "\n"
        "\n"
        "def sibling():\n"
        "    from somewhere import _ORG_ADDITIVE_TIERS\n"
        "    return _orgs_row_fail_soft(select=1, additive_tiers=list(\n"
        "        _ORG_ADDITIVE_TIERS))\n")
    problems, sites, covered = _ladder_tuple_binding_problems(_parse(fake), str(fake))
    assert sites == 2, (
        f"both call sites must be counted — a scanner that saw one proves "
        f"nothing: {sites}")
    assert covered == 2, (
        "both loads must be EXAMINED — a resolver that looks at none of them "
        f"cannot report either as unbound: {covered}")
    assert len(problems) == 1, (
        "the sibling's import must leave the OTHER function's use unbound, and "
        f"a helper's import must not bind its parent; got {problems}")
    assert "outer" in problems[0], problems
    assert "sibling" not in problems[0], problems
    assert "helper" not in problems[0], (
        "the helper BINDS the name — it is `outer` that does not; the report "
        f"must name the scope that fails: {problems}")


def test_the_ladder_guard_checks_a_load_built_inside_a_lambda(tmp_path):
    """The PRODUCTION shape: both ladders are built inside a `lambda` (#14038 P2).

    Measured at the merged head: `checked=0` on `hosted_api` while the row reported
    `sites=2`. `_iter_own_scope` treated `ast.Lambda` as a foreign scope and never
    descended, so the resolver examined NONE of the two loads it exists to guard —
    and because `sites` counts `ast.Call` nodes, nothing noticed. The row was green
    over the exact `NameError` its docstring names.

    A lambda holds an EXPRESSION, so it binds nothing but its parameters: a load
    inside it resolves against the enclosing function. This fixture plants the
    breakage that shape can hide — the alias exists only in a nested `def`, so the
    call-site load inside the lambda is genuinely unbound and must be REPORTED.
    """
    fake = tmp_path / "lambda_module.py"
    fake.write_text(
        "from somewhere import OTHER\n"
        "\n"
        "\n"
        "def outer():\n"
        "    def helper():\n"
        "        from somewhere import _ORG_ADDITIVE_TIERS\n"
        "    return offload(lambda: _orgs_row_fail_soft(\n"
        "        select=1, additive_tiers=list(_ORG_ADDITIVE_TIERS)))\n"
        "\n"
        "\n"
        "def sound():\n"
        "    from somewhere import _ORG_ADDITIVE_TIERS\n"
        "    return offload(lambda: _orgs_row_fail_soft(\n"
        "        select=1, additive_tiers=list(_ORG_ADDITIVE_TIERS)))\n")
    problems, sites, covered = _ladder_tuple_binding_problems(_parse(fake), str(fake))
    assert sites == 2, (
        f"both ladder call sites are in the fixture: {sites}")
    assert covered == 2, (
        "BOTH loads sit inside a lambda and both must be examined — `covered` "
        f"is the number that catches the merged head's `checked=0`: {covered}")
    assert len(problems) == 1, (
        "exactly `outer` must be unbound: its import lives in a nested helper, "
        f"which binds nothing for the enclosing lambda; got {problems}")
    assert "outer" in problems[0], problems
    assert "sound" not in problems[0], (
        "`sound` imports at function scope and reads it inside its own lambda — "
        f"that is legal Python and must not be reported: {problems}")


def test_a_lambda_that_shadows_a_watched_name_is_reported(tmp_path):
    """Descending into lambdas must not credit a SHADOWING parameter.

    `lambda _ORG_ADDITIVE_TIERS: _ORG_ADDITIVE_TIERS` binds the name to the
    parameter; the enclosing function's import does not resolve that load. This is
    the arm that justifies tracking lambda parameters at all — without it the
    shadow set would be dead weight.
    """
    fake = tmp_path / "shadow_module.py"
    fake.write_text(
        "def holder():\n"
        "    from somewhere import _ORG_ADDITIVE_TIERS\n"
        "    return (lambda _ORG_ADDITIVE_TIERS: _ORG_ADDITIVE_TIERS)(1)\n")
    problems, checked = _ladder_import_problems(
        _parse(fake), str(fake), names=frozenset({_LADDER_TUPLE}))
    assert checked == 1, f"the shadowed load must still be examined: {checked}"
    assert len(problems) == 1, (
        f"a lambda parameter shadows the enclosing import; got {problems}")
    assert "holder" in problems[0], problems


def test_the_ladder_resolver_credits_an_enclosing_closure(tmp_path):
    """A legal closure read must NOT red — the mirror of the vacuity defect.

    #14038 review cycle 3 planted it: `outer()` imports the tier and a `def
    nested()` inside it reads it. Python resolves that read against the enclosing
    function's bindings, so a resolver that only unions MODULE + own-scope
    over-blocks correct code, and an over-block on a green PR is its own cost. The
    same fixture keeps a genuinely unbound load (`loser`→`deep`) so crediting the
    closure cannot be achieved by crediting everything.
    """
    planted = tmp_path / "closure_module.py"
    planted.write_text(
        "from somewhere import OTHER\n"
        "\n"
        "\n"
        "def outer():\n"
        "    from somewhere import _ORG_ADDITIVE_0015_TIER\n"
        "\n"
        "    def nested():\n"
        "        return _ORG_ADDITIVE_0015_TIER\n"
        "\n"
        "    return nested\n"
        "\n"
        "\n"
        "def loser():\n"
        "    def deep():\n"
        "        return _ORG_ADDITIVE_BILLING_TIER\n"
        "\n"
        "    return deep\n")
    problems, checked = _ladder_import_problems(_parse(planted), str(planted))
    assert checked == 2, f"expected both loads to be seen, got {checked}"
    assert len(problems) == 1, problems
    assert "deep" in problems[0] and "_ORG_ADDITIVE_BILLING_TIER" in problems[0], problems


def test_every_ladder_module_imports_the_tiers_it_names():
    """A tier named but not imported is a NameError on a schema-drift read.

    Resolved PER SCOPE, not per module: the import blocks live inside the
    endpoint functions, so a missing name is not caught at module import — only
    on the path that is already degraded, and that path is the drift ladder it is
    supposed to make survivable. A module may also DEFINE a tier
    (`supabase_control` owns the registry), so "bound" is
    imported-or-assigned *in the scope that loads it*.

    Complement of `test_every_ladder_call_site_can_name_the_shared_tuple`: that
    one asks whether the LADDER TUPLE is bound where it is passed, this one
    whether every TIER NAME is bound where it is loaded.
    """
    problems: list[str] = []
    checked = 0
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        found, seen = _ladder_import_problems(
            _parse(path), str(path.relative_to(ROOT)))
        problems.extend(found)
        checked += seen
    assert checked >= 5, (
        f"only {checked} tier uses found — the scan broke, it is not green")
    assert problems == [], "\n".join(problems)


def test_the_ladder_import_guard_resolves_per_call_site():
    """The guard's own non-vacuity: a module-wide set cannot catch this mutant.

    `b` loads the tier without binding it while `a` binds it. The pre-fix guard
    collected every `ImportFrom` in the module into ONE set, so `a`'s import made
    `b` look bound and the row stayed green — the review measured exactly that.
    """
    source = (
        "def a():\n"
        "    from tortoise.supabase_control import _ORG_ADDITIVE_0015_TIER\n"
        "    return list(_ORG_ADDITIVE_0015_TIER)\n"
        "def b():\n"
        "    return list(_ORG_ADDITIVE_0015_TIER)\n"
    )
    problems, checked = _ladder_import_problems(ast.parse(source), "synthetic")
    assert checked == 2, ("both loads must be examined — a scanner that saw only "
                          f"one proves nothing: {checked}")
    assert len(problems) == 1, problems
    assert " b uses _ORG_ADDITIVE_0015_TIER " in problems[0], problems


def test_no_artifact_quotes_the_config_reference_for_what_it_does_not_say():
    """⛔ #14038 review (P2): the module docstring quoted Fly's config reference
    as saying that a `rolling` replacement does not guarantee overlap. The page
    contains no such sentence — it contains no occurrence of the word
    "guarantee" at all:

        curl -sL https://fly.io/docs/reference/configuration/ | grep -c guarantee
        -> 0            (page measured 2026-11-21, 1,010,774 bytes)

    (The fabricated phrasing is deliberately NOT reproduced verbatim in this
    file, so this file cannot satisfy its own guard by quoting it: the needle is
    assembled from two pieces and this docstring stays clear of the join.)

    What the page does say is ``rolling``: *"One by one, each running Machine is
    taken down and replaced by a new release Machine."*, and the alongside-boot
    belongs to ``bluegreen``, of which it says *"cannot be used for Machines with
    attached volumes."*

    ⛔ THIS ROW GRADES THE NAMED INSTANCE, NOT THE CLASS. A quotation the cited
    source does not contain cannot be graded offline — the source is a web page
    — so the general class is read in review. What this row does is make THAT
    quotation un-shippable in the two artifacts that carried it (the module
    docstring and this file's docstring), and assert the measured replacement is
    present so a fix cannot be reverted into a removal.
    """
    needle = "generally " + "guarantee overlap"  # assembled: see the docstring
    module_src = (ROOT / "tortoise" / "capacity_notice.py").read_text(encoding="utf-8")
    self_src = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert needle not in module_src, (
        "the module docstring quotes a phrase Fly's config reference does not "
        "contain — quote what the page says, or do not quote it")
    assert needle not in self_src, (
        "this file repeats that fabricated quotation")
    assert "cannot be used for Machines with attached volumes" in module_src, (
        "the measured replacement wording must be what the docstring carries")


# ── 15. WHO gets mailed: the recipient chain (#14038 review, P1) ────────────
#
# ⛔ WHY THIS SECTION EXISTS. The first cut resolved the recipient from
# `organizations.email` and stopped. That column is populated ONLY by the lanes
# that pass `p_email` to `provision_team` (`/v1/register`, the Edge Function) —
# every SESSION-lane create passes none — so it is NULL exactly where a session
# member is the one who upgraded (`hosted_api._billing_owner_email` states this),
# and the notice was silently swallowed for the paying, capacity-holding
# population these two thresholds exist to tell. Reported as a plain `no-email`
# status, so nothing surfaced it as a fault. The rows below pin the chain, the
# rung ORDER, and the cost guard that keeps the third rung off the whole fleet.


def _account(**overrides) -> cn.Account:
    # ⛔ NO `row.update(overrides)` HERE: `update` is one of the ordinary-word
    # receivers the #13899 sweep counts as untyped residue, so a dict mutation in
    # a test helper would move `SWEEP_RESIDUE` for a reason that has nothing to do
    # with the SDK surface. A dict merge has no attribute access at all.
    fields = {"org_id": ORG_ID, "email": "dana@acme.example", "name": "Acme",
              "notice_pct": None} | overrides
    return cn.Account(**fields)


def test_the_recipient_chain_prefers_the_account_own_address():
    """Rung 1: the address the account itself recorded wins."""
    with patch("tortoise.supabase_control.owner_email",
               side_effect=AssertionError("rung 3 must not be paid for")):
        got = cn._notice_recipient(
            _account(email="dana@acme.example",
                     customer_email="billing@acme.example"), object())
    assert got == "dana@acme.example"


def test_the_recipient_chain_falls_to_the_stripe_billing_contact():
    """Rung 2: a session-lane org — `email` NULL, the 0012 twin set.

    This is the row that would have been a silent `no-email` before the fix, and
    it is the COMMON shape for the population: the account upgraded, the Stripe
    webhook mirrored `customer_email`, and no lane ever passed `p_email` at
    create. Rung 2 costs zero round trips because it is a column of the same
    `organizations` row `read_account` already read.
    """
    with patch("tortoise.supabase_control.owner_email",
               side_effect=AssertionError("rung 3 must not be paid for")):
        got = cn._notice_recipient(
            _account(email=None, customer_email="billing@acme.example"),
            object())
    assert got == "billing@acme.example"


def test_the_recipient_chain_falls_to_the_owner_go_true_address():
    """Rung 3: neither column answers, the ACTIVE OWNER's GoTrue address does."""
    with patch("tortoise.supabase_control.owner_email",
               lambda cp, org_id: "owner@acme.example"):
        got = cn._notice_recipient(
            _account(email=None, customer_email=None), object())
    assert got == "owner@acme.example"


def test_a_role_fallback_names_its_rung_and_never_the_address(caplog):
    """⛔ A FALLBACK IS A DELIVERY DECISION, SO IT IS ON THE RECORD.

    When the account's own `organizations.email` is empty the notice is mailed to
    a DIFFERENT mailbox — the mirrored Stripe billing contact, or the owner's
    GoTrue address. Recording only the `sent` counter leaves an operator unable to
    answer "which address was told?" after a bounce, and a silent role fallback is
    the one part of the chain nobody can audit post hoc. The record names the RUNG
    and not the address: the value is customer PII and the log is durable.

    Non-vacuous: before this record existed the chain emitted nothing at all on a
    rung-2 resolve, so the first assertion had no line to read.
    """
    with patch("tortoise.supabase_control.owner_email",
               side_effect=AssertionError("rung 3 must not be paid for")), \
            caplog.at_level(logging.INFO, logger="tortoise.capacity_notice"):
        got = cn._notice_recipient(
            _account(email=None, customer_email="billing@acme.example"),
            object())
    assert got == "billing@acme.example"
    lines = [r.getMessage() for r in caplog.records]
    assert any("SECOND rung" in line and ORG_ID in line for line in lines), lines
    assert not any("billing@acme.example" in line for line in lines), (
        "the rung is recorded, the ADDRESS is not — a log is durable and the "
        f"value is customer PII; lines={lines}")


def test_the_owner_rung_fallback_is_recorded_too(caplog):
    """The THIRD rung is the widest fallback: the notice is now addressed off the
    account's own row entirely, so it is the one that most needs a record.
    """
    with patch("tortoise.supabase_control.owner_email",
               lambda cp, org_id: "owner@acme.example"), \
            caplog.at_level(logging.INFO, logger="tortoise.capacity_notice"):
        got = cn._notice_recipient(
            _account(email=None, customer_email=None), object())
    assert got == "owner@acme.example"
    lines = [r.getMessage() for r in caplog.records]
    assert any("THIRD rung" in line and ORG_ID in line for line in lines), lines
    assert not any("owner@acme.example" in line for line in lines), lines


def test_the_recipient_chain_survives_a_raising_owner_lookup():
    """⛔ Fail-soft on the RUNG, never on the account.

    `supabase_control.owner_email` swallows the GoTrue admin transport error but
    its `owner_user_id` control-plane read sits OUTSIDE that guard
    (`hosted_api._billing_owner_email` says so and catches for the same reason),
    so an exception here must degrade the rung to None rather than let a broken
    admin lookup cost the account the one notice it can ever get.
    """
    with patch("tortoise.supabase_control.owner_email",
               side_effect=RuntimeError("control-plane read escaped")):
        got = cn._notice_recipient(
            _account(email=None, customer_email=None), object())
    assert got is None


def test_a_blank_column_is_not_an_address():
    """Whitespace is not an address, and it must fall THROUGH, not be mailed."""
    with patch("tortoise.supabase_control.owner_email",
               lambda cp, org_id: "   "):
        got = cn._notice_recipient(
            _account(email="   ", customer_email=""), object())
    assert got is None


def test_a_session_lane_account_without_the_column_address_is_still_notified():
    """END TO END: the notice reaches the account the fix exists for.

    Patches the ONE real send path, so the assertion is about the address the
    mailer would actually have been handed — not about an injected `send`, which
    never sees the recipient.
    """
    cp = _fake_cp(email=None, customer_email="billing@acme.example")
    seen: list[str] = []

    async def _fake_send(address, subject, html, text, **_kwargs):
        seen.append(address)
        return {"status": "sent", "message_id": "msg_rung2"}

    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.email_notify.send_notice_email", _fake_send):
        out = _pass(cp=cp, enumerate_orgs=lambda limit=None: [
            _org_row(email=None, customer_email="billing@acme.example")])
    assert out["statuses"]["sent"] == 1
    assert seen == ["billing@acme.example"]
    assert cp.tables["organizations"][0]["storage_notice_pct"] == 80


def _send_with_rung1(stale: str):
    """One pass for a row whose rung-1 value is *stale*; returns (summary, sent-to).

    A helper rather than a loop-local closure: the send fake and the enumerator
    both read ``stale``, and binding a loop variable inside a closure is exactly
    the bug B023 refuses (the last iteration's value would be the one every call
    saw).
    """
    cp = _fake_cp(email=stale, customer_email="billing@acme.example")
    seen: list[str] = []

    async def _fake_send(address, subject, html, text, **_kwargs):
        seen.append(address)
        return {"status": "sent", "message_id": "msg_rung2"}

    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.email_notify.send_notice_email", _fake_send):
        out = _pass(cp=cp, enumerate_orgs=lambda limit=None: [
            _org_row(email=stale, customer_email="billing@acme.example")])
    return out, seen, cp


def test_a_stale_non_address_on_the_first_rung_does_not_shadow_a_deliverable_one():
    """A truthy-but-not-an-address `email` falls through to `customer_email`.

    ⛔ THE RUNG ORDER ALONE DOES NOT DELIVER (#14038 review, mutation pass, P2).
    `organizations.email` is stale-prone and holds non-addresses in this codebase
    (`"api"`, a Supabase user UUID — `hosted_api._billing_email_like` documents
    both), so a truthiness test on rung 1 shadows a REAL address on rung 2: the
    send fails, the mark is never stamped, and every later pass retries the same
    undeliverable string. This row is the one that reds when the shape gate is
    dropped — the plain "no address" row below cannot see the difference.
    """
    for stale in ("api", "0b6c3a4e-6d63-4f3e-9b3e-3f0d5f7a1c22", "  "):
        out, seen, cp = _send_with_rung1(stale)
        assert out["statuses"]["sent"] == 1, (
            f"a stale rung-1 value {stale!r} shadowed the deliverable rung-2 "
            f"address: {out['statuses']}")
        assert seen == ["billing@acme.example"], (
            f"the mailer was handed {seen!r} for a rung-1 value {stale!r}")
        assert cp.tables["organizations"][0]["storage_notice_pct"] == 80


@pytest.mark.parametrize("junk", ["api", "8f14e45f-ceea-467a-9f0c-2a1b2c3d4e5f"])
def test_a_non_address_on_the_owner_rung_is_no_email_not_a_failed_send(junk):
    """⛔ THE GATE APPLIES TO RUNG 3 TOO, where a wrong answer is PERMANENT.

    `_deliverable_address` gates all three rungs, but only rungs 1-2 are exercised
    end to end above. Rung 3 is the one that matters most: the owner lookup can
    answer a non-address (the same writers that put `"api"`/UUIDs into
    `organizations.email` write them here), the ledger stamps ONCE after the
    provider accepts, and a refused send is never retried — so a non-address
    reaching the mailer is the notice that is silently never delivered. It must
    read as `no-email` (the same state as an account with no address at all), not
    as a `failed` send against an undeliverable string.

    Non-vacuous: with the gate dropped on this rung only, the row still resolves a
    truthy address and the pass reports `failed`, so both assertions red.
    """
    cp = _fake_cp(email=None, customer_email=None)
    send = _Sender()
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.supabase_control.owner_email", lambda cp, org_id: junk):
        out = _pass(cp=cp, send=send, enumerate_orgs=lambda limit=None: [
            _org_row(email=None, customer_email=None)])
    assert out["statuses"] == {"no-email": 1}, out
    assert send.calls == [], "a non-address must never reach the provider"


def test_an_account_with_no_address_on_any_rung_is_reported_by_name():
    """The residual, pinned: nothing mailed, and the status says `no-email`."""
    cp = _fake_cp(email=None, customer_email=None)
    send = _Sender()
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.supabase_control.owner_email",
               lambda cp, org_id: None):
        out = _pass(cp=cp, send=send, enumerate_orgs=lambda limit=None: [
            _org_row(email=None, customer_email=None)])
    assert out["statuses"]["no-email"] == 1 and send.calls == []
    assert cp.tables["organizations"][0]["storage_notice_pct"] is None


def test_the_owner_rung_is_not_consulted_when_the_notice_is_not_due():
    """⛔ THE COST GUARD, which is why the resolution moved below `evaluate`.

    Rung 3 is two network calls per account (a membership read plus a GoTrue
    admin GET). Resolving it in `read_account` would pay that on the WHOLE fleet
    every interval to answer a question most accounts never ask. Here the gauge
    says the account is below both thresholds, so the chain must not be entered
    at all — asserted by making the rung itself raise.
    """
    cp = _fake_cp(email=None, customer_email=None)
    send = _Sender()
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(10.0)), \
         patch("tortoise.supabase_control.owner_email",
               side_effect=AssertionError("owner rung entered for a cheap exit")):
        out = _pass(cp=cp, send=send, enumerate_orgs=lambda limit=None: [
            _org_row(email=None, customer_email=None)])
    assert out["statuses"]["below-threshold"] == 1 and send.calls == []


def test_a_schema_without_the_billing_contact_column_still_notifies():
    """`customer_email` is 0012 — DEGRADE the rung, never the pass (#1096).

    A schema older than the billing tier rejects the projection; `read_account`
    retries without its drift-soft columns, so the address falls to rung 1 and
    the account is still notified. Refusing the pass over a FALLBACK column's
    absence would be the same defect in the other direction.
    """
    cp = _ColumnBlindCP(missing="customer_email")
    send = _Sender()
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)):
        out = _pass(cp=cp, send=send, enumerate_orgs=lambda limit=None: [_org_row()])
    assert out["statuses"]["sent"] == 1
    assert any("customer_email" not in select for select in cp.calls), (
        "the retry must actually drop the rejected column")


def test_the_owner_rung_is_paid_once_per_DUE_account_not_once_per_send():
    """⛔ THE COST BOUND, MEASURED — because the published one was false.

    `_notice_recipient`'s docstring said rung 3 is reached "at most ``max_sends``
    times per pass". It is not: `sweep_pass` stops calling `notify_account` once
    `sent` reaches the cap, and a due account that does NOT send (no address on
    any rung, a budget-skipped send, a provider failure) never increments `sent`,
    so the loop never `continue`s past it for that reason and the next account is
    processed — paying rung 3 again. Three due addressless accounts at
    `max_sends=1` pay it three times.

    This grades the CLAIM as well as the measurement. The sentence is prose, so no
    other arm in this suite can see it rot back: reverting the docstring to the
    `max_sends` bound reds HERE even though the code is unchanged.
    """
    calls: list[str] = []

    def _owner(cp, org_id):
        calls.append(org_id)
        return None

    rows = [_org_row(id=f"team-rung3-{n}", email=None, customer_email=None)
            for n in range(3)]
    cp = FakeControlPlane({"organizations": rows})
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.supabase_control.owner_email", _owner):
        out = _pass(cp=cp, send=_Sender(), max_sends=1,
                    enumerate_orgs=lambda limit=None: rows)
    assert out["sent"] == 0, "nothing could send, so the cap never bit — the case under test"
    assert len(calls) == 3, (
        "the rung is paid once per DUE ACCOUNT, not once per send: measured "
        f"{len(calls)} invocations at max_sends=1")
    doc = cn._notice_recipient.__doc__ or ""
    assert "at most ``max_sends`` times" not in doc, (
        "the docstring re-asserts a bound `sweep_pass` does not implement — the "
        "send cap bounds SPEND, not this read")
    assert "DUE ACCOUNT" in doc, (
        "the docstring must publish the true bound it does have: one call pair "
        "per DUE account per pass")


def test_a_failed_owner_lookup_is_logged_and_not_reported_as_absent(caplog):
    """⛔ TWO STATES, ONE `None`: absent vs unreadable (#14038 fix lane).

    `_owner_email` degrades to ``None`` both when the owner has no address and
    when the lookup itself failed. The fail-soft is right; SILENCE is not — an
    operator reading a fleet-wide `no-email` count cannot tell a GoTrue outage
    from a population that genuinely has no address. So the degrade logs its
    cause, and the `no-email` line no longer asserts the rung was empty.
    """
    cp = _fake_cp(email=None, customer_email=None)
    send = _Sender()
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.supabase_control.owner_email",
               side_effect=RuntimeError("control-plane read escaped")), \
         caplog.at_level(logging.INFO, logger="tortoise.capacity_notice"):
        out = _pass(cp=cp, send=send, enumerate_orgs=lambda limit=None: [
            _org_row(email=None, customer_email=None)])

    # The fail-soft survives the logging: the rung degrades, the account is not
    # mailed, and nothing raises.
    assert out["statuses"]["no-email"] == 1 and send.calls == []
    lines = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in lines and ORG_ID in lines, (
        "the rung-3 degrade is silent: a GoTrue failure is indistinguishable "
        "from an account that has no address (causes: "
        f"{[type(r.msg).__name__ for r in caplog.records]})")
    assert "are all empty" not in lines, (
        "the no-address line asserts the owner rung was EMPTY, which this run "
        "cannot know — it was unreadable")


def test_a_SILENTLY_swallowed_owner_lookup_is_reported_as_logged_NOWHERE(caplog):
    """⛔ The rung's fourth state writes NO record, and the line must say so.

    `supabase_control.owner_email` answers `None` with no log record on four paths — no active
    owner, an unset `SUPABASE_URL`/service-role key, an `httpx` transport error, and any non-200
    (a rotated key's 401, a deleted user's 404) — because its contract is "never raises". Only
    an exception that ESCAPES it reaches `_owner_email`'s `logger.warning`, and that is the arm
    the sibling test above covers.

    So a fleet-wide silence caused by a GoTrue outage leaves NOTHING above to read, and copy
    that tells the operator the lookup failed "and logged a warning above" sends them looking
    for a record that was never written — the same mis-diagnosis this line exists to prevent,
    one level down. This arm is the one the raising-arm test cannot see: it patches the lookup
    to answer `None` the way a swallowed transport error does, with no exception and no record.
    """
    cp = _fake_cp(email=None, customer_email=None)
    send = _Sender()
    with patch("tortoise.quota.resolve_org_limits", lambda org_id: _limits(102)), \
         patch("tortoise.allowance_gate.read_dimension",
               lambda dimension, org_id: _Reading(90.0)), \
         patch("tortoise.supabase_control.owner_email", return_value=None), \
         caplog.at_level(logging.INFO, logger="tortoise.capacity_notice"):
        out = _pass(cp=cp, send=send, enumerate_orgs=lambda limit=None: [
            _org_row(email=None, customer_email=None)])

    assert out["statuses"]["no-email"] == 1 and send.calls == []
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [], (
        "this fixture models the SILENT swallow; a warning here means the case is not "
        f"testing what it claims: {[r.getMessage() for r in warnings]}")
    line = " ".join(r.getMessage() for r in caplog.records)
    # ⛔ THE DISCRIMINATOR IS THE SILENT CLASS ITSELF, never a bare "logged" test: the copy
    # legitimately says the RAISING arm logged a warning, so a phrase test on "logged" reds
    # against the CORRECT text (measured — the first cut of this test did exactly that). The
    # regression is the wording that named this state ONLY as "the lookup FAILED and logged a
    # warning above", which cannot produce "LOGGED NOWHERE" nor name any swallowed cause.
    assert "LOGGED NOWHERE" in line, (
        "the no-email line does not tell the operator this state leaves no record, so a "
        f"GoTrue outage still reads as a population with no address. Emitted: {line!r}")
    for cause in ("transport error", "non-200", "missing config"):
        assert cause in line, (
            f"the silent-lookup arm does not name `{cause}`, one of the paths "
            f"`supabase_control.owner_email` swallows with no record. Emitted: {line!r}")
