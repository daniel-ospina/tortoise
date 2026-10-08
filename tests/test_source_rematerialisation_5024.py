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

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL)["summary"] == live


def test_a_hashless_retitle_that_changes_nothing_is_repeat_safe(src):
    """The JOINT-E2E bundle path carries no hash — and its retitle is a no-op.

    The hashless write is 'recorded' only when it CHANGES something. Its
    `title` is hash-gated by the writer (`WHEN $hash IS NULL THEN s.title`),
    so a hashless re-check that merely re-sends a different `title` leaves the
    node byte-identical — and (P1-B) a record for every such re-check is the
    unbounded-growth defect, not durability. `contentHash` is preserved (the
    JOINT-E2E contract) and the node still rebuilds field for field.

    A hashless write that DOES change something (a new extra) is still
    recorded — see `test_a_hashless_write_that_changes_an_extra_is_still_recorded`.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    n = len(_records(events))
    before = _props(sdk, _URL)
    sdk.create_source(_URL, "document", title="v1")   # no contentHash
    assert len(_records(events)) == n, (
        "a hashless retitle the writer drops still appended a record — the "
        "journal grows on every re-check (P1-B)")
    assert _props(sdk, _URL) == before, (
        "the hashless write was supposed to leave the node unchanged")
    assert _props(sdk, _URL)["contentHash"] == "h-0", (
        "a hash-less write must preserve the stored hash (the JOINT-E2E "
        "contract)")
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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

    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live


def test_the_unknown_type_warning_does_not_fire_for_the_transition(src, caplog):
    """The type is IN the projection vocabulary (the #2901 one-copy rule)."""
    import logging
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
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
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live


# ══════════════════════════════════════════════════════════════════════════
# 8. review round 6 — P1-A: the record decision must come from the WRITE
# ══════════════════════════════════════════════════════════════════════════

def test_a_competing_write_cannot_suppress_the_transition(src, monkeypatch):
    """P1-A — a hash-changing write must never be classified from a stale read.

    Before this fix `create_source` pre-read `_stored_before` BEFORE the write
    and compared the incoming hash against it, while the writer's `ON MATCH`
    hash gate is evaluated later, at MERGE time. A second writer to the same
    url in that window made the two disagree, and the branch fell through to
    the no-op test — which, against the STALE snapshot, proved "unchanged" —
    so a real transition emitted NOTHING. Reproduced (the reviewer's exact
    scenario): live `h-0`/version 3, journal ending at `h-0 -> h-1`, rebuilt
    `h-1`/version 2 — DIVERGENT.

    The competing write is injected at `_create_entity`, i.e. exactly between
    the old pre-read and the MERGE, and exactly once (the nested write must not
    recurse into the injector again).
    """
    events, sdk = src
    base = {"title": "v0", "contentHash": "h-0", "summary": "s"}
    sdk.create_source(_URL, "document", **base)

    import tortoise.sdk as sdkmod
    orig = sdkmod.TortoiseSDK._create_entity
    seen = {"n": 0}

    def _inject(self, label, id_val, props, event_type, **kw):
        seen["n"] += 1
        if seen["n"] == 1:
            # the competing writer: lands between the pre-read and the MERGE
            sdk.create_source(_URL, "document", title="v0",
                              contentHash="h-1", summary="s")
        return orig(self, label, id_val, props, event_type, **kw)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_create_entity", _inject)
    # A's payload is byte-identical to the stale snapshot, so the OLD no-op
    # test proved "unchanged" and suppressed its own real h-1 -> h-0 write.
    sdk.create_source(_URL, "document", **base)

    transitions = [(t["previousContentHash"], t["contentHash"])
                   for t in _of_type(events, "SourceVersioned")]
    assert transitions == [("h-0", "h-1"), ("h-1", "h-0")], (
        f"the live hash timeline is not fully recorded: {transitions} — a "
        f"transition was suppressed against a stale pre-write snapshot (P1-A)")

    live = _props(sdk, _URL)
    assert (live["contentHash"], live["version"]) == ("h-0", 3)
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _props(sdk, _URL)
    assert (rebuilt["contentHash"], rebuilt["version"]) == ("h-0", 3), (
        "derived != replay(journal) after a competing write — the last "
        "transition was unrecorded (P1-A)")


# ══════════════════════════════════════════════════════════════════════════
# 9. review round 6 — P1-B: the no-op test must MIRROR the writer
# ══════════════════════════════════════════════════════════════════════════

def test_a_url_variant_recheck_is_repeat_safe(src):
    """P1-B — the first variant records its alias; the repeats record NOTHING.

    `_upsert_source` resolves the inbound spelling to the canonical node and
    only ADDS the raw spelling to `urlAliases`. The old no-op test compared the
    payload `url` against the node's url, so every variant re-check looked like
    a change and appended a record (measured `2 -> 3 -> 4`) while the node —
    `urlAliases` stable after the first — did not move.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    variant = _URL.replace("https://example.test/", "https://example.test:443/")
    n = len(_records(events))
    sdk.create_source(variant, "document", title="v0", contentHash="h-0")
    after_first = len(_records(events))
    assert after_first == n + 1, (
        "the FIRST variant write adds the spelling to `urlAliases` — a real "
        "change that MUST be recorded or the rebuilt node loses the alias")
    for _ in range(3):
        sdk.create_source(variant, "document", title="v0", contentHash="h-0")
    assert len(_records(events)) == after_first, (
        f"an identical URL-variant re-check appended "
        f"{len(_records(events)) - after_first} record(s) — the journal grows "
        f"on every re-check (P1-B)")
    live = _props(sdk, _URL)
    assert live["version"] == 1
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live


def test_a_same_hash_retitle_is_repeat_safe(src):
    """P1-B — the writer PRESERVES `title` on a same-hash write, so nothing
    changed and nothing may be recorded (measured `2 -> 3 -> 4` before)."""
    events, sdk = src
    sdk.create_source(_URL, "document", title="A", contentHash="h-0")
    n = len(_records(events))
    for _ in range(3):
        sdk.create_source(_URL, "document", title="B", contentHash="h-0")
    assert len(_records(events)) == n, (
        "a same-hash retitle the writer preserves still appended a record "
        "(P1-B)")
    live = _props(sdk, _URL)
    assert live["title"] == "A", (
        "the writer is supposed to preserve title on a same-hash write")
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live


def test_a_writer_dropped_key_recheck_is_repeat_safe(src):
    """P1-B — a key the writer cannot persist is not a change.

    A `dict` extra is not persistable (`_persist_extra_props`'s own predicate),
    so it never reaches the node. The old no-op test re-derived the persistence
    rule instead of reusing it and appended a record on every re-check
    (`1 -> 2 -> 3 -> 4`) while the node never gained the key.

    #3998 (D30): `content` was the original second exemplar (a `_DOC_RETIRED_KEYS`
    entry the writer drops). It is no longer a *dropped* key on the live write
    path — the SDK payload guard refuses it outright, before the writer's drop
    predicate is consulted — so it is pinned as a refusal here rather than
    silently dropped from the test. The P1-B subject is carried by `meta`, which
    no guard intercepts and which the writer's own predicate still drops.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    n = len(_records(events))
    with pytest.raises(ValueError, match="raw payload"):
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                          content="body")
    for _ in range(3):
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                          meta={"a": 1})
    assert len(_records(events)) == n, (
        f"a re-check carrying a writer-dropped key (or a refused raw-payload "
        f"key) appended {len(_records(events)) - n} record(s) (P1-B)")
    live = _props(sdk, _URL)
    assert "content" not in live and "meta" not in live, (
        "the writer is supposed to drop `content`/non-persistable extras")
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live


