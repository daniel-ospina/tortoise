"""The :Source door may not declare an embedding marker.

⛔ ATTRIBUTION: the door work here is the SDK-side instance of the unguarded-door
class **#11948** records (that issue carries the `create_source` probe as measured
evidence from a second door). **#11858** is the `_emit_event` `id=`/`**extra`
width-stamp half, already on `main`, and it never named this door — the file
keeps its name for the lane's continuity, not as a claim about which issue this
fixes.

The family (`_SERVER_MANAGED_EMBEDDING_MARKERS`) has TWO halves, and the refusal's
reason differs between them: `embedding_width` / `embedding_model` /
`embedding_revision` / `embedding_text_hash` are DERIVED by the writer beside a
vector, while `embedding_verbatim` / `embedding_preserved` / `embeddingText` are
INPUTS the writer READS (`tortoise/embeddings.py:539`, `:550`, `:692`) — a caller
value there forges the reading rather than duplicating a derivation. (Stated per
key because "the family is minted from the values" is false for the second half;
#11858 review cycles 2 and 3 both caught that sentence.)

`create_source` builds its own payload and passes `_skip_sanitize=True`, so the
intake strip in `_sanitize_props` never sees it — and the door writes the node
BEFORE it emits.

Measured at `7e6baffc5d` (origin/main), one `create_source` call per key, no vector
supplied in any of them — i.e. every marker describing NOTHING:

    create_source(url, "web", embedding_width="f16")
      -> OK, journalled {"type": "SourceCreated", "embedding_width": "f16", ...}
         with NO `embedding` key anywhere
    ...and `embedding_verbatim` / `embedding_preserved` / `embedding_model` /
    `embedding_revision` / `embedding_text_hash` / `embeddingText` land identically.

The same measurement over the other TEN public props doors found this one alone:
`create_point`, `create_entity`, `create_document`, `create_object`,
`create_subject`, `create_event`, `update_point` and `update_entity` refuse the name
at intake; `update` and `update_knowledge` refuse on an EXISTING node and silently
no-op on an unknown id. (`create_source` is the eleventh of the eleven `**props`
write doors and the only leak — that is this file's subject, driven by the rows
above, not by the census.) That is why the census below drives every one of them
instead of pinning this door's row alone — the class this file exists for (a rule
stated at one door and silently absent at the next) has been measured twice in this
repo (#11948, #11835 review round 4/5).

⚠️ THE SANCTIONED ROUTE IS `embedding=`, NOT SILENCE. A `:Source` MAY carry a vector
— `embedding` is a declared, caller-writable prop — and this door journals it, with
the width minted truthfully by the same stamp (`f32` for our embedder's output).
So the refusal is not "a source has no vector"; it is "the MARKER is not yours to
write, because only the writer can derive it from the values".
`test_a_caller_vector_still_gets_a_truthfully_minted_marker` pins that route.

⛔ WHY THE GUARD IS AT THE DOOR AND NOT IN `_emit_event`: the refusal must precede
the write. `_emit_event` is called AFTER `_create_entity` has already MERGEd the
`:Source`, so a refusal there would leave a LIVE-ONLY `:Source` — and, because the
whole emission block is wrapped in `except Exception: _logger.warning(…)`
(the `SourceCreated` emit's own guard), the caller would get **no failure at
all**, only a warning, and
`rebuild_all` would then destroy the node silently (the #2296 live-only
divergence). `test_the_refusal_precedes_the_write` is the row that pins the
ordering, and it is the reason this fix is not "one check in the funnel".
"""
from __future__ import annotations

import contextlib
import json

import pytest

from tortoise.sdk import TortoiseSDK

#: One representative value per key. `embedding_width` is the one with a declared
#: vocabulary, so its value is a real width spelling; the rest are flags/stamps
#: whose mere presence is the forgery.
MARKER_VALUES = {
    "embedding_width": "f16",
    "embedding_verbatim": True,
    "embedding_preserved": True,
    "embedding_model": "forged-model",
    "embedding_revision": "forged-revision",
    "embedding_text_hash": "0" * 64,
    "embeddingText": "text this vector was never computed from",
}


