"""#1992 — the Test Design surface map is checked against itself, not by eye.

WHAT THIS PINS
--------------
`docs/epics/2026-08-29-agent-driven-onboarding-1976/04-test-design.md` is the
canonical surface map every child issue's verification checklist is cut from,
so a row and the prose beside it giving two different answers for one
capability is a defect that ships. Three review cycles on PR #9013 found the
SAME class of bug three times (the table describing the document in its own
words instead of quoting it, and a row disagreeing with its own rationale), so
the invariants that were being checked by hand are checked here instead:

1. **The `Verified at` column is a COPY, not a source.** Every quoted segment is
   character-identical to the cited surface's own `Test Layer` cell.
2. **Both directions hold.** Every in-scope capability has a surface, and every
   surface is reachable from one.
3. **Capability names are the scope's** — one row per `03-scope.md` §2 row, same
   names, same order (W10 is out of scope and must stay absent).
4. **A row cites every surface its own rationale names.** The pre-fix defect:
   the `W5 setup card` row said `9, 17` while the bullet three lines below
   placed the card's counted checklist on surface 10 (`05-ux-decisions.md`
   "Layout: Setup guide"). Row ↔ prose is the reconciliation a reader does, and
   it produced two answers. The rationale must cite in the explicit
   **`surface N`** form: the first version of this check read *any* standalone
   1-17 integer, so `05-ux-decisions.md` #5 in a bullet was read as surface 5
   and a future "step 3"/"§5"/"3 of the 5 doors" would fail with a misleading
   message (and a number >17 would trip the surfaces sanity assert).
5. **An E2E row cites every surface that owns a door it exercises.** The
   pre-fix defect: `E2E-6 graph-held resumption` was `1, 2, 10` while the scope
   defines that re-entry as "the Settings Setup guide **or the Continue-setup
   card**" — the card being surface 9's next-action element, which the same
   document had just written into surface 9's contract. The door set is
   ANCHORED in `03-scope.md` §4 E2E-6 (the authority, which the document under
   test cannot reword) and only *extended* from the map's own Contract column;
   the first version derived it from the contracts alone and asserted only
   surface 10, so a legal reword of surface 9's contract plus a reverted E2E-6
   row passed it — demonstrated in
   `test_the_e2e6_door_guard_rejects_a_reworded_contract_plus_a_reverted_row`.
6. **The counted/dismissible checklist belongs to surface 10, not surface 9.**
   The pre-fix defect: surface 9's contract claimed "a dismissible checklist
   rendered from `OnboardingState`.completed_steps" — but the shipped Overview
   next-action element is ONE calm next step plus a CTA into the Settings Setup
   guide (`website/apps/dashboard/src/main.jsx`, `OverviewNextActionCard`), the
   counted N-of-M checklist is `SetupGuideCard` on the Settings tab, and
   neither card carries a dismissal (the only `dismiss` hits are the recovery,
   claim and billing banners). Surfaces 9/10 shipped under #2000/#2001, both
   CLOSED, so a map that contradicts the shipped dashboard is silent drift.
   `05-ux-decisions.md` row 5 — the row the map cites as its authority — carried
   the same superseded claim, so it is pinned here too.
7. **No orphan `)` after a code span** — the review reported
   `rendered from `OnboardingState`).completed_steps`; the reviewed head had
   already dropped the stray paren, so this is a standing GUARD with the
   detector's non-blindness asserted rather than a pre-fix defect pin.
8. **The checklist child issues consume carries the obligation the surface table
   states** — a projection obligation that lives only in the table is one a card
   implementation can never fail.
9. **The surface-table parser rejects a second inflated row instead of shifting
   columns.** Exactly one cell in the document is baselined as inflated (surface
   4's unescaped pipe, MD056); the table parser asserts that shape, so a future
   unescaped pipe in Contract/Failure Modes fails loudly instead of moving those
   cells into the wrong fields.
10. **Every dismissal claim in the row the map cites names its DOOR.** The
   reviewed head's row 5 said the Settings card "collapses to compact status /
   dismissible-hide with re-open path when complete" AND "neither card carries a
   dismissal" — one claim, two opposite answers, in one sentence. The previous
   guard asserted the absence of one literal phrase, so it passed a row that
   stated both. Claims are now read per clause and attached to the door named in
   that clause; a door-less denial that contradicts an affirming door is a
   failure.
11. **The superseded mirror claim is reconciled where it survived, not only
   where it was first noticed.** `03-scope.md` §4 E2E-6 — the AUTHORITY this
   module's door pin anchors to — still read "mirrored as a dismissible
   checklist card", as did the same claim in §2's W5 bullet, in
   `00-align-and-scope.md` (both places) and in `05-ux-decisions.md`'s `Visual
   affordances` row. Live text states the corrected reading; wording kept as
   history carries a supersession note beside it.
12. **Every surface is exercised by at least one SINGLE-SCENARIO E2E row, and
   the catch-all cannot stand in for that.** The previous version of this check
   unioned every row, including `E2E-12 cross-W journey | 1-17 (all)` — whose
   cell expands to the whole surface set, making the union `{1..17}` BY
   CONSTRUCTION. It therefore passed with `E2E-6` reverted to `1, 2, 10` and
   would have passed if any surface vanished from every scenario row: the same
   defect shape it was written to replace. The catch-all is now excluded and one
   exemption is carried explicitly — surface 14 (telemetry), which the scope's
   E2E-12 `Then` exercises ("W11 fires seed_complete + decide_complete once per
   org") — with the exemption anchored in the scope and re-checked for
   staleness, and with the non-vacuity receipt in
   `test_the_specific_e2e_guard_rejects_the_catch_all_it_replaces`.
13. **The epic plan's `DE2E-N [...]` brackets are the map's E2E rows, copied.**
   `06-plan.md` §7 declares the derivation in its own words — "Test-design surface
   refs [#N] from `04-test-design.md`" — and 11 of the 12 DE2E headers already
   agreed, so the one that did not (`DE2E-6` said `[1,2,10]` while the map now
   says `1, 2, 9, 10`) was the map's own correction stopping one document short.
   The same class the capstone's `Surfaces` column is pinned for, one consumer
   over. `DE2E-6` must also assert every door it cites in its **`Assert:` line
   only** — naming a door in `Steps:` walks it, but the claim that can fail is the
   assertion, and a surface cited in a header with no assertion is how surface 9's
   failure mode went untested. Both of its Overview arms are named, because they
   are different cards: the zero-point re-entry card (`Continue setup →`) and the
   next-action element (`OverviewNextActionCard`).

Every surface is also exercised by at least one high-level E2E row (the E2E-side
reverse direction) — a surface no scenario touches has no scenario that would
catch its regression. Read that against check 12: the union is over
SINGLE-SCENARIO rows, so the claim is not satisfiable by `E2E-12`'s catch-all
alone.

NON-VACUITY
-----------
Checks 4, 5, 6, 8, 10, 11, 12 and 13 fail on the reviewed head's document/text
(each docstring names the pre-fix text; check 10 additionally replays the exact
self-contradicting sentence through the guard in-suite, and check 13 carries both
receipts — the plan's `[1,2,10]` bracket and a deleted door `Assert:` clause —
through the comparison itself). Check 12's receipt drives the comparison with
synthetic rows but reads the live document's exemption paragraph and the live
scope anchor, so it is not hermetic: the same check also fails on any blob that
predates that paragraph. Checks 1-3 are the standing invariants the earlier cycles
established, check 9 is a parser-shape guard and check 7 is a guard whose detector
is proved non-blind — guard rails, not defect pins. The pre-fix blobs are replayed
by pointing this module's pure functions at them (`git show <sha>:<path>`); clear
`_doc_lines` / `_plan_lines` first, since those two are keyed on the PATH — the
helpers built on them are keyed on the content they receive. That is how the
non-vacuity claims were measured; reverting the four docs to `fe06e3a98` fails
checks 10 and 11 (five tests) and 12 (two tests — that blob predates the E2E
exception note 12 reads), and nothing else.
Static: doc-text scans plus one file read per doc. No network, DB or graph.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPIC = ROOT / "docs" / "epics" / "2026-08-29-agent-driven-onboarding-1976"
DOC = EPIC / "04-test-design.md"
SCOPE = EPIC / "03-scope.md"
UX = EPIC / "05-ux-decisions.md"
PLAN = EPIC / "06-plan.md"

# `03-scope.md` §4 E2E-6 is the AUTHORITY for which re-entry doors exist:
# "**When:** they re-enter via the Settings Setup guide or the Continue-setup
# card". Binding a door name to the surface that owns it is the one thing this
# guard states for itself — the surfaces themselves come from the scope, never
# from the document under test. Deriving the set from the map's own contracts
# alone is what made the first version of this check FAIL-OPEN: a legal reword
# of surface 9's contract emptied the door set and `E2E-6 | 1, 2, 10` passed
# (only surface 10 was asserted).
SCOPE_E2E6_DOOR_SURFACES = {
    "the Settings Setup guide": 10,
    "the Continue-setup card": 9,
}

# ` · ` separates the per-surface segments of a multi-surface `Verified at` cell.
_SEGMENT = " · "


def _cells(line: str) -> list[str]:
    """Split a markdown table row into stripped cells on UNESCAPED pipes.

    Two pre-existing shapes in this document need the care: escaped pipes
    inside a cell (surface 1's `'failed'\\|'dismissed'\\|null` enum) and the
    one deliberately unescaped pipe in surface 4 (`fork: 'self'|'build'` — the
    file's baselined MD056). The escaped form must not split a cell; the
    unescaped one does split and is re-joined by `_surfaces` so the row still
    parses.
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


@lru_cache(maxsize=1)
def _doc_lines() -> tuple[str, ...]:
    return tuple(DOC.read_text(encoding="utf-8").splitlines())


@lru_cache(maxsize=1)
def _surfaces() -> dict[int, dict[str, str]]:
    """#1..17 surface table row → its named columns. The table is the first one
    in the document (`| # | Surface | Type | Data Flow | Test Layer | Contract |
    Key Failure Modes |`)."""
    out: dict[int, dict[str, str]] = {}
    for line in _doc_lines():
        m = re.match(r"^\|\s*(\d{1,2})\s*\|", line)
        if not m:
            continue
        cells = _cells(line)
        if len(cells) != 7:
            # Exactly ONE cell in this whole document is baselined as inflated:
            # the unescaped pipe in surface 4's NAME (`fork: 'self'|'build'`,
            # the file's MD056). Both halves of that shape are asserted — one
            # extra cell, and the split landing inside the Surface column — so
            # a future unescaped pipe in Contract / Failure Modes / Test Layer
            # fails HERE instead of silently shifting those cells into the
            # wrong fields and pinning a contract that is really half a name.
            assert len(cells) == 8, (
                f"a surface row has {len(cells)} cells, expected 7: at most one "
                "unescaped pipe is baselined (surface 4's `fork: 'self'|'build'`); "
                f"a second one SHIFTS the columns — {line[:90]}")
            # Locate the Data Flow column by its closed vocabulary and rebuild
            # the row around it rather than assuming a fixed index.
            flow = next(
                (i for i, c in enumerate(cells)
                 if i >= 2 and c in {"Both", "Write", "Out"}),
                None,
            )
            assert flow == 4, (
                "the extra cell is not inside the Surface name (Data Flow found "
                f"at index {flow}) — columns are shifted, not inflated: {line[:90]}")
            cells = [
                cells[0],                       # #
                "|".join(cells[1 : flow - 1]),  # Surface (re-joined)
                cells[flow - 1],                # Type
                cells[flow],                    # Data Flow
                cells[flow + 1],                # Test Layer
                "|".join(cells[flow + 2 : -1]),  # Contract (re-joined)
                cells[-1],                      # Key Failure Modes
            ]
            assert cells[5] and cells[6], (
                f"the rebuilt row lost its Contract or Failure Modes cell: {line[:90]}")
        if len(cells) != 7:  # not the surface table
            continue
        out[int(m.group(1))] = {
            "name": cells[1],
            "layer": cells[4],
            "contract": cells[5],
            "failure_modes": cells[6],
        }
    return out


@lru_cache(maxsize=1)
def _capabilities() -> dict[str, tuple[set[int], str]]:
    """Capability → (declared surfaces, raw `Verified at` cell)."""
    out: dict[str, tuple[set[int], str]] = {}
    for line in _doc_lines():
        m = re.match(r"^\|\s*(W\d+ [^|]+?)\s*\|\s*([\d,\s]+?)\s*\|", line)
        if not m:
            continue
        cells = _cells(line)
        if len(cells) != 3:
            continue
        out[m.group(1)] = (
            {int(n) for n in re.findall(r"\d+", cells[1])},
            cells[2],
        )
    return out


@lru_cache(maxsize=1)
def _e2e_rows() -> dict[str, set[int]]:
    """`E2E-N` → the surfaces its row cites (ranges like `1-17` are expanded)."""
    out: dict[str, set[int]] = {}
    for line in _doc_lines():
        m = re.match(r"^\|\s*(E2E-\d+)\s*[^|]*\|", line)
        if not m:
            continue
        cells = _cells(line)
        if len(cells) != 2:
            continue
        numbers: set[int] = set()
        for chunk in re.findall(r"\d+\s*-\s*\d+|\d+", cells[1]):
            if "-" in chunk:
                lo, hi = (int(p) for p in chunk.split("-"))
                numbers.update(range(lo, hi + 1))
            else:
                numbers.add(int(chunk))
        out[m.group(1)] = numbers
    return out


def _catch_all_rows(
    rows: dict[str, set[int]], surfaces: dict[int, dict[str, str]]
) -> set[str]:
    """E2E rows whose cell names every surface — today only `E2E-12 | 1-17 (all)`.

    A catch-all row is traceability, not a scenario: it cannot be the only thing
    that exercises a surface, or the E2E-side reverse direction is satisfied by
    construction and every single-scenario drop is invisible.
    """
    return {name for name, cited in rows.items() if set(surfaces) <= cited}


def _specific_union(rows: dict[str, set[int]], surfaces) -> set[int]:
    """Surfaces cited by at least one SINGLE-SCENARIO row (catch-alls excluded)."""
    out: set[int] = set()
    for cited in rows.values():
        if set(surfaces) - cited:  # not a catch-all
            out |= cited
    return out


#: The one surface the E2E table reaches only through the `E2E-12` catch-all,
#: carried EXPLICITLY rather than absorbed: `03-scope.md` §4 E2E-12's `Then` is
#: where the W11 events are asserted ("W11 fires seed_complete + decide_complete
#: once per org (deduped)"), so the cross-W journey IS surface 14's scenario — it
#: simply runs through the catch-all cell instead of a row of its own. The
#: exemption is re-validated against the scope below, so rewording the authority
#: fails here instead of silently licensing it.
E2E_CATCH_ALL_EXEMPT_SURFACES = {14}


def _doc_catch_all_note() -> str:
    """The exception paragraph the map carries beside its E2E table.

    The document promises "one documented exception" and names it, so the note and
    the constant below have to move together: a second exempt surface added to
    `E2E_CATCH_ALL_EXEMPT_SURFACES` while the map still says one is the silent
    divergence this reads for.
    """
    lines = list(_doc_lines())
    start = next(
        (i for i, ln in enumerate(lines)
         if ln.startswith("Every surface is reached by at least one")), None)
    if start is None:
        raise AssertionError(
            "the map no longer carries the single-scenario/exception note beside "
            "its E2E table — the guard's claim and the document must move together")
    out = [lines[start]]
    for ln in lines[start + 1 :]:
        if not ln.strip():
            break
        out.append(ln)
    return "\n".join(out)


def _e2e_catch_all_exemptions(surfaces: dict[int, dict[str, str]]) -> set[int]:
    """The catch-all exemptions, re-anchored in `03-scope.md`, the surface name and
    the map's own exception note.

    Three facts make "E2E-12 exercises surface 14" true, and all three are checked:
    the scope's E2E-12 `Then` still asserts the W11 events, surface 14 is still the
    telemetry surface, and the map's note still names exactly the exempted
    surfaces. Any of them can be reworded independently of this module.
    """
    e2e12 = _scope_section("### E2E-12")
    assert "W11" in e2e12 and "seed_complete" in e2e12, (
        "`03-scope.md` E2E-12 no longer asserts the W11 telemetry events — the "
        "anchor for the surface-14 exemption moved; name surface 14 in a "
        "single-scenario E2E row and drop E2E_CATCH_ALL_EXEMPT_SURFACES")
    assert "telemetry" in surfaces[14]["name"].lower(), (
        "surface 14 is no longer the telemetry surface — the exemption's binding "
        f"moved: {surfaces[14]['name']!r}")
    named = {int(n) for n in re.findall(r"\bsurface (\d{1,2})", _doc_catch_all_note(), re.I)}
    assert named == set(E2E_CATCH_ALL_EXEMPT_SURFACES), (
        "the map's exception note and E2E_CATCH_ALL_EXEMPT_SURFACES disagree — "
        f"the note names {sorted(named)}, the constant exempts "
        f"{sorted(E2E_CATCH_ALL_EXEMPT_SURFACES)}")
    return set(E2E_CATCH_ALL_EXEMPT_SURFACES)


def _section_text(prefix: str, *, stop_at_blank_after: bool = True) -> str:
    """The lines of one `- **<prefix>**` bullet in the rationale list."""
    lines = list(_doc_lines())
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith(f"- **{prefix}**")), None
    )
    if start is None:
        raise AssertionError(f"no `- **{prefix}**` bullet in {DOC.name}")
    out = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.startswith("- **") or (stop_at_blank_after and not ln.strip()):
            break
        out.append(ln)
    return "\n".join(out)


