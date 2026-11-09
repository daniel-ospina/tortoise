"""#10852 review — the projection doc must not re-arm the "re-capture heals" premise.

WHY A DOC NEEDS A TEST. This PR measured that a pre-#10000 session `:Source` is
**NOT** repaired by a re-capture: `_materialize_session_source`'s only production
caller sits behind `capture_session`'s fresh-capture gate
(`if not session_existed or (retry_failed_capture and not no_provider)`), so
re-capturing an already-captured session id is the `#2104`/`#1727` no-op replay and
the stale `summary`/`_searchText` pair survives. FOUR artifacts said the opposite
and were corrected — the method docstring, the inline comment above the
`_searchText` SET, a test that was renamed off the claim, and the PR body. Then
`#10852`'s own diff added a FIFTH:
`docs/epics/2026-10-10-7879-postgres-record/06-cache-projection.md` told an
operator the legacy population repairs itself *"until it is re-captured (the
capture now overwrites the pair with the lexicon)"*. That is the actionable form
of the false premise: a reader who believes it skips the route that DOES clear
it — a retry of a prior FAILED capture, which re-runs the mint for the SAME
`url` — and the legacy session stays un-indexed on the recovery door
indefinitely.

A SECOND review round (`#10852` @ `3466bc1ce`, P3) found the correction itself
carried the same defect in miniature: the list it published named THREE routes,
and for the population under discussion — the node that already carries the
pair — two of them clear nothing. A capture under a NEW `session_id` MERGEs a
DIFFERENT node (`url = session:<id>`), so the old node keeps its pair; an
INCREMENTAL record-sourced re-projection is a PREVENTION route (its write is
additive, `MERGE … SET n += $props`, and `summary` is declined on that door), so
it only ever produces a prose-free node where none existed. The guard below
pins every route that really does clear the pair and keeps the wrong two out of
both docs, because a repair list that is wrong for two of its three members is
the same "this population sorts itself out" misdirection the retraction exists
to remove.

⚠️ THE CENSUS IS THREE, not one (#11184 review, cycle 10 — measured): `#11070`'s
repair SHIPPED in `98df3bd4cf` (`tortoise/session_source_prose_migration.py`,
operator path `graph-scripts/11070_repair_session_source_prose.py`), and it
repairs or clears the pair IN PLACE from the record without a wipe; and a full
`record-restore` wipes unconditionally (`_wipe_all_nodes`), clearing the pair
along with everything else. The two PREVENTION routes above are still not
clearing routes — that half of the `#10852` correction stands.

WHY ITS OWN FILE, REGISTERED IN `tier1`. The guarded artifact is under `docs/`,
and a docs-only diff selects no surface and runs `tier1` only, so a pin placed in
`tests/test_10000_source_prose.py` (the `core` surface, graph-touching, one
throwaway FalkorDB per case) would SLEEP on exactly the diff that re-arms the
claim. Same shape and the same reason as `tests/test_10191_record_move_doc_pin.py`.
DB-free, fixture-free, network-free: it reads two files and asserts on text — plus,
through `cache_projection.projected_columns`, the record migration files it parses
(`record_projection._read_migrations`), which is still no DB, fixture or network.

WHAT IT ASSERTS is the CORRECTION in both directions, in the doc AND in the module
docstring the doc is supposed to mirror, so the two cannot drift apart silently:

  (1) the retracted shapes are ABSENT from the "Costs, named rather than implied"
      section — the bullet list a reader consults for what the drop costs;
  (2) the mechanism is PRESENT — the no-op replay, `#1727`, the ONE route that
      actually clears an existing pair (a retry of a prior FAILED capture) and
      the two that only look like it (a NEW `session_id`, a re-projection), so
      deleting the bullet cannot pass this, which is what stops the cheap green
      of an empty section (the three-route list was itself corrected by the
      `#10852` P3 round — see the module docstring above);
  (3) the doc's own "### The pin" list cites the `#10965` per-row legs it claims to
      state, and every `test_*` name it cites RESOLVES in the module it points at
      (`#7948`/`#10852`'s lesson: a hand-maintained prose reference rots and
      nothing notices);
  (4) `tortoise/cache_projection.py`'s module docstring — the artifact the doc
      paraphrases — states the same correction and none of the retracted shapes.

NON-VACUITY (measured, not asserted): all three of (1), (2) and (3) red against
the pre-fix document at `e13c6eeea7`; (4) was already green there, because the
code was corrected first and the doc was the artifact left behind.
"""
from __future__ import annotations

