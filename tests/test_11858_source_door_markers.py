"""#11858: the :Source door may not declare an embedding marker.

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
`:Source`, so a refusal there would hand the caller a failure for a node that
exists and a journal that has no record of it — the #2296 live-only divergence,
which `rebuild_all` then destroys. `test_the_refusal_precedes_the_write` is the row
that pins the ordering, and it is the reason this fix is not "one check in the
funnel".
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
    with a failure handed to the caller.
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
    assert record.get("embedding_width") in ("f32", "f64"), (
        "the writer must declare the width it derived; a vector with no marker is "
        "the population #11821 P0 closed")


@pytest.mark.parametrize("door", [
    "create_point", "create_entity", "create_document", "create_object",
    "create_subject", "create_event", "update_point", "update_entity",
    "update", "update_knowledge",
])
def test_the_other_props_doors_refuse_the_marker(tmp_path, door):
    """The census: the leak population is this one door, and starts that way.

    Each call passes `embedding_width="f16"` and no vector. A door that stops
    refusing is a new member of #11858's population.

    ⛔ The list is the PUBLIC props doors, and the earlier versions did not drive
    it: four rows, then six, while this file's docstring claimed the WHOLE
    population (#11858 review cycles 2 and 3, P3). Measured now — ELEVEN doors
    take `**props`, and nine of them refuse at intake: the eight named here plus
    `create_source`, whose guard also runs before its write. The remaining TWO —
    `update` and `update_knowledge` — refuse on an EXISTING node and silently
    NO-OP on an unknown id, so those two rows create a target first (a row
    without one asserts about a call that did nothing).

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
            else:
                sdk.create_document("a census title", "document",
                                    embedding_width="f16")
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
    `_forged_keys` (`tortoise/api.py:1187-1195`). The name is absent from
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
