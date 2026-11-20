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
import functools
import pathlib
import re
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests.fake_control_plane import FakeControlPlane
from tortoise import capacity_notice as cn
from tortoise import email_notify
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
    # The plain-text half and the subject are pure copy.
    for rendered in (copy.subject, copy.text):
        stripped = rendered.replace(f"{threshold}%", "")
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
    assert copy.text.rstrip().endswith("[ How Tortoise stores and searches "
                                      "your data → ]")


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
    (101.9, 102, 99),     # 99.9 % is NOT 100 % — the cap-at-99 rule
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

    def test_a_missing_ledger_column_still_refuses_to_read_unset(self):
        """The other half: dropping the soft column does not rescue a missing
        ledger — it re-raises, so the account reports `notice-mark-unavailable`
        instead of "never notified" and re-sending every pass.
        """
        cp = _ColumnBlindCP(missing="storage_notice_pct")
        with pytest.raises(RuntimeError, match="storage_notice_pct"):
            cn.read_account(cp, ORG_ID)
        assert len(cp.calls) == 2, "one retry, then fail closed"

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
    """Parse each module ONCE — four guards in this file walk all of `tortoise/`."""
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


def _enclosing_functions(path: pathlib.Path) -> dict[int, int]:
    """Map every node's line to the line its ENCLOSING function starts at.

    A per-call-site binding question ("is `_ORG_ADDITIVE_TIERS` imported where
    this ladder is built?") cannot be answered module-wide: `hosted_api`
    imports the same name in two different endpoint functions, so a module-wide
    "bound" set stays green when ONE of the two imports is deleted. Round 4 of
    this review measured exactly that hole in
    `test_every_ladder_module_imports_the_tiers_it_names`.
    """
    tree = _parse(path)
    owner: dict[int, int] = {}

    def walk(node: ast.AST, current: int) -> None:
        for child in ast.iter_child_nodes(node):
            start = child.lineno if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            for line in range(getattr(child, "lineno", 1),
                              getattr(child, "end_lineno", getattr(child, "lineno", 1)) + 1):
                owner.setdefault(line, start)
            walk(child, start)

    walk(tree, 0)
    return owner


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
    reviewer deleted `_ORG_ADDITIVE_TIERS` from `_session_user_org`'s import block
    and every ladder guard stayed green over a real `NameError`. Module scope is
    now taken from `_enclosing_functions` (a node whose enclosing function is 0 is
    module level, so an import nested in `try:`/`if TYPE_CHECKING:` still counts),
    and `_ladder_import_problems` is the general resolver this row is one case of.
    """
    problems: list[str] = []
    checked = 0
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        tree = _parse(path)
        owners = _enclosing_functions(path)
        module_bound = {alias.name
                        for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom)
                        for alias in node.names
                        if owners.get(node.lineno, 0) == 0}
        module_bound |= {t.id
                         for node in tree.body
                         if isinstance(node, (ast.Assign, ast.AnnAssign))
                         for t in (node.targets if isinstance(node, ast.Assign)
                                   else [node.target])
                         if isinstance(t, ast.Name)}
        per_function: dict[int, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                per_function[node.lineno] = {
                    alias.name
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.ImportFrom)
                    for alias in sub.names}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_orgs_row_fail_soft"):
                continue
            names = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
            if "_ORG_ADDITIVE_TIERS" not in names:
                continue
            checked += 1
            fn_line = owners.get(node.lineno, 0)
            bound = module_bound | per_function.get(fn_line, set())
            if "_ORG_ADDITIVE_TIERS" not in bound:
                problems.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} builds the ladder from "
                    f"_ORG_ADDITIVE_TIERS but the enclosing function (line "
                    f"{fn_line or 'module'}) never imports it — a NameError on the "
                    "drift path, where nothing else works either")
    assert checked >= 4, f"only {checked} shared-ladder call sites found — the scan broke"
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
    """Yield the nodes belonging to THIS scope (nested scopes not descended)."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _ladder_import_problems(tree: ast.Module,
                            label: str) -> tuple[list[str], int]:
    """Every tier Name load, resolved against ITS OWN scope's bindings.

    The pre-fix guard built one ``bound`` set per MODULE, so a module that
    imported a tier in two functions stayed green when one of the two imports was
    deleted — the defect its docstring names ("a NameError on a schema-drift
    read") is per-CALL-SITE, and a module-wide set cannot see it (#14038 review,
    mutation-attribution rule 2/3).
    """
    tiers = set(_additive_tiers())
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
        for node in _iter_own_scope(body):
            if not (isinstance(node, ast.Name) and node.id in tiers
                    and isinstance(node.ctx, ast.Load)):
                continue
            checked += 1
            if node.id not in bound:
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
    """
    fake = tmp_path / "fake_module.py"
    fake.write_text(
        "from somewhere import OTHER\n"
        "\n"
        "\n"
        "def outer():\n"
        "    return _orgs_row_fail_soft(select=1, additive_tiers=list(\n"
        "        _ORG_ADDITIVE_0015_TIER))\n"
        "\n"
        "\n"
        "def sibling():\n"
        "    from somewhere import _ORG_ADDITIVE_0015_TIER\n"
        "    return _orgs_row_fail_soft(select=1, additive_tiers=list(\n"
        "        _ORG_ADDITIVE_0015_TIER))\n")
    problems, checked = _ladder_import_problems(_parse(fake), str(fake))
    assert checked >= 2, f"only {checked} tier loads found — the fixture broke"
    assert len(problems) == 1, (
        "the sibling's import must leave the OTHER function's use unbound; "
        f"got {problems}")
    assert "outer" in problems[0], problems
    assert "sibling" not in problems[0], problems


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
