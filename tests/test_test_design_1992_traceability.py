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

Every surface is also exercised by at least one high-level E2E row (the E2E-side
reverse direction) — a surface no scenario touches has no scenario that would
catch its regression. **The catch-all row is spliced out before that comparison,
and the one exception is stated in the map itself:** `E2E-12 cross-W journey |
1-17 (all)` expands to the whole surface set, so including it made the first
version of the assertion true by construction — it stayed green with every
specific row deleted. Surface **14 (onboarding telemetry)** is the documented
exception: its W11 events fire at the seed/decide write paths that E2E-1 and
E2E-12 walk, so no scenario of its own exists.

NON-VACUITY
-----------
Checks 4, 5, 6, 8, 10 and 11 fail on the reviewed head's document/text (each
docstring names the pre-fix text; check 10 additionally replays the exact
self-contradicting sentence through the guard in-suite). Checks 1-3 are the
standing invariants the earlier cycles established, check 9 is a parser-shape
guard and check 7 is a guard whose detector is proved non-blind — guard rails,
not defect pins. The E2E reverse direction is a **pre-fix defect pin** once its
catch-all is spliced out: surface 14 is then the only uncovered surface, and the
`E2E-6` surface set is pinned against the plan's own `DE2E-6` header too
(`test_the_plans_de2e_surface_refs_are_the_maps_e2e_rows`), because the plan
declares those refs are copied from this map and this PR is what
desynchronised them. The pre-fix blobs are replayed by pointing this module's
pure functions at them (`git show <sha>:<path>`), which is how the non-vacuity
claims were measured; reverting the four docs to `fe06e3a98` fails checks 10 and
11 (five tests) and nothing else.
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
PLAN = EPIC / "06-plan.md"
UX = EPIC / "05-ux-decisions.md"

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


def _plan_de2e_surfaces() -> dict[str, set[int]]:
    """`E2E-N` → the surfaces `06-plan.md`'s `### DE2E-N … [surfaces …]` header
    cites. `06-plan.md:609` declares those refs are copied from the map."""
    out: dict[str, set[int]] = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^### DE2E-(\d+)\b.*?\[([^\]]+)\]\s*—\s*E2E-\d+", line)
        if not m:
            continue
        numbers: set[int] = set()
        for chunk in re.findall(r"\d+\s*-\s*\d+|\d+", m.group(2)):
            if "-" in chunk:
                lo, hi = (int(p) for p in chunk.split("-"))
                numbers.update(range(lo, hi + 1))
            else:
                numbers.add(int(chunk))
        out[f"E2E-{m.group(1)}"] = numbers
    return out


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


#: Surfaces the map documents as covered ONLY by the catch-all
#: `E2E-12 cross-W journey | 1-17 (all)` row. The exception is stated in
#: `04-test-design.md` (pinned by
#: `test_the_catch_all_only_exception_is_stated_in_the_map`), not only here.
CATCH_ALL_ONLY_SURFACES = {14}


def _uncovered_by_specific_e2e_rows(
    rows: dict[str, set[int]], surfaces: set[int]
) -> set[int]:
    """Surfaces no row names EXCEPT the catch-all.

    The catch-all (`E2E-12 … | 1-17 (all)`) expands to the whole surface set, so
    leaving it in the union makes the reverse direction true BY CONSTRUCTION:
    the first version asserted `union(rows) == surfaces`, which stayed green with
    every specific row deleted — and on the pre-fix map. Splicing it out is what
    turns the assertion into a second direction."""
    specific = {k: v for k, v in rows.items() if v != surfaces}
    if not specific:
        return set(surfaces)
    return set(surfaces) - set().union(*specific.values())


def test_every_surface_is_exercised_by_a_specific_e2e_row():
    """The E2E-side reverse direction: a surface no scenario walks has no
    scenario that would catch its regression.

    PRE-FIX GUARD (found on the review AFTER the first fix): the assertion was
    `union(_e2e_rows().values()) == set(_surfaces())`, which the `E2E-12 cross-W
    journey | 1-17 (all)` row satisfies unconditionally. It was green on the
    pre-fix map and would stay green if every specific row were deleted, so it
    read as a second direction while carrying no information. The catch-all is
    spliced out; the only surface left uncovered is the one exception the map
    states — surface 14 (telemetry), whose W11 events fire at the seed/decide
    write paths E2E-1 and E2E-12 walk."""
    surfaces = set(_surfaces())
    uncovered = _uncovered_by_specific_e2e_rows(_e2e_rows(), surfaces)
    assert uncovered == CATCH_ALL_ONLY_SURFACES, (
        "surfaces no specific E2E row exercises: "
        f"{sorted(uncovered - CATCH_ALL_ONLY_SURFACES)} (the documented "
        f"catch-all-only exception is {sorted(CATCH_ALL_ONLY_SURFACES)})")


