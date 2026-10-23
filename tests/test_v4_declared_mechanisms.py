"""#5064 — the declared-mechanism gate (the bounded slice).

THE ROOT THIS PINS, IN ONE SENTENCE
-----------------------------------
*"The design documents a substrate the code does not have"* — extractor v4
declares selection, dedup, merge, span-linkage, an embedding gate, a sufficiency
router and a fan-out cap as adopted decisions, and **nothing compared the
declared set against the code set, so a declared mechanism read exactly like a
shipped one.**

This gate makes the declaration checkable. `config/v4-mechanisms.yml` is the ONE
place the declared set lives; this file is the gate over it. Every declared
mechanism must be either

  * **implemented** — a code path AND a test, every declared path present; or
  * **not-implemented** — an explicit marker (a tracking issue);

and a mechanism that is **neither** FAILS the gate. A mechanism with code but no
test is not "implemented with a test" — that is the `declared ⇒ present`
inference this issue exists to kill, one level down.

⭐ THE CODE ANCHOR IS A SYMBOL, NOT A FILE. `code:` used to prove only that a
file existed at the declared path, so a mechanism whose symbol had been renamed
or removed — or that was never written there — still read as shipped for as long
as any `.py` sat at that path (`tortoise/fanout.py` losing
`PER_ENTITY_FANOUT_CAP` is the concrete case; the cited test is a second opinion,
not an anchor, because re-shaping a mechanism is exactly when its test is
edited). An `implemented` row therefore names its `symbols:` and the gate
resolves each one in the file(s) the SAME row declares — so "the mechanism is
there" is a fact about the code, not about the path.

WHAT THIS GATE DELIBERATELY DOES NOT DO (#5064, bounded slice)
-------------------------------------------------------------
* It does not implement any mechanism. Three of the eight are genuinely absent
  and are *meant* to be marked `not-implemented` — recording an absence is the
  output, not a failure.
* It does not generate a design document from the registry (indicator (2) /
  #5064's open question (a): registry-as-source vs registry-as-check). The
  registry is a CHECK over the two design documents here, not their source.

⭐ MEMBERSHIP IS CHECKED IN BOTH DIRECTIONS, because `DECLARED_IDS` alone cannot
prove it is COMPLETE — and that is how `state_value_carve_out` (the verbatim
state/operational-value carve-out, #2453) stayed unregistered while this gate
stayed green: a hand-written list of seven cannot notice an eighth mechanism the
design declares in prose. So every mechanism the registry knows about carries a
`<!-- v4-mechanism: <id> -->` marker in EACH `docs/architecture/**/*.md` document
its row names — the convention is to place it at the declaration site, and that
placement is a DISCIPLINE, not something this gate can read (see the stated
limits) — and `membership_errors` fails closed on FOUR shapes:

  * a marker in `docs/architecture/**/*.md` whose id has NO registry row — the
    declared-and-absent shape this whole issue exists to kill;
  * a registry row whose id NO design document marks — a row whose declaration
    cannot be found, i.e. an *unmarked* row (#5064 refactor direction 1: "there
    is no third state and no unmarked row");
  * a row's own `declared_in:` document inside the scanned surface that does NOT
    carry that row's marker — half of a bi-document declaration rotted away
    (review cycle 1, P2): comparing the two id SETS proved they agreed, never
    that a row's OWN declaration was the one found, so deleting one of two
    markers stayed green while the row kept claiming both documents declared it;
    and
  * a marker sitting in a document the row does NOT declare, or a row that names
    no document inside the scanned surface at all — the ATTRIBUTION and coverage
    half (#9778 review P2): the enumeration could otherwise shrink back to a
    single document, or read as marked while its declaration site is unreachable.

⚠️ ITS STATED LIMITS, because overclaiming here is the same defect:

  * an UNMARKED prose declaration is invisible, in every shape — measured, all
    three clean: a mechanism declared with no row at all (this issue's own
    starting defect); a restatement of a mechanism whose row exists, in a
    document the row does not name; and a restatement inside a document the row
    DOES name, once some other site in that document is marked. The comparison
    is over markers, so the residual follows from that rather than from a list;
    the marker is a convention and nothing forces prose to adopt it — it is what
    makes the comparison POSSIBLE (there is no other machine-readable surface in
    a prose design). Which declarations exist TODAY is the enumeration
    deliverable #5064's Scope section names, and that enumeration is the
    backfill, not a property the gate can derive. What IS closed is every MARKED
    case: a marker in a document the row omits is refused, so the registry's
    enumeration cannot silently shrink;
  * a marker DETACHED from what it declares is not detected: the marker's
    position inside a document is a convention, so moving it to the file's end
    and deleting the passage it marked leaves the gate green (measured — a
    pristine copy with §13.8's fan-out row removed and the marker appended at
    the end of the file passed every v4 guard). Prose cannot be parsed for
    "this paragraph declares the mechanism", so this is a permanent limit of a
    marker comparison, not a missing rule;
  * a `declared_in:` entry the marker scan cannot read — `embed_gate`'s
    `packs/dev/manifest.yaml` is the live case — is NOT marker-checked: the
    convention is a markdown-comment convention, so the scan is `.md` files under
    `docs/architecture/` and nothing else. Such a row still has to carry its
    marker in one of its scanned documents, so the row is never unverifiable;
    what is uncovered is that extra source, and it is named in the registry
    header rather than silently assumed to be checked. The same holds for a
    non-markdown file INSIDE the architecture dir: the surface is `**/*.md`, so a
    `.yaml` there is an uncovered source, not a document that owes a marker;
  * a SYMLINK to a document or to a directory in the surface is refused rather
    than followed — the walk and the attribution would disagree about its name (a
    linked directory is not descended, so a marker behind it would get no
    verdict), which is a verdict of its own and not a silent hole. No link exists
    there today; a NON-markdown link is an uncovered source, as above;
  * the sibling `*-CHANGELOG.md` is NOT scanned (`CHANGELOG_SUFFIX`): #8542 moved
    status and change narration into those files, so an entry recording a marker
    change may legitimately quote the convention, and scanning it would turn that
    quote into a declaration the row must name. The exclusion is applied by BOTH
    halves, so a row naming a changelog is not a false red either.
* It does not touch the extraction/write-path stages.
* It does not check that a cited test file is REGISTERED in the CI manifest —
  that surface is owned by `tools/ci_selection.py --integrity`. The gate checks
  that the path exists, not that CI runs it.

⭐ WHAT IT DOES CHECK ABOUT ORDER (#5064's Verification Checklist: *"a mechanism
whose precondition is unbuilt cannot be marked implemented"*). A row may declare
`preconditions:` — the OTHER declared mechanisms that must be in place first, so
the ordering is written down rather than discovered at implementation time
(#5064's refactor direction item 3: *"a **precondition** (the fan-out cap gates
deduplication) is ordered in the registry rather than discovered at
implementation time"*). The gate refuses four shapes, all fail-closed:

  * a precondition that is not a DECLARED mechanism (nothing can ever say
    whether it is built, so the dependant's claim is unverifiable);
  * a self-reference (`implemented` would require itself — vacuously true);
  * a CYCLE (`a → b → a` satisfies every per-row rule while nothing can be
    built first, so it is judged over the set);
  * an `implemented` row on top of a precondition that is NOT implemented —
    the same `declared ⇒ present` inference this gate exists to kill, applied
    to *order* instead of existence.

Ordering is declarable on a `not-implemented` row too, because that is where it
matters most: the absent mechanism records what it waits for, so the next lane
meets an ordering instead of a surprise.
"""

from __future__ import annotations

import ast
import re
import sys
import tokenize
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "v4-mechanisms.yml"

#: The declared set, pinned (#5064 O/I/T indicator (1): "VET, source dedup,
#: near-dup MERGE, SPAN link, EMBED gate, sufficiency router, fan-out cap" —
#: plus `state_value_carve_out`, the enumeration's first deliverable: #5064's
#: Verification Checklist names "verbatim operational values" as one of the
#: three write-path behaviours to verify, and the 2026-10-13 E2 comment records
#: *"mark the omission explicit in the registry this issue builds"*). Pinning the
#: tuple means a mechanism cannot be dropped from the registry without the gate
#: going red, which is the whole point: the set is declared, not implied.
DECLARED_IDS = (
    "vet",
    "source_dedup",
    "near_dup_merge",
    "span_link",
    "embed_gate",
    "sufficiency_router",
    "fanout_cap",
    "state_value_carve_out",
)

#: WHERE the design's declarations live, and HOW one is marked. The marker is the
#: only machine-readable surface a prose design has, so the marker convention is
#: what turns "the design declares a set" into something one gate can compare
#: against the registry — in BOTH directions (`membership_errors`) AND attributed
#: to the document the row itself names (#9778 review P2). Bounded to
#: `docs/architecture/**/*.md` (RECURSIVE — the scan and the attribution rule in
#: :func:`_scanned_targets` are ONE definition, not two) on purpose: the two
#: documents that declare the v4
#: write path live there, and widening the scan to every `.md` would make an
#: unrelated doc's prose able to red (or silently satisfy) the gate.
#:
#: ⚠️ A marker must be a WHOLE HTML comment (`<!-- v4-mechanism: <id> -->`, the id
#: lowercase, digits and underscores). The token appearing in ordinary prose —
#: even in backticks, documenting the convention — is not a declaration and is
#: ignored: a scan that counted bare token occurrences reported a malformed
#: marker for a sentence that mentioned one (review cycle 1, P2), which is a
#: false verdict from a gate whose verdicts are meant to be actionable.
ARCH_DOCS = ROOT / "docs" / "architecture"
MARKER_TOKEN = "v4-mechanism:"
MARKER_COMMENT = re.compile(r"^\s*v4-mechanism:\s*([a-z][a-z0-9_]*)\s*$")
#: The sibling change log is HISTORY, not a declaration site (#8542 keeps status
#: and change narration out of the design documents, which is exactly why a
#: changelog entry may quote the convention when it records a marker change).
#: Excluded from BOTH halves of the surface definition, so the scan and the
#: attribution rule cannot disagree about it.
CHANGELOG_SUFFIX = "-CHANGELOG.md"
#: The CONVENTION written down: an angle-bracketed placeholder id. It names no
#: mechanism, so it is neither a declaration nor a defect — skipped, which is
#: what lets a scanned document explain the convention without satisfying a row
#: (#9778 review). It can never make a row pass: a row whose only marker is the
#: placeholder is reported as UNMARKED by `membership_errors`.
PLACEHOLDER_ID = re.compile(r"^\s*v4-mechanism:\s*<[^<>\s]+>\s*$")
HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
IMPLEMENTED = "implemented"
NOT_IMPLEMENTED = "not-implemented"

#: The two states this bounded slice supports. ⚠️ #5064's own row model also
#: names a third, `descriptive` (its open question (c) asks whether it survives).
#: It is deliberately NOT accepted here, so a `descriptive` row FAILS CLOSED
#: until that question is settled — the safe direction, and an allow-list.
STATES = (IMPLEMENTED, NOT_IMPLEMENTED)

#: The optional ORDERING field: the other declared mechanisms a row must wait
#: for (#5064's refactor direction item 3 — *"a precondition … is ordered in the
#: registry rather than discovered at implementation time"*). Read by
#: `_precondition_cycles` and by the per-row rule in `gate_errors`.
PRECONDITIONS = "preconditions"


