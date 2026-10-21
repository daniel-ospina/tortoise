"""#5038 deliverable 3 — the SOURCE-addressed staleness read (the re-inference
TARGETING signal).

`#5038`'s acceptance line is *"given a source re-fetched with **different**
content, the graph can report **which entities were extracted from the earlier
version**"*. The fact-addressed half shipped (`#5581` / `tools/source_currency.py::point_currency`)
and it answers the question ONE Point at a time: it can say *that* a Point is
stale, but a re-fetched source had no way to enumerate what it invalidated. That
enumeration is this read, and it is what a re-inference pass (#5422's problem)
must walk — otherwise "re-infer the source" has no work list.

What this file pins, and why each shape is a pin rather than an incidental:

* the **direction** — the sweep is keyed by the SOURCE, so two Points and one
  derived `Event` linked from one source are all found by one call. A per-Point
  API cannot answer the acceptance line without the caller already knowing the
  ids, which is the information they are asking for.
* **both anchored link classes** — `(Point)-[:extractedFrom]->(Source)` (§4.6)
  and `(Source)-[:references]->(target)` for the derivation form that `#5199`
  gave the anchor. A sweep that reads only `extractedFrom` reports a half-truth
  for exactly the `:Event`/document class `#5038` names in its Indicators.
* **`unknown` is not drift** — an unfilled pair (a minted stub, a source with no
  `contentHash`) is reported, counted, and **excluded** from the targets. A
  re-inference pass that treats "could not compare" as "is stale" re-reads a
  corpus it never needed to; one that treats it as "current" is the false-fresh
  `§4.6` exists to prevent. Both are refused here, in opposite directions.
* **the ambiguity rule** — a property-free `references` link is either
  containment (Source→Source) or an unanchored mention, and at read time those
  are indistinguishable. It is NOT reported as a target and NOT counted as
  `unknown`: only the anchor's presence marks a `references` link as derivation
  (`projection/edges.py`), and inventing a target for a containment edge is the
  one way this read could be actively wrong.
* **nothing is stored and nothing is adopted** — a currency check is a read
  (recorded on `#5038`; restated on `#5422`), so no `status`/`currency` property
  lands anywhere, and the URL resolver used here must not write (`resolve_source_key`
  adopts-on-touch; this read uses the pure normalizer instead).

Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import source_currency
from tools.source_currency import main, source_staleness
from tortoise.sdk import TortoiseSDK


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


def _fixture(sdk: TortoiseSDK, *, content_hash: str = "h1"):
    """One source + an Event + an Object, all at ``content_hash``."""
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (s:Source {url:'src.txt', sourceKind:'document', title:'src.txt', "
        "contentHash:$h, ingestedAt:'2024-01-01'})",
        params={"h": content_hash},
    )
    proj.g.query(
        "CREATE (ev:Event {id:'ev-1', eventId:'ev-1', name:'Meeting', "
        "eventKind:'meeting'})"
    )
    proj.g.query("CREATE (o:Object {id:'obj-1', name:'Widget', objectKind:'product'})")
    return proj


def _set_hash(proj, content_hash: str) -> None:
    """Re-fetch: the Source keeps ONE version (§4.6 — the graph shows current)."""
    proj.g.query(
        "MATCH (s:Source {url:'src.txt'}) SET s.contentHash=$h",
        params={"h": content_hash},
    )


# ── the acceptance line: WHICH entities came from the earlier version ─────────

def test_a_revised_source_names_exactly_the_nodes_read_from_the_old_version():
    """Input: source at 'h1' with two Points + one derivation Event linked; the
    source is then re-fetched at 'h2'.

    FAILS IF the sweep does not find all three (the acceptance line is an
    ENUMERATION, so finding two of three is the defect), if it names anything that
    was read at 'h2', or if it reports the drift on only one of the two link
    classes — the `references` half is the `:Event`/document class the issue's
    Indicators name.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        p1 = sdk.create_point("statement", "alpha claim", extractedFrom="src.txt")
        p2 = sdk.create_point("statement", "beta claim", extractedFrom="src.txt")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        fresh = source_staleness(proj.g, "src.txt")
        assert fresh["status"] == "current", fresh
        assert fresh["re_inference_targets"] == [], (
            "nothing has changed yet, so nothing may be a re-inference target")
        assert fresh["counts"]["stale"] == 0

        _set_hash(proj, "h2")

        sweep = source_staleness(proj.g, "src.txt")
        assert sweep["source"] == "src.txt"
        assert sweep["current"] == "h2"
        assert sweep["status"] == "stale", (
            "one behind link is enough for a stale aggregate (§4.6)")
        assert sorted(sweep["re_inference_targets"]) == sorted(
            [p1["id"], p2["id"], "ev-1"]), sweep["re_inference_targets"]
        assert sweep["counts"]["stale"] == 3, sweep["counts"]
        assert sweep["counts"]["unknown"] == 0, sweep

        # The two classes are named, so a caller can tell a Point from an Event
        # without re-querying the graph.
        by_node = {lk["node"]: lk for lk in sweep["links"]}
        assert by_node[p1["id"]]["via"] == "extractedFrom"
        assert by_node[p1["id"]]["kind"] == "point"
        assert by_node["ev-1"]["via"] == "references"
        assert by_node["ev-1"]["kind"] == "event"
        # ... and the pair that produced the verdict rides along (a bare `stale`
        # with no versions named is the flag-alone form the ruling rejects).
        assert by_node["ev-1"]["recorded"] == "h1"
        assert by_node["ev-1"]["current"] == "h2"
    finally:
        sdk.close()