def _standalone_numbers(text: str) -> set[int]:
    """DEPRECATED — kept only so the guard cannot silently resurrect it.

    Reading every small standalone integer as a surface citation misfires on
    ordinary prose: at `1758bbe59` the W5 bullet cited `05-ux-decisions.md` #5
    and this function reported surface 5, and a bullet reading "step 3", "§5"
    or "3 of the 5 doors" fails with a misleading message (and a number >17
    trips the surfaces sanity assert). Use `_cited_surfaces`.
    """
    raise AssertionError(
        "`_standalone_numbers` must not be used: it reads prose integers as "
        "surface citations — use `_cited_surfaces` (explicit `surface N`)")


#: The citation form the rationale bullets must use: `surface 9`,
#: `surfaces 1, 3 and 10`. Deliberately NOT any bare small integer.
_SURFACE_CITATION = re.compile(
    r"\bsurfaces?\s+(\d{1,2}(?:\s*(?:,|\band\b|/)\s*\d{1,2})*)",
    re.IGNORECASE,
)


def _cited_surfaces(text: str) -> set[int]:
    """Surface numbers cited in the explicit `surface N` / `surfaces N, M` form.

    A section reference (`§5`), a numbered step ("step 3") or an arithmetic
    aside ("3 of the 5 doors") is not a surface citation; only the explicit form
    is. The rationale bullets in the document are written in that form for this
    reason, and the doc says so next to them.
    """
    out: set[int] = set()
    for match in _SURFACE_CITATION.finditer(text):
        out.update(int(n) for n in re.findall(r"\d{1,2}", match.group(1)))
    return out


