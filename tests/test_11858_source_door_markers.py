"""#11858: the :Source door may not declare an embedding marker.

The marker family (`_SERVER_MANAGED_EMBEDDING_MARKERS`) is minted by
`stamp_journal_embedding` from the values that ride WITH the record. `create_source`
builds its own payload and passes `_skip_sanitize=True`, so the intake strip in
`_sanitize_props` never sees it — and the door writes the node BEFORE it emits.

Measured at `7e6baffc5d` (origin/main), one `create_source` call per key, no vector
supplied in any of them — i.e. every marker describing NOTHING:

    create_source(url, "web", embedding_width="f16")
      -> OK, journalled {"type": "SourceCreated", "embedding_width": "f16", ...}
         with NO `embedding` key anywhere
    ...and `embedding_verbatim` / `embedding_preserved` / `embedding_model` /
    `embedding_revision` / `embedding_text_hash` / `embeddingText` land identically.

The same measurement over the other public props doors found this one alone:
`create_point` and `create_entity` refuse the name at intake, `update_point` refuses
it, `create_document` does not accept it. That is why the census below drives the
WHOLE population instead of pinning this door's row alone — the class this file
exists for (a rule stated at one door and silently absent at the next) has been
measured twice in this repo (#11948, #11835 review round 4/5).

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


@pytest.mark.parametrize("door", ["create_point", "create_entity",
                                  "update_point", "create_document"])
def test_the_other_props_doors_refuse_the_marker(tmp_path, door):
    """The census: the leak population is this one door, and starts that way.

    Each call passes `embedding_width="f16"` and no vector. A door that stops
    refusing is a new member of #11858's population.
    """
    sdk, journal = _journal(tmp_path)
    try:
        with pytest.raises((ValueError, TypeError)):
            if door == "create_point":
                sdk.create_point("statement", "a claim",
                                 source_id="s", span="1-2", quote="q",
                                 embedding_width="f16")
            elif door == "create_entity":
                sdk.create_entity("e-census", "thing", embedding_width="f16")
            elif door == "update_point":
                sdk.update_point("p-census", embedding_width="f16")
            else:
                sdk.create_document("https://example.invalid/11858-census",
                                    title="t", embedding_width="f16")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    leaked = [r for r in _records(journal) if "embedding_width" in r]
    assert not leaked, f"{door} journalled a caller-declared width: {leaked!r}"


def test_the_sdk_and_api_spellings_agree():
    """Two modules, ONE family — a drift here is how the third door is missed.

    The SDK set is a superset on purpose: `embeddingText` is a writer-minted
    identity key that `tortoise.api` does not name (it has no `:Source`/`:_point`
    intake of its own to guard). Every name the API funnel refuses must also be
    refused here.
    """
    from tortoise.api import _SERVER_MANAGED_EMBEDDING_KEYS
    from tortoise.sdk import _SERVER_MANAGED_EMBEDDING_MARKERS

    missing = sorted(set(_SERVER_MANAGED_EMBEDDING_KEYS)
                     - set(_SERVER_MANAGED_EMBEDDING_MARKERS))
    assert not missing, (
        f"{missing} are refused by the API funnel but not by the SDK's — a "
        "caller can reach them through a props door")