def test_a_link_made_after_the_revision_is_current_not_stale():
    """Input: source re-fetched at 'h2' FIRST, then a new Point linked.

    FAILS IF the sweep compares against anything but the source's CURRENT hash
    (e.g. the oldest recorded version on any link, or the source's `updatedAt`):
    the newly-read node must be `current`, and the older ones must stay `stale` —
    per-link verdicts, not one verdict for the source.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        old = sdk.create_point("statement", "read at h1", extractedFrom="src.txt")

        _set_hash(proj, "h2")
        new = sdk.create_point("statement", "read at h2", extractedFrom="src.txt")

        sweep = source_staleness(proj.g, "src.txt")
        by_node = {lk["node"]: lk for lk in sweep["links"]}
        assert by_node[old["id"]]["currency"] == "stale", by_node[old["id"]]
        assert by_node[new["id"]]["currency"] == "current", by_node[new["id"]]
        assert sweep["re_inference_targets"] == [old["id"]], (
            "the newer read must not be proposed for re-inference — it is the "
            f"work the sweep exists to avoid; got {sweep['re_inference_targets']!r}")
        assert sweep["counts"] == {"stale": 1, "current": 1, "unknown": 0,
                                   "links": 2}, sweep["counts"]
    finally:
        sdk.close()


# ── the honest-absence states ────────────────────────────────────────────────

def test_an_unversioned_source_reports_unknown_and_no_targets():
    """Input: a Point linked to an auto-minted stub (no `contentHash`), and a
    derivation Event linked to the same stub.

    FAILS IF an unfilled pair is reported as a target (a re-inference pass would
    re-read a whole corpus on no evidence) or as `current` (the false-fresh
    §4.6 exists to prevent). `''` compares EQUAL to a Source's `''`, so the
    naive equality is what this catches.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (ev:Event {id:'ev-2', eventId:'ev-2', name:'Loose'})")
        p = sdk.create_point("statement", "from an unversioned source",
                             extractedFrom="never-seen.txt")
        sdk.link_source_to_entity("never-seen.txt", "ev-2", "Event")

        sweep = source_staleness(proj.g, "never-seen.txt")
        assert sweep["current"] == ""
        assert sweep["status"] == "unknown", (
            "a source with no version cannot verify anything, so the aggregate "
            f"is unknown, never current; got {sweep['status']!r}")
        assert sweep["re_inference_targets"] == [], (
            "'could not compare' is not 'drifted'")
        # The Point's unanchored `extractedFrom` link and the unanchored Event
        # derivation are BOTH derivation links (their classes are unambiguous), so
        # both are reported — as `unknown`. Only the ambiguous `Source`-targeted
        # class is withheld; see the ambiguity test below.
        assert sweep["counts"]["unknown"] == 2, sweep["counts"]
        assert {lk["node"] for lk in sweep["links"]} == {p["id"], "ev-2"}, \
            sweep["links"]
    finally:
        sdk.close()


def test_a_source_that_derived_nothing_is_unknown_not_current():
    """Input: a source with a contentHash and NO inbound links at all.

    FAILS IF an empty link set reads `current` — "current only when every link
    is" is VACUOUSLY true of an empty set, the same anti-vacuity rule the
    Point-level aggregate refuses (a source that derived nothing is not a
    verified-fresh source). Also pins the unknown-ref case: a url that matches no
    node is the same honest answer rather than an exception.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        sdk.create_source("https://ex.com/nothing", "corpus", contentHash="h1")

        sweep = source_staleness(proj.g, "https://ex.com/nothing")
        assert sweep["current"] == "h1"
        assert sweep["status"] == "unknown", sweep["status"]
        assert sweep["counts"]["links"] == 0

        missing = source_staleness(proj.g, "https://ex.com/never-registered")
        assert missing["status"] == "unknown"
        assert missing["current"] == ""
        assert missing["links"] == []
    finally:
        sdk.close()


def test_a_property_free_references_link_is_not_a_target():
    """Input: a `Source -> Source` containment link and an `Object` mention link,
    neither anchored, alongside ONE anchored derivation Event.

    FAILS IF an unanchored `references` link becomes a re-inference target or an
    `unknown` row. At read time a containment link and an unanchored derivation
    link are stored IDENTICALLY (`(Source)-[:references]->(Source)`), so the only
    discriminator is the anchor — reporting the ambiguous ones as targets would
    point a re-inference pass at edges it must never re-derive (the `#5199`
    ruling keeps identity/mention links property-free on purpose).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        # an anchored derivation link (the only class the anchor marks)
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        # an Object MENTION link: no anchor by design
        sdk.link_source_to_entity("src.txt", "obj-1", "Object")
        # a Source -> Source containment link: also property-free
        proj.g.query("CREATE (d:Source {url:'doc-1', sourceKind:'document'})")
        sdk.link_source_to_entity("src.txt", "doc-1", "Source")

        rows = {lk["node"]: lk for lk in source_staleness(proj.g, "src.txt")["links"]}
        assert set(rows) == {"ev-1"}, (
            "only the anchored derivation link may be swept — an Object mention "
            f"link has no version to compare and a containment link is ambiguous; got {sorted(rows)}")
        assert rows["ev-1"]["via"] == "references"
    finally:
        sdk.close()


# ── read-only, and the CLI is the same read ──────────────────────────────────

