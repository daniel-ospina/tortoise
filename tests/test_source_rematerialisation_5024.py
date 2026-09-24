"""#5024 (T6) — a re-fetched `:Source` is a NEW VERSION, not an in-place edit.

THE DEFECT THIS PINS
--------------------
`STORAGE-ARCHITECTURE.md` §3 states the invariant the whole storage design
rests on::

    derived tables  =  replay(the journal)      NOT  recompute(the sources)

`_upsert_source` bumps `updatedAt` / `version` / `contentHash` on a
hash-differing `ON MATCH`, and `create_source` journaled a plain
`SourceCreated` on **every** call — so the write's own record said nothing
about the transition it had just made. Measured on 12 real re-materialisations
before this fix (the numbers below are the observed values, not a model)::

    DIFF updatedAt: live='...57.016262' rebuilt='...57.079810'
    DIFF ingestedAt: live='...56.751580' rebuilt='...57.047168'
    + a NO-OP re-check (identical hash) appended a journal line: 12 -> 13

Both timestamp diffs are the same mechanism: the fold read `_now_iso()` at
**replay** time instead of using the recorded instant. The third is §9.6's
cost bound — *"a version costs three timestamps and a hash, not a copy of the
artifact"* — violated by a re-check that found the same bytes.

THE MODEL HONOURED (`ONTOLOGY.md` v3.15 §4.6 *Versioning*; §9.6)
---------------------------------------------------------------
Identity is `url`; `contentHash` identifies a VERSION; there is **one node per
`url`**, so the graph carries the CURRENT version and *"a version transition
appends a journal record … the prior version's window is a journal fact,
recoverable by replay"*. Therefore:

  * an unchanged hash on a re-check writes **NOTHING** (repeat-safe);
  * a changed hash writes a `SourceVersioned` record carrying
    `previousContentHash` → the prior version stays addressable;
  * a rebuild reproduces the current node **field for field**.

WHAT IS DELIBERATELY *NOT* DONE HERE — the field-by-field split
---------------------------------------------------------------
`:Source` is **not** declared recomputable as a class. `ONTOLOGY.md` §4.7's
own history is the cautionary tale: the per-CLASS test *"append-only — rows are
added, never updated"* was FALSE and the corrected test binds at the
write/field level. The table lives in `docs/durability-posture.md` →
*`:Source` — recorded vs recomputable, field by field*; `contentHash` is
**recorded** (it is the identity anchor, and #3998's absent-raw state is a
third value on the same record, so a re-fetch changes the thing the record is
keyed on).

Run (docker lane):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
      python -m pytest tests/test_source_rematerialisation_5024.py -q
"""
from __future__ import annotations

import itertools
import json

import pytest

from tortoise.sdk import TortoiseSDK

_URL = "https://example.test/doc/rematerialisation"


def _props(sdk, url: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN properties(n)", params={"u": url}
    ).result_set
    assert rows, f"source {url!r} missing from the graph"
    return rows[0][0]


def _records(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _of_type(events_dir, type_) -> list[dict]:
    return [r for r in _records(events_dir) if r.get("type") == type_]


@pytest.fixture
def src(tmp_path):
    """(events_dir, sdk) with the journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "src5024.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield events, sdk
    sdk.close()


# ── 1. acceptance: repeat-safety ─────────────────────────────────────────

def test_an_identical_recheck_journals_nothing(src):
    """Acceptance 1 — an unchanged content hash writes NOTHING.

    FAILS BEFORE: `create_source` emitted `SourceCreated` on every call, so a
    no-op re-check appended a journal line (measured 12 -> 13 on a 12-version
    run). §9.6 bounds a version at "three timestamps and a hash", so the
    re-check must not be a version.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="doc", contentHash="h-0",
                      summary="s-0")
    before = _props(sdk, _URL)
    n_before = len(_records(events))

    # The SAME bytes, re-checked five times.
    for _ in range(5):
        sdk.create_source(_URL, "document", title="doc", contentHash="h-0",
                          summary="s-0")

    assert len(_records(events)) == n_before, (
        "a re-check that found the same content still appended a journal "
        "record — the journal grows on every re-check (§9.6, acceptance 1)")
    after = _props(sdk, _URL)
    assert after == before, "a no-op re-check changed the live node"