def _scope_section(heading: str) -> str:
    """The body of one `### <heading>` section of `03-scope.md`, up to the next
    `### ` — the authority the map cannot reword."""
    text = SCOPE.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n### ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


#: `06-plan.md` §7 header: `### DE2E-6 — Graph-held resumption [1, 2, 9, 10] — E2E-6`.
_DE2E_HEADING = re.compile(r"^###\s+(DE2E-\d+)\s+[^[]*\[([^\]]*)\]")

#: Door name as `03-scope.md` states it → the phrases `06-plan.md` DE2E-6 must
#: ASSERT for that door's surface. The Settings door owns the counted checklist;
#: the Continue-setup card is surface 9, whose name appears on BOTH of its arms
#: (the zero-point re-entry card and the next-action element), so the assertion
#: has to name the card and the next action rather than one of them. Keyed to the
#: same door names as `SCOPE_E2E6_DOOR_SURFACES`, and the test asserts the two
#: tables cover the same doors — a new scope door must not be silently covered by
#: check 5 and skipped here.
PLAN_DE2E6_DOOR_ASSERTIONS = {
    "the Settings Setup guide": ("Settings Setup guide", "counted N-of-M checklist"),
    "the Continue-setup card": ("Continue-setup card", "next action"),
}


def _plan_assert_lines(section: str) -> str:
    """Only the `**Assert:**` line of a DE2E section.

    A door named in `**Steps:**` ("re-enter via the Settings Setup guide") is only
    WALKED; the claim that can fail is the one in `**Assert:**`. Measured on a copy
    of the plan: deleting surface 10's whole Assert clause left a Steps-or-Assert
    scan green, because Steps still names the door — so the assertion, not the
    mention, is what this reads. The note beside the header names both doors too
    (it quotes the scope), which is why the whole section is not scanned either.
    """
    return "\n".join(
        ln for ln in section.splitlines() if ln.startswith("**Assert:**"))