def test_the_value_map_covers_the_whole_family():
    """`MARKER_VALUES` must not drift from the constant it drives.

    ⛔ Without this row the map is a hand-maintained duplicate: measured at
    `9459156eb` (#11858 review cycle 4, P3), adding an eighth member to
    `_SERVER_MANAGED_EMBEDDING_MARKERS` alone left **22 passed** — no row noticed,
    while the parametrised docstring still promised "every key of the family".
    That is the same defect class this file exists for, one level in: a family
    enumerated by hand beside the constant that defines it.
    """
    from tortoise.sdk import _SERVER_MANAGED_EMBEDDING_MARKERS

    assert set(MARKER_VALUES) == set(_SERVER_MANAGED_EMBEDDING_MARKERS), (
        "MARKER_VALUES and _SERVER_MANAGED_EMBEDDING_MARKERS disagree: "
        f"undriven={sorted(set(_SERVER_MANAGED_EMBEDDING_MARKERS) - set(MARKER_VALUES))} "
        f"stale={sorted(set(MARKER_VALUES) - set(_SERVER_MANAGED_EMBEDDING_MARKERS))}")


def _journal(tmp_path, name="events.jsonl"):
    journal = tmp_path / name
    sdk = TortoiseSDK(str(tmp_path / "db"), event_log_path=str(journal))
    return sdk, journal


def _records(journal):
    if not journal.exists():
        return []
    return [json.loads(ln) for ln in
            journal.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _source_count(sdk, url):
    result = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE s.url = $url RETURN count(s)",
        {"url": url})
    return result.result_set[0][0]


@pytest.mark.parametrize("marker", sorted(MARKER_VALUES))
def test_create_source_refuses_a_caller_embedding_marker(tmp_path, marker):
    """Every key of the family is refused on the one door that bypassed intake."""
    sdk, _journal_path = _journal(tmp_path)
    url = f"https://example.invalid/11858-{marker}"
    try:
        with pytest.raises(ValueError) as excinfo:
            sdk.create_source(url, "web", **{marker: MARKER_VALUES[marker]})
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert marker in str(excinfo.value), (
        f"the refusal must NAME the key it refused, got {excinfo.value!r}")


def test_the_refusal_precedes_the_write(tmp_path):
    """A refused write leaves NO node and NO journal line.

    ⛔ This is the row that decides WHERE the guard lives. `create_source` MERGEs
    the `:Source` and only then calls `_emit_event` (measured: the emit follows
    `_create_entity` in the same method body), so a guard in the funnel would pass
    this assertion for the record and FAIL it for the node — a live-only mutation
    that the emission block's own `except Exception: _logger.warning(…)`
    (the `SourceCreated` emit's own guard) would not report to the caller at all.
    """
    sdk, journal = _journal(tmp_path)
    url = "https://example.invalid/11858-order"
    try:
        with pytest.raises(ValueError):
            sdk.create_source(url, "web", embedding_width="f16")
        nodes = _source_count(sdk, url)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert nodes == 0, (
        "the refusal landed AFTER the node write — a live-only :Source with no "
        "record to replay is the #2296 divergence this guard is placed to avoid")
    assert _records(journal) == [], (
        f"a refused write journalled something: {_records(journal)!r}")


def test_a_plain_source_still_journals_and_declares_no_marker(tmp_path):
    """The control: the guard must not touch a legitimate source write.

    Without this row a mutation that refuses EVERY :Source write would pass every
    row above.
    """
    sdk, journal = _journal(tmp_path)
    url = "https://example.invalid/11858-control"
    try:
        sdk.create_source(url, "web", contentHash="h1")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    records = _records(journal)
    created = [r for r in records if r.get("type") == "SourceCreated"]
    assert len(created) == 1, f"expected one SourceCreated, got {records!r}"
    leaked = sorted(set(created[0]) & set(MARKER_VALUES))
    assert not leaked, (
        f"a legitimate write declared {leaked} — the writer mints those, so this "
        "is the seam stamping a marker over no vector")