# ══════════════════════════════════════════════════════════════════════════
# 9b. review round 6 — P1-B: a falsy non-None hash still opens the gate
# ══════════════════════════════════════════════════════════════════════════

def test_a_falsy_non_none_hash_write_is_not_suppressed(src):
    """`contentHash=""` DOES open the writer's hash gate — so it must record.

    `hosted_api` re-ingests with `contentHash=src.contentHash or ""`, so an
    empty-string hash is production-reachable. The writer's gate is
    `$hash IS NOT NULL AND s.contentHash <> $hash` — `""` is NOT NULL, so the
    node moves to `contentHash=""` and bumps `version`. The versioned arm only
    fires for a TRUTHY hash, so this write reaches the no-op test: a predicate
    that blanket-skipped `contentHash` (a plausible reading of "hash-gated")
    would suppress the record and the rebuild would revert to the old hash.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    n = len(_records(events))
    sdk.create_source(_URL, "document", title="v0", contentHash="")
    assert len(_records(events)) == n + 1, (
        "a `contentHash=''` write changed the node but appended no record")
    live = _props(sdk, _URL)
    assert (live["contentHash"], live["version"]) == ("", 2)
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live, (
        "the `contentHash=''` transition did not survive replay")


# ══════════════════════════════════════════════════════════════════════════
# 10. review round 6 — P2-B: no pre-read to fail, no wasted round trip
# ══════════════════════════════════════════════════════════════════════════

_PRE_READ_SQL = "MATCH (s:Source {url: $u}) RETURN properties(s)"


def test_a_failed_pre_read_cannot_downgrade_a_transition_to_a_create(
        src, monkeypatch):
    """P2-B — the old pre-read swallowed its own failure into the CREATE arm.

    `_stored_before = None` on ANY exception took the create arm, so a
    hash-CHANGING write journalled `SourceCreated` and silently dropped
    `previousContentHash`. The pre-state now comes from the write statement, so
    there is no separate read to fail. The injector raises on the REMOVED pre-read
    query only; the fix never issues it.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")

    g = sdk._get_proj().g
    orig_query = type(g).query

    def _boom(self, query, *a, **kw):
        if _PRE_READ_SQL in query:
            raise RuntimeError("injected pre-read failure")
        return orig_query(self, query, *a, **kw)

    monkeypatch.setattr(type(g), "query", _boom)
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")

    assert len(_of_type(events, "SourceCreated")) == 1, (
        "a failed pre-read downgraded the transition to SourceCreated (P2-B)")
    assert [(t["previousContentHash"], t["contentHash"])
            for t in _of_type(events, "SourceVersioned")] == [("h-0", "h-1")]
    live = _props(sdk, _URL)
    assert (live["contentHash"], live["version"]) == ("h-1", 2)
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL) == live