def _precondition_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """Every precondition cycle, each reported ONCE, as the path that closes it.

    Judged over the SET rather than per row, because a cycle satisfies every
    per-row rule: `a → b → a` with both rows `implemented` names a precondition
    that IS implemented, twice. Left unchecked the ordering is silently
    unsatisfiable — nothing in the cycle can be built first — while the gate
    reports nothing, which is the same class of defect as a declared-but-absent
    mechanism (true by construction, and invisible).

    Iterative (a registry is data, not a stack budget) and colour-marked: a
    GREY target closes a cycle over the current path; a BLACK node is finished
    and cannot take part in a cycle that has not been reported yet. Cycles are
    deduplicated by their node SET, so two entry points into one loop report it
    once.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    cycles: list[list[str]] = []
    reported: set[frozenset[str]] = set()
    for start in edges:
        if color.get(start, WHITE) != WHITE:
            continue
        color[start] = GREY
        path = [start]
        stack: list[tuple[str, object]] = [(start, iter(edges.get(start, ())))]
        while stack:
            node, it = stack[-1]
            descended = False
            for nxt in it:  # type: ignore[union-attr]
                seen = color.get(nxt, WHITE)
                if seen == WHITE:
                    color[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(edges.get(nxt, ()))))
                    descended = True
                    break
                if seen == GREY:  # a back edge: `path[path.index(nxt):]` is a loop
                    cycle = [*path[path.index(nxt):], nxt]
                    key = frozenset(cycle)
                    if key not in reported:
                        reported.add(key)
                        cycles.append(cycle)
            if not descended:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return cycles


def load_registry(path: Path = REGISTRY) -> dict:
    """Parse the declared-mechanism registry."""
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict), f"{path} must parse to a mapping"
    return doc


def _prose_only(text: str) -> str:
    """The document as scanned for markers — deliberately NOT code-stripped.

    An earlier revision removed fenced blocks and inline code spans first, and
    that was a half-implemented markdown parser: `~~~` fences, indented code
    blocks and double-backtick spans survived it, so a code-quoted marker was a
    declaration in some shapes and not others (review cycle 2, P2). The rule is
    now the TEXT, not the shape: a marker is an HTML comment naming an id, and it
    counts wherever it appears, code block included — a declaration sitting in a
    fenced example is still a declaration a reader of that document meets.

    The line is drawn on MEANING, not on shape: an angle-bracketed placeholder
    id (`<!-- v4-mechanism: <id> -->`) is the convention written down and is
    skipped (:data:`PLACEHOLDER_ID`); anything else comment-shaped that names no
    usable id is reported, because a neutered marker must not read as absent.
    """
    return text


def declaration_markers(docs_dir: Path = ARCH_DOCS) -> tuple[dict[str, list[str]], list[str]]:
    """``{mechanism id: [doc name, ...]}`` from the design docs' own markers.

    The marker is `<!-- v4-mechanism: <id> -->` and it sits at the DECLARATION
    SITE, so the id is read where the design says the thing, not from a list
    someone later maintained beside it. Two docs may declare the same mechanism
    (`span_link`, `state_value_carve_out`) — that is a declaration, not a
    duplicate defect, so repeats are collected rather than refused.

    FAIL CLOSED on the shapes that would otherwise be silent: a MALFORMED
    `v4-mechanism:` comment — one that neither names a mechanism id nor is the
    convention placeholder described two paragraphs below (`<!-- v4-mechanism:
    -->`, `Foo`, a hyphenated id) — is reported rather than skipped: a malformed
    declaration is not an absent one, and treating it as absent is how a marker
    gets neutered by a typo. An UNTERMINATED comment opener is reported too, for
    the same reason. The token in ordinary PROSE is not a declaration (see
    :func:`_prose_only` for where the line is drawn and why it is drawn there
    rather than by parsing markdown). A missing documents directory is reported:
    an unreadable surface is a verdict, not an empty set (the same polarity as
    `_defined_names` returning ``None``).

    The CONVENTION itself — an angle-bracketed placeholder id
    (``<!-- v4-mechanism: <id> -->``, which is how the rule is written down, and
    any other body that is one bracket-delimited run with no inner space, such as
    ``<span_link>``: the exemption is on the bracket, not on the word) — is
    skipped: it names no mechanism, so counting it as a declaration made
    describing the rule in a scanned document a red gate (#9778 review P3).

    The scan also EXCLUDES the sibling `*-CHANGELOG.md` (`CHANGELOG_SUFFIX`),
    which records history rather than design — that is the document #8542 moved
    status and change narration INTO, so an entry there may legitimately quote
    the convention, and scanning it would make every such quote a declaration.
    `_scanned_targets` applies the same exclusion, so the two halves keep one
    definition of the surface.

    The scan is RECURSIVE (`**/*.md`), and that is not a detail: the attribution
    rule in :func:`_scanned_targets` treats a document under the dir as inside the
    surface, so a non-recursive scan made the two halves disagree at nesting depth
    ≥ 1 — a marker in `docs/architecture/sub/x.md` was never collected (a
    declaration with NO verdict, silently: the fail-open shape this gate exists to
    refuse) while a row naming that same file was reported as an unmarked row even
    when it carried the marker (#9778 review P2). One surface, read the same way
    by both halves; markers are keyed by their path relative to the docs dir
    (`sub/x.md`), so a nested document is named exactly as the row names it — and
    every verdict below names the document that way too, because a basename is
    not a document identity (`a.md` can exist at two depths).

    ⛔ A SYMLINK to a DOCUMENT or to a DIRECTORY in the surface is a REFUSAL, not a
    document. The surface is read two ways — this scan WALKS it (`rglob`) while
    attribution RESOLVES each `declared_in:` entry — and those two readings cannot
    be kept identical across a link: `rglob` does not descend a linked DIRECTORY (a
    `v4-mechanism:` marker inside one would get NO verdict at all, the fail-open
    direction), and a linked DOCUMENT whose target lies outside the surface is
    walked under its link name while resolving outside it, so a row naming the
    link could never be attributed (an unsatisfiable red). Rather than teach both
    halves to agree about aliases, the alias is refused: no link exists in the
    surface today, and a declaration belongs in a document, not behind one. A
    non-markdown link is untouched by this — it is an uncovered SOURCE, exactly
    like a real `.yaml` beside it (see the stated limits).

    The second return value is the violation list, so one caller can report the
    malformed shapes and the membership verdicts together.
    """
    errors: list[str] = []
    markers: dict[str, list[str]] = {}
    if not docs_dir.is_dir():
        return markers, [
            f"the declaration-marker surface is missing: {docs_dir} is not a "
            f"directory — the gate cannot compare the declared set against "
            f"anything (fail closed, never an empty pass)"
        ]
    for doc in sorted(docs_dir.rglob("*")):
        rel = doc.relative_to(docs_dir).as_posix()
        if doc.is_symlink() and (doc.suffix == ".md" or doc.is_dir()):
            errors.append(
                f"{rel}: a symlink inside the declaration surface — this scan "
                f"does not follow a linked directory, and a linked document is "
                f"walked under one name while it resolves under another, so a "
                f"marker behind it could be read here and attributed nowhere "
                f"(fail closed): declare the mechanism in a real document under "
                f"{docs_dir.name}/"
            )
            continue
        if doc.name.endswith(CHANGELOG_SUFFIX):
            continue  # history, not a declaration site — see the docstring
        if doc.suffix != ".md" or not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:  # unreadable is a verdict
            errors.append(
                f"{rel}: a declaring document could not be read "
                f"({type(exc).__name__}) — its declarations cannot be "
                f"compared, which is not the same as having none"
            )
            continue
        comments = HTML_COMMENT.findall(_prose_only(text))
        for body in comments:
            if MARKER_TOKEN not in body:
                continue
            if PLACEHOLDER_ID.match(body):
                continue  # the convention itself, not a declaration
            match = MARKER_COMMENT.match(body)
            if match is None:
                errors.append(
                    f"{rel}: a `{MARKER_TOKEN}` comment does not name a "
                    f"mechanism id (expected `<!-- v4-mechanism: "
                    f"<lowercase_id> -->`, got `{body.strip()[:60]}`) — a "
                    f"malformed declaration is a violation, not an absence"
                )
                continue
            markers.setdefault(match.group(1), []).append(rel)
        # An opener with no closer is not a comment at all, so its marker (if it
        # carries one) is invisible to the scan above — the fail-OPEN direction.
        if _prose_only(text).count("<!--") > len(comments):
            errors.append(
                f"{rel}: an unterminated HTML comment (`<!--` with no "
                f"`-->`) — a marker inside it cannot be read, which must not "
                f"read as 'no declaration here'"
            )
    return markers, errors


def _declaring_paths(row: dict) -> list[str]:
    """The row's own `declared_in:` entries, defensively read.

    `gate_errors` owns the field's SHAPE (a non-list, a non-string entry, an
    escaping path are all its verdicts). This only has to answer "WHICH
    documents does the row say declare it" for the attribution and coverage
    rules, so it accepts the entries it can use and ignores the rest.

    An EMPTY answer is not this function's verdict to report either: the caller
    skips a row that names nothing, because `gate_errors` already says
    "declares no declaring document" and the membership rule already says
    UNMARKED — a coverage error on top of those two is a THIRD message for one
    defect, which is the noise this docstring exists to refuse (review P2).
    """
    declared = row.get("declared_in")
    if not isinstance(declared, list):
        return []
    return [d for d in declared if isinstance(d, str) and d.strip()]


def _scanned_targets(entries: list[str], docs_dir: Path) -> set[Path]:
    """The declared documents that resolve INSIDE the gate's scanned surface.

    ONE definition of the surface, shared with :func:`declaration_markers`, which
    walks it with `docs_dir.rglob("*")`: containment (recursive, so a
    `docs/architecture/sub/x.md` IS inside) AND a `.md` suffix (so a non-markdown
    source is not) — the two halves cannot disagree about what a scanned document
    is, which is what made a nested document invisible to the scan while being
    demanded of the row (#9778 review P2). The one place a walk and a resolution
    could still diverge — a SYMLINK — is refused outright by the scan rather than
    admitted here (`declaration_markers`: a linked directory is not descended and
    a linked file's target is outside the name it is walked under).

    Resolved against the docs dir's own root (`<root>/docs/architecture` ->
    `<root>`), not string-compared: a declaration written as
    `./docs/architecture/X.md` or with a doubled separator names the same
    document, and a string comparison would silently attribute it to nothing (a
    false red on a legitimate registry). Containment — not mere
    existence — is the test, so `packs/dev/manifest.yaml` is NOT a scanned
    declaring document: the markdown marker convention does not reach it. A
    `.yaml`/`.yml` INSIDE the architecture dir is not one either, for the same
    reason: `gate_errors` accepts such a source, it can carry no markdown
    comment, and demanding a marker there emits a remedy no edit can satisfy.
    (A symlink is refused by the scan, never resolved here — see
    :func:`declaration_markers`.)
    """
    scanned_dir = docs_dir.resolve()
    root = docs_dir.parent.parent
    targets: set[Path] = set()
    for entry in entries:
        rel = Path(entry)
        if rel.is_absolute() or rel.suffix != ".md":
            continue
        if rel.name.endswith(CHANGELOG_SUFFIX):
            continue  # the same exclusion the marker scan makes
        try:
            resolved = (root / rel).resolve()
        except (OSError, RuntimeError, ValueError):  # unresolvable: not attributable
            continue
        if resolved.is_relative_to(scanned_dir):
            targets.add(resolved)
    return targets


def membership_errors(
    mechanisms: list[dict], docs_dir: Path = ARCH_DOCS
) -> list[str]:
    """The BIDIRECTIONAL membership rule: no unregistered declaration, no
    undeclared row — and every scanned document a row declares must mark it.

    `gate_errors` proves every row is implemented-with-a-test or explicitly
    marked absent. It cannot prove the row set is COMPLETE — a hand-pinned tuple
    is exactly the shape that let a declared mechanism stay unregistered (#5064).
    So membership is read from the design documents and checked both ways:

      * a marker id with no registry row -> the declaration has no verdict, i.e.
        a declared mechanism that reads exactly like a shipped one (the root);
      * a registry row with no marker -> a row whose declaration cannot be found,
        which #5064's refactor direction rules out by name ("no unmarked row");
      * a row's own `declared_in:` document inside the scanned surface that does
        NOT carry that row's marker -> half of a bi-document declaration rotted
        away. The composite rule above compares `id` SETS, so deleting ONE of two
        markers left the suite green (review cycle 1, P2) while the registry kept
        claiming both documents declare it — the same declared-implies-present
        shape one level down;
      * a row whose `declared_in:` entries ALL lie outside the scanned surface
        -> its declaration site is unreachable, so the marker convention cannot
        confirm it, and an unverifiable row must not read as a marked one (#9778
        review P2). The rules above would pass it vacuously: with no scanned
        declaring document there is no per-document check to fail. A row with an
        EMPTY `declared_in:` is excluded from this bullet on purpose — it names
        nothing to reason about, and the shape defect is `gate_errors`' verdict,
        not a second one here.

    Kept SEPARATE from `gate_errors` on purpose: `gate_errors` is also the
    per-row mutation surface, and folding a repository-wide scan into it would
    make every synthetic row in those proofs also a membership defect — a verdict
    the synthetic case never meant to make.

    ⚠️ STATED LIMIT: a `declared_in:` entry the scan cannot read (e.g.
    `packs/dev/manifest.yaml`, `embed_gate`'s policy source, or a non-`.md` file
    inside the architecture dir) is not marker-checked — the convention is a
    markdown-comment convention and this gate scans `docs/architecture/**/*.md`
    only. Such a row must ALSO declare a scanned document carrying its marker, so
    the row is never unverifiable; what is uncovered is that extra source, and it
    is named in the registry header rather than silently assumed.

    ``mechanisms`` is the parsed registry list (same shape `gate_errors` takes).
    """
    markers, errors = declaration_markers(docs_dir)
    ids: set[str] = set()
    declared_docs: dict[str, list[str]] = {}
    for row in mechanisms:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        if not (isinstance(row_id, str) and row_id):
            continue
        ids.add(row_id)
        declared_docs[row_id] = _declaring_paths(row)

    for mid in sorted(set(markers) - ids):
        where = ", ".join(sorted(set(markers[mid])))
        errors.append(
            f"{mid}: declared in {where} with a `{MARKER_TOKEN}` marker but has "
            f"NO row in config/v4-mechanisms.yml — a declared mechanism with no "
            f"row is exactly the declared-and-absent shape this gate exists to "
            f"catch (#5064): mark it `implemented` (code path + test + symbol) "
            f"or `not-implemented` (tracking issue), so it cannot be read as "
            f"shipped"
        )
    for mid in sorted(ids - set(markers)):
        errors.append(
            f"{mid}: a registry row with NO `{MARKER_TOKEN} {mid}` marker in "
            f"{docs_dir.name}/**/*.md — a row whose declaration cannot be found is "
            f"an UNMARKED row, and #5064's refactor direction allows none: put "
            f"the marker at the site that declares the mechanism, or drop the row"
        )
    # A row that names documents in `declared_in:` must be marked in EVERY one
    # that lies inside the scanned surface, and must name at least one of them.
    # Only entries under `docs_dir` are checkable here (a pack manifest is cited
    # as a policy source and carries no marker); an entry outside it is neither
    # silently required nor silently accepted, and the coverage rule below
    # refuses a row that relies on such an entry ALONE.
    for mid, entries in sorted(declared_docs.items()):
        if not entries:
            continue  # `gate_errors` owns "declares nothing" (see _declaring_paths)
        scanned_targets = _scanned_targets(entries, docs_dir)
        if not scanned_targets:
            errors.append(
                f"{mid}: the row names NO declaring document inside the gate's "
                f"scanned surface ({docs_dir.name}/**/*.md: "
                f"{', '.join(entries) or 'nothing'}) — its marker can never be "
                f"found where the declaration is, so the row is UNVERIFIABLE and "
                f"reads as an unmarked row (#9778 review P2): declare the "
                f"document that carries the marker, or move the marker to a "
                f"document the row names"
            )
            continue
        if mid not in markers:
            continue  # already reported above as an UNMARKED row
        named = set()
        for resolved in sorted(scanned_targets):
            # Compare the DOCUMENT, not its basename: two `a.md` in different
            # directories are different documents, and a basename comparison let
            # a marker in one satisfy the row for the other (review P2).
            rel = resolved.relative_to(docs_dir.resolve()).as_posix()
            named.add(rel)
            if rel in markers[mid]:
                continue
            errors.append(
                f"{mid}: `declared_in:` names {rel}, but that document "
                f"carries no `{MARKER_TOKEN} {mid}` marker — half of a "
                f"multi-document declaration has rotted away, and a reader of "
                f"{rel} would not meet the mechanism's verdict where it is "
                f"declared"
            )
        # ... and the OTHER direction: a document that marks the mechanism must be
        # one the row NAMES. Without this, a marker could be added at a genuine
        # declaration site while `declared_in:` silently omitted it, so the
        # enumeration could shrink back to the single document it started from
        # and every rule above would still pass (review P2 — the live case this
        # closed was `fanout_cap` in EXTRACTOR §13.8 and `near_dup_merge` in
        # STORAGE §14.1 O4).
        for rel in sorted(set(markers.get(mid, [])) - named):
            errors.append(
                f"{mid}: {rel} carries a `{MARKER_TOKEN} {mid}` marker, but the "
                f"row's `declared_in:` does not name it — a declaring document "
                f"the registry omits is an enumeration that will shrink back "
                f"silently, so name every document that declares the mechanism"
            )
    return errors


def _defined_names(path: Path) -> set[str] | None:
    """Module-level names a source file DEFINES, or ``None`` if unreadable.

    Only the shapes a declared mechanism can be carried by: a function, a class,
    a module-level assignment (an ``Assign``/``AnnAssign`` target —
    ``PER_ENTITY_FANOUT_CAP = 200`` is the fan-out cap). DUNDER names are not
    definitions: ``__all__`` re-exports other modules' names and is present in
    almost every module, so admitting it would let a row anchor itself to a name
    that proves nothing — the one name that must never count for a gate whose
    premise is "a symbol importable from elsewhere does not count".

    The source is read with :func:`tokenize.open`, i.e. with the SAME encoding
    rules the interpreter applies (PEP 263 coding cookies and a UTF-8 BOM). A
    plain ``read_text(encoding="utf-8")`` refused a legal
    ``# -*- coding: latin-1 -*-`` module with ``UnicodeDecodeError`` and HANDED
    a BOM-prefixed source to ``ast.parse``, which then raised ``SyntaxError``:
    a file Python imports happily was reported as unreadable, i.e. a present
    mechanism claimed absent — the wrong verdict direction.

    ``None`` — not an empty set — for a source that cannot be read or parsed at
    all: the caller must keep "this file defines nothing named that" (a
    violation) distinct from "this file could not be turned into names" (a
    DIFFERENT violation, and never a silent pass — see `gate_errors`).
    """
    try:
        with tokenize.open(path) as fh:
            source = fh.read()
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError, UnicodeError):
        return None
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    names.update(
                        elt.id for elt in target.elts if isinstance(elt, ast.Name)
                    )
    return {n for n in names if not (n.startswith("__") and n.endswith("__"))}


def _resolve_in_repo(rel: str, root: Path) -> Path | None:
    """``root / rel`` when it is a real file INSIDE the repo, else ``None``.

    The same containment discipline ``_path_violations`` applies — an absolute
    path, ``..``, or a committed symlink that escapes the repo is not a declared
    artifact — reduced to the one question the symbol check asks: WHICH FILE TO
    READ. Kept separate rather than shared so a change to the reporting rule
    cannot silently change what the anchor reads.

    ⚠️ BELT-AND-BRACES when called from ``gate_errors``: the path rule runs
    first and reports every entry these guards reject, so the anchor can only
    skip a file the path rule ALREADY reported — one defect, one report, and no
    symbol verdict for a path the row never validly declared. This is the same
    documented exception to "a guard must be seen to fail" as ``not p.parts``
    in ``_path_violations``: the shared discipline is the thing worth keeping
    aligned, and it must still hold if the reporting rule ever narrows.
    """
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    try:
        resolved = (root / p).resolve()
        if not (resolved.is_file() and resolved.is_relative_to(root.resolve())):
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return resolved


def gate_errors(mechanisms: list[dict], root: Path = ROOT) -> list[str]:
    """The gate. Returns every violation — an EMPTY list is the only pass.

    FAIL CLOSED: a row whose ``state`` is missing, misspelled, or anything
    outside :data:`STATES` is reported as NEITHER implemented nor marked. A new
    state word cannot silently become a pass — an unrecognised state is a
    violation, not a default (the same allow-list polarity the checks rule uses).
    """
    errors: list[str] = []
    seen: set[str] = set()

    # A non-iterable registry is a violation, not a crash (cycle-4 P2): the
    # `mechanisms` value of `yaml.safe_load("mechanisms:\n")` is `None`.
    if not isinstance(mechanisms, list):
        return [f"mechanisms must be a list, got {type(mechanisms).__name__}"]

    #: The declared set, read BEFORE any row is judged. A precondition may name
    #: a row that appears LATER in the list, so a single pass would judge the
    #: ordering against a set it has not finished reading — and would refuse a
    #: legal forward reference, i.e. a WRONG verdict from a gate whose job is to
    #: refuse a *missing* mechanism. `state_by_id` is what the ordering rule
    #: resolves against; `setdefault` keeps the FIRST row's state so a duplicate
    #: id (reported on its own) cannot be laundered by the later copy.
    declared_ids: list[str] = []
    state_by_id: dict[str, object] = {}
    for row in mechanisms:
        row_id = row.get("id") if isinstance(row, dict) else None
        if isinstance(row_id, str) and row_id:
            declared_ids.append(row_id)
            state_by_id.setdefault(row_id, row.get("state"))
    id_set = set(declared_ids)

    #: Field -> the KIND of artifact it must name. A declared path that EXISTS
    #: but is the wrong kind (a README satisfying `code:`, a source file
    #: satisfying `tests:`) is the same fail-open class as a missing one: the
    #: row claims an artifact it does not name. Cycle 4 closed it for `tests:`,
    #: cycle 5 for `code:`/`declared_in:` — closed here as one rule.
    def _is_test_name(name: str) -> bool:
        return (
            (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
        )

    #: The KIND rule for ONE artifact name, or ``None`` when it is the right
    #: kind. Applied to BOTH the declared name and the RESOLVED name (cycle-7
    #: P1): a committed symlink named `fake_code.py` -> `docs/design.md`
    #: satisfied a name-only check while no `.py` was ever named.
    def _kind_violation(field: str, kind: str, name: str) -> str | None:
        if kind == "test" and not _is_test_name(name):
            return f"{field} entry is not a test file (expected `test_*.py` or `*_test.py`)"
        if kind == "code" and (Path(name).suffix != ".py" or _is_test_name(name)):
            return f"{field} entry is not production source (expected a non-test `.py`)"
        if kind == "doc" and Path(name).suffix not in (".md", ".yaml", ".yml"):
            return f"{field} entry is not a declaring document (expected `.md`/`.yaml`/`.yml`)"
        return None

    def _path_violations(mid: str, field: str, entries: object, root: Path,
                         kind: str) -> list[str]:
        """A declared path must be the RIGHT KIND of artifact, on disk, IN the repo.

        A bare `root / rel` is fail-OPEN (cycle-2 review P1): `""` resolves to
        `root` itself, an absolute `rel` replaces `root` entirely, `..` escapes
        the repo, and a non-string raises `TypeError` — a crash, not a verdict.
        A mapping whose KEYS are existing paths also passed by key iteration.
        So the entry is validated before it is joined.

        `kind` (cycles 4-5) pins WHAT the path must be, because existence alone
        does not prove the claim: `tests:` must match the repo's own naming
        convention (`test_*.py` / `*_test.py`, wherever tests live — `tests/`,
        `graph-scripts/`, `integrations/tests/` — so no prefix is required);
        `code:` must be a non-test `.py`; `declared_in:` must be a document
        (`.md`/`.yaml`/`.yml`).

        Containment (cycle-5 P2) is by RESOLVED path: the repo carries committed
        symlinks (`scripts/`, `skills/` → agent-infra), so `is_file()` alone
        followed a declared path out of the repo.

        PRECONDITION: `entries` is a non-empty list. Every caller checks that
        first and emits its own message, so a container-shape guard here would
        be unreachable (cycle-8 P2).
        """
        out: list[str] = []
        root_resolved = root.resolve()
        for rel in entries:
            if not isinstance(rel, str) or not rel:
                out.append(f"{mid}: {field} entry is not a non-empty string: {rel!r}")
                continue
            p = Path(rel)
            # ⚠️ `not p.parts` (and the `not rel` above) are BELT-AND-BRACES, the
            # one documented exception to this file's "a guard must be seen to
            # fail" rule (cycle-10 P2-4): an empty or dot basename is also
            # rejected by the per-field kind rule (`Path("").suffix == ""` is no
            # valid doc/code/test name), so neither operand can be the DECIDING
            # check in any test. They are kept because the kind rules are the
            # part most likely to change, and a future kind that accepted a bare
            # name must still not admit `""` or `"."` as an artifact.
            if p.is_absolute() or ".." in p.parts or not p.parts:
                out.append(
                    f"{mid}: {field} path must be a repo-relative path inside "
                    f"the repo: {rel!r}"
                )
                continue
            bad = _kind_violation(field, kind, p.name)
            if bad is not None:
                out.append(f"{mid}: {bad}: {rel!r}")
                continue
            # `is_file()`, not `exists()`: a DIRECTORY satisfied an exists-only
            # check (cycle-3 P2). The probe is wrapped because a declared path
            # the OS cannot probe must be a VERDICT, not a crash: NAME_MAX
            # overflow raises `OSError`, an embedded NUL or a lone surrogate
            # raises `ValueError`/`UnicodeEncodeError`, and a symlink loop
            # raises `RuntimeError` (cycles 3 and 6).
            try:
                resolved = (root / p).resolve()
                present = resolved.is_file() and resolved.is_relative_to(root_resolved)
            except (OSError, ValueError, RuntimeError):
                present = False
            if not present:
                out.append(f"{mid}: declared path does not exist: {rel}")
                continue
            # The KIND must hold for the RESOLVED target too — otherwise the
            # kind is a property of the NAME, and a symlink launders it.
            bad = _kind_violation(field, kind, resolved.name)
            if bad is not None:
                out.append(
                    f"{mid}: {bad}, but {rel!r} resolves to {resolved.name!r}"
                )
        return out

    for row in mechanisms:
        if not isinstance(row, dict):
            errors.append(f"mechanism row is not a mapping: {row!r}")
            continue

        mid = row.get("id")
        if not mid or not isinstance(mid, str):
            errors.append(f"a mechanism row has no string id: {row!r}")
            continue
        if mid in seen:
            errors.append(f"{mid}: duplicate id — one row per mechanism")
        seen.add(mid)

        if not isinstance(row.get("name"), str) or not row.get("name").strip():
            errors.append(f"{mid}: declares no name")

        # "Declared" must itself be checkable: a row that names no declaring
        # document, or names one that is not on disk, is not declared anywhere.
        declared_in = row.get("declared_in")
        if not isinstance(declared_in, list) or not declared_in:
            errors.append(f"{mid}: declares no declaring document (declared_in)")
        else:
            errors.extend(_path_violations(mid, "declared_in", declared_in, root, "doc"))

        # ⭐ THE ORDERING — keyed on the field rather than on `state`, so an
        # absent row can record what it waits for (the case that matters most:
        # the next lane meets the ordering instead of a surprise). The SHAPE is
        # validated for both states; only the `implemented` polarity depends on
        # the precondition's own state, below. A bare string (`preconditions:
        # fanout_cap`) would iterate CHARACTER BY CHARACTER and a mapping would
        # iterate its KEYS, so the empty-ordering fail-open is closed with an
        # allow-list like every other field here.
        declared_preconditions: list[str] = []
        # `in row` rather than `row.get(...) is not None`: an EXPLICIT
        # `preconditions:` with no value parses to `None` and would then read as
        # "not ordered" — a declaration of nothing that looks like a
        # declaration. Omission is clean; present-and-empty is a violation.
        if PRECONDITIONS in row:
            preconditions = row.get(PRECONDITIONS)
            if not isinstance(preconditions, list) or not preconditions:
                errors.append(
                    f"{mid}: `{PRECONDITIONS}` must be a non-empty list of "
                    f"declared mechanism ids (got {preconditions!r}) — omit the "
                    f"field on a mechanism that is not ordered"
                )
            elif not all(
                isinstance(p, str) and p.strip() for p in preconditions
            ):
                errors.append(
                    f"{mid}: `{PRECONDITIONS}` entries must be non-empty "
                    f"strings: {preconditions!r}"
                )
            elif len(set(preconditions)) != len(preconditions):
                errors.append(
                    f"{mid}: `{PRECONDITIONS}` names a mechanism twice: "
                    f"{preconditions!r}"
                )
            else:
                for precondition in preconditions:
                    if precondition == mid:
                        errors.append(
                            f"{mid}: lists itself as a {PRECONDITIONS[:-1]} — a "
                            f"mechanism cannot be built on itself (the rule "
                            f"would be satisfied by construction)"
                        )
                    elif precondition not in id_set:
                        errors.append(
                            f"{mid}: {PRECONDITIONS[:-1]} {precondition!r} is not "
                            f"a declared mechanism — an ordering must name a row "
                            f"in this registry, or nothing can ever say whether "
                            f"it is built and the dependant's claim is "
                            f"unverifiable"
                        )
                    else:
                        declared_preconditions.append(precondition)

        # The polarity is read FROM `STATES`, so the accepted set and the
        # message naming it cannot drift (cycle-2 review P2).
        state = row.get("state")
        if state not in STATES:
            errors.append(
                f"{mid}: NEITHER implemented (a code path with a test) NOR "
                f"marked not-implemented (state={state!r}, expected one of "
                f"{STATES}) — a declared-and-absent mechanism must be DECLARED, "
                f"not implied"
            )
            continue

        if state == IMPLEMENTED:
            # ⭐ THE ORDER RULE (#5064's checklist: *"a mechanism whose
            # precondition is unbuilt cannot be marked implemented"*). This is
            # the `declared ⇒ present` inference one level up: the row is
            # truthfully `implemented` in itself while what it is built on is
            # recorded as absent, so the substrate cannot exist and no verdict
            # says so. A precondition whose own state is unreadable counts as
            # NOT implemented — the safe direction, and its own row is already
            # the row that reports the bad state.
            unbuilt = [
                p for p in declared_preconditions
                if state_by_id.get(p) != IMPLEMENTED
            ]
            if unbuilt:
                errors.append(
                    f"{mid}: implemented, but its declared "
                    f"{PRECONDITIONS[:-1]}(s) {sorted(unbuilt)} are not "
                    f"implemented — a mechanism cannot ship on top of one this "
                    f"registry records as absent. Mark this row "
                    f"`not-implemented` with its tracking issue, or build the "
                    f"{PRECONDITIONS[:-1]} first (#5064: the ordering is "
                    f"declared, not discovered)"
                )

            if not isinstance(row.get("code"), list) or not row.get("code"):
                errors.append(f"{mid}: implemented but declares no code path")
            else:
                errors.extend(_path_violations(mid, "code", row["code"], root, "code"))
            if not isinstance(row.get("tests"), list) or not row.get("tests"):
                errors.append(
                    f"{mid}: implemented but declares no test — a code path "
                    f"without a test is exactly the declared-but-absent shape "
                    f"this gate exists to catch"
                )
            else:
                errors.extend(
                    _path_violations(mid, "tests", row["tests"], root, "test")
                )
            # ⭐ THE CODE ANCHOR. `code:` above proves the FILE; this proves the
            # MECHANISM. Both are required: a resolved symbol in a file the row
            # declares is the strongest claim this registry can make without
            # executing the code, and it is the one a rename cannot satisfy by
            # accident (the file still exists, the symbol does not).
            symbols = row.get("symbols")
            if not isinstance(symbols, list) or not symbols:
                errors.append(
                    f"{mid}: implemented but declares no symbol — a file at "
                    f"`code:` is not the mechanism. Name the symbol that "
                    f"carries it in `symbols:` (a function, class or "
                    f"module-level constant), so a rename or removal cannot "
                    f"leave this row claiming an implementation"
                )
            elif not all(isinstance(s, str) and s.strip() for s in symbols):
                errors.append(
                    f"{mid}: `symbols` entries must be non-empty strings: "
                    f"{symbols!r}"
                )
            else:
                code_entries = row.get("code")
                defined: set[str] = set()
                resolved_entries: list[str] = []
                unreadable: list[str] = []
                for rel in (code_entries if isinstance(code_entries, list) else []):
                    if not isinstance(rel, str) or not rel:
                        continue
                    # ASK THE PATH RULE, per entry: its verdict is the authority
                    # on whether this declared entry is a valid artifact at all
                    # (shape, kind, containment, existence). `_resolve_in_repo`
                    # only asks WHICH FILE TO READ and applies none of the kind
                    # discipline, so an entry it resolves may still be one the
                    # path rule REJECTED (`code: ["README.md"]` is on disk and
                    # inside the repo). Reading that entry too would report one
                    # defect twice — and claim a wrong-kind file "could not be
                    # parsed" when the real verdict is "not production source".
                    if _path_violations(mid, "code", [rel], root, "code"):
                        continue
                    resolved = _resolve_in_repo(rel, root)
                    if resolved is None:
                        # Unprobeable between the two probes: the path rule will
                        # report it on the next run, and there is no file to read.
                        continue
                    resolved_entries.append(rel)
                    names = _defined_names(resolved)
                    if names is None:
                        unreadable.append(rel)
                        continue
                    defined |= names
                # ⭐ A declared source that the PATH RULE ACCEPTED — right
                # shape, right kind, on disk, inside the repo — but that cannot
                # be read or parsed is a violation of its OWN. Nothing else
                # reports it: `_path_violations` never opens the file, so
                # treating "unparsable" as "the path rule already said so" left
                # the row certifying a mechanism whose code was never read at
                # all (the fail-open this rule exists to close, one level down).
                # Silent only for entries the path rule already reported, so one
                # defect still yields exactly one verdict.
                for rel in unreadable:
                    errors.append(
                        f"{mid}: declared `code:` source {rel!r} could not be "
                        f"read or parsed, so the declared symbol(s) {symbols} "
                        f"cannot be verified in it — an unreadable source is "
                        f"not the mechanism"
                    )
                # The absence check runs only over a COMPLETE read: with any
                # declared source unparsed, "not found in the files I could "
                # read" is not evidence of absence, and claiming it would add a
                # second, wrong verdict on top of the real defect.
                if resolved_entries and not unreadable:
                    missing = sorted(s for s in symbols if s not in defined)
                    if missing:
                        errors.append(
                            f"{mid}: symbol(s) {missing} are not defined at "
                            f"module level in the `code:` file(s) this row "
                            f"declares ({code_entries}) — the declared "
                            f"mechanism is not where the row says it is. A "
                            f"file at the declared path is not the mechanism"
                        )

        else:  # NOT_IMPLEMENTED — the only other member of STATES
            # A tracking issue must be a REAL issue number — a positive,
            # non-bool int. `row.get(...) in (None, "", 0)` used `==` and passed
            # `[]`, `" "`, `"0"`, `"TBD"` and any negative int — a placeholder
            # on the one row that claims to be marked. The check is an allow-list
            # on the type and the sign (cycle-1 review P1). ⚠️ EXISTENCE is NOT
            # verified — `tracking_issue: 424242` passes; link-checking is a
            # different job, not this gate's.
            issue = row.get("tracking_issue")
            if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
                errors.append(
                    f"{mid}: not-implemented with no real tracking_issue "
                    f"(got {issue!r}) — an unbuilt mechanism needs a home"
                )

    # ── the ordering must itself be orderable: a cycle is not an ordering ────
    # Judged over the SET, after the rows: `a → b → a` satisfies every per-row
    # rule, so no single row can report it. Unknown ids and self-references are
    # dropped here — they are already a violation on the row that declares them,
    # and re-reporting them as a cycle would be a second verdict for one defect.
    edges: dict[str, list[str]] = {}
    for row in mechanisms:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            continue
        edges.setdefault(row_id, [])
        row_preconditions = row.get(PRECONDITIONS)
        if not isinstance(row_preconditions, list):
            continue
        for precondition in row_preconditions:
            if (
                isinstance(precondition, str)
                and precondition != row_id
                and precondition in id_set
            ):
                edges[row_id].append(precondition)
    for cycle in _precondition_cycles(edges):
        errors.append(
            "precondition cycle: "
            + " → ".join(cycle)
            + " — an ordering that returns to its start is not an ordering: "
            "nothing in the cycle can be built first, so the substrate it "
            "orders never lands and no row-level rule says so"
        )

    return errors


# ── the gate over the real registry ─────────────────────────────────────────


def test_declared_set_is_exactly_the_eight_named_mechanisms() -> None:
    """The registry declares the closed set from #5064 indicator (1) + the enumeration."""
    mechanisms = load_registry()["mechanisms"]
    assert tuple(r["id"] for r in mechanisms) == DECLARED_IDS
    assert len(mechanisms) == len(DECLARED_IDS)


def test_every_declared_mechanism_is_implemented_with_a_test_or_marked_absent() -> None:
    """#5064 indicator (1), checkable: 0 mechanisms with neither code nor a marker."""
    errors = gate_errors(load_registry()["mechanisms"])
    assert errors == [], "declared-mechanism gate violations:\n  " + "\n  ".join(errors)


def test_every_declared_mechanism_has_a_declaration_marker_and_vice_versa() -> None:
    """#5064 indicator (2) as a CHECK: the declared set is compared, both ways.

    The root sentence is *"nothing compared the declared set against the code
    set"*. The code half is `gate_errors`; this is the DECLARATION half — every
    row points at a marker at the site that declares it, and every marker has a
    row. Without it the registry's completeness rests on a hand-written tuple,
    which is exactly how `state_value_carve_out` stayed unregistered while the
    gate stayed green.
    """
    errors = membership_errors(load_registry()["mechanisms"])
    assert errors == [], "declared-mechanism membership violations:\n  " + \
        "\n  ".join(errors)


def test_the_state_value_carve_out_is_registered_and_marked_absent() -> None:
    """The enumeration's first deliverable, pinned by name (#5064, #2453, #9775).

    `STATE_VALUE_CARVE_OUT` is declared by name in §7 of BOTH design documents and
    the table carried only seven rows — so the mechanism the checklist names
    ("verbatim operational values hold on a live write") had **neither code entry
    nor marker** in the registry while the gate passed. Its state must stay
    `not-implemented` while the guarantee is prompt-only and one render path
    (`_render_master_compact`) deliberately omits it: a row marked `implemented`
    here would make prompt text read as the guarantee, which is the defect this
    registry exists to catch.
    """
    rows = {r["id"]: r for r in load_registry()["mechanisms"]}
    assert "state_value_carve_out" in rows, sorted(rows)
    row = rows["state_value_carve_out"]
    assert row["state"] == NOT_IMPLEMENTED, row
    assert isinstance(row.get("tracking_issue"), int), row
    # The compact render's omission is NAMED in the row — the bounded alternative
    # #5064's E2 comment records ("mark the omission explicit in the registry").
    assert "_render_master_compact" in row["note"], row["note"]


def test_the_compact_render_omits_the_carve_out_the_row_records() -> None:
    """The row's one ABSENCE claim is pinned to the code, not to its own prose.

    Without this, the probe that would falsify it is a grep of the note: if a
    later change appends the carve-out to `_render_master_compact` (the first
    closing option in #9775) the registry would keep claiming an omission that
    no longer exists and nothing would red. So the render is exercised: the
    carve-out must be present where the row says it is, and ABSENT from the
    compact render — the shape `tests/test_extractor_v2.py` already pins for
    `SHORT_SESSION_FACT_RULE` (review cycle 1, P2).

    The compact render is called with a story that RENDERS its one conditional
    block: passing `None` (as an earlier revision did) skips the `if story:`
    branch entirely, so a fix that appended the carve-out INSIDE it — #9775's
    first closing option — would have left this assertion green while the row
    kept recording an omission that no longer existed (review P2). The
    granularity block's presence is asserted first, so the vacancy claim cannot
    pass by rendering nothing.
    """
    from tortoise import extractor_v2 as v2

    master = v2.build_master_list()
    marker = "STATE-VALUE CARVE-OUT"
    assert marker in v2._granularity_text(master), "S1 lost the carve-out"
    assert marker in v2._render_master_core_only(master), "core-only lost it"
    assert marker in v2._render_master_verbose(master), "verbose lost it"
    # A dev:-triggered story selects granularity subsections, i.e. the branch a
    # carve-out fix would land in. If this stops rendering the block, the
    # assertion below would be vacuous — so it is pinned too.
    compact = v2._render_master_compact(master, "we shipped the dev: extractor change")
    assert "MEMORY GRANULARITY" in compact, (
        "the compact render no longer emits its granularity block for a "
        "dev:-matched story, so the carve-out assertion below proves nothing"
    )
    assert marker not in compact, (
        "the compact render now carries the carve-out: the `state_value_carve_out` "
        "row still records the omission, so update the row (and #9775) with the "
        "decision instead of deleting this assertion"
    )


# ── MEMBERSHIP: the declared set is compared, not pinned by hand ─────────────
#
# A hand-written tuple cannot prove it is COMPLETE. Each case below is a way the
# comparison can be defeated, so each is a permanent proof rather than a comment:
# a declaration with no row (the root), a row with no declaration (an unmarked
# row), a marker that does not parse, a marker outside the scanned surface, and
# a surface that cannot be read at all.


#: Where the fixture rows below declare themselves, relative to `tmp_path`. The
#: attribution rule resolves each row's `declared_in:` against a root, so a
#: fixture row has to name a document under the fixture docs dir — the same
#: relationship the real registry has to the real one.
FIXTURE_DECLARED_DOC = "docs/architecture/a.md"


def _row(mid: str = "x", declared_in: list[str] | None = None) -> dict:
    return {
        "id": mid,
        "name": mid,
        "declared_in": (
            [FIXTURE_DECLARED_DOC] if declared_in is None else list(declared_in)
        ),
        "state": "implemented",
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
        "symbols": ["PER_ENTITY_FANOUT_CAP"],
    }


def _docs(tmp_path: Path, **files: str) -> Path:
    docs = tmp_path / "docs" / "architecture"
    docs.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (docs / name.replace("__", ".")).write_text(body, encoding="utf-8")
    return docs


def test_membership_accepts_a_row_and_its_marker(tmp_path) -> None:
    docs = _docs(tmp_path, a__md="text <!-- v4-mechanism: x -->\n")
    assert membership_errors([_row("x")], docs) == []


def test_membership_fails_closed_on_a_marker_with_no_registry_row(tmp_path) -> None:
    """The root sentence: a mechanism the design declares must have a verdict."""
    docs = _docs(
        tmp_path,
        a__md="text <!-- v4-mechanism: x -->\n",
        EXTRACTOR__md="more text <!-- v4-mechanism: ghost -->\n",
    )
    errors = membership_errors([_row("x")], docs)
    assert any("ghost" in e and "NO row" in e for e in errors), errors


def test_membership_fails_closed_on_a_row_with_no_marker(tmp_path) -> None:
    """An UNMARKED row: the declaration cannot be found (#5064 direction 1)."""
    docs = _docs(tmp_path, a__md="no declaration here\n")
    errors = membership_errors([_row("x")], docs)
    assert any("x:" in e and "UNMARKED row" in e for e in errors), errors


def test_membership_accepts_a_mechanism_two_documents_declare(tmp_path) -> None:
    """`span_link` and `state_value_carve_out` are declared in BOTH docs."""
    docs = _docs(
        tmp_path,
        EXTRACTOR__md="a <!-- v4-mechanism: x -->\n",
        STORAGE__md="b <!-- v4-mechanism: x -->\n",
    )
    row = _row(
        "x",
        declared_in=[
            "docs/architecture/EXTRACTOR.md",
            "docs/architecture/STORAGE.md",
        ],
    )
    assert membership_errors([row], docs) == []


def test_membership_fails_closed_on_a_malformed_marker(tmp_path) -> None:
    """A declaration a reader cannot parse must not read as 'nothing here'."""
    docs = _docs(
        tmp_path,
        a__md="text <!-- v4-mechanism: x -->\nand <!-- v4-mechanism: -->\n",
    )
    errors = membership_errors([_row("x")], docs)
    assert any("malformed" in e for e in errors), errors


def test_a_placeholder_id_is_the_convention_not_a_declaration(tmp_path) -> None:
    """`<id>` names no mechanism: not a marker, and not a defect either.

    A scanned document may explain the convention. The placeholder is therefore
    skipped — and it can never make a row pass, because the row's own marker is
    then absent and `membership_errors` reports it as UNMARKED (asserted here as
    the second half, so skipping can never become a fail-open).
    """
    docs = _docs(tmp_path, a__md="<!-- v4-mechanism: <id> -->\n")
    errors = membership_errors([_row("x")], docs)
    assert not any("does not name a mechanism id" in e for e in errors), errors
    assert any("UNMARKED row" in e for e in errors), errors
    # With the real marker alongside it, the document is clean.
    docs = _docs(
        tmp_path,
        a__md="<!-- v4-mechanism: <id> -->\n<!-- v4-mechanism: x -->\n",
    )
    assert membership_errors([_row("x")], docs) == []


def test_membership_ignores_the_token_in_prose(tmp_path) -> None:
    """Documenting the token must not be reported as a declaration."""
    docs = _docs(
        tmp_path,
        a__md="Markers use the `v4-mechanism:` token, documented in the registry;\n"
        "the token alone is not a declaration.\n"
        "<!-- v4-mechanism: x -->\n",
    )
    assert membership_errors([_row("x")], docs) == []


def test_a_marker_inside_a_code_block_still_counts(tmp_path) -> None:
    """Shape is not parsed: a comment-shaped marker is a declaration anywhere.

    The alternative — stripping fenced/inline code — was a half-implemented
    markdown parser (review cycle 2, P2): `~~~` fences, indented blocks and
    double-backtick spans survived it, so the same marker counted in some code
    shapes and not others. Counting the text uniformly is the rule; a
    placeholder id is exempted by MEANING instead (:data:`PLACEHOLDER_ID`).
    """
    docs = _docs(tmp_path, a__md="```\n<!-- v4-mechanism: x -->\n```\n")
    assert membership_errors([_row("x")], docs) == []


def test_membership_reds_on_a_row_citing_a_document_that_does_not_exist(tmp_path) -> None:
    """`membership_errors` is the membership half; `gate_errors` owns paths.

    Pinned as the PAIR, because the two halves report a renamed declaring
    document DIFFERENTLY and neither message is sufficient alone: `gate_errors`
    says the path does not exist (the actual defect), while
    `membership_errors` — which never stats anything — sees a document that
    carries no marker and reports it as a rotted half-declaration. Losing
    either means one of the two ways this drifts goes unreported (review cycle
    2, P2; `_path_violations` is the half that names the cause).
    """
    docs = _docs(tmp_path, a__md="a <!-- v4-mechanism: x -->\n")
    row = _row("x")
    row["declared_in"] = ["docs/architecture/renamed-away.md"]
    errors = gate_errors([row], tmp_path)
    assert any("declared path does not exist" in e for e in errors), errors
    # The membership half does not stat, so it reports the SAME defect as a
    # missing marker — a different message for the same rot, never silence.
    member_errors = membership_errors([row], docs)
    assert any("carries no" in e for e in member_errors), member_errors



def test_membership_fails_closed_on_an_unterminated_comment(tmp_path) -> None:
    """An opener with no closer hides any marker inside it — fail closed."""
    docs = _docs(tmp_path, a__md="<!-- v4-mechanism: x\n")
    errors = membership_errors([_row("x")], docs)
    assert any("unterminated" in e for e in errors), errors


def test_membership_requires_a_marker_in_every_declared_document(tmp_path) -> None:
    """A row naming two documents must be marked in BOTH (review cycle 1, P2)."""
    docs = _docs(
        tmp_path,
        a__md="a <!-- v4-mechanism: x -->\n",
        b__md="b declares it but carries no marker\n",
    )
    row = _row("x")
    row["declared_in"] = ["docs/architecture/a.md", "docs/architecture/b.md"]
    errors = membership_errors([row], docs)
    assert any("b.md" in e and "rotted away" in e for e in errors), errors


def test_membership_scans_only_the_architecture_docs(tmp_path) -> None:
    """A marker elsewhere must not silently satisfy a row's declaration."""
    docs = _docs(tmp_path, a__md="no declaration here\n")
    elsewhere = tmp_path / "docs" / "plans"
    elsewhere.mkdir(parents=True)
    (elsewhere / "p.md").write_text("<!-- v4-mechanism: x -->\n", encoding="utf-8")
    errors = membership_errors([_row("x")], docs)
    assert any("UNMARKED row" in e for e in errors), errors


def test_membership_fails_closed_on_a_missing_documents_directory(tmp_path) -> None:
    """An unreadable surface is a verdict, not an empty set."""
    missing = tmp_path / "docs" / "architecture"
    errors = membership_errors([_row("x")], missing)
    assert any("is not a directory" in e for e in errors), errors
    assert any("UNMARKED row" in e for e in errors), errors


def test_membership_fails_closed_on_an_unreadable_document(tmp_path) -> None:
    """A doc that cannot be decoded is reported, never skipped."""
    docs = _docs(tmp_path, a__md="text <!-- v4-mechanism: x -->\n")
    (docs / "latin1.md").write_bytes(b"\xff\xfe not utf-8 \xff")
    errors = membership_errors([_row("x")], docs)
    assert any("could not be read" in e for e in errors), errors


# ── COVERAGE: a row's declaration site must be REACHABLE (#9778 P2) ──────────
#
# Comparing the two id sets (and even requiring each declared document to carry
# the marker) is VACUOUS for a row whose every declaring document lies outside
# the scanned surface: there is no per-document check to fail, so the row passes
# while the convention can never confirm it. That is exactly what a
# `declared_in:` of `packs/dev/manifest.yaml` alone would look like.


def test_membership_fails_closed_on_a_marker_in_an_undeclared_document(tmp_path) -> None:
    """A marker sitting in a document the row does NOT declare is not its marker.

    Pre-fix (id-set comparison only) this was a PASS: the row's own declared
    document carries no marker and nothing said so, because some OTHER document
    happened to carry its id.
    """
    docs = _docs(
        tmp_path,
        a__md="the declared site — and NO marker here\n",
        b__md="an unrelated passage <!-- v4-mechanism: x -->\n",
    )
    errors = membership_errors([_row("x")], docs)
    assert any("a.md" in e and "rotted away" in e for e in errors), errors
    # The verdict is not the UNMARKED one: a marker WAS found, just not where the
    # row says its declaration is. Conflating the two would let a mutation that
    # drops the per-document rule hide behind the other rule's message.
    assert not any("UNMARKED row" in e for e in errors), errors


def test_membership_compares_documents_not_basenames(tmp_path) -> None:
    """Two `a.md` in different directories are DIFFERENT documents (review P2).

    The attribution used to compare `resolved.name` against the recorded names,
    so a marker in `architecture/a.md` satisfied a row declaring
    `architecture/sub/a.md` — the document was resolved (as the docstring
    promises) and then identified by its last path segment. Latent only because
    `docs/architecture/` has no subdirectories today; a nested doc would make it
    live the moment one is added.
    """
    docs = _docs(tmp_path, a__md="top level <!-- v4-mechanism: x -->\n")
    nested = docs / "sub"
    nested.mkdir()
    (nested / "a.md").write_text("no marker in THIS a.md\n", encoding="utf-8")
    row = _row("x")
    row["declared_in"] = ["docs/architecture/sub/a.md"]
    errors = membership_errors([row], docs)
    assert any("sub/a.md" in e and "rotted away" in e for e in errors), errors


def test_membership_sees_a_marker_in_a_nested_document(tmp_path) -> None:
    """The scan is RECURSIVE, like the attribution rule (#9778 review P2).

    `declaration_markers` globbed `*.md` (top level only) while
    `_scanned_targets` resolved ANY path under the docs dir as inside the surface,
    so a marker one directory down was never collected: a mechanism the design
    declares in `docs/architecture/sub/x.md` read as nothing at all — the
    declared-and-absent shape, silently, which is the one direction this gate is
    built to refuse. Measured pre-fix: this returned no errors.
    """
    docs = _docs(tmp_path, a__md="top <!-- v4-mechanism: x -->\n")
    nested = docs / "sub"
    nested.mkdir()
    (nested / "x.md").write_text(
        "nested <!-- v4-mechanism: ghost -->\n", encoding="utf-8"
    )
    errors = membership_errors([_row("x")], docs)
    assert any("ghost" in e and "NO row" in e for e in errors), errors


def test_membership_accepts_a_nested_document_that_carries_the_marker(tmp_path) -> None:
    """The mirror image, from the SAME definition of the surface (review P2).

    Pre-fix the scan could not read the file the row was being held against, so a
    legitimate nested declaration was reported as a rotted half-declaration — a
    false red whose remedy (`add the marker`) no edit could satisfy, because the
    marker was already there. Both halves now name the document identically
    (`adr/0007.md`), so the per-document rules apply to it unchanged.
    """
    docs = _docs(tmp_path, a__md="top <!-- v4-mechanism: x -->\n")
    nested = docs / "adr"
    nested.mkdir()
    (nested / "0007.md").write_text(
        "a decision <!-- v4-mechanism: x -->\n", encoding="utf-8"
    )
    # Declared alongside the flat document: both are marked, so the row is clean.
    row = _row("x", declared_in=[FIXTURE_DECLARED_DOC, "docs/architecture/adr/0007.md"])
    assert membership_errors([row], docs) == []
    # And the row naming ONLY the nested document is MARKED, not unmarked: the
    # verdict that used to be wrong pre-fix.
    row = _row("x", declared_in=["docs/architecture/adr/0007.md"])
    errors = membership_errors([row], docs)
    assert not any("UNMARKED row" in e or "rotted away" in e for e in errors), errors
    # ... while the flat document's marker is now one the row omits, so the
    # bidirectional rule still fires — recursion did not turn the gate permissive.
    assert any("a.md" in e and "does not name it" in e for e in errors), errors


def test_membership_does_not_scan_the_sibling_changelog(tmp_path) -> None:
    """A changelog records history, so it is not a declaration site (#8542).

    Scanning it would make every quoted convention in a change record a
    declaration the row must name, and the changelogs are exactly where #8542
    moved status and narration. Both halves exclude it, so a row naming one is
    not a false red either. Pinned as BEHAVIOUR with the constant disabled: this
    is what makes deleting the skip red rather than silently widen the surface
    (a rule with no test is the blind-rule shape this file exists to refuse).
    """
    docs = _docs(tmp_path, a__md="a <!-- v4-mechanism: x -->\n")
    (docs / "EXTRACTOR-CHANGELOG.md").write_text(
        "records <!-- v4-mechanism: ghost -->\n", encoding="utf-8"
    )
    markers, errors = declaration_markers(docs)
    assert "ghost" not in markers, markers
    assert errors == [], errors
    # And the row-naming half: a changelog is outside the surface, so naming one
    # is the coverage verdict, not a "carries no marker" one.
    row = _row("x")
    row["declared_in"] = ["docs/architecture/EXTRACTOR-CHANGELOG.md"]
    assert any("names NO declaring document" in e for e in membership_errors([row], docs))


def test_membership_treats_a_non_markdown_source_inside_the_surface_as_unscanned(
    tmp_path,
) -> None:
    """A `.yaml` under the architecture dir is an uncovered SOURCE, not a doc.

    `gate_errors` accepts `.yaml`/`.yml` in `declared_in:` (`embed_gate` names
    `packs/dev/manifest.yaml`) and YAML carries no markdown comment, so counting
    such a file as a marker-bearing target red-ed the row with a remedy no edit
    could satisfy (#9778 review P2 note). The surface is `**/*.md`, so a
    non-markdown source is treated exactly like one outside it: it does not owe a
    marker, and a row relying on it ALONE is still unverifiable.
    """
    docs = _docs(tmp_path, a__md="the real one <!-- v4-mechanism: x -->\n")
    (docs / "policy.yaml").write_text("policy: {}\n", encoding="utf-8")
    row = _row("x", declared_in=["docs/architecture/policy.yaml"])
    errors = membership_errors([row], docs)
    assert any("names NO declaring document inside" in e for e in errors), errors
    # The control for the rule above — `embed_gate`'s REAL shape: the uncovered
    # source alongside a marked document is a stated limit, not a violation.
    ok = _row("x", declared_in=["docs/architecture/policy.yaml", FIXTURE_DECLARED_DOC])
    assert membership_errors([ok], docs) == []


def test_membership_refuses_a_linked_directory_in_the_surface(tmp_path) -> None:
    """A linked DIRECTORY is not descended, so its markers would get NO verdict.

    `rglob` does not follow a directory symlink, while `_scanned_targets` resolves
    a `declared_in:` entry — so a marker inside `docs/architecture/linked/` would
    be invisible to the scan, and a mechanism declared only there would read as
    nothing at all: the fail-open direction. A symlink in the surface is therefore
    refused, with a message that says what to do (#9778 review P3). Measured
    pre-fix: no errors here — `ghost` had no verdict.
    """
    docs = _docs(tmp_path, a__md="top <!-- v4-mechanism: x -->\n")
    outside = tmp_path / "elsewhere" / "arch"
    outside.mkdir(parents=True)
    (outside / "y.md").write_text("nested <!-- v4-mechanism: ghost -->\n", encoding="utf-8")
    (docs / "linked").symlink_to(outside, target_is_directory=True)
    errors = membership_errors([_row("x")], docs)
    assert any("linked" in e and "symlink" in e for e in errors), errors


def test_membership_refuses_a_linked_file_whose_target_is_outside(tmp_path) -> None:
    """The mirror case: walked under one name, attributed under none (review P3).

    `docs/architecture/link.md -> docs/notes/real.md` is read by the scan (so its
    marker IS collected under `link.md`) but resolves outside the surface (so the
    row's own `declared_in: link.md` is never attributed to it): the row was told
    "link.md carries the marker, but the row does not name it" — a red whose
    remedy no edit could satisfy. Refusing the link replaces that with one
    actionable verdict, and no name-mismatch red.
    """
    docs = _docs(tmp_path, a__md="top <!-- v4-mechanism: x -->\n")
    notes = tmp_path / "docs" / "notes"
    notes.mkdir(parents=True)
    (notes / "real.md").write_text("real <!-- v4-mechanism: x -->\n", encoding="utf-8")
    (docs / "link.md").symlink_to(notes / "real.md")
    row = _row("x", declared_in=[FIXTURE_DECLARED_DOC, "docs/architecture/link.md"])
    errors = membership_errors([row], docs)
    assert any("link.md" in e and "symlink" in e for e in errors), errors
    assert not any("does not name it" in e for e in errors), errors


def test_a_non_markdown_link_in_the_surface_is_an_uncovered_source(tmp_path) -> None:
    """The symlink refusal reaches DOCUMENTS and DIRECTORIES, not every entry.

    A linked `.yaml`/`.yml` is exactly what a real `.yaml` there is: a source the
    marker convention cannot reach, i.e. a stated limit. Refusing it would
    contradict that limit and tell its author to move a declaration that is not
    there (#9778 review P3) — so the refusal is narrowed to a linked doc or dir.
    """
    docs = _docs(tmp_path, a__md="the real one <!-- v4-mechanism: x -->\n")
    outside = tmp_path / "policies"
    outside.mkdir()
    (outside / "p.yaml").write_text("policy: {}\n", encoding="utf-8")
    (docs / "policy.yaml").symlink_to(outside / "p.yaml")
    assert membership_errors([_row("x")], docs) == []


def test_a_nested_unreadable_document_is_named_by_its_relative_path(tmp_path) -> None:
    """A verdict names the DOCUMENT, not its basename (#9778 review P3).

    The scan keys markers by relative path (`sub/a.md`) because a basename is not
    an identity — two `a.md` can exist at two depths. The verdicts of the same
    function still used `doc.name`, so an unreadable NESTED document was reported
    against the top-level one, naming the document that is fine.
    """
    docs = _docs(tmp_path, a__md="readable <!-- v4-mechanism: x -->\n")
    nested = docs / "sub"
    nested.mkdir()
    (nested / "a.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    errors = membership_errors([_row("x")], docs)
    assert any(e.startswith("sub/a.md: ") and "could not be read" in e for e in errors), (
        errors
    )
    assert not any(e.startswith("a.md: ") for e in errors), errors


def test_membership_refuses_a_marker_in_a_document_the_row_omits(tmp_path) -> None:
    """The rule is bidirectional per document: a marker the row omits is a red.

    Without this direction the registry's enumeration could SHRINK silently —
    delete a `declared_in:` entry, keep the marker at the real declaration site,
    and every other rule still passes while the row again claims a single
    document (the live shape this closed: `fanout_cap` declared in EXTRACTOR
    §13.8 and `near_dup_merge` in STORAGE §14.1 O4, neither named by its row).
    """
    docs = _docs(
        tmp_path,
        a__md="declared <!-- v4-mechanism: x -->\n",
        b__md="also declares it <!-- v4-mechanism: x -->\n",
    )
    row = _row("x")  # declares only a.md
    errors = membership_errors([row], docs)
    assert any("b.md" in e and "does not name it" in e for e in errors), errors


def test_membership_accepts_a_row_naming_every_marking_document(tmp_path) -> None:
    """The control: naming both documents clears the rule above."""
    docs = _docs(
        tmp_path,
        a__md="declared <!-- v4-mechanism: x -->\n",
        b__md="also declares it <!-- v4-mechanism: x -->\n",
    )
    row = _row("x")
    row["declared_in"] = ["docs/architecture/a.md", "docs/architecture/b.md"]
    assert membership_errors([row], docs) == []


def test_an_empty_declared_in_is_the_gates_verdict_not_memberships(tmp_path) -> None:
    """One defect, one message, from the function that owns it (review P2).

    A row with `declared_in: []` used to draw THREE messages: `gate_errors`'
    own "declares no declaring document", the UNMARKED rule where the marker was
    absent, and — noise — the coverage rule firing on the empty list. That rule
    exists for a row whose named documents are ALL outside the scanned surface;
    when the row names nothing there is no set to reason about, and the shape
    defect is the gate's to report. Membership is therefore SILENT here, and
    this test pins both halves: the silence is deliberate, and the red still
    exists one function over.
    """
    docs = _docs(tmp_path, a__md="a <!-- v4-mechanism: x -->\n")
    row = _row("x")
    row["declared_in"] = []
    assert membership_errors([row], docs) == []
    gate = gate_errors([row], tmp_path)
    assert any("declares no declaring document" in e for e in gate), gate


#: `declared_in:` entries the gate cannot scan. `embed_gate` really carries one
#: (`packs/dev/manifest.yaml`, its policy source), so the coverage rule below is
#: about a row relying on such a source ALONE.
UNSCANNED_DECLARED_DOC = "packs/dev/manifest.yaml"


def test_membership_fails_closed_on_a_row_declaring_only_unscanned_sources(
    tmp_path,
) -> None:
    """A row whose every declaring document is outside the surface is UNVERIFIABLE.

    The marker convention does not reach a YAML pack or a `docs/plans/` page, so
    such a row's declaration can never be found — and an unfindable declaration
    must not read as a marked one. Pre-fix this passed as long as ANY scanned
    document carried the id (measured: this case returned no errors at HEAD).
    """
    docs = _docs(tmp_path, a__md="a passage <!-- v4-mechanism: x -->\n")
    row = _row("x", declared_in=[UNSCANNED_DECLARED_DOC])
    errors = membership_errors([row], docs)
    assert any("names NO declaring document inside" in e and "x:" in e for e in errors), (
        errors
    )


def test_membership_accepts_a_row_that_also_declares_an_unscanned_source(
    tmp_path,
) -> None:
    """The control for the rule above — `embed_gate`'s REAL shape.

    A policy source outside the surface is legitimate AS LONG AS the row also
    names a scanned document that carries its marker; the uncovered source is a
    stated limit, not a violation. Without this control the coverage rule could
    be passing because it always fires.
    """
    docs = _docs(tmp_path, a__md="a passage <!-- v4-mechanism: x -->\n")
    row = _row("x", declared_in=[UNSCANNED_DECLARED_DOC, "docs/architecture/a.md"])
    assert membership_errors([row], docs) == []


# ── the gate's own fail-closed proof (mutation) ─────────────────────────────
#
# A guard that cannot fail is worse than none. These cases are the ones that
# must NEVER become a pass, and they are permanent so the gate cannot regress
# into a no-op while the registry still passes.


def test_gate_fails_closed_on_a_mechanism_that_is_neither() -> None:
    errors = gate_errors([{"id": "x", "name": "x", "declared_in": []}])
    assert any("NEITHER" in e for e in errors), errors


def test_gate_fails_closed_on_an_unknown_state_word() -> None:
    """An unrecognised state is a violation, not a default (allow-list polarity)."""
    errors = gate_errors([{"id": "x", "name": "x", "declared_in": [], "state": "maybe"}])
    assert any("NEITHER" in e for e in errors), errors


def test_gate_fails_closed_on_implemented_without_a_test() -> None:
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "implemented",
                "code": ["tortoise/fanout.py"],
            }
        ]
    )
    assert any("declares no test" in e for e in errors), errors


def test_gate_fails_closed_on_a_missing_declared_path() -> None:
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "implemented",
                "code": ["tortoise/does_not_exist_5064.py"],
                "tests": ["tests/does_not_exist_5064.py"],
            }
        ]
    )
    assert any("does not exist" in e for e in errors), errors