def test_the_family_is_not_blanket_refused_at_the_funnel(tmp_path):
    """`update_point` legitimately rides `embedding_verbatim` into its record.

    ⛔ The false-block row. `update_point` sets `embedding_verbatim` from the
    vector it was HANDED (#5004 round-8) and that value must reach the
    `PointRevised` record — so the family cannot be refused as a blanket rule
    over `_emit_event`'s props, only at prop intake and at the one door that
    bypasses intake.
    """
    sdk, journal = _journal(tmp_path)
    try:
        created = sdk.create_point("statement", "a claim",
                                   source_id="s", span="1-2", quote="q")
        point_id = created["id"] if isinstance(created, dict) else created
        # The store refuses a vector whose width it cannot index (`#5004`
        # round-6), so the control must supply the width THIS store wants —
        # otherwise the vector is CLEARED, the door takes its clear arm, and
        # the row would pass or fail for a reason that has nothing to do with
        # the marker family.
        dim = sdk._get_proj().required_embedding_dim or 2
        sdk.update_point(point_id, embedding=[0.5] * dim)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    revised = [r for r in _records(journal) if r.get("type") == "PointRevised"]
    assert revised, "the update journalled no PointRevised — nothing to assert"
    assert any(r.get("embedding_verbatim") is True for r in revised), (
        "the door's own marker was blocked: it must ride the record beside the "
        "vector it describes")


def test_a_caller_vector_still_gets_a_truthfully_minted_marker(tmp_path):
    """The sanctioned route: hand over the VECTOR, let the writer declare it.

    ⛔ This row exists because the refusal's first draft claimed "a `:Source`
    carries no vector" — false: `embedding` is a declared, caller-writable prop
    and this door journals it. Measured at this head: a 4-float caller vector
    lands on the node AND on the `SourceCreated` record, with `embedding_width`
    minted `f32` by the stamp. So the rule is about WHO derives the marker, not
    about the door having no vector — and this row keeps the message honest: if
    the door ever stops minting for a caller vector, the message's remedy breaks
    and this reds.

    ⚠️ WHAT THIS ROW DOES NOT PIN, stated because the row must not imply it: the
    reader must not read "lands on the node AND on the record" as "the two agree".
    They do for `[0.5]*4` (a value already in shortest-f32 form, so the stamp's
    narrowing is a no-op) and they do NOT for a vector off the f32 text grid:
    measured at this head with `0.001230153371579945`, the node keeps that value's
    raw float64 form (read back as `0.00123015337157995` by the store) while the
    record carries the narrowed `0.0012301534`, so a rebuild of the journal writes
    the record's form back. That divergence is the
    `_emit_event` stamp half's (#11858), it predates this PR, and asserting value
    parity here would fail today — so it is recorded rather than pinned, and this
    row pins only what the door owns: the marker's presence and its derived
    width.
    """
    sdk, journal = _journal(tmp_path)
    try:
        sdk.create_source("https://example.invalid/11858-vector", "web",
                         embedding=[0.5] * 4)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    created = [r for r in _records(journal) if r.get("type") == "SourceCreated"]
    assert created, "the caller's vector was not journalled — nothing to assert"
    record = created[-1]
    assert "embedding" in record, (
        "`embedding=` is documented as the sanctioned route, so the record must "
        "carry the vector the message tells the caller to pass instead")
    assert record.get("embedding_width") == "f32", (
        "the writer must declare the width it DERIVED from this vector, and "
        "`[0.5]*4` is on the float32 grid, so the seam's answer is deterministic: "
        "`f32` (measured at this head). Pinning membership of `(\"f32\", \"f64\")` "
        "instead would let a regression that stops NARROWING a non-verbatim caller "
        "vector — declaring `f64` for everything — leave this row green, which is "
        "the declaration half of #11821 P0")


#: The public `**props` write doors OTHER than `create_source` — the census below.
#: A module constant, not a literal inside the decorator, because
#: `test_the_census_covers_every_public_props_door` has to be able to name the
#: same set it drives: a hand-maintained list beside a hand-maintained dispatch
#: chain is exactly the drift class this file exists to stop.
CENSUS_DOORS = (
    "create_point", "create_entity", "create_document", "create_object",
    "create_subject", "create_event", "update_point", "update_entity",
    "update", "update_knowledge",
)


