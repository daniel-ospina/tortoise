"""#5581 — the read path: report the Point's §4.6 currency, and disclose a stale
fact CARRYING ITS SOURCE.

The **read half of #5038**. The owner ruled (2026-09-26T11:17:30Z, `#5038`
comment `5845802608`): *"We should not return an out-of-date fact when we have a
newer one"* — the out-of-date fact is **withheld as the answer** and **disclosed
as an FYI carrying its source** (*"let the user know that (newer fact but no
source, and older fact from source X) … so I can disambiguate"*). Reporting rides
the **existing** read surface and no `status` field is stored (§4.6: *"a read,
never a stored flag"*).

What this file pins, and why each shape is a pin rather than an incidental:

* the **AGGREGATE** is §4.6's, not one link's. `extractedFrom` is many→many
  (§3.3), so *"stale if ANY link is behind, current only when every link is"*
  — a single link's verdict reported for the Point is the false-current
  `tests/test_source_version_read_wiring_5199.py::test_search_hit_makes_no_version_claim`
  reserves the hit against. That file's module docstring sends the Point-level
  verdict HERE ("It goes to the read-path item on #5038 with #5256's arrival");
  #5256 is merged (`git grep sourceVersion -- '*.py'` is no longer 0 matches).
* **zero links is `unknown`, never `current`.** "current only when every link
  is" is VACUOUSLY true of an empty link set, which would report an orphan Point
  — no recorded read at all — as verified-fresh. Refuted by §4.6's own intent.
* the **disclosure carries its source**. A bare `stale` flag beside the fact is
  the field's practice the plan's `OVERRIDES:` line rejects (a visible flag is
  measurably not acted on: `arXiv 2609.08258`, `arXiv 2605.06527`).
* **nothing is stored.** A `currency`/`linkCurrency` property on the edge or the
  node would drift from the pair it is derived from and need a backfill on every
  source edit.

Every test below names the input that makes it FAIL.

Scope note (read before "completing" this file): this ships the derivation and
the REPORTING. The ruling's enforcement half — a read actually *withholding* the
stale fact from the result set — composes `answer_eligible` into the existing
participation gate (`search_engine.py`'s `has_ep`) and is NOT delivered here;
see the issue's PR.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.source_currency import (
    answer_eligible,
    point_currency,
    read_links,
    sourced_fyi,
)
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import (
    FYI_REASON_SOURCE_DRIFT,
    aggregate_currency,
    currency_status,
    read_currency_links,
)


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


def _bump(graph, url: str, version: str) -> None:
    """Simulate a re-fetch of ``url`` landing a new version.

    A version bump is exactly what makes a previously-current link read
    ``stale`` — the Source node keeps ONE version (§4.6: *"the graph shows the
    current version"*), so this is the same in-place write `_upsert_source`
    makes.
    """
    graph.query("MATCH (s:Source {url:$url}) SET s.contentHash=$h",
                params={"url": url, "h": version})


# ── the aggregate: §4.6's rule, and the anti-vacuity refusal ──────────────────

def test_aggregate_is_the_section_46_rule():
    """stale if ANY; current only when EVERY; unknown otherwise.

    FAILS IF the precedence inverts — most importantly if a stale link among
    current ones stops dominating, or if an unfilled pair is reported as
    `current`."""
    assert aggregate_currency([("h1", "h1"), ("h1", "h1")]) == "current"
    # ONE behind link is enough — the sibling cannot rehabilitate it.
    assert aggregate_currency([("h1", "h1"), ("h1", "h2")]) == "stale"
    assert aggregate_currency([("h1", "h2"), ("h1", "h1")]) == "stale", (
        "the verdict must not depend on link ORDER")
    # `stale` OUTRANKS `unknown`: an unfilled pair on one edge says nothing
    # about a pair that is filled and differs on another.
    assert aggregate_currency([("h1", "h2"), ("", "h1")]) == "stale"
    # No stale link, one unfillable pair ⇒ unknown, never current.
    assert aggregate_currency([("h1", "h1"), ("", "h1")]) == "unknown"
    assert aggregate_currency([("h1", "")]) == "unknown"
    assert aggregate_currency([("", "")]) == "unknown"


def test_an_empty_link_set_is_unknown_never_current():
    """⛔ THE ANTI-VACUITY RULE — the one that bites hardest.

    "current only when every link is" is vacuously true of an empty set, so a
    Point with no `extractedFrom` link at all would read `current` — the exact
    false-current §4.6 exists to prevent, reported for a Point that has no
    recorded read to compare.

    FAILS IF the aggregate is implemented as `all(verdicts == {"current"})`
    without the empty-set guard, which is what a literal reading of §4.6
    produces."""
    assert aggregate_currency([]) == "unknown"
    assert aggregate_currency(iter([])) == "unknown", (
        "a generator argument must not slip past the guard")


def test_aggregate_borrows_the_one_pair_verdict():
    """The aggregate must not grow its own comparator.

    The pair semantics (exact string equality, no "newer" inference, absent ⇒
    unknown) live in ONE home. A second copy is the drift the repo has already
    paid for (#4097's env-truthiness contract is the precedent).

    FAILS IF the aggregate case-folds, trims, or orders the versions — content
    hashes carry no ordering, so a `stale`/`current` decision must be exact
    string equality and nothing else."""
    for pair in [("h1", "h1"), ("h1", "h2"), ("h1", "H1"), ("h1", " h1"),
                 ("", "h1"), ("h1", "")]:
        assert aggregate_currency([pair]) == currency_status(*pair), pair
    assert aggregate_currency([("h1", "H1")]) == "stale", (
        "the comparison is exact — a case-folded hash is a different version")


# ── the read: per link, and aggregated ────────────────────────────────────────

def test_point_currency_aggregates_every_link_and_names_each():
    """A Point read from TWO sources reports BOTH links, and the aggregate.

    FAILS IF the read collapses the links to one row (the disclosure must be
    able to name *which* source moved), or if the aggregate lets a current link
    mask a stale one."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        graph = sdk._get_proj().g
        sdk.create_source("https://ex.com/a", "corpus", contentHash="h1")
        sdk.create_source("https://ex.com/b", "corpus", contentHash="h9")
        p = sdk.create_point("statement", "beta ships in October",
                             extractedFrom=["https://ex.com/a",
                                            "https://ex.com/b"])
        fresh = point_currency(graph, p["id"])
        assert fresh["status"] == "current", fresh
        assert {lk["source"] for lk in fresh["links"]} == {
            "https://ex.com/a", "https://ex.com/b"}, fresh["links"]
        assert all(lk["currency"] == "current" for lk in fresh["links"])

        _bump(graph, "https://ex.com/a", "h2")
        stale = point_currency(graph, p["id"])
        assert stale["status"] == "stale", (
            "one behind link must make the POINT stale, whatever the other says")
        by_source = {lk["source"]: lk for lk in stale["links"]}
        assert by_source["https://ex.com/a"]["currency"] == "stale"
        assert by_source["https://ex.com/b"]["currency"] == "current", (
            "the untouched source must not be reported as drift")
        assert by_source["https://ex.com/a"]["recorded"] == "h1"
        assert by_source["https://ex.com/a"]["current"] == "h2"
    finally:
        sdk.close()


def test_a_point_with_no_link_reads_unknown():
    """An orphan Point (no `extractedFrom` at all) is `unknown` — the graph
    half of the anti-vacuity rule.

    FAILS IF the graph read reports `current` for a Point it found no link for
    (the empty-set case dressed up as a lookup)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        graph = sdk._get_proj().g
        p = sdk.create_point("statement", "an orphan claim")
        assert read_links(graph, p["id"]) == []
        assert point_currency(graph, p["id"])["status"] == "unknown"
        # ... and a Point that does not exist at all is the same honest answer,
        # not an exception a caller must special-case.
        assert point_currency(graph, "pt_does_not_exist")["status"] == "unknown"
    finally:
        sdk.close()


def test_a_source_with_no_version_reads_unknown_not_current():
    """The capture/stub shape: the Source exists, the link is BARE.

    A minted stub carries `contentHash=''` (and the capture path links Points
    before materialization sets the real hash), so the pair is unfillable.

    FAILS IF a bare note or an empty hash is treated as "matches" — `''`
    compares EQUAL to a Source's `''`, so a naive equality check reads the
    commonest shape in the graph as a false `current`."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        graph = sdk._get_proj().g
        # auto-minted stub: no contentHash
        p = sdk.create_point("statement", "from an unversioned source",
                             extractedFrom="https://ex.com/nohash")
        assert point_currency(graph, p["id"])["status"] == "unknown"

        # ... and the same for a Point whose Point-level link IS anchored but
        # whose Source has no version (the operand is what is missing, not the
        # link).
        sdk.create_source("https://ex.com/versioned", "corpus", contentHash="h1")
        q = sdk.create_point("statement", "a second claim",
                             extractedFrom="https://ex.com/versioned")
        assert point_currency(graph, q["id"])["status"] == "current"
        graph.query("MATCH (s:Source {url:'https://ex.com/versioned'}) "
                    "SET s.contentHash=''")
        assert point_currency(graph, q["id"])["status"] == "unknown", (
            "an unreadable current operand must not read as fresh")
    finally:
        sdk.close()


def test_read_currency_links_is_batched_and_omits_points_with_no_link():
    """The search path's read: ONE query for many Points, and a Point with no
    link ABSENT rather than present-with-an-empty-list.

    FAILS IF the batch read emits an entry for a linkless Point — a caller
    checking membership must not mistake "no versioned provenance" for "not in
    this batch"."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        graph = sdk._get_proj().g
        sdk.create_source("https://ex.com/a", "corpus", contentHash="h1")
        linked = sdk.create_point("statement", "linked claim",
                                  extractedFrom="https://ex.com/a")
        orphan = sdk.create_point("statement", "orphan claim")
        out = read_currency_links(graph, [linked["id"], orphan["id"]])
        assert set(out) == {linked["id"]}, out
        assert out[linked["id"]][0]["currency"] == "current"
        assert read_currency_links(graph, []) == {}, (
            "an empty batch must not run a query nor invent rows")
    finally:
        sdk.close()


# ── the disclosure ────────────────────────────────────────────────────────────

def test_a_stale_verdict_discloses_its_source():
    """The FYI CARRIES ITS SOURCE — the whole requirement.

    FAILS IF the disclosure is a bare flag (no `sources`), or if it fires for a
    verdict that disclosed nothing to disclose."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        graph = sdk._get_proj().g
        sdk.create_source("https://ex.com/a", "corpus", contentHash="h1")
        p = sdk.create_point("statement", "beta ships in October",
                             extractedFrom="https://ex.com/a")
        assert sourced_fyi(point_currency(graph, p["id"])) is None, (
            "a current Point has no drift to disclose — an empty FYI would make "
            "`no drift` indistinguishable from `drift we failed to name`")
        _bump(graph, "https://ex.com/a", "h2")
        fyi = sourced_fyi(point_currency(graph, p["id"]))
        assert fyi is not None
        assert fyi["reason"] == FYI_REASON_SOURCE_DRIFT
        assert fyi["point"] == p["id"]
        assert [s["source"] for s in fyi["sources"]] == ["https://ex.com/a"]
        assert fyi["sources"][0]["recorded"] == "h1"
        assert fyi["sources"][0]["current"] == "h2"
        # The caller may name the replacement it knows about; the helper never
        # invents one (the window before #5422 emits a successor).
        assert "newer" not in fyi
        with_newer = sourced_fyi(point_currency(graph, p["id"]),
                                 newer={"id": "pt_new", "source": None})
        assert with_newer["newer"] == {"id": "pt_new", "source": None}
    finally:
        sdk.close()


def test_the_disclosure_is_refused_when_it_can_name_no_source():
    """A hand-built `stale` verdict with no behind link must disclose NOTHING.

    FAILS IF a `stale` label with no source emits an FYI with an empty
    `sources` list — the flag-alone shape the ruling rejects, wearing the
    disclosure's name."""
    assert sourced_fyi({"point": "p", "status": "stale", "links": []}) is None
    assert sourced_fyi({"point": "p", "status": "stale"}) is None
    assert sourced_fyi({"point": "p", "status": "current", "links": []}) is None
    assert sourced_fyi({"point": "p", "status": "unknown", "links": []}) is None


def test_answer_eligibility_follows_the_owner_ruling():
    """`NOT (stale ∧ a newer fact exists)` — and ONLY that term.

    FAILS IF `unknown` is withheld (it says "not compared", not "out of date" —
    withholding it would suppress every orphan Point, a change no policy
    makes), or if a stale fact with NO successor is withheld (supersession is
    deferred until the replacement exists: *"a stale belief is strictly better
    than no belief"*)."""
    assert answer_eligible("stale", newer_fact_exists=True) is False
    assert answer_eligible("stale", newer_fact_exists=False) is True
    assert answer_eligible("current", newer_fact_exists=True) is True
    assert answer_eligible("unknown", newer_fact_exists=True) is True
    assert answer_eligible("unknown", newer_fact_exists=False) is True


# ── nothing is stored ─────────────────────────────────────────────────────────

def test_currency_is_derived_not_stored():
    """The link carries ONLY the note; the Point carries no verdict.

    FAILS IF a `currency`/`linkCurrency` property is ever written: a stored
    verdict needs a backfill on every source edit and can disagree with the pair
    it was derived from (§4.6: *"a read, never a stored flag"*)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        graph = sdk._get_proj().g
        sdk.create_source("https://ex.com/a", "corpus", contentHash="h1")
        p = sdk.create_point("statement", "beta ships in October",
                             extractedFrom="https://ex.com/a")
        edge_keys = graph.query(
            "MATCH (:Point {id:$pid})-[r:extractedFrom]->(:Source) "
            "RETURN keys(r)", params={"pid": p["id"]}).result_set
        assert edge_keys[0][0] == ["sourceVersion"], (
            f"the link must carry only the note, got {edge_keys[0][0]!r}")
        node_keys = graph.query("MATCH (p:Point {id:$pid}) RETURN keys(p)",
                                params={"pid": p["id"]}).result_set[0][0]
        assert not {"currency", "linkCurrency", "sourceVersion",
                    "stale"} & set(node_keys), (
            f"a currency verdict became a node property: {sorted(node_keys)}")
    finally:
        sdk.close()


# ── the report on the EXISTING read surface ───────────────────────────────────

def _scan(sdk, kind="statement", limit=50):
    return sdk.tortoise_fts_query(query=None, kind=kind, entity_type="point",
                                  limit=limit)


def test_search_hit_reports_the_verdict_and_discloses_the_source(tmp_path,
                                                                monkeypatch):
    """The ruling's REPORTING half, on the existing result row.

    Flag OFF: the row is byte-identical to pre-#5581 output (no `provenance`
    key at all). Flag ON: the §4.6 verdict rides inside `provenance`, every
    other key/value is untouched, and a `stale` verdict carries the sourced FYI.

    FAILS IF the flag stops being purely additive, if the verdict is derived
    from a single link instead of §4.6's aggregate, or if a `stale` hit is
    reported WITHOUT the source that moved."""
    sdk = TortoiseSDK(str(tmp_path / "search.db"))
    try:
        graph = sdk._get_proj().g
        sdk.create_source("https://ex.com/a", "corpus", contentHash="h1")
        sdk.create_source("https://ex.com/b", "corpus", contentHash="h9")
        p = sdk.create_point("statement", "beta ships in October",
                             extractedFrom=["https://ex.com/a",
                                            "https://ex.com/b"])

        # flag OFF — byte-identical: the block must not appear at all.
        monkeypatch.delenv("TORTOISE_SEARCH_PROVENANCE", raising=False)
        off = next(r for r in _scan(sdk) if r["id"] == p["id"])
        assert "provenance" not in off, "flag OFF must not add a response field"

        monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
        on = next(r for r in _scan(sdk) if r["id"] == p["id"])
        prov = on.pop("provenance")
        assert prov["currency"] == "current", prov
        assert "fyi" not in prov, (
            "a current Point discloses no drift — the FYI key must be absent, "
            "not an empty shell")
        assert on == off, "the flag must be purely additive"

        # one source moves: the POINT reads stale (any link), and the row
        # discloses WHICH source moved rather than flagging a binary state.
        _bump(graph, "https://ex.com/a", "h2")
        stale_hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
        prov = stale_hit["provenance"]
        assert prov["currency"] == "stale", (
            "one behind link must make the POINT stale (§4.6), even though the "
            "other link is current")
        assert prov["fyi"]["reason"] == FYI_REASON_SOURCE_DRIFT
        assert [s["source"] for s in prov["fyi"]["sources"]] == [
            "https://ex.com/a"], prov["fyi"]
        assert prov["fyi"]["sources"][0]["recorded"] == "h1"
        assert prov["fyi"]["sources"][0]["current"] == "h2"
    finally:
        sdk.close()
