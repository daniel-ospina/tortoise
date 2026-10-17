"""#2352 — the evidence-currency predicate, as a LIVE / STALE / DECAYED fixture.

**What #2352 asked for, and what is already there.** The predicate itself (a
claim whose supporting source moved on reads ``stale`` with that source named,
never silently ``current``) shipped through #5199 / #5256 / #5581: the
``extractedFrom`` read-version anchor is compared against the Source's current
``contentHash`` (ONTOLOGY §4.6 — *"a read, never a stored flag"*) and the
Point-level AGGREGATE rides the existing search row's flag-gated ``provenance``
block. That half is covered by ``test_source_currency_5581.py`` (the aggregate
+ disclosure) and ``test_source_version_read_wiring_5199.py`` (the reader).

**What this file adds is the issue's own Target**: *"currency E2E green (live /
stale / decayed three-way fixture)"*. No test in the repo asserted the THREE
signals TOGETHER, and no test file mentions both ``currency`` and ``decay`` —
so the indicator the fixture exists to prove was unguarded:

  (2) *"Stale and decayed are distinct signals — a freshly-dated claim from a
  dead source is stale, not decayed; a live source with an old date decays,
  not stale."*

The two axes are genuinely orthogonal in the model (§10 recency modulation is
the automatic, time-based half; currency is the explicit, source-lifecycle
half), but nothing pinned it. A future change that folded decay into the
currency verdict (or vice versa) — the natural, wrong simplification — would
have turned the two into one signal with every test still green.

Each test below names the input that makes it FAIL. The fixture drives the
STANDARD read path (``TortoiseSDK.tortoise_fts_query``, the method behind
``tortoise_search``) plus the source-reliability read (``get_source_reliability``
— where the decay component is exposed), so it also pins that the two signals
are reachable from the surfaces the issue names. ``+0`` MCP tools, no stored
field, no EP math change: this file only asserts.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import FYI_REASON_SOURCE_DRIFT

#: The two tiers of freshness this fixture contrasts. A source with NO
#: ``sourceDate`` decays on ``ingestedAt`` (the documented arrival proxy), which
#: is the ingest clock — so its decay is ~1.0 whatever wall-clock the suite runs
#: at. The aged source is pinned to a fixed past date, so its decay only ever
#: FALLS as the suite ages (a lower bound assertion stays honest).
FRESH_DECAY_MIN = 0.99
DECAYED_DECAY_MAX = 0.90

LIVE_URL = "https://ex.com/currency/live"
DEAD_URL = "https://ex.com/currency/dead"
AGED_URL = "https://ex.com/currency/aged"
AGED_DATE = "2015-01-01T00:00:00Z"


def _scan(sdk):
    """The standard read surface: the SDK method behind the MCP ``tortoise_search``.

    Full-scan form (``query=None``) so every fixture claim is returned
    deterministically — the same shape ``test_source_currency_5581.py`` uses.
    """
    return sdk.tortoise_fts_query(query=None, kind="statement", limit=50)


def _hit(rows, point_id):
    for row in rows:
        if row["id"] == point_id:
            return row
    raise AssertionError(f"point {point_id} absent from the read result")


def _decay(sdk, url):
    """The decay component of the source's derived reliability (§10 half)."""
    rel = sdk.get_source_reliability(url)
    assert rel["components"].get("tier") == "T1", rel
    return rel["components"]["decay"]


@pytest.fixture(scope="module")
def fixture_sdk(tmp_path_factory):
    """Three claims, one per source — the three-way fixture, built ONCE.

    Module-scoped on purpose: the fixture is read-only after ``_build`` (the
    hash bump happens inside it), so four independent SDKs would buy no
    isolation and would rebuild the graph four times — the first ``create_point``
    already pays the one cold embedder load this lane's ``TORTOISE_EMBEDDER_WARMUP=0``
    makes synchronous.

    * ``live``    — T1 source, hash untouched since extraction.
    * ``stale``   — T1 source, FRESHLY dated (no ``sourceDate`` ⇒ ingest clock),
      hash moved to ``h2`` after the claim was read from ``h1``.
    * ``decayed`` — T1 source, hash untouched, dated 2015 (evidence is old).

    Yields ``(sdk, {name: point_id})``.
    """
    sdk = TortoiseSDK(str(tmp_path_factory.mktemp("currency2352") / "f.db"))
    try:
        yield sdk, _build(sdk)
    finally:
        sdk.close()


def _build(sdk):
    """Mint the three sources + claims, then move the DEAD source's version."""
    g = sdk._get_proj().g
    sdk.create_source(LIVE_URL, "T1", contentHash="L1")
    sdk.create_source(DEAD_URL, "T1", contentHash="D1")
    sdk.create_source(AGED_URL, "T1", contentHash="A1", sourceDate=AGED_DATE)
    points = {
        "live": sdk.create_point("statement", "currency fixture live claim",
                                 extractedFrom=LIVE_URL)["id"],
        "stale": sdk.create_point("statement", "currency fixture stale claim",
                                  extractedFrom=DEAD_URL)["id"],
        "decayed": sdk.create_point("statement", "currency fixture aged claim",
                                    extractedFrom=AGED_URL)["id"],
    }
    # The source MOVES ON after the claim was read from it. §4.6: "a re-fetched
    # source whose content differs is a new version" — the same in-place bump
    # `_upsert_source` makes.
    g.query("MATCH (s:Source {url:$url}) SET s.contentHash='D2'",
            params={"url": DEAD_URL})
    return points