def test_gate_fails_closed_on_not_implemented_without_a_tracking_issue() -> None:
    errors = gate_errors(
        [{"id": "x", "name": "x", "declared_in": [], "state": "not-implemented"}]
    )
    assert any("no real tracking_issue" in e for e in errors), errors


# ── the CODE ANCHOR: `implemented` must name a SYMBOL, not just a file ──────
#
# The gate's remaining `declared ⇒ present` inference: `code:` proved a FILE
# existed, so a mechanism whose symbol had been renamed, deleted or never
# written still read as shipped for as long as any `.py` remained at the
# declared path. `tortoise/fanout.py` losing `PER_ENTITY_FANOUT_CAP` is the
# concrete case — the row would keep claiming the fan-out cap is implemented
# while the constant that IS the cap is gone (the cited test is a second, not
# an anchor: it is exactly the thing a re-shaping PR edits).
#
# A row now names the SYMBOL that carries its mechanism, and the gate resolves
# it in the declared source. Same polarity as every other rule here: an
# unnameable symbol, or a symbol the declared source does not define, is a
# violation rather than a default.


def _implemented_row(**overrides) -> dict:
    row = {
        "id": "x",
        "name": "x",
        "declared_in": ["config/v4-mechanisms.yml"],
        "state": "implemented",
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
        "symbols": ["PER_ENTITY_FANOUT_CAP"],
    }
    row.update(overrides)
    return row