def test_a_journal_less_sdk_does_not_pre_read_the_source(tmp_path, monkeypatch):
    """P2-B — no event log means nothing to decide, so there is no pre-read.

    The removed pre-read (plus its internal `resolve_source_key`) ran on EVERY
    `create_source` even when `_event_log_path is None`, where `_emit_event` is
    a no-op — a new unconditional round trip on the hottest ingest path.
    """
    sdk = TortoiseSDK(str(tmp_path / "nojournal.db"))
    try:
        g = sdk._get_proj().g
        seen: list[str] = []
        orig_query = type(g).query

        def _rec(self, query, *a, **kw):
            seen.append(query)
            return orig_query(self, query, *a, **kw)

        monkeypatch.setattr(type(g), "query", _rec)
        sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
        offenders = [q for q in seen if _PRE_READ_SQL in q]
        assert not offenders, (
            f"a journal-less SDK issued the pre-write read {len(offenders)} "
            f"time(s) (P2-B): {offenders}")
    finally:
        sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 11. review round 6 — P2-A: a torn transition must not block recovery
# ══════════════════════════════════════════════════════════════════════════

def test_a_torn_source_versioned_tail_is_tolerated(src, tmp_path):
    """P2-A — a crash mid-append of a transition must not refuse recovery.

    `SourceVersioned` folds through the IDENTICAL MERGE + SET as
    `SourceCreated` (the type pre-#5024 used for this same transition, whose
    torn tail WAS tolerated), so it belongs in
    `TORN_TAIL_HARMLESS_EVENT_TYPES`. Absent, a torn transition raised
    `TornTailResurrectionError`, blocking `rebuild_all`/`recover_from_log`/
    `backup.restore` — a silent tightening of the recovery posture.
    """
    _events, sdk = src
    legacy = tmp_path / "torn"
    legacy.mkdir()
    complete = [
        json.dumps({"type": "SourceCreated", "url": _URL, "id": _URL,
                    "contentHash": "h-0", "sourceKind": "document",
                    "title": "v0"}),
        json.dumps({"type": "SourceVersioned", "url": _URL, "id": _URL,
                    "contentHash": "h-1", "previousContentHash": "h-0",
                    "sourceKind": "document", "title": "v1"}),
    ]
    torn = ('{"event_id": "01J", "ts": "2026-01-01T00:00:00+00:00", '
            f'"type": "SourceVersioned", "url": "{_URL}", '
            '"contentHash": "h-2", "previousCont')
    (legacy / "torn.jsonl").write_text("\n".join(complete) + "\n" + torn)

    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-1", 2), (
        "the torn transition must be dropped, leaving the last COMPLETE record")


