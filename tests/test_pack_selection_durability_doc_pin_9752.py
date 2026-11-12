"""#9752 — `docs/durability-posture.md` must keep the `:PackSelection` declaration.

The class `:PackSelection` is the one node per graph whose **presence** says "this
graph's pack set was DELIBERATELY configured". #9752 makes a `rebuild_all` carry
it through the wipe as a `pack_selection` sidecar section, and the canonical
statement of what a rebuild preserves is `docs/durability-posture.md` — which is
what an operator reads, and what the next lane editing the durability matrix
will change.

⛔ WHY THIS PIN LIVES IN ITS OWN FILE, measured (review P2 on #11839): it
originally sat at the end of `tests/test_rebuild_pack_selection_marker_9752.py`,
which is GRAPH-TOUCHING and therefore registered only under the `api` and `core`
surfaces. The guarded artifact is under `docs/`, and a docs-only diff selects
**no surface at all** — measured at that head:

    $ echo docs/durability-posture.md | \
        python3 tools/ci_selection.py --changed-files - --event pull_request
    {"surfaces": [], "full": false, "test_files": [...87 files...]}
    # test_rebuild_pack_selection_marker_9752.py ABSENT from test_files

so the guard ran ZERO times on exactly the change class that can remove or
re-word the declaration. This is the recorded shape of `#11810` review r7 (a
pin `test_capture_install.py` never ran on `docs/research/...`) and of
`test_10000_cache_projection_doc_pin.py`: a `docs/` pin must be registered in
`tier1`, which `select()` unions into EVERY PR selection, so a docs-only edit
still grades it. This file is hermetic — one file read, no fixture, no DB, no
network, no model — which is what lets it ride the URI-less `tier1` legs.
"""
from __future__ import annotations

from pathlib import Path


def test_marker_declared_in_the_durability_posture():
    """T2: the class has a DECLARED disposition, not an inference.

    FAILING VALUE: `:PackSelection` appears in the `preserved-sections` block
    with its section, its restore rule and #9752, in the operator audit query,
    and NOT in `unenrolled`.

    REACHABLE: `docs/durability-posture.md` is the repo's canonical durability
    authority and the blocks are literals in it.
    """
    doc = Path(__file__).resolve().parent.parent / "docs" / "durability-posture.md"
    text = doc.read_text()

    def block(name: str) -> str:
        start = text.index(f"<!-- config-registry:{name} -->")
        end = text.index("<!-- config-registry:end -->", start)
        return text[start:end]

    preserved = block("preserved-sections")
    # ⛔ Scope the assertions to the `:PackSelection` ENTRY, not the 7.4 KB
    # block (review P3 on #11839): `removed` and `#9752` each occur twice in
    # this entry's own prose, so a block-wide substring survives deletion of the
    # very clause it names. The three tokens below occur ONCE in the entry, so
    # deleting the declaration, the citation or the consequence reds its own
    # assertion.
    start = preserved.index("- `:PackSelection`")
    rest = preserved[start + 1:]
    nxt = rest.find("\n- `")
    entry = rest if nxt == -1 else rest[:nxt]
    assert "`:PackSelection`" in entry, (
        "the section-preserved block must name :PackSelection")
    assert "`pack_selection`" in entry, (
        "the entry must name the carrier section")
    assert "**#9752**" in entry, (
        "the declaration must cite #9752 next to the class, or a reader cannot "
        "trace the section back to its decision")
    assert "re-admitted" in entry, (
        "the declaration must state the load-bearing consequence — an absent "
        "marker re-admits a pack the admin removed")
    assert "PackSelection" not in block("unenrolled"), (
        # ⛔ NEGATIVE CONTROL, and its message claims only that: this guards the
        # "declared twice" shape, it does NOT record a move. Measured against
        # `origin/main`, `:PackSelection` never appeared in this block either —
        # so nothing was removed from it by #9752, and a message saying "it is
        # no longer 'unenrolled'" would describe a state change that did not
        # happen (the class was never an unenrolled registry row; it is a
        # SECTION, like `:TeamMeta`). The clause reds if a future edit adds it
        # here as well as to `preserved-sections`.
        "the class must not be declared in BOTH blocks — it is a preserved "
        "SECTION, never an unenrolled registry row (#9752)")
    assert ":PackSelection" in block("audit-query"), (
        "the operator audit must enumerate the class, or it under-reports what "
        "survived a rebuild")