def test_the_sweep_writes_nothing_and_is_stable_across_reads():
    """Input: an anchored derivation link + a Point, source at 'h1'; sweep twice,
    then bump the source and sweep again.

    FAILS IF the read stores a verdict (`status`/`currency` on the edge or node —
    §4.6 *"a read, never a stored flag"*), if it adopts a canonical identity
    (the URL resolver here must not write), or if two reads of an unchanged graph
    disagree.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        p = sdk.create_point("statement", "a claim", extractedFrom="src.txt")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        first = source_staleness(proj.g, "src.txt")
        second = source_staleness(proj.g, "src.txt")
        assert first == second, "two reads of an unchanged graph must agree"

        # No verdict is stored anywhere.
        stored = proj.g.query(
            "MATCH (p:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:'src.txt'}) "
            "RETURN keys(r)",
            params={"pid": p["id"]},
        ).result_set
        assert stored and all(
            k not in ("currency", "status", "stale") for k in (stored[0][0] or [])
        ), f"a verdict must never be stored on the link; keys(r)={stored!r}"
        ev_keys = proj.g.query(
            "MATCH (:Source {url:'src.txt'})-[r:references]->(:Event {id:'ev-1'}) "
            "RETURN keys(r)"
        ).result_set
        assert ev_keys and all(
            k not in ("currency", "status", "stale") for k in (ev_keys[0][0] or [])
        ), f"keys(r)={ev_keys!r}"

        # The read never adopts a canonical identity (that write lives in
        # `resolve_source_key`, deliberately NOT used here). Snapshot the
        # identity BEFORE the read: `create_point`'s write path may already have
        # adopted it, and what this pins is that THE READ adds nothing.
        identity_before = proj.g.query(
            "MATCH (s:Source {url:'src.txt'}) RETURN s.canonicalUrl, s.urlAliases"
        ).result_set
        source_staleness(proj.g, "src.txt")
        identity_after = proj.g.query(
            "MATCH (s:Source {url:'src.txt'}) RETURN s.canonicalUrl, s.urlAliases"
        ).result_set
        assert identity_after == identity_before, (
            "a currency read must not adopt/register the source; "
            f"before={identity_before!r} after={identity_after!r}")

        _set_hash(proj, "h2")
        after = source_staleness(proj.g, "src.txt")
        assert after["status"] == "stale"
        assert after["re_inference_targets"] == [p["id"], "ev-1"], after
    finally:
        sdk.close()


def test_cli_source_mode_reports_the_same_read():
    """Input: `--source src.txt --json` against a graph with one stale link.

    FAILS IF the operator entry point is not the same read as the function (a CLI
    that computes its own verdict would be a second copy of the vocabulary), or
    if the two modes are both/neither accepted — a silent default would make
    `--source` optional-by-accident.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        p = sdk.create_point("statement", "a claim", extractedFrom="src.txt")
        _set_hash(proj, "h2")

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--source", "src.txt", "--json", "--db", sdk._db_path])
        assert rc == 0
        payload = json.loads(buf.getvalue())
        assert payload["status"] == "stale"
        assert payload["re_inference_targets"] == [p["id"]]
        assert payload == source_staleness(proj.g, "src.txt"), (
            "the CLI must be the same read, not a re-derivation of it")

        # text mode names the counts and the targets in one place
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert main(["--source", "src.txt", "--db", sdk._db_path]) == 0
        out = buf.getvalue()
        assert "stale" in out and p["id"] in out, out
    finally:
        sdk.close()


def test_guards_reuse_the_one_vocabulary_home():
    """The sweep must not grow its own comparator or aggregate.

    FAILS IF `tools/source_currency` stops deferring to
    `tortoise.search_engine.currency_status` / `aggregate_currency` (a second
    copy of a three-state vocabulary is drift the repo has already paid for
    twice, #4097 being the precedent). It also pins the export, so a caller can
    rely on the name staying.
    """
    assert source_currency.currency_status is not None
    assert source_currency.aggregate_currency is not None
    assert "source_staleness" in source_currency.__all__
    import inspect
    src = inspect.getsource(source_currency.source_staleness)
    assert "currency_status(" in src and "aggregate_currency(" in src, (
        "the sweep must call the shared verdict helpers, never reimplement them")
    for banned in ('== "current"', "'current' if", '"stale" if'):
        assert banned not in src, (
            f"a hand-rolled verdict appears in the sweep: {banned!r}")


# ── review findings (fresh-context reviewers, 2026-10-21) ─────────────────────
# Each block below closes a hole a reviewer reproduced with a surviving mutant.