def test_a_noop_recheck_does_not_bump_the_version(src):
    """The version ordinal is the cheap witness that nothing happened."""
    events, sdk = src
    sdk.create_source(_URL, "document", title="doc", contentHash="h-0")
    v = _props(sdk, _URL)["version"]
    sdk.create_source(_URL, "document", title="doc", contentHash="h-0")
    assert _props(sdk, _URL)["version"] == v
    assert len(_of_type(events, "SourceVersioned")) == 0


# ── 2. acceptance: the transition record ─────────────────────────────────

def test_a_changed_hash_journals_a_transition_carrying_the_previous_hash(src):
    """Acceptance 2 (first half) — the PRIOR version stays addressable.

    FAILS BEFORE: only plain `SourceCreated` records were emitted, so the
    record sequence never named which hash a version superseded.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    sdk.create_source(_URL, "document", title="v2", contentHash="h-2")

    transitions = _of_type(events, "SourceVersioned")
    assert len(transitions) == 2, (
        f"expected 2 transition records, got {len(transitions)}")
    assert [(t["previousContentHash"], t["contentHash"]) for t in transitions] \
        == [("h-0", "h-1"), ("h-1", "h-2")], (
        "the record must name the version it superseded — without it "
        "'what did this source say before?' is unanswerable")
    # The transition instant is the payload's OWN `updatedAt` — there is no
    # second `at` key to disagree with (one clock, minted once by the producer).
    assert "at" not in transitions[-1]
    assert transitions[-1]["updatedAt"] == _props(sdk, _URL)["updatedAt"]
    # ... and no recorded ordinal: the replay reproduces `version` through the
    # same hash-diff gate the live write used.
    assert "version" not in transitions[-1]
    # The source is CREATED once — the transitions are not creates.
    assert len(_of_type(events, "SourceCreated")) == 1


def test_a_rebuild_reproduces_the_source_field_for_field(src):
    """Acceptance 2 (second half) — the core assertion of this issue.

    FAILS BEFORE on TWO fields (`updatedAt` and `ingestedAt`), both because the
    fold read `_now_iso()` at replay time instead of the recorded instant.
    """
    events, sdk = src
    for i in range(6):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}", summary=f"s-{i}")
    live = _props(sdk, _URL)

    sdk._get_proj().rebuild_all(str(events))
    rebuilt = _props(sdk, _URL)

    # `reliability*` are the #398 query-time CACHE (§3's named pattern: never
    # authoritative, recomputed on rebuild) — excluded by design, not by
    # convenience.
    _CACHE = {"reliability", "reliabilityComponents", "reliability_derived_at"}
    diffs = {
        k: (v, rebuilt.get(k)) for k, v in live.items()
        if k not in _CACHE and rebuilt.get(k) != v
    }
    assert not diffs, (
        f"derived != replay(journal) for the :Source — {diffs} "
        f"(§3's central invariant; T6/#5024)")
    assert rebuilt["version"] == 6
    assert rebuilt["contentHash"] == "h-5"


def test_the_prior_version_is_recoverable_from_the_journal_alone(src):
    """The version HISTORY is a journal fact — read it back without the graph."""
    events, sdk = src
    for i in range(4):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")
    sdk.close()  # the graph is not consulted below

    create = _of_type(events, "SourceCreated")[0]
    transitions = _of_type(events, "SourceVersioned")
    timeline = [create["contentHash"]] + [t["contentHash"] for t in transitions]
    assert timeline == ["h-0", "h-1", "h-2", "h-3"], (
        "the hash timeline must be reconstructible from the journal alone — "
        "this is what makes a prior version addressable after the single "
        ":Source node has moved on")
    # Every transition closes the window it opened, in order.
    for prev, cur in itertools.pairwise(transitions):
        assert cur["previousContentHash"] == prev["contentHash"]


def test_a_rebuild_after_a_transition_keeps_the_new_hash_not_the_old_one(src):
    """The fold must APPLY, not merely tolerate: a wrong branch leaves h-0."""
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    sdk._get_proj().rebuild_all(str(events))
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-1", 2), (
        "the rebuild kept the superseded version")


# ── 3. the transition's extras must survive, like SourceCreated's ─────────

def test_the_transition_carries_the_callers_extras_into_the_rebuild(src):
    """`summary` is a payload extra, not a fixed clause.

    Caught on the first cut of this fold: the transition applied the extras
    LIVE (`_persist_extra_props`) but the fold did not, so a rebuild reverted
    the summary to the CREATION value — trading a timestamp divergence for a
    content one. Extras ride the transition record exactly as they ride
    `SourceCreated`.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="first")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1",
                      summary="second")
    sdk.create_source(_URL, "document", title="v2", contentHash="h-2",
                      summary="third")
    live = _props(sdk, _URL)["summary"]
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL)["summary"] == live == "third"


