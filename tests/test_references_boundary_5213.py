"""#5213 — the NON-provenance writers of a derivation ``references`` link.

`#5199` (option A, owner-approved 2026-09-25) anchors a **derivation** edge
(``Source → Event|Document``) with an optional ``sourceVersion``, stamped at link
time by the provenance writers. `STORAGE-ARCHITECTURE.md` §9.6 states that as a
**pipeline guarantee, not a graph invariant**, and names the two writers that
bypass it: the generic escape hatch ``create_edge`` (public SDK, exposed as the
MCP tool ``tortoise_create_edge``) and ``graph-scripts/backfill_references.py``.

The **decision for those two is option 3 — document, do not re-route** (the
alternative silently changes what a public generic API writes, or rejects a
predicate existing callers may pass). This file is what makes the record a
MEASURED fact instead of prose: it pins the boundary's behaviour, its mechanism,
and its reachability — so re-opening the decision (option 1/2) is a deliberate
change with a test to re-point, not a silent drift.

``unknown`` (never ``current``) is the honest read for an edge minted here — the
anchor's whole point is that "no recorded version" must not render as "up to
date" (``tortoise/search_engine.py::currency_status``).

Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK

_ROOT = Path(__file__).resolve().parent.parent


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


def _fixture(sdk: TortoiseSDK, *, content_hash: str = "h1"):
    """Source(url='src.txt') + an Event and a document-bearing :Source.

    The document carries an ``id`` because ``create_edge`` resolves its TARGET by
    ``id``/``eventId`` only (a url-only ``:Source`` is deliberately not a valid
    target) — so this is the id-keyed document shape, which IS reachable.
    """
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
    proj.g.query(
        "CREATE (d:Source {id:'doc-1', url:'doc.txt', sourceKind:'document', "
        "documentKind:'meeting', title:'doc.txt', contentHash:$h, "
        "ingestedAt:'2024-01-01'})",
        params={"h": content_hash},
    )
    return proj


def _anchor_exists(proj, key: str, value: str) -> bool:
    rows = proj.g.query(
        f"MATCH (:Source {{url:'src.txt'}})-[r:references]->(e {{id:$v}}) "
        "WHERE e:Event OR e:Source RETURN exists(r.sourceVersion)",
        params={"v": value},
    ).result_set
    assert len(rows) == 1, f"expected exactly one (source, {key}={value!r}) link"
    return rows[0][0]


# ── the behaviour: the escape hatch writes a BARE edge ─────────────────────


def test_create_edge_mints_an_unanchored_derivation_link():
    """Input: Source(contentHash='h1') → Event, and → a document-bearing :Source,
    both linked through the PUBLIC ``sdk.create_edge('references', ...)``.

    FAILS IF ``create_edge`` starts stamping ``r.sourceVersion`` (that is option 1
    of #5213 — a deliberate decision reopening: update §9.6 and this pin in the
    same change, and handle the pre-existing-edge case below, or the fix is
    partial), OR if the edge is not created at all (the escape hatch silently
    stopping to reach a derivation target is the other way this boundary moves).
    """
    from tortoise.search_engine import currency_status

    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk)
        for target_id in ("ev-1", "doc-1"):
            res = sdk.create_edge("references", "src.txt", target_id)
            assert res["edge"] == {
                "relation": "references", "from": "src.txt", "to": target_id,
            }
            assert res["created"] is True, f"no edge minted to {target_id!r}"
            assert _anchor_exists(proj, "id", target_id) is False, (
                f"the escape hatch stamped a sourceVersion on the "
                f"{target_id!r} link — the option-3 boundary is gone"
            )
        # …and the read is HONEST, not a false `current`: absent is `unknown`.
        assert currency_status("", "h1") == "unknown", (
            "an absent anchor must never render as `current`"
        )
    finally:
        sdk.close()


def test_a_bare_escape_hatch_edge_pre_empts_the_provenance_anchor():
    """Input: ``create_edge`` links a pair FIRST, then the provenance writer
    (``link_source_to_event``) links the SAME pair.

    FAILS IF the bare edge is silently repaired — i.e. if the stamp stops being
    ``ON CREATE``-only (``_DERIVATION_ANCHOR_SET`` / ``_anchor_on_create``). That
    change is not a fix for #5213: ``ON CREATE`` is load-bearing so a source-only
    re-poll cannot advance a recorded version and erase the staleness the anchor
    exists to expose (#5199 §9.6 KNOWN LIMITATION). The reverse order is asserted
    in the same test as the control, because without it a broken anchor that never
    stamps at all would also pass the first half.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk)

        # (1) escape hatch first → the provenance writer cannot repair it.
        sdk.create_edge("references", "src.txt", "ev-1")
        proj.link_source_to_event("src.txt", "ev-1")
        assert _anchor_exists(proj, "id", "ev-1") is False, (
            "the provenance writer anchored an edge that already existed — the "
            "ON CREATE-only guarantee has been changed; re-read §9.6 and #5213 "
            "before touching it"
        )
    finally:
        sdk.close()

    # (2) control: provenance writer first → the anchor lands and SURVIVES a
    # later create_edge MERGE over the same pair.
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk)
        proj.link_source_to_event("src.txt", "ev-1")
        assert _anchor_exists(proj, "id", "ev-1") is True, (
            "the provenance writer did not anchor a new derivation link"
        )
        sdk.create_edge("references", "src.txt", "ev-1")
        assert _anchor_exists(proj, "id", "ev-1") is True, (
            "an idempotent re-MERGE through the escape hatch dropped an existing "
            "anchor — the generic path must be property-free, never destructive"
        )
    finally:
        sdk.close()