def test_a_second_source_is_not_swept_and_its_nodes_are_not_targets():
    """Input: TWO sources, each with its own Point and its own derivation Event;
    only `src.txt` is re-fetched.

    FAILS IF the sweep is not scoped to the source it names (`WHERE $url IS NOT
    NULL` in place of `{url:$url}` passes every other test in this file, because
    they all hold exactly one Point-bearing source). The other source's Point
    would then be proposed for re-inference on a source that never moved.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        proj.g.query(
            "CREATE (s:Source {url:'other.txt', sourceKind:'document', "
            "title:'other.txt', contentHash:'h1', ingestedAt:'2024-01-01'})")
        proj.g.query(
            "CREATE (ev:Event {id:'ev-other', eventId:'ev-other', name:'Other', "
            "eventKind:'meeting'})")
        mine = sdk.create_point("statement", "mine", extractedFrom="src.txt")
        theirs = sdk.create_point("statement", "theirs",
                                  extractedFrom="other.txt")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        sdk.link_source_to_entity("other.txt", "ev-other", "Event")

        _set_hash(proj, "h2")

        sweep = source_staleness(proj.g, "src.txt")
        assert sorted(sweep["re_inference_targets"]) == sorted(
            [mine["id"], "ev-1"]), (
            "the other source's nodes must not be targets for THIS source; "
            f"got {sweep['re_inference_targets']!r}")
        assert theirs["id"] not in {lk["node"] for lk in sweep["links"]}
        assert "ev-other" not in {lk["node"] for lk in sweep["links"]}
        assert sweep["counts"]["links"] == 2, sweep["counts"]

        # ... and the other source is untouched by the sweep: still current.
        other = source_staleness(proj.g, "other.txt")
        assert other["status"] == "current", other
        assert other["re_inference_targets"] == []
    finally:
        sdk.close()


def test_an_unanchored_event_derivation_reads_unknown_neither_dropped_nor_a_target():
    """Input: a legacy `(Source)-[:references]->(Event)` edge with NO anchor
    (reachable through `create_edge` / `graph-scripts/backfill_references.py`;
    §9.6 records *"no backfill, deliberately"*), plus one anchored link.

    FAILS IF the unanchored Event link is dropped from the payload. The label IS
    the write-time discriminator for this class (no other `references` writer
    mints an `Event`), so silently omitting it lets a source whose legacy Event
    derivations were never anchored read `current` over an incomplete link set —
    and the acceptance line (*which entities came from the earlier version*) would
    answer "none" instead of "these, unverifiable".
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")   # anchored
        proj.g.query("CREATE (ev:Event {id:'ev-legacy', eventId:'ev-legacy', "
                     "name:'Legacy', eventKind:'meeting'})")
        # a bare derivation edge, exactly what create_edge / the ops backfill mint
        proj.g.query(
            "MATCH (s:Source {url:'src.txt'}), (e:Event {id:'ev-legacy'}) "
            "CREATE (s)-[:references]->(e)")

        sweep = source_staleness(proj.g, "src.txt")
        rows = {lk["node"]: lk for lk in sweep["links"]}
        assert "ev-legacy" in rows, (
            "an unanchored Event derivation is derivation, not ambiguity — it "
            f"must be reported (as unknown); got {sorted(rows)}")
        assert rows["ev-legacy"]["currency"] == "unknown", rows["ev-legacy"]
        assert rows["ev-legacy"]["recorded"] == ""
        assert sweep["re_inference_targets"] == [], (
            "unknown is not evidence of drift, so it is not a target")
        assert sweep["counts"]["unknown"] == 1, sweep["counts"]
    finally:
        sdk.close()


def test_an_unreadable_link_class_degrades_the_sweep_to_unknown_never_current():
    """Input: a healthy graph where one link class reads fine and the OTHER query
    raises (the engine is the same one a partial outage reaches).

    FAILS IF a failed read is swallowed: the silently shorter link set makes the
    §4.6 aggregate answer `current` with an empty target list, i.e. the read that
    could not complete is reported as verified-fresh — the exact false-fresh this
    module exists to prevent. The stale row found before the failure must still
    be listed (hiding it would be a second defect) and the payload must declare
    itself incomplete.
    """
    class _Broken:
        """A graph handle on which exactly ONE read site raises.

        ``fail_on`` is the substring that identifies the site, so each of the
        three failure points (the source lookup, the ``extractedFrom`` read, the
        ``references`` read) is exercised independently — a stub that only ever
        fails one of them leaves the other two silently untested, which is how a
        swallowed error survives a green suite.
        """

        def __init__(self, graph, fail_on):
            self._g = graph
            self._fail_on = fail_on

        def query(self, cypher, **kwargs):
            if self._fail_on in cypher:
                raise RuntimeError(f"{self._fail_on} read refused")
            return self._g.query(cypher, **kwargs)

    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        p = sdk.create_point("statement", "a claim", extractedFrom="src.txt")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        _set_hash(proj, "h2")

        healthy = source_staleness(proj.g, "src.txt")
        assert healthy["status"] == "stale" and healthy["complete"] is True

        # Each read site independently: (failing substring, error must name it,
        # the row the OTHER (healthy) class yields — positive evidence that must
        # survive the failure).
        for fail_on, named, survivor in (
            ("MATCH (s:Source) WHERE", "source lookup", None),
            ("[r:extractedFrom]", "extractedFrom", "ev-1"),
            ("references", "references", p["id"]),
        ):
            broken = source_staleness(_Broken(proj.g, fail_on), "src.txt")
            assert broken["complete"] is False, (
                f"a sweep whose {named} read failed must say so; got {broken}"
            )
            assert broken["status"] == "unknown", (
                "an incomplete sweep certifies NOTHING — `current` here would be "
                f"a false-fresh; failing site={named}, got {broken['status']!r}"
            )
            assert broken["errors"] and named in broken["errors"][0], broken
            if survivor is None:
                assert broken["re_inference_targets"] == [], broken
            else:
                assert broken["re_inference_targets"] == [survivor], (
                    f"the stale row the healthy class ({survivor}) DID find is "
                    f"positive evidence and must survive; got {broken['re_inference_targets']!r}"
                )
            if fail_on != "MATCH (s:Source) WHERE":
                assert "source lookup" not in " ".join(broken["errors"]), (
                    "only the failing site may be reported; the other reads are "
                    f"healthy; got {broken['errors']!r}"
                )

        # A source that does not exist is NOT a failed read: honest-absent, and
        # the sweep is COMPLETE (nothing was unreadable).
        absent = source_staleness(proj.g, "https://ex.com/never-registered")
        assert absent["complete"] is True, (
            f"'no such source' is an answer, not a failure; got {absent!r}")
        assert absent["errors"] == []
    finally:
        sdk.close()