import re
from pathlib import Path

from tortoise import cache_projection

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "epics" / "2026-10-10-7879-postgres-record"
       / "06-cache-projection.md")

#: The section of the doc that states what the drop costs — the bullet list an
#: operator reads before deciding whether their legacy sessions are fine.
COSTS_HEADING = "### Costs, named rather than implied"

#: The section that names the legs pinning the door.
PIN_HEADING = "### The pin"

#: Shapes a future lane re-introduces the retracted claim in. The first is the
#: pre-fix text VERBATIM (so this arm was RED at `e13c6eeea7`); the rest are the
#: mechanism clause that followed it and the two casings a lane reaches for when
#: it paraphrases the claim back in.
RETRACTED_SHAPES = (
    "until it is re-captured",
    "the capture now overwrites the pair",
    "now overwrites the pair",
    "heals on re-capture",
    "HEALS on re-capture",
    # `#10852` review P3: the three-route repair list was wrong for two of its
    # three members. Both casings, because the doc and the module docstring
    # spell the same sentence with different emphasis.
    "The routes that DO clear it are a capture under a NEW",
    "The routes that do clear it are a capture under a NEW",
)

#: The correction, as keywords rather than a sentence: a reworded but CORRECT
#: statement passes, a correct-looking one that loses the mechanism does not.
CORRECTION_KEYWORDS = ("does not repair", "no-op replay", "1727")

#: Every route that clears an EXISTING legacy node's pair, and each is pinned
#: because a repair list short by one sends the operator to the wrong lane:
#:   * a retry of a prior FAILED capture re-runs the mint for the same `url`, so
#:     the capture's `SET`s overwrite the pair (`retry_failed_capture`, `#2335`);
#:   * `#11070`'s reconciler repairs or CLEARS the pair IN PLACE from the record
#:     without a wipe (shipped `98df3bd4cf`, `#11152`) — the only non-destructive
#:     route;
#:   * a full `record-restore` wipes unconditionally (`_wipe_all_nodes`), so it
#:     clears the pair along with the rest of the graph.
#: ⚠️ The `#10852` review's "SINGLE clearing route" was true when it was measured
#: and stopped being true when `#11070` shipped; the PREVENTION half of it — a NEW
#: `session_id` and an incremental re-projection clear nothing — still stands.
CLEARING_ROUTES = (
    "retry of a prior FAILED capture",
    "reconcile_session_source_prose",
    "record-restore",
)

#: The two routes the earlier cut published as clearing ones. They must still be
#: NAMED — an operator who was told about them needs to learn they do not clear
#: an existing node — while `RETRACTED_SHAPES` keeps the sentence that presented
#: them as repairs out of both docs, so deleting the paragraph cannot pass.
NOT_CLEARING_ROUTES = ("NEW `session_id`", "projection CREATES")

#: The `#10965` per-row legs this PR added. They must be NAMED here, because the
#: section's whole claim is that it states "the pin" — a list short by one arm
#: reads as coverage and delivers less.
REQUIRED_PINNED_LEGS = (
    "test_a_post_10000_session_row_keeps_the_field_its_fts_index_is_declared_on",
    "test_the_drop_is_decided_by_the_rows_own_provenance_not_by_the_column_name",
    "test_a_list_shaped_legacy_utterance_is_not_mistaken_for_the_lexicon",
    "test_the_witness_fails_closed_on_every_unprovable_summary",
    "test_the_display_summary_stays_declined_and_the_residual_is_named",
    "test_the_reconciler_compares_the_restored_lexicon_on_a_post_10000_row",
)