# ── the three-way fixture ─────────────────────────────────────────────────────

def test_live_stale_and_decayed_are_three_distinct_signals(fixture_sdk,
                                                           monkeypatch):
    """The issue's Target, as one assertion set.

    FAILS IF the currency verdict and the decay component are ever conflated —
    specifically if the freshly-dated DEAD source reads anything but ``stale``,
    if the old-but-LIVE source reads anything but ``current``, or if the two
    axes stop being pairwise distinct across the fixture.
    """
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    sdk, p = fixture_sdk
    rows = _scan(sdk)

    def verdict(name):
        prov = _hit(rows, p[name]).get("provenance") or {}
        return prov.get("currency", ""), _decay(
            sdk, {"live": LIVE_URL, "stale": DEAD_URL,
                  "decayed": AGED_URL}[name])

    live = verdict("live")
    stale = verdict("stale")
    decayed = verdict("decayed")

    # 1. A live source, freshly read → current, no decay worth the name.
    assert live[0] == "current", live
    assert live[1] >= FRESH_DECAY_MIN, live

    # 2. INDICATOR 2a: a FRESHLY-DATED claim from a DEAD source is STALE,
    #    not decayed. The evidence is new; it is the SOURCE that moved.
    assert stale[0] == "stale", stale
    assert stale[1] >= FRESH_DECAY_MIN, (
        "a stale claim from a freshly-dated source must NOT also read as "
        f"decayed — the two signals are orthogonal; got decay {stale[1]}")

    # 3. INDICATOR 2b: a LIVE source with an OLD date decays, NOT stale.
    assert decayed[0] == "current", (
        "age is not drift: an old-but-unchanged source is current, "
        f"got {decayed[0]}")
    assert decayed[1] < DECAYED_DECAY_MAX, decayed

    # 4. The three verdict PAIRS are pairwise distinct — the fixture is a
    #    three-way, not two states wearing three labels.
    assert len({live, stale, decayed}) == 3, (live, stale, decayed)


def test_a_stale_claim_names_the_source_that_moved(fixture_sdk, monkeypatch):
    """INDICATOR 1: a claim supported ONLY by a moved source reads ``stale``
    with that source named — never silently ``current``.

    FAILS IF the ``fyi`` disclosure is a bare flag (no source), or if the
    recorded/current versions are not carried, or if the flag alone is mistaken
    for current.
    """
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    sdk, p = fixture_sdk
    rows = _scan(sdk)
    prov = _hit(rows, p["stale"]).get("provenance") or {}
    assert prov.get("currency") == "stale", prov
    fyi = prov.get("fyi")
    assert fyi and fyi.get("reason") == FYI_REASON_SOURCE_DRIFT, prov
    # The claim's ONLY link is the one that moved — so the disclosure names
    # exactly that source, with both operands of the comparison.
    assert [s["source"] for s in fyi["sources"]] == [DEAD_URL], fyi
    assert fyi["sources"][0]["recorded"] == "D1", fyi
    assert fyi["sources"][0]["current"] == "D2", fyi

    # ... while the two live claims disclose nothing: an empty FYI would make
    # "no drift" indistinguishable from "drift we failed to name".
    assert "fyi" not in (_hit(rows, p["live"]).get("provenance") or {})
    assert "fyi" not in (_hit(rows, p["decayed"]).get("provenance") or {})


def test_a_stale_claim_is_annotated_not_hidden(fixture_sdk, monkeypatch):
    """INDICATOR 3: stale claims stay ON the read path, annotated.

    Retraction (``retract_point``) is the only silencing mechanism; staleness
    must not remove the claim from the result set — "a stale belief is strictly
    better than no belief" (§4.6).

    FAILS IF the stale claim is withheld from the result set (the enforcement
    half is a DIFFERENT ruling — #5038/#5581's withhold-when-a-successor-exists
    — and must not leak into the plain read as a silent drop).
    """
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    sdk, p = fixture_sdk
    stale_row = _hit(_scan(sdk), p["stale"])  # raises if the claim was hidden
    assert (stale_row.get("provenance") or {}).get("currency") == "stale"
    # The annotation is additive: the fact itself is still returned.
    assert stale_row["content"] == "currency fixture stale claim", stale_row
    assert stale_row.get("status") == "live", stale_row


def test_the_three_way_fixture_does_not_need_the_provenance_flag(fixture_sdk,
                                                                 monkeypatch):
    """The DECAY half is not flag-gated — only the currency REPORT is.

    ``TORTOISE_SEARCH_PROVENANCE`` (default OFF, #3837) gates the additive
    ``provenance`` block on the search row; ``get_source_reliability`` is
    unconditional. Pin the boundary so "the two signals are distinct" is not
    accidentally true only when the flag is on.

    FAILS IF the decay read becomes flag-dependent, or if the currency report
    leaks onto the row with the flag OFF (a default-off response field is the
    #3986 carve-out, and turning it on is a surface change).
    """
    monkeypatch.delenv("TORTOISE_SEARCH_PROVENANCE", raising=False)
    sdk, p = fixture_sdk
    decayed_row = _hit(_scan(sdk), p["decayed"])
    assert "provenance" not in decayed_row, (
        "flag OFF must not add a response field")
    assert _decay(sdk, AGED_URL) < DECAYED_DECAY_MAX, (
        "the decay component is a plain source read, never flag-gated")
    assert _decay(sdk, DEAD_URL) >= FRESH_DECAY_MIN