@pytest.mark.parametrize("door", CENSUS_DOORS)
def test_the_other_props_doors_refuse_the_marker(tmp_path, door):
    """The census: the leak population is this one door, and starts that way.

    Each call passes `embedding_width="f16"` and no vector. A door that stops
    refusing is a new member of #11858's population.

    ⛔ The list is the PUBLIC props doors, and the earlier versions did not drive
    it: four rows, then six, while this file's docstring claimed the WHOLE
    population (#11858 review cycles 2 and 3, P3). Measured now — ELEVEN doors
    take `**props` (13 public methods do; `query` and `paginated_query` are
    READS), and the split is EIGHT + ONE + TWO, not "nine at intake": eight
    refuse at INTAKE (`_sanitize_props`), `create_source` — the ninth — refuses
    over its assembled `ev` before the write (it bypasses intake entirely,
    `_skip_sanitize=True`), and `update` / `update_knowledge` refuse only on an
    EXISTING node, silently NO-OPing on an unknown id (so those two rows create
    a target first — a row without one asserts about a call that did nothing).

    ⛔ `ValueError` + the key named in the message, not `(ValueError, TypeError)`:
    a bare TypeError is exactly what a positional-order regression raises, and
    accepting it is how two earlier rows passed while asserting nothing
    (review cycle 1's finding, cycle 3's latent note).
    """
    sdk, journal = _journal(tmp_path)
    try:
        target = "p-census"
        if door in ("update", "update_knowledge"):
            # Only these two need an existing node; `update_point` and
            # `update_entity` refuse at intake BEFORE any id lookup (measured),
            # so creating a target for them would be dead setup that reads as if
            # the constraint applied to all four.
            created = sdk.create_point("statement", "a census claim",
                                       source_id="s", span="1-2", quote="q")
            target = created["id"] if isinstance(created, dict) else created
        with pytest.raises(ValueError) as excinfo:
            # ⛔ The positionals matter: `create_entity(type, name)` and
            # `create_document(title, documentKind)` are NOT interchangeable with
            # this row's intent. An earlier draft passed them in the wrong order,
            # so each row raised for a reason that had nothing to do with the
            # marker (`unknown type 'e-census'` / `multiple values for 'title'`)
            # and stayed GREEN under the mutant that removes the key from the
            # constant — a false PASS in the file written to stop exactly that.
            if door == "create_point":
                sdk.create_point("statement", "a claim",
                                 source_id="s", span="1-2", quote="q",
                                 embedding_width="f16")
            elif door == "create_entity":
                sdk.create_entity("object", "e-census", embedding_width="f16")
            elif door == "create_object":
                sdk.create_object("o-census", None, embedding_width="f16")
            elif door == "create_subject":
                sdk.create_subject("s-census", None, embedding_width="f16")
            elif door == "create_event":
                sdk.create_event("e-census", "thing", embedding_width="f16")
            elif door == "update_point":
                sdk.update_point("p-census", embedding_width="f16")
            elif door == "update_entity":
                sdk.update_entity("e-census", embedding_width="f16")
            elif door == "update":
                sdk.update(target, embedding_width="f16")
            elif door == "update_knowledge":
                sdk.update_knowledge(target, embedding_width="f16")
            elif door == "create_document":
                sdk.create_document("a census title", "document",
                                    embedding_width="f16")
            else:
                # ⛔ No catch-all call. An `else:` that drove some OTHER door
                # made every unknown name pass: measured at `f063e01ffd`
                # (#11858 review cycle 5, P3), adding `"create_source"` and
                # `"no_such_door_at_all"` to the parametrize list still gave
                # 12 passed — two rows asserting about `create_document` while
                # claiming to test doors that were never called. A row must fail
                # loudly when the list and the dispatch drift apart.
                raise AssertionError(
                    f"census row has no call for door {door!r} — the "
                    "parametrize list and the dispatch have drifted")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert "embedding_width" in str(excinfo.value), (
        f"{door} raised for something other than the marker: {excinfo.value!r}")
    leaked = [r for r in _records(journal) if "embedding_width" in r]
    assert not leaked, f"{door} journalled a caller-declared width: {leaked!r}"