#: The `#10394` review's last open P3: the value-filling class was proved by
#: naming the fold's KEYS as record COLUMNS. `FOLD_ON_CREATE_DEFAULTS` is keyed by
#: PROPERTY name (`subjectKind` — what the node and the placement table carry);
#: `projected_columns` speaks COLUMN space (`subject_kind` — what the record row
#: carries). Two of the three members named were not columns in ANY table, so the
#: proof was false for them — in the one sentence whose entire job is to prove the
#: class. The first shape is the pre-fix text VERBATIM, so this arm was RED at
#: `ce056ec857` (measured); the second is the paraphrase a lane reaches for when it
#: re-types the claim from the fold's dict keys.
PROPERTY_AS_COLUMN_SHAPES = (
    "`subjectKind`/`objectKind`/`Object.title` are all in `projected_columns`",
    "`subjectKind`/`objectKind`/`title` are all in `projected_columns`",
)

def _snake(prop: str) -> str:
    """`subjectKind` → `subject_kind`: the record's spelling of a property name.

    The two namespaces the `#10394` review P3 conflated: `FOLD_ON_CREATE_DEFAULTS`
    is keyed in PROPERTY space (what the node carries) and `projected_columns`
    answers in COLUMN space (what the row carries). A single-word key spells the
    same in both, which is why one of the three members was true as written and
    the two camel-case ones were not.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", prop).lower()


#: The fold's OWN table, derived from the label it is keyed by — never a hand
#: restated list. The first cut hardcoded the three members, which is the same
#: restated-enumeration cost the commit message claims to avoid one level up: a new
#: fold default would have left both the doc's "all three" and this pin's member
#: list silently short.
FOLD_LABEL_TABLES = (("Subject", "subject"), ("Object", "object"))


def _fold_columns() -> tuple[tuple[str, str], ...]:
    """`(table, column)` for every declared fold default, DERIVED not restated."""
    owned = dict(FOLD_LABEL_TABLES)
    return tuple(
        sorted((owned[label], _snake(prop))
               for label, defaults in cache_projection.FOLD_ON_CREATE_DEFAULTS.items()
               if label in owned
               for prop in defaults))


#: The same fields in the space the claim has to live in — derived from the fold and
#: MEASURED against the projector, so a column that stops projecting (or a new fold
#: default) reds the sentence that cites the class instead of rotting quietly.
FOLD_COLUMNS = _fold_columns()

#: The section that makes the non-widening claim. The presence arm is SCOPED to it:
#: an unscoped `column in whole` check is vacuous at this very head — `### 0.6
#: Verification` names the same three columns in another bullet (measured, and the
#: reason a review found the first cut GREEN on the deletion it exists to catch).
FOLD_PROOF_HEADING_PREFIX = "## §9.1/D1 at the projection door"
FOLD_PROOF_PREFIX_LABEL = "§9.1/D1 at the projection door"

#: The column space the proof must be stated IN, within that section.
PROOF_CITATION = "projected_columns"

#: How much text around a `projected_columns` mention counts as "cited beside it".
#: A mixed-space paraphrase (`subject_kind`/`objectKind` "are all in
#: `projected_columns`") is the likeliest re-entry of the defect, and it is not any
#: single string — so the guard checks the WINDOW around every mention instead of
#: blacklisting shapes. 400 chars covers the parenthetical a lane rewrites without
#: reaching into the next paragraph.
COLUMN_CITATION_WINDOW = 400

#: A camel-case key inside that window is FINE when the text is drawing the
#: contrast — naming the namespace is the correct thing to do, and the corrected
#: sentence does exactly that. So the qualifier is what separates the contrast from
#: the claim: a namespace marker shortly before the key. Without one, the key reads
#: as a member of the set it is cited beside, which is the defect.
NAMESPACE_MARKERS = ("PROPERTY", "property name", "property names")
NAMESPACE_LOOKBACK = 160

#: The tables whose column lists are the universe for the property-space check —
#: the four an entity/node row is built from, NOT all ten `table_columns()` answers
#: (`record_projection.table_columns()` also keys `journal_event`, `node_identity`,
#: `point`, `relation`, `session`, `session_narrative`). Measured: no column in ANY
#: of the ten carries an uppercase character, so the camel-case arm is equivalent
#: over the full set; the own-table arm below is what keeps the check exact.
COLUMN_SPACE_TABLES = ("subject", "object", "event", "source")

#: The modules a cited `test_*` name may resolve in — the two files the pin
#: section points at, and nothing else.
PIN_MODULES = (
    ROOT / "tests" / "test_10853_session_source_prose.py",
    ROOT / "tests" / "test_10000_source_prose.py",
    # This file is itself a leg the pin section cites.
    ROOT / "tests" / "test_10000_cache_projection_doc_pin.py",
)


def _section_by_prefix(prefix: str) -> str:
    """The body of the ONE heading starting with ``prefix``, verbatim.

    Prefix-anchored rather than exact (unlike :func:`_section`) because this heading
    carries a long tail that lanes legitimately extend; the EXACTLY-ONCE assertion is
    kept, because a prefix that matches twice would inspect the wrong prose and read
    as coverage.
    """
    lines = DOC.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    assert len(starts) == 1, (
        f"`{DOC.name}` must carry exactly ONE heading starting `{prefix}`; found "
        f"{len(starts)} at lines {starts}. Re-point this guard at the section rather "
        "than deleting it — it is what ties the doc's claim to the code's")
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _doc_text() -> str:
    """The whole doc, verbatim — the scope of the ABSENCE arm.

    ⛔ THE ABSENCE ARM SCANS THE WHOLE FILE, not only the costs section: moving
    the retracted sentence to another section (or back into the introduction)
    must not take it out of the guard's scope. The PRESENCE arms stay
    section-scoped, because the correction belongs to the cost list an operator
    reads.
    """
    return DOC.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """``heading``'s body, verbatim, from the doc.

    ⛔ THE ANCHOR IS ASSERTED TO MATCH EXACTLY ONCE. A ``break``-on-first-hit
    extraction over a doc with two candidate headings would silently inspect the
    wrong section, and the guard would then be measuring prose nobody reads —
    the failure mode `_the_search_text_set_comment_block` in
    ``tests/test_10000_source_prose.py`` calls out for the same reason. An anchor
    that stops matching is a FAIL, never an empty section.
    """
    lines = DOC.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == heading]
    assert len(starts) == 1, (
        f"`{DOC.name}` must carry the heading `{heading}` EXACTLY ONCE; found "
        f"{len(starts)} occurrence(s) at lines {starts}. Re-point this guard at "
        "the section rather than deleting it — it is the only thing tying the "
        "doc's claim to the code's")
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("### ")), len(lines))
    return "\n".join(lines[start:end])


def _module_doc() -> str:
    """``tortoise/cache_projection.py``'s module docstring, verbatim."""
    return cache_projection.__doc__ or ""