# ── 4. fail-safe polarity: the record is never suppressed for a real change ──

def test_a_same_hash_write_that_changes_an_extra_is_still_journalled(src):
    """Repeat-safety must not swallow a change that rides the same hash.

    This is the polarity guard for the no-op test: a hash-identical call can
    still carry a new `summary`, and those extras reach the graph through
    `_persist_extra_props` on replay. A no-op test written as "same hash"
    would drop that record and manufacture a live != replay divergence — the
    defect class this lane exists to remove.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="before")
    n = len(_records(events))
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="after")
    assert len(_records(events)) == n + 1, (
        "a same-hash write that changed an extra was silently dropped")
    live = _props(sdk, _URL)["summary"]
    assert live == "after"
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL)["summary"] == live


def test_a_hashless_write_is_still_recorded(src):
    """The JOINT-E2E bundle path carries no hash and must not lose its write."""
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    n = len(_records(events))
    sdk.create_source(_URL, "document", title="v1")   # no contentHash
    assert len(_records(events)) == n + 1
    assert _props(sdk, _URL)["contentHash"] == "h-0", (
        "a hash-less write must preserve the stored hash (the JOINT-E2E "
        "contract)")
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL)["contentHash"] == "h-0"


def test_a_stub_completion_still_emits_a_create(src):
    """A source created WITHOUT a hash, then completed with one.

    `_upsert_source`'s ON MATCH completes a stub (stored hash NULL) from a real
    hash. That is a CREATE in the model's terms (there was no version yet), so
    it must stay a `SourceCreated` — a `SourceVersioned` here would claim a
    prior version that never existed.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="stub")   # no hash
    sdk.create_source(_URL, "document", title="real", contentHash="h-0")
    assert len(_of_type(events, "SourceCreated")) == 2
    assert _of_type(events, "SourceVersioned") == []
    sdk._get_proj().rebuild_all(str(events))
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-0", 2)


# ── 5. back-compat: a pre-#5024 journal still replays ────────────────────

def test_a_legacy_journal_of_sourcecreated_records_still_replays(src, tmp_path):
    """A journal written before `SourceVersioned` existed must still replay.

    The pre-#5024 sequence was N `SourceCreated` records with differing hashes,
    and the fold's conditional ON MATCH reproduced the bump from them. That
    path is KEPT (`coalesce($updatedAt, $now)`), so an old log yields the same
    node it always did — including for the timestamps, which now come from the
    record when it carries them.
    """
    _events, sdk = src
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    lines = []
    for i in range(3):
        lines.append(json.dumps({
            "type": "SourceCreated", "url": _URL, "id": _URL,
            "contentHash": f"h-{i}", "sourceKind": "document",
            "title": f"v{i}", "ingestedAt": f"2026-01-0{i + 1}T00:00:00+00:00",
            "updatedAt": f"2026-02-0{i + 1}T00:00:00+00:00",
        }))
    (legacy / "legacy.jsonl").write_text("\n".join(lines) + "\n")

    sdk._get_proj().rebuild_all(str(legacy))
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-2", 3)
    assert p["ingestedAt"] == "2026-01-01T00:00:00+00:00", (
        "a legacy record's own ingestedAt must be honoured, not overwritten "
        "with the replay clock")
    assert p["updatedAt"] == "2026-02-03T00:00:00+00:00"


def test_a_legacy_record_without_timestamps_falls_back_to_the_clock(src):
    """`coalesce` — the pre-#5024 behaviour for a record that carries neither."""
    events, sdk = src
    legacy = events  # reuse the wired dir
    (legacy / "old.jsonl").write_text(
        json.dumps({"type": "SourceCreated", "url": _URL, "id": _URL,
                    "contentHash": "h-legacy", "sourceKind": "document",
                    "title": "old"}) + "\n")
    sdk._get_proj().rebuild_all(str(legacy))
    p = _props(sdk, _URL)
    assert p["contentHash"] == "h-legacy"
    assert p["ingestedAt"], "a missing ingestedAt must still be stamped"


# ── 6. the transition keys are payload-only (the #330/#3312 class) ────────

def test_the_transition_keys_never_become_node_properties(src):
    """A payload key that lands on only one side is the #330/#3312 class."""
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    live = _props(sdk, _URL)
    assert "previousContentHash" not in live, (
        "`previousContentHash` is transition PAYLOAD metadata and must never "
        "become a node property — it is recorded in the journal, not on the "
        "node")
    assert live["version"] == 2, "`version` IS a declared node property (§4.6)"
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL) == live