@lru_cache(maxsize=1)
def _plan_lines() -> tuple[str, ...]:
    """The plan's lines. Cached on the PATH, so a replay that re-points `PLAN`
    must `clear()` this first — everything downstream is keyed on the CONTENT."""
    return tuple(PLAN.read_text(encoding="utf-8").splitlines())


def _plan_section(heading_prefix: str) -> str:
    """The body of one `### <heading_prefix> …` section of `06-plan.md`."""
    lines = list(_plan_lines())
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith(f"### {heading_prefix}")),
        None)
    if start is None:
        raise AssertionError(f"no `### {heading_prefix}` section in {PLAN.name}")
    out = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.startswith("### "):
            break
        out.append(ln)
    return "\n".join(out)


@lru_cache(maxsize=8)
def _plan_de2e_surfaces(lines: tuple[str, ...]) -> dict[str, set[int]]:
    """`DE2E-N` → the surfaces its header bracket names (`[1, 2, 9, 10]`, `[1-17]`).

    Keyed on the LINES rather than on nothing: an argument-free `lru_cache` would
    return the live plan for a replay that only re-pointed `PLAN`, i.e. report "no
    drift" on the very pre-fix state this exists to catch.
    """
    out: dict[str, set[int]] = {}
    for line in lines:
        m = _DE2E_HEADING.match(line)
        if not m:
            continue
        numbers: set[int] = set()
        for chunk in re.findall(r"\d+\s*-\s*\d+|\d+", m.group(2)):
            if "-" in chunk:
                lo, hi = (int(p) for p in chunk.split("-"))
                numbers.update(range(lo, hi + 1))
            else:
                numbers.add(int(chunk))
        out[m.group(1)] = numbers
    return out


def _de2e_drift(
    plan: dict[str, set[int]], mapped: dict[str, set[int]]
) -> dict[str, tuple[set[int], set[int]]]:
    """`DE2E-N` → (plan surfaces, map surfaces) for every bracket whose surface
    set DIFFERS from the map's row.

    A `DE2E-N` with no map counterpart is skipped rather than reported here — key
    parity is the caller's check, so a removed map row cannot pass as "no drift".
    """
    out: dict[str, tuple[set[int], set[int]]] = {}
    for name, cited in plan.items():
        e2e = f"E2E-{name.split('-')[1]}"
        if e2e in mapped and mapped[e2e] != cited:
            out[name] = (cited, mapped[e2e])
    return out


def _missing_e2e6_doors(
    cited: set[int], *, scope_text: str, contract_doors: set[int]
) -> set[int]:
    """Surfaces E2E-6 must cite but does not.

    Two sources, and the first is the one that cannot be reworded by the
    document under test: the doors `03-scope.md` §4 E2E-6 names (bound to their
    surfaces in `SCOPE_E2E6_DOOR_SURFACES`), plus — supplementary — every
    surface whose own Contract column names a door today, so a door that MOVES
    surfaces is followed instead of hard-coded.
    """
    required = {
        surface
        for door, surface in SCOPE_E2E6_DOOR_SURFACES.items()
        if door in scope_text
    }
    return (required | contract_doors) - cited


# ── dismissal claims: every one of them belongs to a DOOR ──────────────
#: The two doors `05-ux-decisions.md` row "Affordance: setup card" describes.
#: The Settings Setup guide owns the recorded dismissible-hide; the Overview
#: next action owns none.
_DOOR_WORDS = {"settings": "the settings setup guide", "overview": "the overview"}

#: A denial quantified over BOTH doors ("neither card carries a dismissal",
#: "no door ..."). This is the shape that swallowed the Settings door's
#: recorded dismissal while an adjacent clause asserted it.
_DOORLESS_DENIAL = re.compile(
    r"\b(?:neither|no)\s+(?:of\s+the\s+)?(?:both\s+)?(?:cards?|doors?)\b", re.I)
_NEGATION = re.compile(r"\b(?:no|not|never|neither|nor|without)\b", re.I)
_DISMISSAL = re.compile(r"dismiss", re.I)


def _dismissal_claims(text: str) -> dict[str, set[str]]:
    """Clause-level `door -> {"affirms", "denies"}` for every dismissal claim.

    `;`/`.` split each claim away from its neighbours so it stays attached to the
    door named in the SAME clause. Averaging the whole row into one boolean is
    what let "the Settings card collapses / dismissible-hides when complete" and
    "neither card carries a dismissal" sit in one sentence: two doors, two
    answers, and a reader tracing either one gets a different claim.
    """
    claims: dict[str, set[str]] = {"settings": set(), "overview": set()}
    for clause in re.split(r"[;.]", text):
        if not _DISMISSAL.search(clause):
            continue
        verdict = "denies" if _NEGATION.search(clause) else "affirms"
        doors = [d for d, word in _DOOR_WORDS.items() if word in clause.lower()]
        for door in doors:
            claims[door].add(verdict)
        if verdict == "denies" and _DOORLESS_DENIAL.search(clause):
            claims.setdefault("*", set()).add(verdict)
    return claims


def _dismissal_claim_conflicts(live: str) -> list[str]:
    """The ways one row can give two answers for one dismissal claim.

    Returns the violations (an empty list = one answer per door).
    """
    claims = _dismissal_claims(live)
    out: list[str] = []
    if "affirms" in claims["overview"]:
        out.append("the Overview door carries no dismissal in the shipped "
                   "dashboard (`OverviewNextActionCard`), but the row affirms one")
    if "denies" not in claims["overview"]:
        out.append("the row must state that the Overview door carries NO "
                   "dismissal — that is the claim the map corrected")
    if "denies" in claims["settings"]:
        out.append('the decision recorded for the Settings door IS a '
                   'dismissible-hide with a re-open path (row "Layout: Setup '
                   'guide"), but the row denies it')
    if "denies" in claims.get("*", set()) and "affirms" in claims["settings"]:
        out.append("a door-LESS denial (\"neither card ...\" / \"no door ...\") "
                   "contradicts the Settings door's affirmed dismissible-hide")
    return out