def test_the_doc_does_not_claim_a_re_capture_heals_a_pre_fix_node():
    """The false premise this PR retracted must not return to the doc's cost list.

    RED against the pre-fix document at `e13c6eeea7`, in THIS file's own subject:
    the bullet said *"A legacy session stays unfindable on the recovery door until
    it is re-captured (the capture now overwrites the pair with the lexicon)"* —
    both clauses false by the mint gate, and contradicted 150 lines earlier in the
    same repo by `tortoise/cache_projection.py` and
    `tests/test_10000_source_prose.py::test_the_write_site_does_not_claim_a_re_capture_heals_a_pre_fix_node`
    (the replay arm that used to sit beside it went with the SDK `capture_session`
    door this branch deletes — `#4282`, loss recorded on `#10227`).
    """
    costs = _section(COSTS_HEADING)
    whole = _doc_text()

    for retracted in RETRACTED_SHAPES:
        assert retracted not in whole, (
            f"`{DOC.name}` re-arms the RETRACTED mechanism ({retracted!r}). A "
            "re-capture does NOT heal a pre-#10000 node: re-capturing an "
            "already-captured session id is the `#2104`/`#1727` no-op replay, so "
            "`_materialize_session_source` — whose only caller sits behind the "
            "fresh-capture gate — never runs and the stale pair survives "
            "(measured: `#10852` review, P1). Mirror "
            "`cache_projection.py`'s docstring instead.\n\n"
            f"{costs}")