def test_the_specific_e2e_guard_rejects_a_table_whose_only_coverage_is_the_catch_all():
    """Receipt for the vacuity fix, driven through the same helper.

    The pre-fix assertion PASSED on both of these states. Post-fix the first is
    rejected outright, and the second (a specific row reverted to the pre-fix
    `E2E-6 | 1, 2, 10`) is caught by the per-surface assertion above."""
    surfaces = set(_surfaces())
    rows = _e2e_rows()
    catch_all = {k: v for k, v in rows.items() if v == surfaces}
    assert catch_all, "no catch-all row parsed — this receipt would be vacuous"
    assert _uncovered_by_specific_e2e_rows(catch_all, surfaces) == surfaces, (
        "with only the catch-all present the guard must see EVERY surface "
        "uncovered — that is exactly the state the old assertion passed")
    assert _uncovered_by_specific_e2e_rows(rows, surfaces) == CATCH_ALL_ONLY_SURFACES, (
        "the real table leaves only the documented exception uncovered")


def test_the_catch_all_only_exception_is_stated_in_the_map():
    """The exception belongs in the document a reader consults, not only in this
    module's constant: without it, `14` reads as a dropped row instead of the
    one surface with no scenario of its own."""
    paragraphs = [
        p for p in re.split(r"\n\s*\n", DOC.read_text(encoding="utf-8"))
        if "catch-all" in p.lower()
    ]
    assert paragraphs, (
        "the map must state which surface only the E2E-12 catch-all covers")
    assert any("telemetry" in p.lower() and "14" in p for p in paragraphs), (
        "the exception paragraph must name surface 14 (onboarding telemetry) — "
        "the guard's CATCH_ALL_ONLY_SURFACES is that paragraph's copy, and a "
        "silent exception is the drift this module exists to remove")


def test_the_plans_de2e_surface_refs_are_the_maps_e2e_rows():
    """PRE-FIX: `06-plan.md` read `### DE2E-6 — Graph-held resumption
    [1,2,10]` while the map says `1, 2, 9, 10`. The plan declares those refs are
    copied "from `04-test-design.md`" (L609) and 11 of its 12 headers matched, so
    this PR changed the one row that was in sync and left the plan on the old
    value — two answers for "which surfaces does E2E-6 exercise?" in the document
    the map's footer names as its first consumer, with no guard reading it."""
    mapped = _e2e_rows()
    planned = _plan_de2e_surfaces()
    assert planned, "could not parse any `### DE2E-N … [surfaces …]` header"
    assert set(planned) == set(mapped), (
        f"the plan's DE2E rows are {sorted(planned)} but the map's E2E rows are "
        f"{sorted(mapped)}")
    for key in sorted(mapped):
        assert planned[key] == mapped[key], (
            f"{key}: the plan's DE2E header cites {sorted(planned[key])} but the "
            f"map's E2E row is {sorted(mapped[key])}")


def test_de2e6_exercises_the_surface_9_door_it_cites():
    """A citation with nothing behind it is the defect the bracket pin above
    catches structurally; this catches it substantively.

    PRE-FIX: DE2E-6 cited `1,2,10` and its Steps/Assert exercised only the
    Settings door ("re-enter via Settings Setup guide"; "Setup guide card
    renders same state"), so the failure mode surface 9's Key Failure Modes
    names — an Overview next action rendering a second, independently-tracked
    checklist — had no assertion anywhere in the implementation-ready E2E. If
    this prose is rewritten, keep the door and the no-second-list assertion in
    it: that is what makes the surface-9 citation true."""
    lines = PLAN.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("### DE2E-6 ")), None)
    assert start is not None, "the plan's `### DE2E-6` header is gone"
    end = next(
        (i for i, ln in enumerate(lines[start + 1 :], start + 1)
         if ln.startswith("### ")), len(lines))
    block = "\n".join(lines[start:end]).lower()
    assert "overview" in block, (
        "DE2E-6 cites surface 9 (the Overview re-entry door) but never steps "
        "through it — the citation would be decorative")
    assert "one next step" in block and "never a second" in block, (
        "DE2E-6 must assert the Overview door's shape (one next step + a CTA, "
        "never a second, independently-tracked checklist) — otherwise the "
        "failure mode surface 9's Key Failure Modes names is unasserted")


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
# 4-9: the defect pins and the parser/projection guards beside them
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


# ═══════════════════════════════════════════════════════════════════════
# 10: every dismissal claim names its DOOR (the row, and the receipt)
# ═══════════════════════════════════════════════════════════════════════

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
# 11: the superseded mirror claim, reconciled everywhere it survived
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