def test_code_anchor_accepts_an_implemented_row_whose_symbol_its_code_defines() -> None:
    """The control: a resolvable symbol is NOT a violation (so the two below
    fail for the reason they claim, not because the rule always fires)."""
    errors = [e for e in gate_errors([_implemented_row()]) if "symbol" in e]
    assert errors == [], errors


def test_gate_fails_closed_on_an_implemented_row_with_no_declared_symbol() -> None:
    errors = gate_errors([_implemented_row(symbols=None)])
    assert any("declares no symbol" in e for e in errors), errors


def test_gate_fails_closed_on_a_symbol_its_declared_code_does_not_define() -> None:
    """A file at the declared path is not the mechanism — the SYMBOL is."""
    errors = gate_errors([_implemented_row(symbols=["NOT_DEFINED_IN_FANOUT_5064"])])
    assert any(
        "NOT_DEFINED_IN_FANOUT_5064" in e and "symbol" in e for e in errors
    ), errors


def test_gate_fails_closed_on_a_symbol_defined_only_in_an_undeclared_file() -> None:
    """The symbol must be in a file the ROW declares for the mechanism —
    otherwise `symbols:` would be satisfied by anything importable anywhere.

    ⚠️ `embedding_verdict` is DEFINED — at `tortoise/embed_gate.py:122` — and
    the fanout row declares only `tortoise/fanout.py`, so the case fails for
    CONTAINMENT. The first version of this case named `EMBED_GATE`, which is
    defined NOWHERE in the repo: it was a duplicate of the case above and a
    gate that resolved symbols REPO-WIDE (the regression this case claims to
    pin) still rejected it, so the containment claim was unverified. With a
    symbol that really exists elsewhere, a repo-wide scan finds it and this
    case goes red — which is the proof it is named for.
    """
    row = _implemented_row(symbols=["embedding_verdict"])
    errors = gate_errors([row])
    assert any(
        "embedding_verdict" in e and str(row["code"]) in e for e in errors
    ), errors
    # The control for the SAME symbol: in a file the row DOES declare it is
    # accepted, so the failure above is containment and not the name.
    assert gate_errors(
        [
            _implemented_row(
                code=["tortoise/embed_gate.py"], symbols=["embedding_verdict"]
            )
        ]
    ) == []