def test_the_doc_states_the_mechanism_and_the_routes_that_do_clear_the_pair():
    """The correction must be PRESENT, not merely the old claim deleted.

    Deleting the bullet is the cheap green this arm exists to stop: a cost list
    that is silent about the population is how the premise gets re-derived from
    scratch (measured — five artifacts carried it).
    """
    costs = _section(COSTS_HEADING)
    lowered = costs.lower()

    for keyword in CORRECTION_KEYWORDS:
        assert keyword.lower() in lowered, (
            f"`{DOC.name}`'s cost list no longer states the correcting "
            f"mechanism ({keyword!r}). Without the mint gate / no-op replay the "
            "sentence is readable as 'the legacy population sorts itself out', "
            f"which is false:\n\n{costs}")

    for route in CLEARING_ROUTES:
        assert route in costs, (
            f"`{DOC.name}`'s cost list omits the route {route!r} that DOES clear "
            "an existing legacy pair. A reader told only that a re-capture does "
            "not help has no way to repair the node — name the route the code "
            f"names:\n\n{costs}")

    for route in NOT_CLEARING_ROUTES:
        assert route in costs, (
            f"`{DOC.name}`'s cost list no longer mentions {route!r} at all. It "
            "is not a clearing route, but it was published as one, so the "
            "correction has to say what it IS — deleting the mention leaves the "
            f"reader's earlier belief standing:\n\n{costs}")


def test_the_pin_section_names_the_per_row_legs_and_they_resolve():
    """The `### The pin` list must cite the `#10965` legs — and cite real ones.

    `#10852`'s review recorded that the list was short by the four per-row legs
    the same PR added, so the section's own claim ("the pin") understated the
    suite. Citations are made EXECUTABLE rather than trusted (`#7948`'s lesson):
    a name that does not resolve is a prose reference that reads as proof and
    pins nothing.
    """
    pin = _section(PIN_HEADING)

    for leg in REQUIRED_PINNED_LEGS:
        assert leg in pin, (
            f"`{DOC.name}`'s `{PIN_HEADING}` does not name `{leg}` — the "
            "section states 'the pin' for the per-row (`#10965`) decision, so a "
            "list short by an arm reads as coverage it does not have")

    sources = {path: path.read_text(encoding="utf-8") for path in PIN_MODULES}
    # ⛔ The pattern skips a FILE reference (`tests/this_file.py`, which the
    # section cites for the leg modules) and admits only the bare-symbol use, so
    # the guard resolves the thing that must exist rather than the filename it
    # lives in — measured: matching the bare `\btest_…` shape red the guard on
    # this guard's own path instead of on a cited leg (same arm as the write-site
    # guard in `tests/test_10000_source_prose.py`).
    for cited in sorted(set(re.findall(
            r"(?<![/\w])(test_[a-z0-9_]+)(?!\.py)", pin))):
        assert any(re.search(rf"^def {cited}\(", text, re.MULTILINE)
                   for text in sources.values()), (
            f"`{DOC.name}`'s `{PIN_HEADING}` cites `{cited}`, which is not "
            f"defined in {' or '.join(p.name for p in PIN_MODULES)} — a doc that "
            "names a leg that does not exist pins nothing")