def test_the_read_never_adopts_a_source_identity():
    """Input: a source + Point + derivation Event created ENTIRELY by raw Cypher,
    so no write path has adopted the identity; sweep, then inspect the node.

    FAILS IF the resolver used is the adopting one
    (`source_identity.resolve_source_key`, which WRITES `canonicalUrl` /
    `urlAliases`). The neighbouring test snapshots the identity AFTER the write
    path already adopted it, so an adopting resolver's fast path writes nothing
    and survives it — this fixture cannot be reached by that path.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'https://raw.example/doc', "
            "sourceKind:'document', contentHash:'h1', "
            "ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (p:Point {id:'pt-raw', statement:'a claim', "
                     "kind:'statement', createdAt:'2024-01-01'})")
        proj.g.query("CREATE (ev:Event {id:'ev-raw', eventId:'ev-raw', "
                     "name:'Raw', eventKind:'meeting'})")
        proj.g.query(
            "MATCH (s:Source {url:'https://raw.example/doc'}), (p:Point {id:'pt-raw'}) "
            "CREATE (p)-[:extractedFrom {sourceVersion:'h1'}]->(s)")
        proj.g.query(
            "MATCH (s:Source {url:'https://raw.example/doc'}), (e:Event {id:'ev-raw'}) "
            "CREATE (s)-[:references {sourceVersion:'h1'}]->(e)")

        before = proj.g.query(
            "MATCH (s:Source {url:'https://raw.example/doc'}) "
            "RETURN s.canonicalUrl, s.urlAliases").result_set
        assert before == [[None, None]], (
            f"fixture precondition: no write path may have adopted; got {before!r}")

        sweep = source_staleness(proj.g, "https://raw.example/doc")
        assert sweep["status"] == "current", sweep

        after = proj.g.query(
            "MATCH (s:Source {url:'https://raw.example/doc'}) "
            "RETURN s.canonicalUrl, s.urlAliases").result_set
        assert after == [[None, None]], (
            "a currency sweep is a READ: it must not adopt/register the source "
            f"(resolve_source_key would); got {after!r}")
    finally:
        sdk.close()


def test_a_url_variant_resolves_to_the_registered_source_read_only():
    """Input: the node is stored under a tracker-suffixed raw url and carries a
    canonical `canonicalUrl`; the sweep is asked for the CLEAN spelling.

    FAILS IF the `canonicalUrl` branch of the read-only resolver is dropped: the
    sweep would then read the clean spelling as an unknown, unversioned source
    and answer `unknown` (or match nothing at all) instead of reporting the
    stale derivation it holds.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'https://v.example/doc?utm_source=x', "
            "canonicalUrl:'https://v.example/doc', sourceKind:'document', "
            "contentHash:'h2', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (p:Point {id:'pt-v', statement:'v', "
                     "kind:'statement', createdAt:'2024-01-01'})")
        proj.g.query(
            "MATCH (s:Source {url:'https://v.example/doc?utm_source=x'}), "
            "(p:Point {id:'pt-v'}) "
            "CREATE (p)-[:extractedFrom {sourceVersion:'h1'}]->(s)")

        sweep = source_staleness(proj.g, "https://v.example/doc")
        assert sweep["source"] == "https://v.example/doc?utm_source=x", (
            f"the raw spelling must resolve through canonicalUrl; got {sweep['source']!r}")
        assert sweep["current"] == "h2"
        assert sweep["re_inference_targets"] == ["pt-v"], sweep
    finally:
        sdk.close()