def test_code_anchor_does_not_admit_a_dunder_reexport() -> None:
    """`__all__` is a re-export list, not the mechanism (see `_defined_names`).

    `tortoise/fanout.py` declares `__all__` at module level, so a naive
    "any module-level assignment is a definition" admitted it — letting a row
    anchor itself to a name present in almost every module, for a gate whose
    premise is that a symbol importable from elsewhere does not count.
    """
    errors = gate_errors([_implemented_row(symbols=["__all__"])])
    assert any(
        "__all__" in e and "not defined at module level" in e for e in errors
    ), errors


def test_gate_fails_closed_when_a_declared_code_file_cannot_be_parsed(
    tmp_path,
) -> None:
    """A resolved `code:` file that cannot be READ/PARSED is a violation.

    The fail-open this closes: `_path_violations` checks existence, kind and
    containment and never OPENS the file, so treating an unparsable source as
    "already reported by the path rule" produced `[]` — a PASS for a row whose
    declared mechanism was never read. Nothing else reports it, so the silence
    was total (settled by this case before the fix: it expected a pass).
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "broken.py").write_text("def broken(:\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/broken.py"],
        "tests": ["tests/test_x.py"],
        "symbols": ["ANYTHING"],
    }
    errors = gate_errors([row], root=repo)
    assert any(
        "could not be read or parsed" in e and "broken.py" in e for e in errors
    ), errors
    # One defect, one verdict: the symbol rule must NOT also claim the symbol is
    # absent from the file it could not read (that would be a second, wrong one).
    assert not any("not defined at module level" in e for e in errors), errors


def test_code_anchor_reads_source_with_pythons_own_encoding_rules(tmp_path) -> None:
    """A legal non-UTF-8 source (PEP 263) and a BOM-prefixed source are READABLE.

    Python imports both, so a gate that refuses them reports a present
    mechanism as absent — the wrong direction for a fail-closed check.
    `read_text(encoding="utf-8")` did exactly that (`UnicodeDecodeError` on a
    latin-1 coding cookie; a BOM left in the string, which `ast.parse` then
    rejects); `tokenize.open` applies the interpreter's own rules.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "pep263.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nX_PEP263 = 1\n# \xe9\n"
    )
    (repo / "src" / "bom.py").write_bytes(b"\xef\xbb\xbfX_BOM = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    for name, symbol in (("pep263.py", "X_PEP263"), ("bom.py", "X_BOM")):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": ["docs/design.md"],
            "code": [f"src/{name}"],
            "tests": ["tests/test_x.py"],
            "symbols": [symbol],
        }
        assert gate_errors([row], root=repo) == [], (name, gate_errors([row], root=repo))