def test_the_unknown_type_warning_does_not_fire_for_the_transition(src, caplog):
    """The type is IN the projection vocabulary (the #2901 one-copy rule)."""
    import logging
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        sdk._get_proj().rebuild_all(str(events))
    assert not [r for r in caplog.records
                if "unrecognized event type" in r.getMessage()], (
        "the replay logged the type as unrecognized — add it to "
        "_NO_POINT_FOLD so the warning keeps meaning 'outside the vocabulary'")


# ── 7. identity: a URL variant must not mint a second node on replay ─────

def test_a_url_variant_transition_replays_onto_the_canonical_node(src):
    """#5012 S0b × #5024 — the fold must resolve identity the way the writer does.

    `_upsert_source` runs `resolve_source_key` before its MERGE, so a variant
    spelling (host case, default port) merges into the ONE canonical node. A
    fold keyed on the raw `ev["url"]` would put the replay's transition on a
    SECOND node — a divergence minted by the fold.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    # The same source, spelled with a redundant default port.
    variant = _URL.replace("https://example.test/", "https://example.test:443/")
    sdk.create_source(variant, "document", title="v1", contentHash="h-1")

    live = _props(sdk, _URL)
    n = sdk._get_proj().g.query(
        "MATCH (s:Source) RETURN count(s)").result_set[0][0]
    assert n == 1, f"the live write minted {n} nodes for one canonical source"

    sdk._get_proj().rebuild_all(str(events))
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source) RETURN properties(s)").result_set
    assert len(rows) == 1, (
        f"the replay created {len(rows)} :Source nodes — the fold did not "
        f"resolve the variant to the canonical identity (#5012 S0b)")
    p = rows[0][0]
    assert (p["contentHash"], p["version"]) == ("h-1", 2)
    assert p["updatedAt"], "the transition's recorded instant must be applied"
    # The resolver's alias bookkeeping must replay too. The first cut of the
    # fold kept its OWN clause list and dropped `urlAliases` / `canonicalUrl` /
    # `sourcePath` — three live != replay divergences the review gate caught.
    assert [a for a in p["urlAliases"]] == [a for a in live["urlAliases"]], (
        f"urlAliases diverged: live={live['urlAliases']} rebuilt={p['urlAliases']}")
    assert p["canonicalUrl"] == live["canonicalUrl"]
    assert p == live or {k: (live.get(k), p.get(k))
                         for k in set(live) | set(p)
                         if live.get(k) != p.get(k)} == {}


def test_a_same_hash_write_that_adds_a_FALSY_extra_is_still_journalled(src):
    """The no-op test must not treat `""`/`[]` as "no representation".

    `_persist_extra_props` skips ONLY `None`, so an empty string or empty list
    IS written to the node. An earlier cut of `_source_payload_is_noop` returned
    no-op for a falsy value on a key the node does not carry — measured: the
    live node gained `{'bar': ''}`, **no record was emitted**, and the rebuild
    lost it. That is fail-OPEN toward suppression, the opposite of the declared
    polarity. This is the regression pin.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    for extra in ({"summary": ""}, {"topics": []}):
        n = len(_records(events))
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                          **extra)
        assert len(_records(events)) == n + 1, (
            f"a same-hash write adding a falsy extra {extra} was silently "
            f"dropped — the live node gains it and the rebuild loses it")
        live = _props(sdk, _URL)
        for k, v in extra.items():
            assert live.get(k) == v, f"{k} not written live"
        sdk._get_proj().rebuild_all(str(events))
        assert _props(sdk, _URL) == live, "the falsy extra did not survive replay"