def test_links_are_sorted_so_two_reads_of_an_unchanged_graph_agree():
    """Input: a graph whose INSERTION order differs from the sorted order (the
    later-created node sorts first).

    FAILS IF the sort is removed — the assertions elsewhere pass on engine row
    order alone, so without this the documented determinism contract is unpinned
    (`b-b` is created after `a-a` but sorts before it).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.create_point("statement", "zzz", extractedFrom="src.txt")  # id unknown
        proj.g.query("CREATE (p:Point {id:'b-b', statement:'b', "
                     "kind:'statement', createdAt:'2024-01-01'})")
        proj.g.query("CREATE (p:Point {id:'a-a', statement:'a', "
                     "kind:'statement', createdAt:'2024-01-02'})")
        for pid in ("b-b", "a-a"):
            proj.g.query(
                "MATCH (p:Point {id:$pid}), (s:Source {url:'src.txt'}) "
                "CREATE (p)-[:extractedFrom {sourceVersion:'h1'}]->(s)",
                params={"pid": pid})

        sweep = source_staleness(proj.g, "src.txt")
        points = [lk["node"] for lk in sweep["links"] if lk["via"] == "extractedFrom"]
        assert points == sorted(points), (
            f"rows must be sorted, not engine-ordered; got {points!r}")
        assert points.index("a-a") < points.index("b-b"), points
        assert sweep == source_staleness(proj.g, "src.txt")
    finally:
        sdk.close()


def test_the_cli_refuses_ambiguous_mode_selection():
    """Input: no destination, both destinations, and `--source` with `--fyi`.

    FAILS IF the mode guards are dropped (a silent default would make `--source`
    optional-by-accident, and `--fyi` describes a withheld Point answer that a
    source sweep has no place for).
    """
    import pytest

    for argv in ([], ["a-point", "--source", "src.txt"],
                 ["--source", "src.txt", "--fyi"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2, argv


# ── cycle-3 findings ─────────────────────────────────────────────────────────

def test_an_anchored_document_derivation_is_a_target_and_names_its_url():
    """Input: a DOCUMENT derivation link — `link_source_to_entity(..., "Document")`,
    the retired alias that still marks derivation (post-D10 the stored target is a
    `:Source` keyed by `url`), then a re-fetch.

    FAILS TWO WAYS. (1) If the `references` filter keeps only `head(labels(t)) =
    'Event'`, the anchored document half is dropped: the §4.6 aggregate then
    answers `current` while a stale derivation stands — a false-fresh on the exact
    class `_DERIVATION_REFERENCES_LABELS` anchors. (2) If the node id drops
    `t.url`, a document target (which carries NO `id`/`eventId`) yields `""` — a
    target no re-inference lane can act on.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        proj.g.query(
            "CREATE (doc:Source {url:'doc-1', sourceKind:'document', "
            "title:'doc-1', ingestedAt:'2024-01-01'})")
        sdk.link_source_to_entity("src.txt", "doc-1", "Document")
        # the anchor landed on the LINK, from the source's hash at link time
        anchored = proj.g.query(
            "MATCH (:Source {url:'src.txt'})-[r:references]->(:Source {url:'doc-1'}) "
            "RETURN r.sourceVersion").result_set
        assert anchored == [["h1"]], (
            f"fixture precondition: the derivation alias must anchor; got {anchored!r}")

        _set_hash(proj, "h2")

        sweep = source_staleness(proj.g, "src.txt")
        rows = {lk["node"]: lk for lk in sweep["links"]}
        assert "doc-1" in rows, (
            "an anchored document derivation is derivation and must be swept; "
            f"got {sorted(rows)}")
        assert rows["doc-1"]["via"] == "references"
        assert rows["doc-1"]["kind"] == "source"
        assert rows["doc-1"]["currency"] == "stale", rows["doc-1"]
        assert sweep["re_inference_targets"] == ["doc-1"], sweep
        assert all(lk["node"] for lk in sweep["links"]), (
            "a target with an empty id is not an address a lane can act on: "
            f"{sweep['links']!r}")
    finally:
        sdk.close()


def test_a_duplicate_source_identity_is_ambiguous_not_certified():
    """Input: TWO `:Source` nodes carrying ONE canonical identity (the state the
    dedup writers produce and `tools/source_dedup_report.py` repairs), one holding
    a stale derivation and one a current one.

    FAILS IF the sweep resolves a single node and certifies it: it would answer
    `current` with an empty target list while the OTHER node's stale derivation
    stands — a false-fresh over links the read cannot see. The honest answer is an
    error that names both nodes, `complete: false`, and `unknown`.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        for url, label in (("https://dup.example/doc", "a"),
                           ("https://DUP.example/doc", "b")):
            proj.g.query(
                "CREATE (s:Source {url:$url, "
                "canonicalUrl:'https://dup.example/doc', sourceKind:'document', "
                "contentHash:'h2', ingestedAt:'2024-01-01'})", params={"url": url})
            proj.g.query("CREATE (p:Point {id:$pid, statement:$pid, "
                         "kind:'statement', createdAt:'2024-01-01'})",
                         params={"pid": f"pt-{label}"})
            proj.g.query(
                "MATCH (s:Source {url:$url}), (p:Point {id:$pid}) "
                "CREATE (p)-[:extractedFrom {sourceVersion:$v}]->(s)",
                params={"url": url, "pid": f"pt-{label}",
                        "v": "h1" if label == "a" else "h2"})

        sweep = source_staleness(proj.g, "https://dup.example/doc")
        assert sweep["complete"] is False, (
            f"one identity on two nodes is not a certified read; got {sweep!r}")
        assert sweep["status"] == "unknown", sweep
        assert "AMBIGUOUS" in " ".join(sweep["errors"]), sweep["errors"]
        assert "https://dup.example/doc" in " ".join(sweep["errors"])
        assert "https://DUP.example/doc" in " ".join(sweep["errors"])
        # ... and it resolves the caller's OWN spelling, not the alphabetically
        # first alias ('D' < 'd'), so the named node is the one asked for.
        assert sweep["source"] == "https://dup.example/doc", sweep["source"]
    finally:
        sdk.close()


def test_the_caller_spelling_wins_over_an_alias_node():
    """Input: the stored node is a differently-cased alias that also matches the
    canonical form; the sweep is asked for the canonical spelling.

    FAILS IF the resolver orders by `s.url` alone: `'E' < 'e'` hands back the
    alias node and its version, so the sweep reports a currency claim about a node
    the caller did not ask for (and can read the wrong `contentHash`).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'https://Case.Example/doc', "
            "canonicalUrl:'https://case.example/doc', sourceKind:'document', "
            "contentHash:'h9', ingestedAt:'2024-01-01'})")
        proj.g.query(
            "CREATE (s:Source {url:'https://case.example/doc', "
            "canonicalUrl:'https://case.example/doc', sourceKind:'document', "
            "contentHash:'h2', ingestedAt:'2024-01-01'})")
        sweep = source_staleness(proj.g, "https://case.example/doc")
        assert sweep["source"] == "https://case.example/doc", (
            f"the caller's spelling must win; got {sweep['source']!r}")
        assert sweep["current"] == "h2", sweep
    finally:
        sdk.close()