def _orphan_close_parens(text: str) -> list[str]:
    """`)` clumps that follow a code span at paren depth 0 — the shape a missing
    `(` leaves behind (`rendered from `OnboardingState`).completed_steps`)."""
    found: list[str] = []
    balance = 0
    for i, ch in enumerate(text):
        if ch == "(":
            balance += 1
        elif ch == ")":
            if balance == 0 and i and text[i - 1] == "`":
                found.append(text[max(0, i - 40) : i + 20])
            else:
                balance = max(0, balance - 1)
    return found


# ═══════════════════════════════════════════════════════════════════════
# 1-3: the standing invariants (guard rails, green before and after)
# ═══════════════════════════════════════════════════════════════════════

def test_every_capability_quotes_its_surfaces_test_layer_verbatim():
    surfaces = _surfaces()
    assert len(surfaces) == 17, sorted(surfaces)
    for capability, (cited, verified_at) in _capabilities().items():
        assert cited, f"{capability}: no surface cited"
        assert cited <= set(surfaces), f"{capability}: cites {cited - set(surfaces)}"
        if len(cited) == 1:
            (only,) = cited
            assert verified_at == surfaces[only]["layer"], (
                f"{capability}: `Verified at` is not surface {only}'s Test Layer")
            continue
        quoted = {}
        for segment in verified_at.split(_SEGMENT):
            m = re.match(r"^(\d{1,2}): (.+)$", segment)
            assert m, f"{capability}: segment without a surface prefix — {segment!r}"
            quoted[int(m.group(1))] = m.group(2)
        assert set(quoted) == cited, (
            f"{capability}: quotes {sorted(quoted)} but cites {sorted(cited)}")
        for number, layer in quoted.items():
            assert layer == surfaces[number]["layer"], (
                f"{capability}: surface {number} quoted as {layer!r}, but its "
                f"Test Layer is {surfaces[number]['layer']!r}")


def test_every_surface_is_reachable_from_a_capability():
    cited = set().union(*(c[0] for c in _capabilities().values()))
    assert cited == set(_surfaces()), (
        f"surfaces cited by no capability: {sorted(set(_surfaces()) - cited)}")


def test_every_surface_is_exercised_by_at_least_one_specific_e2e_row():
    """The E2E-side reverse direction: a surface no high-level scenario walks has
    no scenario that would catch its regression.

    PRE-FIX (and the reason this was rewritten): the check unioned EVERY row,
    including `E2E-12 cross-W journey | 1-17 (all)`, whose cell expands to
    `{1..17}` — so the union equalled the surface set by construction, whatever
    the other eleven rows said. It passed with `E2E-6` reverted to `1, 2, 10` and
    would pass if a surface disappeared from every scenario row: the same "cannot
    fail independently" shape it replaced.

    The union is now over SINGLE-SCENARIO rows only (the catch-all excluded), and
    the one surface the table legitimately reaches only through `E2E-12` —
    surface 14, telemetry, which the scope's E2E-12 `Then` asserts — is carried as
    an explicit, scope-anchored exemption rather than being absorbed silently.
    """
    surfaces = _surfaces()
    rows = _e2e_rows()
    catch_all = _catch_all_rows(rows, surfaces)
    assert catch_all == {"E2E-12"}, (
        "the cross-W journey row `1-17 (all)` is what this guard must exclude — "
        f"the rows citing every surface are {sorted(catch_all)}")
    specific = _specific_union(rows, surfaces)
    exempt = _e2e_catch_all_exemptions(surfaces)
    missing = set(surfaces) - specific - exempt
    assert not missing, (
        f"surfaces no SINGLE-SCENARIO E2E row exercises: {sorted(missing)} — "
        "`1-17 (all)` is not a scenario for a surface; name it in one or state "
        "the exemption here with the scope anchor that justifies it")
    assert not (exempt & specific), (
        f"stale exemption: surface(s) {sorted(exempt & specific)} are exempted "
        "from the single-scenario rule but a single-scenario row now exercises "
        "them — remove the exemption (and the doc's exception note)")


def test_the_specific_e2e_guard_rejects_the_catch_all_it_replaces():
    """Non-vacuity, measured on the parser rather than asserted.

    Three receipts:
      1. the OLD assertion (union over every row) is green on the map even with
         `E2E-6` reverted — i.e. it could not have failed for its stated reason;
      2. it is also green when a surface is deleted from every single-scenario
         row, because `1-17 (all)` puts it back;
      3. the new union sees exactly that deletion.
    """
    surfaces = set(_surfaces())
    rows = _e2e_rows()
    # 1 — the pre-fix row state: the old union is blind to it
    reverted = {**rows, "E2E-6": {1, 2, 10}}
    assert set().union(*reverted.values()) == surfaces
    # 2/3 — surface 13's only single-scenario row is E2E-9; drop it everywhere a
    # single scenario would carry it
    no_13 = {k: (v if v >= surfaces else v - {13}) for k, v in rows.items()}
    assert set().union(*no_13.values()) == surfaces, (
        "the old union still equals the surface set — that is the vacuity")
    missing = surfaces - _specific_union(no_13, surfaces) - _e2e_catch_all_exemptions(
        _surfaces())
    assert missing == {13}, missing


def test_de2e6_exercises_every_door_it_cites():
    """A surface named in a `DE2E-N` header and never asserted in `Steps`/`Assert`
    is a scenario that cannot fail for that surface. PRE-FIX: `DE2E-6` cited
    `[1,2,10]` and its steps re-entered "via Settings Setup guide" only, so the
    failure mode the map writes into surface 9's Key Failure Modes — the next
    action rendering a second, independently-tracked setup list — was the one
    assertion no implementation-ready E2E would ever make.

    The scan is limited to `Steps`/`Assert` on purpose: the note beside the header
    quotes the scope and therefore names both doors, which would satisfy a
    whole-section search for a door whose assertion had been deleted.
    """
    section = _plan_section("DE2E-6")
    scope = _scope_section("### E2E-6")
    assert set(PLAN_DE2E6_DOOR_ASSERTIONS) == set(SCOPE_E2E6_DOOR_SURFACES), (
        "the plan's door table and the scope's door table must cover the same "
        f"doors: plan {sorted(PLAN_DE2E6_DOOR_ASSERTIONS)}, "
        f"scope {sorted(SCOPE_E2E6_DOOR_SURFACES)} — check 5 pins a door this "
        "check would then skip")
    exercised = _plan_assert_lines(section)
    for door, phrases in PLAN_DE2E6_DOOR_ASSERTIONS.items():
        assert door in scope, (
            f"`03-scope.md` E2E-6 no longer names {door!r} — the door authority "
            "moved; update SCOPE_E2E6_DOOR_SURFACES and this table together")
        for phrase in phrases:
            assert phrase in exercised, (
                f"`06-plan.md` DE2E-6 never ASSERTS {phrase!r} ({door}) — naming "
                "the door in Steps walks it; the claim that can fail is the "
                "Assert line, and it is missing")