def test_a_transition_carrying_source_path_replays_it(src):
    """`sourcePath` rides the sanctioned `source_path=` route — and the fold.

    Part of the same defect the URL-variant test pins: the first cut of
    `_fold_source_versioned` kept its own clause list, so a `source_path` on a
    transition was written LIVE by `_upsert_source` and dropped on replay.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1",
                      source_path="corpus/notes/a.md")
    live = _props(sdk, _URL)
    assert live["sourcePath"] == "corpus/notes/a.md"
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL) == live, (
        "a source_path carried on the transition was lost on replay")


def test_a_same_hash_write_that_FLIPS_A_TYPE_is_still_journalled(src):
    """`True == 1` in Python; they are DIFFERENT values in the graph.

    Round-2 review finding. `_source_payload_is_noop` compared with plain `==`,
    so a same-hash re-check flipping a stored extra's type was classified a
    no-op: **no record emitted**, the live node changed, and the rebuild
    reverted it. Measured before the fix:

        create_source(..., flag=True); create_source(..., flag=1)
        -> 0 records appended; live flag=1 (int), rebuilt flag=True (bool)

    This is the `isinstance(True, int)` trap the repo already documents in
    `BELIEF_BOOL_PROPS`, and it is a #330/#3312-class live != replay divergence
    introduced by the mechanism meant to prevent one. `is_episodic` is a real
    node property with exactly this shape, so the case is not hypothetical.
    """
    events, sdk = src
    for key in ("flag_extra", "is_episodic"):
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                          **{key: True})
        n = len(_records(events))
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                          **{key: 1})
        assert len(_records(events)) == n + 1, (
            f"a same-hash write flipping {key} from bool to int was treated as "
            f"a no-op — the live node changes and the rebuild reverts it")
        live = _props(sdk, _URL)
        assert not isinstance(live.get(key), bool), (
            f"{key} should now be the int the caller asked for")
        sdk._get_proj().rebuild_all(str(events))
        rebuilt = _props(sdk, _URL)
        assert not isinstance(rebuilt.get(key), bool), (
            f"{key} reverted to a bool on replay — the record did not carry the "
            f"live value")
        assert type(rebuilt.get(key)) is type(live.get(key))


def test_a_same_hash_write_with_an_identical_type_is_still_a_no_op(src):
    """The type-aware comparison must not defeat repeat-safety."""
    events, sdk = src
    payload = {"is_episodic": True, "summary": "s", "topics": ["a", "b"],
               "sourceDate": "2026-01-01"}
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0", **payload)
    n = len(_records(events))
    for _ in range(3):
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                          **payload)
    assert len(_records(events)) == n, (
        "type-aware comparison broke repeat-safety for an identical re-check")


def test_a_hashless_recheck_of_a_stub_journals_nothing(src):
    """The STUB path must be repeat-safe too (round-3 review finding).

    A hashless create stores `contentHash = coalesce($hash,'') = ''`, so
    `not _old_hash` stayed **true forever** on that node and the create arm
    pre-empted the no-op test: measured, 5 identical
    `create_source(url, "document")` calls appended **5** records while the node
    (and its `version`) never moved. Production-reachable — `hosted_api.py`
    re-ingests with `contentHash=anchor` (`None` when no provenance ref carries
    a hash).

    The stub-completion contract is untouched: a stub later completed by a REAL
    hash is still recorded (it is the transition out of the stub).
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="stub")           # no hash
    assert len(_records(events)) == 1
    before = _props(sdk, _URL)
    assert before["contentHash"] == "" and before["version"] == 1

    for _ in range(5):                                          # identical re-checks
        sdk.create_source(_URL, "document", title="stub")
    assert len(_records(events)) == 1, (
        f"a hashless re-check appended {len(_records(events)) - 1} record(s) — "
        f"the create arm pre-empted the no-op test (unbounded journal growth)")
    assert _props(sdk, _URL) == before

    # A stub completed by a REAL hash still records — the #1032 contract.
    sdk.create_source(_URL, "document", title="real", contentHash="h-0")
    assert len(_records(events)) == 2
    assert _props(sdk, _URL)["contentHash"] == "h-0"

    # ... and the whole sequence still replays field for field.
    live = _props(sdk, _URL)
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL) == live


def test_a_hashless_write_that_changes_an_extra_is_still_recorded(src):
    """The stub guard must not swallow a real hashless change (the joint path).

    Note what a hashless write CAN and CANNOT change live: `_upsert_source`
    gates `title`/`contentHash`/`version`/`updatedAt`/`_searchText` behind
    `$hash IS NOT NULL`, so a hashless write leaves every one of those alone —
    but `sourcePath`, the identity aliases and the `_persist_extra_props`
    extras DO change. The extras are therefore the real case where suppressing
    the record would trade log growth for a lost write.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="stub")           # no hash
    n = len(_records(events))
    sdk.create_source(_URL, "document", title="stub", summary="new")
    assert len(_records(events)) == n + 1, (
        "a hashless write that changed an extra was suppressed")
    live = _props(sdk, _URL)
    assert live["summary"] == "new", "the extra was not written live"
    sdk._get_proj().rebuild_all(str(events))
    assert _props(sdk, _URL) == live