def test_all_extracted_from_rows_precede_all_references_rows():
    """Input: one Point (`node='zzz-point'`) whose id SORTS AFTER the derivation
    Event's (`'ev-1'`).

    FAILS IF the sort key is `(node,)` — the documented contract is the 3-tuple
    `(via, node, recorded)`, so the link CLASS groups first and a Point never
    interleaves with a `references` row. With a node-only key the Event would sort
    first here, which is only invisible while every target id happens to order the
    same way as its class.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        proj.g.query("CREATE (p:Point {id:'zzz-point', statement:'z', "
                     "kind:'statement', createdAt:'2024-01-01'})")
        proj.g.query(
            "MATCH (p:Point {id:'zzz-point'}), (s:Source {url:'src.txt'}) "
            "CREATE (p)-[:extractedFrom {sourceVersion:'h1'}]->(s)")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        sweep = source_staleness(proj.g, "src.txt")
        vias = [lk["via"] for lk in sweep["links"]]
        assert vias == sorted(vias), (
            f"rows must group by link class first; got {vias!r}")
        assert vias == ["extractedFrom", "references"], vias
    finally:
        sdk.close()


def test_an_incomplete_sweep_exits_non_zero():
    """Input: `--source` against a graph where one link read fails.

    FAILS IF the operator entry point exits 0 on an incomplete sweep: the JSON
    carries `complete`, but a shell caller sees only the exit status, and an
    incomplete read must not be indistinguishable from a certified answer.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.create_point("statement", "a claim", extractedFrom="src.txt")

        class _Broken:
            def __init__(self, graph):
                self._g = graph

            def query(self, cypher, **kwargs):
                if "extractedFrom" in cypher:
                    raise RuntimeError("refused")
                return self._g.query(cypher, **kwargs)

        real = sdk._get_proj

        class _Proj:
            g = _Broken(proj.g)

        buf = io.StringIO()
        with redirect_stdout(buf):
            import unittest.mock as mock
            with mock.patch.object(type(sdk), "_get_proj", lambda self: _Proj):
                rc = main(["--source", "src.txt", "--db", sdk._db_path])
        assert rc == source_currency.CLI_INCOMPLETE, (
            f"an incomplete sweep must exit non-zero; got {rc}")
        assert "INCOMPLETE" in buf.getvalue(), buf.getvalue()
        del real
    finally:
        sdk.close()


def test_a_link_with_no_anchor_against_a_versioned_source_reads_unknown():
    """Input: the Point is linked BEFORE its source is materialized, so the edge
    carries NO `sourceVersion`, while the Source then gains a real `contentHash`.

    FAILS IF an absent note is filled from the source's own current hash
    (`remembered or current` — one character away). That mutation makes every
    unanchored edge read `current`, i.e. a stale fact certified fresh, and it is
    invisible in every other test here because their unversioned fixtures have
    `contentHash=''`, where the substitution is a no-op. It is public-SDK
    reachable: `create_point(extractedFrom=...)` then `create_source(..., contentHash=...)`
    leaves the edge bare against a versioned source.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        # link first (auto-created stub, no hash), materialize the source after
        p = sdk.create_point("statement", "linked before materialization",
                             extractedFrom="late.txt")
        sdk.create_source("late.txt", "document", contentHash="h1")

        edge = proj.g.query(
            "MATCH (p:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:'late.txt'}) "
            "RETURN r.sourceVersion, s.contentHash", params={"pid": p["id"]}
        ).result_set
        assert edge == [[None, "h1"]], (
            f"fixture precondition: a bare note against a versioned source; got {edge!r}")

        sweep = source_staleness(proj.g, "late.txt")
        assert sweep["current"] == "h1"
        row = {lk["node"]: lk for lk in sweep["links"]}[p["id"]]
        assert row["recorded"] == "", (
            "an absent note must stay absent — filling it from the source's own "
            f"hash fabricates a read; got {row!r}")
        assert row["currency"] == "unknown", row
        assert sweep["status"] == "unknown", sweep["status"]
        assert sweep["re_inference_targets"] == [], (
            "an unfilled pair is not evidence of drift")
        assert sweep["counts"]["unknown"] == 1, sweep["counts"]
    finally:
        sdk.close()


def test_two_source_nodes_sharing_one_spelling_are_ambiguous():
    """Input: TWO `:Source` nodes with the SAME `url`, carrying different versions,
    one of them holding an anchored derivation.

    FAILS IF the ambiguity rule dedupes on the URL SPELLING instead of counting
    matched rows: the sweep would certify whichever node the engine returned first
    while its sibling carried a different version — the same false-fresh the
    distinct-spelling case guards, one level quieter. Reachable by raw Cypher / ops
    scripts (the SDK MERGEs on `url`, so it never mints this itself).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        for hash_ in ("h1", "h2"):
            proj.g.query(
                "CREATE (s:Source {url:'https://same.example/doc', "
                "sourceKind:'document', contentHash:$h, "
                "ingestedAt:'2024-01-01'})", params={"h": hash_})
        proj.g.query("CREATE (p:Point {id:'pt-same', statement:'s', "
                     "kind:'statement', createdAt:'2024-01-01'})")
        proj.g.query(
            "MATCH (p:Point {id:'pt-same'}), (s:Source {url:'https://same.example/doc'}) "
            "CREATE (p)-[:extractedFrom {sourceVersion:'h1'}]->(s)")

        sweep = source_staleness(proj.g, "https://same.example/doc")
        assert sweep["complete"] is False, (
            f"one identity on two nodes is not a certified read; got {sweep!r}")
        assert sweep["status"] == "unknown", sweep["status"]
        assert "AMBIGUOUS" in " ".join(sweep["errors"]), sweep["errors"]
    finally:
        sdk.close()