def test_the_escape_hatch_offers_no_way_to_supply_the_anchor():
    """Input: the three ``create_edge`` entry points, introspected.

    FAILS IF any of them grows a property/``**kwargs`` parameter: §9.6's boundary
    statement says a caller minting a derivation edge through the escape hatch
    leaves the anchor absent, and the REMEDY that phrasing implies (\"supply the
    property yourself\") is only honest while no such parameter exists. If one
    appears, the writer's docstring and §9.6 must say what it does — and the
    option-3 record must be revisited rather than quietly amended.
    """
    from tortoise import mcp_server
    from tortoise.projection.edges import _EdgeHandlers

    entry_points = {
        "TortoiseSDK.create_edge": TortoiseSDK.create_edge,
        "mcp_server.tortoise_create_edge": mcp_server.tortoise_create_edge,
        "EdgeHandlers.create_edge": _EdgeHandlers.create_edge,
    }
    allowed = {"self", "relation", "predicate", "from_id", "to_id",
               "source_id", "target_id"}
    for name, fn in entry_points.items():
        params = inspect.signature(fn).parameters
        extra = [p for p in params
                 if p not in allowed
                 or params[p].kind in (inspect.Parameter.VAR_KEYWORD,
                                       inspect.Parameter.VAR_POSITIONAL)]
        assert not extra, (
            f"{name} grew {extra!r} — if it now accepts the anchor (or any "
            f"property), the \"the caller cannot supply it\" half of §9.6 and "
            f"#5213's option-3 record are stale"
        )
        assert len(params) == (3 if "self" not in params else 4), (
            f"{name} signature changed: {list(params)!r}"
        )


def test_backfill_references_merges_a_bare_reference():
    """Input: the second writer named in §9.6, read as source.

    FAILS IF its ``_MERGE_REFERENCE`` gains the anchor suffix (option 1 for this
    writer — same re-pointing duty as above), or if its derivation rows stop being
    reachable (the ``aboutEvent`` / ``aboutDocument`` mappings are what make it a
    derivation writer at all). Structural on purpose: the script's whole job is a
    MERGE against a live graph, and the property set IS the fact being pinned.
    """
    source = (_ROOT / "graph-scripts" / "backfill_references.py").read_text(
        encoding="utf-8")
    merge = re.search(r'_MERGE_REFERENCE = """(.*?)"""', source, re.S)
    assert merge, "the script's _MERGE_REFERENCE fragment moved — re-point this pin"
    statement = merge.group(1)
    assert "MERGE (s)-[:references]->(e)" in statement, statement
    assert "sourceVersion" not in statement, (
        "the ops script now stamps the anchor — §9.6 names it as a bypassing "
        "writer and #5213 recorded option 3; if this is deliberate, the record "
        "and this pin change together"
    )
    # The two derivation rows (the identity rows aboutSubject/aboutObject are
    # deliberately property-free either way), so this pin cannot be satisfied by
    # a script that only writes mention links.
    assert '"aboutEvent": "Event"' in source, "the Event derivation row is gone"
    assert '"aboutDocument": "Source"' in source, "the document derivation row is gone"


def test_storage_architecture_still_records_the_boundary():
    """Input: ``docs/architecture/STORAGE-ARCHITECTURE.md`` §9.6, read as text.

    FAILS IF the boundary statement is removed or reworded out of the section.
    This is the DELIVERABLE of #5213 (option 3 is *document only*), so a pin on
    the record itself is the one thing that keeps the decision from being tidied
    away — the code pins above cannot see a deletion. Deliberately narrow: it
    asserts the two load-bearing claims and the two writer names, not prose.
    """
    doc = (_ROOT / "docs/architecture/STORAGE-ARCHITECTURE.md").read_text(
        encoding="utf-8")
    start = doc.index("### 9.6 ")
    end = doc.index("\n## 10.", start)
    section = doc[start:end]
    assert "NOT a graph invariant" in section, (
        "§9.6 no longer states the anchor is a pipeline guarantee rather than a "
        "graph invariant — the boundary #5213 tracks is unrecorded"
    )
    assert "create_edge" in section and "backfill_references.py" in section, (
        "§9.6 no longer names BOTH bypassing writers (#5213)"
    )
    assert "#5213" in section, (
        "§9.6 no longer points at #5213 — the boundary's owning issue"
    )