def test_the_transition_documentation_names_the_key_the_record_carries():
    """P2-C — the documentation must name `updatedAt`, not a phantom `at`.

    `docs/event-catalog.md` and the record itself both carry the instant as
    `updatedAt`; the state comment beside the `updatedAt` CASE claimed the
    record "carries its own `at`", a key that does not exist (the issue's own
    test asserts `"at" not in transitions[-1]`). The fold's docstring must say
    the same thing the payload does.
    """
    from pathlib import Path

    from tortoise.projection import entities as _ent
    src = Path(_ent.__file__).read_text()
    assert "carries its own `at`" not in src, (
        "the state comment still claims a phantom `at` key (P2-C)")
    assert "which carries its own `updatedAt`" in src, (
        "the state comment must name the key the record actually carries")
    doc = _ent._EntityHandlers._fold_source_versioned.__doc__ or ""
    assert "updatedAt" in doc, "the docstring must name the recorded key"


# ══════════════════════════════════════════════════════════════════════════
# 8b. review round 2 — P2-2: the pre-state is PER CALL, not a shared slot
# ══════════════════════════════════════════════════════════════════════════

def test_a_concurrent_write_to_another_url_cannot_swap_the_pre_state(
        src, monkeypatch):
    """P2-2 — the captured pre-state must not ride a projection-level slot.

    One SDK shared by two threads takes a DIFFERENT per-url lock per url
    (`_source_merge_lock_for`), so a write to url B can interleave with a write
    to url A. While the pre-write state rode `proj._source_merge_result`, B's
    write overwrote that slot AFTER A's MERGE had captured A's state and BEFORE
    A's `create_source` read it — so A classified its own h-0 -> h-1 transition
    against B's pre-state and journalled a plain `SourceCreated`, losing the
    `previousContentHash` that keeps the prior version addressable.

    A real two-thread race is not required: the injector performs B's write at
    exactly the interleaving point (inside A's `_create_entity`, after A's
    `apply`), which is the faithful deterministic injection of the swap.
    """
    events, sdk = src
    other = "https://example.test/doc/other"
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")

    import tortoise.sdk as sdkmod
    orig = sdkmod.TortoiseSDK._create_entity
    state = {"injected": False}

    def _inject(self, label, id_val, props, event_type, **kw):
        out = orig(self, label, id_val, props, event_type, **kw)
        if (label == "Source" and not state["injected"]
                and props.get("url") == _URL):
            # The other thread's write to a DIFFERENT url lands here — after
            # A's MERGE captured its pre-state, before A reads it.
            state["injected"] = True
            sdk.create_source(other, "document", title="B", contentHash="h-1")
        return out

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_create_entity", _inject)
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")

    transitions = [(t["previousContentHash"], t["contentHash"])
                   for t in _of_type(events, "SourceVersioned")
                   if t.get("url") == _URL]
    assert transitions == [("h-0", "h-1")], (
        f"A's transition was classified against another url's pre-state: "
        f"{transitions} — the pre-state is per call, not a shared slot (P2-2)")

    live = _props(sdk, _URL)
    assert (live["contentHash"], live["version"]) == ("h-1", 2)
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _props(sdk, _URL)
    assert (rebuilt["contentHash"], rebuilt["version"]) == ("h-1", 2)


# ══════════════════════════════════════════════════════════════════════════
# 12. review round 2 — P3-1: a real alias addition must be recorded
# ══════════════════════════════════════════════════════════════════════════

def test_a_node_without_aliases_records_the_alias_the_writer_adds(src):
    """P3-1 — `urlAliases` is NOT create-only, so a same-url write can change it.

    `_upsert_source`'s ON MATCH appends the raw spelling to `urlAliases`
    whenever it is absent — including when the incoming `url` EQUALS the
    node's `url` and the node carries no aliases. That is a partially-adopted
    legacy graph: `canonicalUrl` is already set (so the resolver's
    adopt-on-touch backfill, which only fires when `canonicalUrl IS NULL`, is
    skipped) while `urlAliases` was never written. The no-op test skipped
    `urlAliases` as "create-only" AND required `v != stored["url"]` before
    treating a url as a change, so this write's real alias addition was
    journalled by nothing — a rebuild lost it (fail-open toward suppression,
    the defect class this lane exists to remove).
    """
    events, sdk = src
    # A pre-canonical node: canonical identity adopted, alias list never written.
    sdk._get_proj().g.query(
        "CREATE (:Source {url:$u, canonicalUrl:$cu, contentHash:'h-0', version:1})",
        params={"u": _URL, "cu": _URL})
    n = len(_records(events))

    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")

    live = _props(sdk, _URL)
    assert live["urlAliases"] == [_URL], (
        "the writer appends the raw spelling to `urlAliases` on this write")
    assert len(_records(events)) == n + 1, (
        f"the alias the writer added live was not recorded "
        f"({len(_records(events)) - n} records) — a rebuild loses it (P3-1)")