def test_an_unknown_ref_is_echoed_back_as_the_source():
    """Input: a ref that matches no `:Source` node at all.

    FAILS IF the resolver returns an empty source instead of the caller's own
    spelling: the payload would name `""` as the thing that was swept, so a report
    could not say what it failed to find.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        sweep = source_staleness(sdk._get_proj().g,
                                 "https://ex.com/never-registered")
        assert sweep["source"] == "https://ex.com/never-registered", sweep
        assert sweep["complete"] is True, sweep
        assert sweep["status"] == "unknown", sweep
    finally:
        sdk.close()


# ── cycle-5 findings ─────────────────────────────────────────────────────────

def test_a_whitespace_only_version_is_absent_not_an_operand():
    """Input: a source whose `contentHash` is `'   '` (reachable through the
    public SDK — `_anchor_on_create` nulls only the EMPTY string), and a
    derivation link anchored with it.

    FAILS IF a blank version is treated as an operand: equal to itself it reads
    `current` — a false-fresh on a source that recorded nothing — and after a real
    re-fetch it reads `stale` with a bogus re-inference target. The write side
    already states the rule (*"a blank hash is not a version"*,
    `tests/test_source_version_extractedfrom_5038.py`); the READ applies it through
    `search_engine.version_operand`, the one place a graph value becomes an
    operand.
    """
    from tortoise.search_engine import version_operand

    assert version_operand("   ") == ""
    assert version_operand("\t\n") == ""
    assert version_operand(None) == ""
    assert version_operand(7) == ""
    assert version_operand("h1") == "h1", "a real hash passes through untouched"

    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (ev:Event {id:'ev-ws', eventId:'ev-ws', "
                     "name:'W', eventKind:'meeting'})")
        sdk.create_source("https://ws.example/doc", "document",
                          contentHash="   ")
        sdk.link_source_to_entity("https://ws.example/doc", "ev-ws", "Event")
        anchored = proj.g.query(
            "MATCH (:Source {url:'https://ws.example/doc'})-"
            "[r:references]->(:Event {id:'ev-ws'}) "
            "RETURN r.sourceVersion").result_set
        assert anchored == [["   "]], (
            f"fixture precondition: a blank hash anchors; got {anchored!r}")

        sweep = source_staleness(proj.g, "https://ws.example/doc")
        assert sweep["current"] == "", (
            f"a blank hash is not a version; got {sweep['current']!r}")
        assert sweep["status"] == "unknown", (
            f"a blank version cannot certify freshness; got {sweep['status']!r}")
        row = sweep["links"][0]
        assert row["recorded"] == "" and row["currency"] == "unknown", row
        assert sweep["re_inference_targets"] == []

        # A real re-fetch must not turn the blank into a bogus target either.
        proj.g.query("MATCH (s:Source {url:'https://ws.example/doc'}) "
                     "SET s.contentHash='h2'")
        after = source_staleness(proj.g, "https://ws.example/doc")
        assert after["re_inference_targets"] == [], (
            "no version was ever recorded, so nothing can be behind it; "
            f"got {after['re_inference_targets']!r}")
        assert after["status"] == "unknown", after["status"]

        # ... and the POINT-direction read agrees, because both call the same
        # operand rule (a raw edge is the only way to plant a blank anchor there).
        proj.g.query("CREATE (p:Point {id:'pt-ws', statement:'w', "
                     "kind:'statement', createdAt:'2024-01-01'})")
        proj.g.query(
            "MATCH (p:Point {id:'pt-ws'}), (s:Source {url:'https://ws.example/doc'}) "
            "CREATE (p)-[:extractedFrom {sourceVersion:'   '}]->(s)")
        from tools.source_currency import point_currency
        assert point_currency(proj.g, "pt-ws")["status"] == "unknown", (
            "the two directions must agree about a blank version")
    finally:
        sdk.close()


def test_the_verdict_aggregates_the_references_class_too():
    """Input: a source whose ONLY version-carrying link is an anchored derivation
    Event (no Point at all).

    FAILS IF the aggregate is computed over `extractedFrom` rows only: the sweep
    would answer `unknown` for a source whose derivation is provably stale — a
    false-fresh on the `:Event`/document class #5038's Indicators name — while
    still listing the node in `re_inference_targets`, i.e. a payload that
    contradicts itself.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        _set_hash(proj, "h2")

        sweep = source_staleness(proj.g, "src.txt")
        assert sweep["counts"]["links"] == 1, sweep["counts"]
        assert sweep["links"][0]["via"] == "references", sweep["links"]
        assert sweep["status"] == "stale", (
            "the status must aggregate EVERY link class, not the Points only; "
            f"got {sweep['status']!r} with {sweep['re_inference_targets']!r}")
        assert sweep["re_inference_targets"] == ["ev-1"]
    finally:
        sdk.close()


def test_the_incomplete_exit_code_is_the_contract_not_a_symbol():
    """Input: an incomplete sweep through `main()`.

    FAILS IF the exit contract is asserted only against the constant: setting
    `CLI_INCOMPLETE = 0` would make every incomplete sweep exit 0 — the false
    certification the constant exists for — while `assert rc == CLI_INCOMPLETE`
    still passes. The contract is a NON-ZERO status, and specifically 3 (never
    argparse's 2, never 0).
    """
    assert source_currency.CLI_INCOMPLETE == 3, (
        "0 would certify an incomplete sweep silently; 2 is argparse's usage error")
    assert source_currency.CLI_INCOMPLETE != 0