def test_every_implemented_mechanism_names_a_symbol_its_code_defines() -> None:
    """The registry itself: no `implemented` row may stand on a file alone."""
    errors = [
        e for e in gate_errors(load_registry()["mechanisms"]) if "symbol" in e
    ]
    assert errors == [], (
        "every implemented row must anchor its mechanism to a symbol its own "
        "`code:` files define:\n  " + "\n  ".join(errors)
    )


def test_gate_fails_closed_on_a_placeholder_tracking_issue() -> None:
    """A tracking issue must be a REAL issue number, not a truthy placeholder.

    Cycle-1 review P1: `in (None, "", 0)` passed `[]`, `" "`, `"0"`, `"TBD"`
    and negative ints, so a `not-implemented` row could be "marked" with a
    placeholder on the one field whose whole job is to be a marker.
    """
    for bad in ([], " ", "TBD", "0", 0, -3, True, 0.0):  # int 0 pins `<= 0`
        errors = gate_errors(
            [
                {
                    "id": "x",
                    "name": "x",
                    "declared_in": [],
                    "state": "not-implemented",
                    "tracking_issue": bad,
                }
            ]
        )
        assert any("tracking_issue" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_fake_declared_path() -> None:
    """A declared path must be a NON-EMPTY, REPO-RELATIVE string that exists.

    Cycle-2 review P1: a bare `root / rel` was fail-OPEN. `""` resolves to
    `root` itself, an absolute path replaces `root`, `..` escapes the repo, and
    a non-string raises `TypeError` instead of reporting. So an `implemented`
    row could carry zero real code and zero real tests and still pass — the
    `declared => present` inference this gate exists to kill.
    """
    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
        # The CODE ANCHOR is resolvable here on purpose: without it the
        # unconditional "declares no symbol" verdict satisfies every bare
        # `assert errors` below whatever the rule under test does (the new
        # rule turned five cycle-tested cases into tautologies).
        "symbols": ["PER_ENTITY_FANOUT_CAP"],
    }
    for field in ("declared_in", "code", "tests"):
        # `"."` is included because `Path(".").parts == ()` and `root / "."` is
        # `root` — it EXISTS, so an exists-only check passed it silently.
        for bad in ([""], ["."], [".."], ["/etc/hosts"], ["../../etc/hosts"],
                    [123], [None], [{}], ["a/../b"], ["tortoise"], ["tests"]):
            row = dict(base)
            row[field] = bad
            errors = gate_errors([row])
            assert errors, (field, bad, "the gate PASSED a fake path")

    # ⚠️ The cases above are all KIND-INVALID, so `_kind_violation` rejects them
    # before the shape guard runs — and `assert errors` cannot tell the two
    # apart. Dropping `or ".." in p.parts` (and `p.is_absolute()`) survived the
    # whole suite (cycle-9 P2). These are KIND-VALID, so ONLY the repo-relative
    # guard can reject them, and the assertion names the message that must fire.
    for field, val in (
        ("declared_in", "config/../config/v4-mechanisms.yml"),
        ("code", "tortoise/../tortoise/fanout.py"),
        ("tests", "tests/../tests/test_fanout_cap.py"),
        ("code", str(ROOT / "tortoise" / "fanout.py")),
    ):
        row = dict(base)
        row[field] = [val]
        errors = gate_errors([row])
        assert any("repo-relative path inside the repo" in e for e in errors), (field, val, errors)