def test_the_plan_door_pin_rejects_a_deleted_door_assertion():
    """Non-vacuity, on the shapes a looser scan accepts.

    1. Deleting surface 10's whole `**Assert:**` clause must fail — while the door
       survives in `**Steps:**` and in the note beside the header (a whole-section
       scan, and a Steps-or-Assert scan, are both green on that state).
    2. Deleting surface 9's next-action arm must fail even though the
       Continue-setup arm still names the surface.
    """
    section = _plan_section("DE2E-6")
    without_assert_clause = re.sub(
        r"\*\*Settings Setup guide \(surface 10\).*?\*\*;", "", section)
    assert without_assert_clause != section, (
        "the surface-10 Assert clause the replay deletes is gone from the plan — "
        "update this receipt")
    assert "Settings Setup guide" in without_assert_clause, (
        "the door still appears in Steps and in the note — that is the trap a "
        "whole-section scan and a Steps-or-Assert scan fall into")
    assert "Settings Setup guide" not in _plan_assert_lines(without_assert_clause), (
        "the guard did not notice the deleted Assert clause")
    without_next_action = re.sub(
        r"\*\*Overview next action \(surface 9, points>0 arm\).*?\*\*[^;]*;", "",
        section)
    assert without_next_action != section, (
        "the surface-9 next-action arm the replay deletes is gone from the plan")
    assert "Continue-setup card" in _plan_assert_lines(without_next_action), (
        "surface 9's other arm still names it — the trap")
    assert "next action" not in _plan_assert_lines(without_next_action).lower(), (
        "the guard did not notice the deleted next-action arm")
    asserted = _plan_assert_lines(section)
    assert "Settings Setup guide" in asserted, "the live plan must ASSERT door 1"
    assert "Continue-setup card" in asserted and "next action" in asserted.lower(), (
        "the live plan must ASSERT both of surface 9's arms")


def test_every_de2e_row_cites_the_maps_e2e_surfaces():
    """`06-plan.md` §7 derives its DE2E rows from this map in its own words ("Test-
    design surface refs [#N] from `04-test-design.md`"), so each header bracket is
    a COPY of the map's E2E row — the same claim the capstone's `Surfaces` column
    makes, one consumer over, where nothing read it.

    PRE-FIX: `### DE2E-6 — Graph-held resumption [1,2,10]` while the map says
    `1, 2, 9, 10`. Eleven of the twelve agreed, so the disagreement was this
    map's own correction stopping one document short.
    """
    plan, mapped = _plan_de2e_surfaces(_plan_lines()), _e2e_rows()
    assert len(plan) == 12, f"could not parse the plan's 12 DE2E rows: {sorted(plan)}"
    plan_keys = {f"E2E-{name.split('-')[1]}" for name in plan}
    assert plan_keys == set(mapped), (
        "the plan's DE2E rows drifted from the map — "
        f"missing {sorted(set(mapped) - plan_keys)}, "
        f"extra {sorted(plan_keys - set(mapped))}")
    drifted = _de2e_drift(plan, mapped)
    assert not drifted, (
        "`06-plan.md` copies its DE2E surface brackets from the map but disagrees "
        "(plan vs map): "
        + "; ".join(f"{k}: {sorted(v[0])} vs {sorted(v[1])}"
                    for k, v in sorted(drifted.items())))


def test_the_de2e_pin_rejects_the_pre_fix_bracket():
    """Non-vacuity: the comparison above must fail on the exact pre-fix state
    (`[1,2,10]` against the map's `1, 2, 9, 10`) and must not fire on a copy,
    including the range form `1-17` the map writes as `1-17 (all)`."""
    mapped = {"E2E-6": {1, 2, 9, 10}, "E2E-12": set(range(1, 18))}
    assert _de2e_drift({"DE2E-6": {1, 2, 10}, "DE2E-12": set(range(1, 18))},
                       mapped) == {"DE2E-6": ({1, 2, 10}, {1, 2, 9, 10})}
    assert not _de2e_drift({"DE2E-6": {1, 2, 9, 10},
                            "DE2E-12": set(range(1, 18))}, mapped)
    # the live documents, read through their own parsers, agree
    assert not _de2e_drift(_plan_de2e_surfaces(_plan_lines()), _e2e_rows())


def test_capability_rows_are_the_scopes_rows_in_order():
    scope_names = [
        _cells(line)[0]
        for line in SCOPE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\|\s*(W\d+) [^|]*\|", line)
    ]
    doc_names = list(_capabilities())
    assert scope_names, "could not parse 03-scope.md §2"
    assert doc_names == scope_names, (
        "the map's capability rows drifted from the scope — "
        f"missing {sorted(set(scope_names) - set(doc_names))}, "
        f"extra {sorted(set(doc_names) - set(scope_names))}, "
        f"order {doc_names} vs {scope_names}")
    assert not any(name.startswith("W10 ") for name in doc_names), (
        "W10 is out of scope (03-scope.md §1) and must not have a row")


# ═══════════════════════════════════════════════════════════════════════
# 4-8: the defect pins (each fails on the pre-fix document)
# ═══════════════════════════════════════════════════════════════════════

def test_a_capability_row_cites_every_surface_its_rationale_names():
    """PRE-FIX: `W5 setup card` cited `9, 17` while its bullet placed the counted
    checklist and the re-open path on surface 10 — two answers for one
    capability. Post-fix the row is `9, 10, 17`.

    Citations must use the explicit `surface N` form (see `_cited_surfaces`), and
    `assert named` keeps the guard from passing vacuously on a bullet with no
    explicit citation at all."""
    capabilities = _capabilities()
    for prefix in ("W5 setup card", "W9 trigger/entry", "W7 fusion",
                   "W2 universal command"):
        cited = capabilities[prefix][0]
        named = _cited_surfaces(_section_text(prefix))
        assert named, (
            f"{prefix}: the rationale cites no surface in the explicit "
            "`surface N` form — the row/prose guard would pass vacuously")
        assert named <= set(_surfaces()), (
            f"{prefix}: rationale names {sorted(named - set(_surfaces()))}, "
            "which are not surfaces")
        assert named <= cited, (
            f"{prefix}: the rationale names surface(s) {sorted(named - cited)} "
            f"but the row cites only {sorted(cited)} — a reader reconciling "
            "row and prose gets two answers")