def test_the_value_filling_class_is_proved_in_COLUMN_space_and_the_proof_is_measured():
    """`FOLD_ON_CREATE_DEFAULTS` does not widen the field set — proved in columns.

    RED against the pre-fix document at `ce056ec857`: its parenthetical said the
    fold's keys "are all in `projected_columns`", and `projected_columns` is
    COLUMN space. Measured there: `subject_kind` is a `Subject` column and
    `object_kind`/`title` are `Object` columns, while `subjectKind`/`objectKind`
    are property names present in NO table's column list — the claim was false for
    two of its three members, in the sentence whose whole job is to prove the
    value-filling class.

    WHY A FALSE PROOF IS NOT COSMETIC. The sentence exists to settle a conflation
    (field-set vs value) that `#17.2`/point 3 rely on; a reader who checks it
    against `projected_columns` finds `subjectKind` absent and concludes the fold
    DOES widen the set — the exact misreading the paragraph forbids. The measured
    arm below keeps the corrected sentence honest in both namespaces.
    """
    whole = _doc_text()
    for shape in PROPERTY_AS_COLUMN_SHAPES:
        assert shape not in whole, (
            f"`{DOC.name}` proves the value-filling class in PROPERTY space "
            f"({shape!r}) while citing COLUMN space. `FOLD_ON_CREATE_DEFAULTS` is "
            "keyed by property name; `projected_columns` returns the record's "
            "snake_case columns. Name the three fields as columns (measured), or "
            "drop the citation — a proof whose members are not members of the set "
            "it cites is the field-set/value conflation the paragraph exists to "
            "prevent.")

    # ⛔ THE CLASS, not the shape: exact strings only catch the wording that has
    # already happened. A camel-case fold key within a citation window of
    # `projected_columns` is the defect however the sentence is arranged —
    # measured, that table holds `subject_kind`, never `subjectKind`, so any such
    # citation is false about its own members.
    camel_keys = sorted({key for defaults
                         in cache_projection.FOLD_ON_CREATE_DEFAULTS.values()
                         for key in defaults if _snake(key) != key})
    assert camel_keys, (
        "`FOLD_ON_CREATE_DEFAULTS` no longer carries a camel-case key — the "
        "property/column namespace split this pin measures does not exist. "
        "Re-derive the census (module-docstring point 3) before trusting the arm.")
    lowered = whole.lower()
    start = lowered.find("projected_columns")
    while start != -1:
        window = whole[max(0, start - COLUMN_CITATION_WINDOW):
                       start + len("projected_columns") + COLUMN_CITATION_WINDOW]
        for prop in camel_keys:
            at = window.find(prop)
            while at != -1:
                context = window[max(0, at - NAMESPACE_LOOKBACK):at]
                assert any(marker in context for marker in NAMESPACE_MARKERS), (
                    f"`{DOC.name}` cites `{prop}` (a PROPERTY name) within "
                    f"{COLUMN_CITATION_WINDOW} chars of `projected_columns` (COLUMN "
                    "space) without saying what namespace it belongs to — "
                    f"measured, no table's column list contains `{prop}`; the row "
                    f"spells it `{_snake(prop)}`. A mixed-space paraphrase is the "
                    "likeliest re-entry of the P3 this pin closes: either state the "
                    "proof in column space, or name the namespace so the contrast "
                    "is unambiguous (a bare key beside the citation reads as a "
                    "member of it).")
                at = window.find(prop, at + 1)
        start = lowered.find("projected_columns", start + 1)

    # The corrected sentence must be PRESENT, not merely un-replaced: the file's
    # own convention is that "fixing" a false claim by deleting it does not pass
    # (`test_the_doc_states_the_mechanism_and_the_routes_that_do_clear_the_pair`
    # exists for the same reason).
    # ⛔ SCOPED TO THE SECTION, and that scoping is the whole arm: an unscoped
    # `column in whole` check was GREEN on the deletion it names, because
    # `### 0.6 Verification` names the same three columns in a different bullet
    # (found by review at `f9adf39da5`, P2 — a guard reporting OK on the exact
    # mutation it exists to catch is the false-PASS class, not a wording nit).
    proof = _section_by_prefix(FOLD_PROOF_HEADING_PREFIX)
    assert PROOF_CITATION in proof, (
        f"`{DOC.name}`'s `{FOLD_PROOF_PREFIX_LABEL}` section no longer mentions "
        f"`{PROOF_CITATION}`. The value-filling class is settled there by citing "
        "the COLUMNS the fold fills; without that citation the paragraph's claim "
        "is unbacked and a reader concludes the fold widens the field set.")
    for _table, column in FOLD_COLUMNS:
        assert column in proof, (
            f"`{DOC.name}`'s `{FOLD_PROOF_PREFIX_LABEL}` section no longer names "
            f"`{column}`. Deleting the proof is not a repair — the class has to be "
            "proved in the row's own spelling, in that section (naming it in "
            "`### 0.6 Verification` instead leaves the §9.1/D1 paragraph — the one "
            "a reader consults for this door — without its proof).")

    for table, column in FOLD_COLUMNS:
        assert column in cache_projection.projected_columns(table), (
            f"`projected_columns({table!r})` no longer projects `{column}`, so "
            f"`{DOC.name}`'s proof of the value-filling class is no longer true "
            "for it. Re-check the class BEFORE repairing the sentence: the claim "
            "is that the fold supplies a VALUE for a field the record HAS, so "
            "every member must still be one (a field-set widening is a different "
            "class and needs its own entry in the census).")

    # The distinction the sentence must preserve, measured instead of asserted.
    # ⛔ NOT every fold key is absent from column space: `title` is a single word
    # and spells identically in both namespaces, so it IS a column (measured —
    # which is exactly why the pre-fix claim was false for two members and true
    # for the third). The defect class is the CAMEL-CASE key, which no table
    # spells: those must never appear in a column list, and their snake spelling
    # must. That asymmetry is the whole point, so it is measured, not assumed.
    column_space = {table: set(cache_projection.projected_columns(table))
                    for table in COLUMN_SPACE_TABLES}
    for label, table in FOLD_LABEL_TABLES:
        defaults = cache_projection.FOLD_ON_CREATE_DEFAULTS[label]
        assert defaults, f"`FOLD_ON_CREATE_DEFAULTS[{label!r}]` lost its defaults"
        for prop in defaults:
            if _snake(prop) == prop:
                continue
            assert not any(prop in cols for cols in column_space.values()), (
                f"`{label}.{prop}` is BOTH a `FOLD_ON_CREATE_DEFAULTS` key (PROPERTY "
                "space) and a record COLUMN — the distinction this pin exists to "
                "keep separate has collapsed, so `projected_columns` is no longer "
                "the wrong space for the fold's keys. Re-derive the census "
                "(module-docstring point 3) before touching the doc.")
            assert _snake(prop) in column_space[table], (
                f"`{label}.{prop}`'s snake spelling `{_snake(prop)}` is not in "
                f"`projected_columns({table!r})` — the fold's OWN table. Either the "
                "fold now supplies a VALUE for a field the record does not HAVE "
                "(a field-set WIDENING, a different class) or it is landing on "
                "another table. Re-derive the census before repairing the sentence.")


def test_the_module_docstring_the_doc_paraphrases_says_the_same_thing():
    """Doc and code must not disagree — the code was right and the doc wrong.

    The retracted claim lives in the doc, but the doc IS a paraphrase of
    `cache_projection`'s module docstring, so this arm keeps the pair in step in
    both directions: no retracted shape, and the correction stated.
    """
    module_doc = _module_doc()
    assert module_doc.strip(), "`cache_projection` lost its module docstring"
    assert "NOT a re-capture" in module_doc, (
        "`cache_projection`'s module docstring no longer states that the repair "
        "for a legacy node is NOT a re-capture — that sentence is the source the "
        "epic doc paraphrases and the reason the doc's cost bullet is correct")

    for retracted in RETRACTED_SHAPES:
        assert retracted not in module_doc, (
            f"`cache_projection`'s module docstring re-arms the retracted "
            f"mechanism ({retracted!r})")