def test_gate_accepts_the_star_test_suffix(tmp_path) -> None:
    """`*_test.py` is a legitimate test name, not only `test_*.py` (cycle-9 P2).

    The second operand of `_is_test_name` was never the DECIDING check in any
    test, so mutating it to `or False` survived the suite — even though the repo
    ships `*_test.py` tests (`graph-scripts/smoke_test.py`, `benchmarks/load_test.py`).
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "impl.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "smoke_test.py").write_text("x\n")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/smoke_test.py"],
        "symbols": ["x"],  # the code anchor — `src/impl.py` defines it
    }
    assert gate_errors([row], root=repo) == []


def test_gate_fails_closed_on_an_unprobeable_path_without_raising() -> None:
    """A path the OS cannot probe is a violation, not a crash.

    Cycle-3 P2: a component longer than NAME_MAX made the probe raise
    `OSError`. Cycle-6 P2: an embedded NUL or a lone surrogate raises
    `ValueError`/`UnicodeEncodeError`. ⚠️ The hostile names are KIND-VALID, so
    they actually REACH the probe — an over-long name with the wrong extension
    is rejected by the kind check first, which left this test vacuous and the
    `except` uncovered (cycle-6 P2).
    """
    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
        "symbols": ["PER_ENTITY_FANOUT_CAP"],  # keeps `assert errors` deciding
    }
    hostile = {
        "declared_in": ["x" * 4996 + ".md", "a\x00b.md", "\ud800.md"],
        "code": ["x" * 4997 + ".py", "a\x00b.py", "\ud800.py"],
        "tests": ["test_" + "x" * 4994 + ".py", "test_\x00x.py", "test_\ud800.py"],
    }
    for field, bads in hostile.items():
        for bad in bads:
            row = dict(base)
            row[field] = [bad]
            errors = gate_errors([row])  # must NOT raise
            assert errors, (field, bad[:12])


def test_gate_fails_closed_on_a_symlink_loop(tmp_path) -> None:
    """A symlink loop raises `RuntimeError` from `resolve()` — a verdict, not a crash."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    (repo / "loop").symlink_to(repo / "loop")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["loop/a.py"],
        "tests": ["tests/test_x.py"],
        "symbols": ["PER_ENTITY_FANOUT_CAP"],  # no entry resolves; the loop does
    }
    errors = gate_errors([row], root=repo)  # must NOT raise
    assert errors, errors


def test_gate_fails_closed_on_a_mapping_code_or_declared_in() -> None:
    """A dict `code`/`declared_in` iterates its KEYS — it must be rejected.

    `{config/v4-mechanisms.yml: 1}` passed silently before cycle 2 (P1).

    ⚠️ The mapping key is KIND-VALID per field (cycle-10 P2-1): a kind-invalid
    key is rejected by `_kind_violation` before the container type is ever the
    deciding guard, and `assert errors` cannot tell the two apart — dropping the
    `isinstance(..., list)` check survived the suite while
    `code: {"tortoise/fanout.py": 1}` passed.
    """
    for field, key in (
        ("declared_in", "config/v4-mechanisms.yml"),
        ("code", "tortoise/fanout.py"),
        ("tests", "tests/test_fanout_cap.py"),
    ):
        row = {
            "id": "x",
            "name": "x",
            "declared_in": ["config/v4-mechanisms.yml"],
            "state": "implemented",
            "code": ["tortoise/fanout.py"],
            "tests": ["tests/test_fanout_cap.py"],
            "symbols": ["PER_ENTITY_FANOUT_CAP"],
        }
        row[field] = {key: 1}
        errors = gate_errors([row])
        # The container guard's OWN message: a bare `assert errors` was
        # satisfied by the unconditional "declares no symbol" verdict, so
        # dropping `isinstance(..., list)` survived the suite.
        expected = {
            "declared_in": "no declaring document",
            "code": "no code path",
            "tests": "declares no test",
        }[field]
        assert any(expected in e for e in errors), (field, expected, errors)


def test_gate_fails_closed_on_a_non_py_test_prefixed_name(tmp_path) -> None:
    """`test_*.py` requires the `.py` SUFFIX too (cycle-10 P2-2).

    Every non-test case lacked the `test_` prefix, so the `and name.endswith(
    ".py")` operand was never the deciding check — dropping it let an existing
    `tests/test_notes.txt` satisfy "has a test".
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "impl.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_notes.txt").write_text("x\n")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/test_notes.txt"],
    }
    errors = gate_errors([row], root=repo)
    assert any("not a test file" in e for e in errors), errors


def test_gate_accepts_the_yaml_document_suffix() -> None:
    """`.yml` is a legitimate declaring document, not only `.md` (cycle-10 P2-3).

    No test accepted a `.yml` `declared_in`, so removing that tuple member
    survived while the registry itself declares `packs/dev/manifest.yaml`.
    """
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
        "symbols": ["PER_ENTITY_FANOUT_CAP"],
    }
    assert gate_errors([row]) == []


def test_gate_fails_closed_on_a_non_test_file_in_the_tests_field() -> None:
    """`tests:` must name a TEST, not merely a file that exists (cycle-4 P2).

    Before this, `tests: ["README.md"]` satisfied "implemented WITH A TEST" —
    the gate's whole claim, unverifiable. The check is the repo's own naming
    convention, so it holds wherever tests live and needs no `tests/` prefix.
    """
    for bad in (
        ["tortoise/vet_gate.py"],
        ["config/v4-mechanisms.yml"],
        ["README.md"],
        ["tests/__init__.py"],
        # ⚠️ These two discriminate the OPERANDS (cycle-13 P1): with
        # `startswith("test")` (no underscore), `tools/testdb_canary_classify.py`
        # was accepted as a test; with `endswith("test.py")` (no underscore),
        # `tests/conftest.py` was. Both are real repo files and neither is
        # collected by pytest, so both must be rejected.
        ["tools/testdb_canary_classify.py"],
        ["tests/conftest.py"],
    ):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": ["config/v4-mechanisms.yml"],
            "code": ["tortoise/vet_gate.py"],
            "tests": bad,
        }
        errors = gate_errors([row])
        assert any("test file" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_non_list_registry() -> None:
    """A non-list `mechanisms` is a VERDICT, not a crash (cycle-4 P2).

    `yaml.safe_load("mechanisms:\n")` is `None`; it used to raise `TypeError`
    instead of reporting. Every other malformed shape was already reported.
    """
    for bad in (None, {}, "mechanisms", 7):
        errors = gate_errors(bad)
        assert any("must be a list" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_non_source_code_file() -> None:
    """`code:` must name production SOURCE, not merely a file that exists.

    Cycle-5 P2: existence-only let `code: ["README.md"]`, the registry YAML, or
    a TEST file satisfy "implemented (a code path with a test)" — the same
    fail-open class cycle 4 closed for `tests:`, left unfixed on `code:`.

    ⭐ ONE DEFECT, ONE VERDICT: these entries are on disk and inside the repo,
    so `_resolve_in_repo` (which asks only WHICH FILE TO READ, and applies no
    kind discipline) happily returns a path for them. Reading them anyway would
    report the SAME entry twice — once as "not production source" and once as
    "could not be read or parsed" — so the anchor consults the path rule per
    entry first. A wrong-kind entry yields the kind verdict ALONE.
    """
    for bad in (
        ["README.md"],
        ["config/v4-mechanisms.yml"],
        ["tests/test_fanout_cap.py"],
        ["tortoise/fanout.pyc"],
    ):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": ["config/v4-mechanisms.yml"],
            "code": bad,
            "tests": ["tests/test_fanout_cap.py"],
            "symbols": ["PER_ENTITY_FANOUT_CAP"],
        }
        errors = gate_errors([row])
        assert any("production source" in e for e in errors), (bad, errors)
        assert not any("could not be read or parsed" in e for e in errors), (
            bad,
            "one defect must not be reported twice",
            errors,
        )


def test_gate_fails_closed_on_a_non_document_declared_in() -> None:
    """`declared_in:` must name a DOCUMENT, not any file that exists.

    ⚠️ `notes.txt` is included because the tuple's PERMISSIVE direction was
    unpinned (cycle-10 P2-3): every rejection case had no suffix or a `.py` one,
    so adding `.txt`/`.rst` to the accepted tuple survived the suite.
    """
    for bad in (["tortoise/fanout.py"], ["tests/test_fanout_cap.py"], ["README"], ["notes.txt"], ["NOTES.rst"]):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": bad,
            "code": ["tortoise/fanout.py"],
            "tests": ["tests/test_fanout_cap.py"],
        }
        errors = gate_errors([row])
        assert any("declaring document" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_path_outside_the_repo_via_symlink(tmp_path) -> None:
    """Containment is by RESOLVED path (cycle-5 P2).

    The repo carries committed symlinks (`scripts/`, `skills/` → agent-infra),
    so `is_file()` alone followed a declared path OUT of the repo.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text("x = 1\n")
    (repo / "link").symlink_to(outside, target_is_directory=True)

    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["link/escaped.py"],
        "tests": ["tests/test_x.py"],
    }
    errors = gate_errors([row], root=repo)
    assert any("does not exist" in e for e in errors), errors


def test_gate_fails_closed_on_a_symlink_that_launders_the_kind(tmp_path) -> None:
    """A symlink's KIND is the TARGET's, not its name (cycle-7 P1).

    `fake_code.py -> docs/design.md` passed a name-only kind check, so a row
    could be `implemented` with no `.py` ever named — the existence probe
    resolved while the kind check did not.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    (repo / "src").mkdir()
    (repo / "src" / "impl.py").write_text("x = 1\n")
    (repo / "fake_code.py").symlink_to(repo / "docs" / "design.md")
    (repo / "test_fake.py").symlink_to(repo / "docs" / "design.md")
    (repo / "fake_doc.md").symlink_to(repo / "src" / "impl.py")

    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/test_x.py"],
        "symbols": ["x"],  # `src/impl.py` defines it — `assert errors` decides
    }
    for field, bad in (
        ("code", ["fake_code.py"]),
        ("tests", ["test_fake.py"]),
        ("declared_in", ["fake_doc.md"]),
    ):
        row = dict(base)
        row[field] = bad
        errors = gate_errors([row], root=repo)
        assert errors, (field, bad, "the gate PASSED a kind-laundering symlink")


def test_gate_fails_closed_on_a_kind_valid_directory(tmp_path) -> None:
    """A DIRECTORY with a KIND-VALID name is still not an artifact (cycle-8 P2).

    The cycle-3 directory cases used kind-invalid names (`tortoise`, `tests`),
    so the kind check rejected them BEFORE the probe — leaving `is_file()`
    unpinned: mutating it to `exists()` survived the whole suite.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "real.py").write_text("x = 1\n")
    (repo / "src" / "mod.py").mkdir()  # a DIRECTORY named `mod.py`
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("x\n")
    (repo / "docs" / "policy.md").mkdir()  # a DIRECTORY named `policy.md`
    (repo / "tests").mkdir()
    (repo / "tests" / "test_real.py").write_text("x\n")
    (repo / "tests" / "test_dir.py").mkdir()  # a DIRECTORY named `test_dir.py`

    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/real.md"],
        "code": ["src/real.py"],
        "tests": ["tests/test_real.py"],
    }
    for field, bad in (
        ("declared_in", ["docs/policy.md"]),
        ("code", ["src/mod.py"]),
        ("tests", ["tests/test_dir.py"]),
    ):
        row = dict(base)
        row[field] = bad
        errors = gate_errors([row], root=repo)
        assert any("does not exist" in e for e in errors), (field, errors)