def _contract_doors() -> set[int]:
    """Surfaces whose own Contract cell names a Setup-guide re-entry door."""
    return {
        n for n, s in _surfaces().items()
        if "Setup guide" in s["contract"] or "Continue-setup" in s["contract"]
    }


def test_e2e6_cites_every_surface_that_owns_a_reentry_door():
    """PRE-FIX: `E2E-6 graph-held resumption | 1, 2, 10` omitted surface 9, the
    Overview element the document itself called the Continue-setup card — the
    scope's second door in "via the Settings Setup guide **or the Continue-setup
    card**" (`03-scope.md` E2E-6). Post-fix surface 9 is cited (`1, 2, 9, 10`).

    The door set is anchored in the scope (see `_missing_e2e6_doors`), so the pin
    cannot be shrunk by rewording the document under test.
    """
    scope = _scope_section("### E2E-6")
    for door in SCOPE_E2E6_DOOR_SURFACES:
        assert door in scope, (
            f"`03-scope.md` E2E-6 no longer names {door!r} — the door authority "
            "moved; update SCOPE_E2E6_DOOR_SURFACES in the same edit")
    contract_doors = _contract_doors()
    assert 10 in contract_doors, (
        "surface 10 owns the Settings Setup guide — the contract scan found "
        f"{sorted(contract_doors)}")
    cited = _e2e_rows()["E2E-6"]
    missing = _missing_e2e6_doors(
        cited, scope_text=scope, contract_doors=contract_doors)
    assert not missing, (
        f"E2E-6 cites {sorted(cited)} but the re-entry doors are on "
        f"{sorted(missing)} too — a door with no E2E row has no scenario")


def test_the_e2e6_door_guard_rejects_a_reworded_contract_plus_a_reverted_row():
    """The first version of the E2E-6 pin was FAIL-OPEN, and this is the receipt.

    It derived `doors` only from the map's Contract column and asserted only
    `10 in doors`; the door that motivated the pin lived on surface 9, whose
    contract named it. So this state passed it:

        surface 9's contract reworded to drop "Setup guide"/"Continue-setup"
        E2E-6 reverted to `1, 2, 10`

    An anchored door set rejects the same state — the scope says both doors
    exist, and it is not the document under test.
    """
    scope = _scope_section("### E2E-6")
    assert _missing_e2e6_doors(
        {1, 2, 10}, scope_text=scope, contract_doors={10}) == {9}


def test_the_counted_dismissible_checklist_belongs_to_surface_10_not_9():
    """PRE-FIX: surface 9's contract claimed "a dismissible checklist rendered
    from `OnboardingState`.completed_steps". The shipped Overview next-action
    element is one next step + a CTA into the Settings Setup guide; the counted
    N-of-M checklist is the Settings-tab Setup guide card; neither is
    dismissible. If the dashboard ever GAINS a dismissal affordance, update the
    doc and this pin together — they are the same claim."""
    surfaces = _surfaces()
    nine, ten = surfaces[9], surfaces[10]
    assert "dismissible" not in nine["contract"].lower(), (
        "surface 9's next action is not dismissible in the shipped dashboard "
        "(the only `dismiss` hits are the recovery/claim/billing banners)")
    assert "dismissible" not in nine["failure_modes"].lower(), (
        "surface 9's failure modes must not assume a dismissal affordance")
    assert "checklist" in ten["contract"].lower(), (
        "surface 10 owns the counted checklist (the Setup-guide card) — its "
        "contract must say so, or the checklist has no owner and surface 9's "
        "`not a checklist` claim has nothing to point at")
    assert "same graph-held state" in nine["contract"].lower(), (
        "surface 9's next-action element must be stated as rendering the SAME "
        "graph-held state as the Settings Setup guide (`completed_steps` is a "
        "read projection, never a second store)")


def test_no_orphan_close_paren_after_a_code_span():
    """The reviewed head already read ``OnboardingState``'s completed_steps`` —
    the stray `)` the review reported (`rendered from
    `OnboardingState`).completed_steps`) was gone by `1758bbe59`. Kept as a
    GUARD (it would have caught it), with the detector's own non-blindness
    asserted on synthetic text so it cannot pass by seeing nothing."""
    assert _orphan_close_parens("rendered from `OnboardingState`).completed"), (
        "the detector misses the shape the review reported")
    assert not _orphan_close_parens("rendered from `OnboardingState`'s completed")
    assert not _orphan_close_parens("(`completed_steps` is a read projection)"), (
        "a `)` that CLOSES a real `(` is not an orphan")
    offenders: list[str] = []
    for number, surface in _surfaces().items():
        for column in ("contract", "failure_modes", "layer"):
            offenders += [
                f"surface {number}.{column}: …{frag}…"
                for frag in _orphan_close_parens(surface[column])
            ]
    assert not offenders, "orphan `)` after a code span:\n" + "\n".join(offenders)


def test_the_checklist_row_carries_the_surface_9_projection_obligation():
    """PRE-FIX: the `9-10 overview + settings` checklist row said only
    "3 elements zero toggles; sources only in Settings; single-owner merge
    discipline" — the projection obligation lived only in the surface table,
    which is not what a child issue is told to satisfy."""
    row = next(
        (ln for ln in _doc_lines()
         if ln.startswith("| 9-10 overview + settings")), None)
    assert row, "the `9-10 overview + settings` verification-checklist row is gone"
    lowered = row.lower()
    assert "read projection" in lowered, (
        "the child-issue checklist row must carry the `completed_steps` read-"
        "projection obligation the surface table states")
    assert "never a second store" in lowered
    assert "checklist" in lowered, (
        "the row must say the Overview next action is not a counted checklist "
        "of its own")


def test_the_ux_decision_row_the_map_cites_no_longer_claims_a_dismissible_list():
    """PRE-FIX: `05-ux-decisions.md` row 5 — "Affordance: setup card", the row the
    map's W5 rationale cites as its authority — read "Dashboard renders the SAME
    state as a **dismissible checklist card**; disappears when complete". It named
    no door, so it licensed exactly the claim the map had to correct: the shipped
    Overview next action is one next step + a CTA (`OverviewNextActionCard`), the
    counted checklist is the Settings-tab `SetupGuideCard`, and neither carries a
    dismissal. A map that points, for its corrected claim, at a decision row still
    stating the superseded one is the same "two answers for one capability" defect
    one document over.

    The LIVE half of the row is checked (everything before the recorded
    correction), so the superseded wording may stay quoted as history.
    """
    row = next(
        (ln for ln in UX.read_text(encoding="utf-8").splitlines()
         if ln.startswith("| 5 | Affordance: setup card")), None)
    assert row, "the `Affordance: setup card` decision row is gone"
    live = row.split("Corrected")[0].lower()
    assert "overview" in live, (
        "the row must say which door renders what — naming no door is what let "
        "the Overview next action be read as the dismissible checklist")
    assert "never a counted checklist" in live, (
        "the row must state that the Overview next action is not a counted "
        "checklist of its own")
    assert "dismissible checklist card" not in live, (
        "the shipped Overview next-action element carries no dismissal — the "
        "collapse/dismissible-hide belongs to the Settings Setup guide")
    conflicts = _dismissal_claim_conflicts(live)
    assert not conflicts, (
        "the row gives two answers for the dismissal claim it is cited for:\n- "
        + "\n- ".join(conflicts) + "\nrow: " + live)