def test_the_sdk_and_api_spellings_agree():
    """Two modules, ONE family — a drift here is how the third door is missed.

    The SDK set is a superset on purpose, and the reason is NOT that `api` lacks an
    intake to guard: `EventAPI.add_point` refuses `embeddingText` through its own
    `_forged_keys` (the set `add_point` subtracts before it emits). The name is absent from
    `_SERVER_MANAGED_EMBEDDING_KEYS` because it is the enriched TEXT — a payload
    field, not a marker (`api.py:58-60`) — and the SDK files it under the marker
    family because the SDK's own `_sanitize_props` must refuse it the same way.
    So: every name the API funnel refuses must also be refused here; the extra name
    is an SDK placement, not an API gap.
    """
    from tortoise.api import _SERVER_MANAGED_EMBEDDING_KEYS
    from tortoise.sdk import _SERVER_MANAGED_EMBEDDING_MARKERS

    missing = sorted(set(_SERVER_MANAGED_EMBEDDING_KEYS)
                     - set(_SERVER_MANAGED_EMBEDDING_MARKERS))
    assert not missing, (
        f"{missing} are refused by the API funnel but not by the SDK's — a "
        "caller can reach them through a props door")


def test_the_census_covers_every_public_props_door():
    """The census list must be the WHOLE `**props` population, not a literal.

    ⛔ Without this row the door half of the file has the same defect the key half
    had before `test_the_value_map_covers_the_whole_family`: `CENSUS_DOORS` is
    hand-written, so a NEW public `**props` write door — `create_note(self, text,
    **props)` forwarding to `_create_entity(..., _skip_sanitize=True)` like this
    door does — would leave every row in this file green while the module
    docstring's claim
    ("`create_source` is the eleventh of the eleven `**props` write doors and the
    only leak") silently became false.

    The precedent is the KEY half, measured at `9459156eb` (#11858 review cycle 4,
    P3): an eighth member added to the constant alone left 22 rows green, because
    `MARKER_VALUES` was a second hand-maintained enumeration — fixed by
    `test_the_value_map_covers_the_whole_family`, which is what this row is for the
    DOOR half. The door-half drift itself was measured at `f063e01ffd` (cycle 5):
    `"no_such_door_at_all"` added to the parametrize list stayed green because the
    dispatch chain's bare `else:` re-ran `create_document`.

    Derived from the class, never from a second list: 13 public methods carry a
    `**kwargs`-style splat, and exactly TWO of them are reads — `query` and
    `paginated_query`, whose splat is the filter bag `**filters` — so 11 write
    doors remain. The derivation keys on the splat KIND, not on its name: a door
    spelled `**kwargs` (or `**extra`, or reached through a `classmethod` or a
    non-function descriptor) is a props door too, and keying on the literal name
    `props` measured GREEN for both of those spellings (#11858 review round 3,
    P1 — the row is only worth having if it sees the door the docstring above
    hypothesises).
    """
    import inspect

    from tortoise.sdk import TortoiseSDK

    #: The two READ doors: their splat is `**filters`, and neither writes.
    read_doors = {"query", "paginated_query"}
    props_doors = {
        name for name, member in inspect.getmembers(TortoiseSDK)
        if not name.startswith("_") and callable(member)
        and any(p.kind is inspect.Parameter.VAR_KEYWORD
                for p in inspect.signature(member).parameters.values())
    } - read_doors
    assert props_doors == set(CENSUS_DOORS) | {"create_source"}, (
        "the `**props` write-door population and this file's census disagree: "
        f"uncensused={sorted(props_doors - set(CENSUS_DOORS) - {'create_source'})} "
        f"stale={sorted(set(CENSUS_DOORS) - props_doors)} — a new door is how "
        "'a rule stated at one door and silently absent at the next' recurs")