def test_gate_fails_closed_on_a_non_string_name() -> None:
    """`name` must be a non-blank string, like `id` (cycle-7 P2)."""
    for bad in (True, float("nan"), " ", 7, None):
        row = {
            "id": "x",
            "name": bad,
            "state": "not-implemented",
            "declared_in": ["config/v4-mechanisms.yml"],
            "tracking_issue": 5006,
        }
        errors = gate_errors([row])
        assert any("declares no name" in e for e in errors), (bad, errors)


def test_gate_pins_the_branches_the_ad_hoc_cases_do_not_reach() -> None:
    """Every branch of `gate_errors` is reached by a case (cycle-9 pre-empt).

    A branch no test reaches is a guard that cannot be SEEN to fail — the file's
    own rule. These are the branches the targeted cases above do not cover.
    """
    good = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
        "symbols": ["PER_ENTITY_FANOUT_CAP"],
    }
    cases = {
        "row is not a mapping": (["not a dict"], "not a mapping"),
        "id missing": ([{"name": "x", "state": "not-implemented", "tracking_issue": 5006}], "no string id"),
        "id is not a string": ([{**good, "id": 1}], "no string id"),
        "id is empty": ([{**good, "id": ""}], "no string id"),
        "duplicate id": ([good, dict(good)], "duplicate id"),
        "declared_in missing": ([{**good, "declared_in": []}], "no declaring document"),
        "declared_in not a list": ([{**good, "declared_in": "x"}], "no declaring document"),
        "code missing": ([{**good, "code": []}], "no code path"),
        "tests missing": ([{**good, "tests": []}], "declares no test"),
        "code not a list": ([{**good, "code": {}}], "no code path"),
        # The `symbols:` field's own shape guards (the code anchor). A
        # non-string or blank entry must be a VERDICT, not a `TypeError` from
        # `sorted()` on a mixed list — the crash-vs-verdict class this file
        # pins elsewhere. None of these was reached by any case, so mutating
        # the guard away survived the suite.
        "symbols not a list": ([{**good, "symbols": "PER_ENTITY_FANOUT_CAP"}], "declares no symbol"),
        "symbols is empty": ([{**good, "symbols": []}], "declares no symbol"),
        "symbols entry is not a string": ([{**good, "symbols": [123]}], "non-empty strings"),
        "symbols entry is blank": ([{**good, "symbols": ["   "]}], "non-empty strings"),
        "symbols entry is None": ([{**good, "symbols": [None, "PER_ENTITY_FANOUT_CAP"]}], "non-empty strings"),
        "empty registry": ([], None),
    }
    for label, (mechs, expected) in cases.items():
        errors = gate_errors(mechs)
        if expected is None:
            # An empty registry is empty, not a violation — the CLOSED-SET tuple
            # pin is what refuses it (see the closed-set test), not this gate.
            assert errors == [], (label, errors)
            continue
        assert any(expected in e for e in errors), (label, expected, errors)


def test_gate_fails_closed_on_a_descriptive_state() -> None:
    """`descriptive` is NOT accepted until #5064 open question (c) settles.

    ⚠️ The valid `tracking_issue` is load-bearing: without it the row fails on
    the missing issue instead, so the test would pass for the wrong reason and
    not pin `STATES`. Adding `"descriptive"` to `STATES` survived until this
    case existed (cycle-11 P2).
    """
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "descriptive",
                "tracking_issue": 5006,
            }
        ]
    )
    assert any("NEITHER" in e for e in errors), errors


def test_gate_accepts_the_issue_number_one_and_a_symlinked_root(tmp_path) -> None:
    """Positive pins: the accepted LOWER bound, and a symlinked root.

    `tracking_issue: 1` is a legitimate issue number — cycle 12 pinned the `0`
    boundary from above, but nothing asserted that `1` is ACCEPTED, so mutating
    `issue <= 0` to `issue <= 1` survived (cycle-13 P2).

    A symlinked `root` must resolve, not report every declared path absent —
    mutating `root.resolve()` to `root` survived (cycle-13 P2), and the failure
    direction here is a WRONG verdict (a present mechanism claimed absent).
    """
    assert gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "not-implemented",
                "tracking_issue": 1,
            }
        ]
    ) == []

    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    (real / "src" / "impl.py").write_text("x = 1\n")
    (real / "docs").mkdir()
    (real / "docs" / "design.md").write_text("x\n")
    (real / "tests").mkdir()
    (real / "tests" / "test_x.py").write_text("x\n")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/test_x.py"],
        "symbols": ["x"],
    }
    assert gate_errors([row], root=linked) == []


def test_gate_reports_exactly_one_violation_for_a_neither_row() -> None:
    """The NEITHER branch must not ALSO report a tracking-issue error.

    Dropping the `continue` after the NEITHER branch survived, because every
    assertion was `any(...)`-shaped: the row stayed red while gaining a
    spurious "not-implemented with no real tracking_issue" for a state that is
    not `not-implemented` (cycle-13 P2).
    """
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "maybe",
            }
        ]
    )
    assert len(errors) == 1, errors
    assert "NEITHER" in errors[0]


def test_gate_does_not_swallow_an_unlisted_probe_exception(monkeypatch) -> None:
    """The probe converts only the THREE named classes into a verdict.

    Broadening the `except` to `Exception` survived, silently turning any
    unexpected error into "does not exist" (cycle-13 P2).
    """
    real_resolve = Path.resolve

    def boom(self, *args, **kwargs):
        if self.name == "fanout.py":
            raise TypeError("not a filesystem error")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", boom)
    with pytest.raises(TypeError):
        gate_errors(
            [
                {
                    "id": "x",
                    "name": "x",
                    "declared_in": ["config/v4-mechanisms.yml"],
                    "state": "implemented",
                    "code": ["tortoise/fanout.py"],
                    "tests": ["tests/test_fanout_cap.py"],
                }
            ]
        )


# ── the ORDERING of the declared set: a precondition cannot be unbuilt ──────
#
# #5064's refactor direction, item 3, names this as part of the substrate:
#
#   *"a **precondition** (the fan-out cap gates deduplication) is ordered in the
#   registry rather than discovered at implementation time"*
#
# and its Verification Checklist carries the row that executes it:
#
#   `precondition ordering | unit | a mechanism whose precondition is unbuilt
#    cannot be marked implemented`
#
# The registry shipped (`#7553`) with the state model and the code anchor but
# no ordering: `preconditions:` was an UNKNOWN FIELD, and `gate_errors` read
# only the keys it knew, so a row could declare `state: implemented` on top of
# a mechanism that does not exist yet and stay green — the exact
# `declared ⇒ present` inference one level up, applied to *order* instead of
# existence. These cases are the mutation proof for the ordering rule: each one
# passes the gate WITHOUT it (that is why they are written against the same
# `gate_errors` the registry is judged by, not against a helper).


def _absent_row(**overrides) -> dict:
    """A validly MARKED `not-implemented` row — the unbuilt precondition."""
    row = {
        "id": "y",
        "name": "y",
        "declared_in": ["config/v4-mechanisms.yml"],
        "state": "not-implemented",
        "tracking_issue": 5006,
    }
    row.update(overrides)
    return row


def test_gate_accepts_an_implemented_row_whose_preconditions_are_implemented() -> None:
    """The control: a BUILT precondition is not a violation.

    Without this case the three fail-closed cases below could be passing
    because the rule always fires.
    """
    errors = gate_errors(
        [
            _implemented_row(id="a"),
            _implemented_row(id="b", preconditions=["a"]),
        ]
    )
    assert errors == [], errors


def test_gate_fails_closed_on_an_implemented_row_whose_precondition_is_unbuilt() -> None:
    """THE rule: `implemented` on top of an unbuilt precondition is a violation.

    This is #5064's checklist row verbatim. The precondition row here is
    perfectly well MARKED (`not-implemented` + a tracking issue) — the defect is
    the ORDER: the dependant claims to be shipped while what it is built on is
    recorded as absent.
    """
    errors = gate_errors([_absent_row(id="a"), _implemented_row(id="b", preconditions=["a"])])
    assert any("precondition" in e and "b" in e for e in errors), errors
    # The MARKED row itself is not the defect: it must stay clean, or the
    # verdict would be about the absent mechanism rather than the ordering.
    assert not any(e.startswith("a:") for e in errors), errors


def test_gate_fails_closed_on_a_precondition_that_is_not_a_declared_mechanism() -> None:
    """A precondition must name a row in THIS registry.

    An ordering against `5009` (an issue number), a typo (`fanoutcap`), or a
    mechanism from another registry is not an ordering: nothing can ever say
    whether it is built, so the dependant's claim is unverifiable and fails
    closed.
    """
    errors = gate_errors([_implemented_row(id="b", preconditions=["fanoutcap"])])
    # ⚠️ The message must be the UNDECLARED-ID one, not the unbuilt-ordering
    # one: a mutated gate that lets the unknown id through still reds the row
    # ("precondition(s) ['fanoutcap'] are not implemented"), so an
    # `any("fanoutcap" in e)` assertion passed while the rule it names was GONE
    # (measured — mutation M3 survived on the earlier wording).
    assert any("an ordering must name a row in this registry" in e for e in errors), errors


def test_gate_fails_closed_on_a_self_precondition() -> None:
    """A mechanism cannot be its own precondition.

    Self-reference would make the rule vacuous (`implemented` requires
    itself — true by construction) and is a data error a reader would never
    suspect from a green gate.
    """
    errors = gate_errors([_implemented_row(id="b", preconditions=["b"])])
    assert any("itself" in e for e in errors), errors


def test_gate_fails_closed_on_a_precondition_cycle() -> None:
    """An ordering that returns to its start is not an ordering.

    `a → b → a` with both rows `implemented` passes the per-row rule (each names
    a precondition that IS implemented), so the cycle has to be judged over the
    SET. Left unchecked it defeats the ordering silently: nothing can be built
    first, so the substrate never lands and no gate says so.
    """
    errors = gate_errors(
        [
            _implemented_row(id="a", preconditions=["b"]),
            _implemented_row(id="b", preconditions=["a"]),
        ]
    )
    assert any("cycle" in e for e in errors), errors


def test_gate_fails_closed_on_a_preconditions_value_that_is_not_a_list() -> None:
    """The field's own shape guards, same polarity as `symbols:`.

    `preconditions: fanout_cap` (a bare string) iterates CHARACTER BY CHARACTER;
    `{}` iterates keys. Both must be a verdict, not a silently empty ordering.
    """
    for value in ("fanout_cap", {}, ["  "], [], None):
        errors = gate_errors([_implemented_row(id="b", preconditions=value)])
        assert any("preconditions" in e for e in errors), (value, errors)


def test_gate_accepts_a_precondition_declared_by_a_not_implemented_row() -> None:
    """Ordering is recorded on ABSENT rows too — that is its whole point.

    `near_dup_merge` is `not-implemented` and carries the ordering today; a
    marked-absent row declaring its precondition is the case the registry
    actually ships, so it must be clean. So must a row that declares NO ordering
    (the field omitted) — and only that: an explicit `preconditions:` with no
    value is refused above, because an empty declaration must not read as one.
    """
    assert gate_errors([_absent_row(id="a"), _absent_row(id="b", preconditions=["a"])]) == []
    assert gate_errors([_absent_row()]) == []


def test_gate_pins_the_preconditions_branches_the_ordering_cases_do_not_reach() -> None:
    """The two operands no ordering case above reaches, plus the forward reference.

    ⚠️ Found by an independent review of this diff (P2, confidence 90): the
    duplicate-entry guard and the non-string-entry operand were UNPINNED — a
    mutation turning either into a no-op left all 45 cases green while the gate
    silently accepted `preconditions: ["a", "a"]` or `[123]`. The rule is only
    as real as the case that fails without it, so both are pinned here by their
    OWN message (an `any("preconditions" in e)` assertion would pass on the
    shape message every case above already emits).

    The forward reference is the third half: a dependant row listed BEFORE its
    precondition must be ACCEPTED — the pre-pass that resolves the declared set
    exists for exactly that, it is the wrong-verdict direction this gate must
    not have, and nothing exercised it.
    """
    duplicate = gate_errors(
        [_implemented_row(id="a"), _implemented_row(id="b", preconditions=["a", "a"])]
    )
    assert any("names a mechanism twice" in e for e in duplicate), duplicate

    non_string = gate_errors([_implemented_row(id="b", preconditions=[123])])
    assert any("entries must be non-empty strings" in e for e in non_string), non_string

    # The control for the two above: both rows are otherwise clean, so the
    # verdict is about the ordering field and nothing else.
    assert gate_errors([_implemented_row(id="a"), _implemented_row(id="b")]) == []

    # A precondition declared LATER in the list than its dependant.
    forward = gate_errors(
        [_implemented_row(id="b", preconditions=["a"]), _implemented_row(id="a")]
    )
    assert forward == [], forward


def test_registry_orders_the_fanout_precondition_under_near_dup_merge() -> None:
    """The REAL registry carries the documented ordering, not just fixtures.

    #5010 ("the fan-out cap … the precondition for ever deriving `aboutObject`",
    as #5064's own symptom table summarises it: *"the adopted fan-out cap (200)
    has no implementation — and it is the precondition for ever deduplicating"*)
    and #5064's refactor direction, which names the same pair as its ordering
    example. If a regression drops the field, every fixture case above still
    passes — this is the pin that the ordering survives in the file the gate
    actually reads.
    """
    rows = {r["id"]: r for r in load_registry()["mechanisms"]}
    assert rows["near_dup_merge"].get("preconditions") == ["fanout_cap"]
    assert rows["fanout_cap"].get("preconditions") in (None, [])
    assert gate_errors(load_registry()["mechanisms"]) == []


def test_load_registry_refuses_a_non_mapping_document(tmp_path) -> None:
    """`load_registry` guards the document shape (cycle-13 P2)."""
    bad = tmp_path / "bad.yml"
    bad.write_text("- a\n- b\n")
    with pytest.raises(AssertionError):
        load_registry(bad)


if __name__ == "__main__":  # pragma: no cover - manual mutation aid
    raise SystemExit(pytest.main([__file__, "-q"]))