def test_the_dismissal_door_guard_rejects_the_one_sentence_contradiction():
    """Non-vacuity, and the RECEIPT for why the previous guard was too narrow.

    The reviewed head's live row said the Settings card "collapses to compact
    status / dismissible-hide with re-open path when complete" and then "neither
    card carries a dismissal" — both claims in one sentence. The literal-only
    assertion above (`"dismissible checklist card" not in live`) passed it,
    because the contradiction lives in the two doors, not in one phrase.
    """
    prefix_head = (
        "Dashboard renders the SAME state on both of its doors — the Settings "
        "Setup guide is the counted checklist card (collapses to compact status "
        "/ dismissible-hide with re-open path when complete, per row \"Layout: "
        "Setup guide\"); the Overview next action mirrors that state as ONE next "
        "step + a CTA into the guide — never a counted checklist of its own, "
        "and neither card carries a dismissal. ")
    assert _dismissal_claim_conflicts(prefix_head), (
        "the guard passes the exact self-contradicting sentence it exists for")
    assert any("door-LESS" in c for c in _dismissal_claim_conflicts(prefix_head))
    # a row that never says what the Overview door does is the same defect from
    # the other side: silence let the next action be read as the checklist.
    silent = ("the Settings Setup guide is the counted checklist card "
              "(dismissible-hide with re-open path when complete)")
    assert _dismissal_claim_conflicts(silent), (
        "a row that never states the Overview door's answer must not pass")
    # and the corrected shape must be clean, in both doors' presence
    good = ("the Settings Setup guide is the counted checklist card, and the "
            "decision recorded for THAT door is dismissible-hide with a "
            "re-open path when complete; the Overview next action mirrors that "
            "state as ONE next step + a CTA and carries no dismissal")
    assert not _dismissal_claim_conflicts(good), _dismissal_claim_conflicts(good)


# ═══════════════════════════════════════════════════════════════════════
# 10-12: the superseded mirror claim, reconciled everywhere it survived
# ═══════════════════════════════════════════════════════════════════════

#: The pre-fix wording. It named no door, so it read as licensing the
#: "Overview renders the counted dismissible checklist" claim the map dropped.
_SUPERSEDED_MIRROR = "dismissible checklist card"
_ALIGN = EPIC / "00-align-and-scope.md"


def test_the_scope_e2e6_mirror_claim_is_reconciled_where_it_was_left_stale():
    """PRE-FIX: `03-scope.md` §4 E2E-6 — the AUTHORITY this module's E2E-6 door
    pin anchors to, "which the document under test cannot reword" — still read
    "mirrored as a dismissible checklist card" after the map and
    `05-ux-decisions.md` row 5 were corrected. The epic then carried two answers
    for one claim, in the document a reader is told to trust.

    Only the MIRROR claim is reconciled: the `When:` line's door names are what
    the anchor binds to and they are untouched (asserted by
    `test_e2e6_cites_every_surface_that_owns_a_reentry_door`)."""
    section = _scope_section("### E2E-6")
    live = section.split("Superseded wording")[0].lower()
    assert "counted checklist" in live, (
        "the scope's E2E-6 must name the counted checklist card explicitly — "
        "`dismissible checklist card` alone named no door: " + live)
    assert "one next step" in live and "cta" in live, (
        "the scope's E2E-6 must state the Overview mirror's actual shape (one "
        "next step + a CTA into the guide), not just that a card exists")
    for line in section.splitlines():
        if _SUPERSEDED_MIRROR in line:
            assert "superseded" in line.lower(), (
                "the pre-fix mirror claim may survive only where it is marked "
                "as superseded history: " + line)


def test_the_historical_align_copy_carries_the_supersession_note():
    """`00-align-and-scope.md` keeps the pre-fix wording as history (its E2E-6
    is the record of what was aligned). History is fine; UNMARKED history is
    the third answer for one claim."""
    lines = _ALIGN.read_text(encoding="utf-8").splitlines()
    hits = [i for i, ln in enumerate(lines) if _SUPERSEDED_MIRROR in ln]
    assert hits, (
        "the align record no longer carries the wording this pin exists for — if "
        "it was deleted outright, delete this guard in the same edit")
    for i in hits:
        window = " ".join(lines[i : i + 4]).lower()
        assert "superseded" in window, (
            "the align record keeps the pre-fix wording, so the supersession "
            "note must sit beside it: " + lines[i])


def test_the_scope_w5_capability_bullet_does_not_assert_the_dismissible_mirror():
    """The same claim appears a second time in `03-scope.md` §2's **W5** bullet
    — "dashboard mirror (**dismissible checklist card**)" — which is the
    capability definition the map's W5 rows are cut from, and
    `00-align-and-scope.md` carries the same bullet in its alignment record.

    Found by this guard, not by the review: reconciling only the E2E-6 line
    leaves the scope still stating the superseded mirror in the place a child
    issue reads its capability from."""
    for path in (SCOPE, _ALIGN):
        bullet = next(
            (ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.startswith("- **W5 — Graph-held")), None)
        assert bullet, f"{path.name}: the W5 capability bullet is gone"
        assert _SUPERSEDED_MIRROR not in bullet, (
            f"{path.name}: the W5 bullet still states the superseded "
            "\"dashboard mirror (dismissible checklist card)\" — the counted "
            "checklist is the Settings door's and the Overview mirror is one "
            "next step + a CTA")
        assert "counted checklist card" in bullet and "Overview" in bullet, (
            f"{path.name}: the W5 bullet must name both doors' shapes")


def test_the_ux_classification_row_does_not_re_cite_the_superseded_claim():
    """PRE-FIX: the `Visual affordances` classification row sourced itself to
    "Epic body (**dismissible card**, ...)" — pointing the reader at the very
    line that was corrected."""
    row = next(
        (ln for ln in UX.read_text(encoding="utf-8").splitlines()
         if ln.startswith("| 5 | Visual affordances")), None)
    assert row, "the `Visual affordances` classification row is gone"
    lowered = row.lower()
    assert "dismissible card" not in lowered, (
        "the row still cites the epic body's superseded `dismissible card` "
        "wording; the affordance is the counted checklist on the Settings door "
        "and one next step + a CTA on the Overview one")
    assert "affordance: setup card" in lowered, (
        "the row must point at the corrected decision row it describes")