@pytest.mark.parametrize("item_extra", [
    {"embedding_width": "f16"},
    # ⛔ The NESTED spelling is parametrised on purpose: the gate reads at the
    # door depth (`_item_prop_at_depth`), and a regression to a top-level
    # `if key in item:` leaves a top-level-only row green while the wrapped form
    # reaches `create_source` and aborts mid-bundle (measured, #11858 review
    # round 6, P1: the mutant gave "25 passed" and one live `:Source`).
    {"props": {"embedding_width": "f16"}},
], ids=["top-level", "nested-props"])
def test_an_ingest_bundle_refuses_the_marker_before_it_writes(tmp_path, item_extra):
    """The bundle route must abort in Phase 1, not after an earlier item wrote.

    ⛔ The door guard alone was NOT enough, and this row is the measurement that
    says so. `ingest` splats a `sources` item into the same door
    (`self.create_source(url, source_kind, **item)`), so before the bundle gate
    existed a marker on the SECOND source raised from the door while the FIRST
    was already committed — measured (#11858 review round 5, P2):
    `ingest({"sources": [ok, {… "embedding_width": "f16"}]})` raised a raw
    `ValueError` and left one live `:Source` behind. Phase 1 is the zero-mutation
    contract (INGEST_CONTRACT.md), so the refusal belongs with the other
    server-managed bundle-item keys, not only at the door.

    Asserts the CONTRACT, not the message: a `BundleValidationError` naming the
    key, and NO `:Source` written by either item.
    """
    from tortoise.exceptions import BundleValidationError

    sdk, journal = _journal(tmp_path)
    first = "https://example.invalid/11858-bundle-ok"
    forged = "https://example.invalid/11858-bundle-forged"
    try:
        with pytest.raises(BundleValidationError) as excinfo:
            sdk.ingest({"sources": [
                {"url": first, "sourceKind": "web"},
                {"url": forged, "sourceKind": "web", **item_extra},
            ]})
        assert "embedding_width" in str(excinfo.value), (
            f"the bundle refused for something other than the marker: "
            f"{excinfo.value!r}")
        proj = sdk._get_proj()
        for url in (first, forged):
            rows = proj.g.query(
                "MATCH (s:Source) WHERE s.url = $u RETURN count(s)",
                {"u": url}).result_set
            assert rows[0][0] == 0, (
                f"ingest wrote {url} despite refusing the bundle — the "
                f"zero-mutation Phase-1 contract is broken")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert not [r for r in _records(journal)
                if r.get("type") == "SourceCreated"], (
        "the refused bundle journalled a SourceCreated record")


def test_the_refusal_precedes_the_adopt_on_touch_write(tmp_path):
    """The guard must sit above the `documentKind` resolve, not just the MERGE.

    ⛔ `nodes == 0` was NOT enough to prove "the refusal precedes every write".
    The `documentKind` demotion check runs `_resolve_source_url` →
    `source_identity.resolve_source_key`, which ADOPTS-ON-TOUCH — it writes
    `s.canonicalUrl` and `s.urlAliases` onto a pre-canonical `:Source`. Measured
    (#11858 review round 6, P3): with the guard below that check, a call carrying
    BOTH `documentKind` and a marker returned `ValueError` while having written
    both properties — a refused call that mutated the graph. Round 7 (P1) measured
    that no row pinned the fix: moving the guard back below the check left all 26
    rows green.

    So this row drives exactly that pair against a pre-canonical node and asserts
    the adoption did NOT happen. It reds if the guard ever moves back down.
    """
    sdk, journal = _journal(tmp_path)
    stored = "https://example.invalid/11858-legacy"
    try:
        proj = sdk._get_proj()
        # A pre-canonical Source: `canonicalUrl` / `urlAliases` unset, which is
        # the state `resolve_source_key` rewrites on touch.
        proj.g.query(
            "CREATE (:Source {url:$u, sourceKind:'document'})", {"u": stored})

        def adoption():
            rows = proj.g.query(
                "MATCH (s:Source {url:$u}) RETURN s.canonicalUrl, s.urlAliases",
                {"u": stored}).result_set
            return tuple(rows[0]) if rows else None

        assert adoption() == (None, None), (
            f"the fixture is not pre-canonical: {adoption()!r}")

        with pytest.raises(ValueError) as excinfo:
            sdk.create_source(f"{stored}?utm_source=x", "web",
                              documentKind="document",
                              embedding_width="f16")
        assert "embedding_width" in str(excinfo.value), (
            f"refused for something other than the marker: {excinfo.value!r}")
        assert adoption() == (None, None), (
            "the refusal landed AFTER the adopt-on-touch write: "
            f"canonicalUrl/urlAliases = {adoption()!r}. `nodes == 0` cannot see "
            "this — the guard must precede the `documentKind` resolve")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert not [r for r in _records(journal)
                if r.get("type") == "SourceCreated"], (
        "the refused call journalled a SourceCreated record")
