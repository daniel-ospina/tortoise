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
* It does not implement any mechanism. Four of the thirteen are genuinely
  absent
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
    "exact_dedup",
    "near_dup_detect",
    "near_dup_merge",
    "against_priors_consolidation",
    "span_link",
    "embed_gate",
    "sufficiency_router",
    "fanout_cap",
    "state_value_carve_out",
    "atomic_point_write",
    "failure_policy",
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


def test_declared_set_is_exactly_the_named_mechanisms() -> None:
    """The registry declares the closed set: indicator (1) + the enumeration."""
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


def test_the_state_value_carve_out_row_records_what_is_enforced_and_what_is_not() -> None:
    """The enumeration's first deliverable, pinned by name (#5064, #2453, #9775).

    `STATE_VALUE_CARVE_OUT` is declared by name in §7 of BOTH design documents and
    the table carried only seven rows — so the mechanism the checklist names
    ("verbatim operational values hold on a live write") had **neither code entry
    nor marker** in the registry while the gate passed. The row carried
    `not-implemented` while the guarantee was prompt-only (#2453's own closing
    records that).

    ⛔ Why the state may now read `implemented`, and what keeps that from being
    the defect this registry exists to catch. The row moved only when the
    ENFORCEMENT half landed in the write path (`#10360`: `execute_embed` refuses
    a point whose own verbatim quote holds a different value in the same slot),
    with a test that goes red when the rule is removed. The state word alone
    would be overclaiming, so this test pins the residual IN the row: the note
    must keep naming what is still not enforced. A future lane that deletes that
    section while keeping `implemented` reds this test instead of shipping a
    blanket claim.
    """
    rows = {r["id"]: r for r in load_registry()["mechanisms"]}
    assert "state_value_carve_out" in rows, sorted(rows)
    row = rows["state_value_carve_out"]
    assert row["state"] == IMPLEMENTED, row
    # An `implemented` row names its code, its test and its symbols — that is
    # the `declared ⇒ present` contract the gate reads.
    for field in ("code", "tests", "symbols"):
        assert row.get(field), (field, row)
    assert "tortoise/extractor_v2.py" in row["code"], row
    assert "tests/test_state_value_verbatim_10360.py" in row["tests"], row
    assert "_refuse_rewritten_value" in row["symbols"], row
    # The residual stays STATED, as a literal: the claim it guards is "nothing
    # is silently absorbed". The class the write path cannot reach (a value the
    # model never emitted) must stay readable in the row.
    assert "STILL NOT ENFORCED" in row["note"], row["note"]
    assert "never EMITS" in row["note"], row["note"]
    # The compact render is NAMED in the row: it is the path the row's claim was
    # once false on (#9775), so a reader is sent to the render the compaction
    # rule lives in rather than to a prose assurance.
    assert "_render_master_compact" in row["note"], row["note"]
    # And the mint guard's COARSE KEY is stated as a residual (#10513 review
    # cycle-11 P3): the slot key carries the value and no identity for the
    # claim, so a distinct endpoint sharing an anchor+value is dropped too. The
    # claim this row makes is "nothing is silently absorbed", so the cost has to
    # stay readable in the artifact a lane acts on.
    assert "THAT KEY IS COARSE" in row["note"], row["note"]
    assert "sharing an anchor word and" in row["note"], row["note"]
    # And the SHARED tokenizer arm's over-read is stated too (#10513 review
    # cycle-11 advisory): the dash-as-sign fix is not local to this rule — it
    # feeds `_value_bindings`, which the against-store fold path also consumes —
    # and it reads an em dash written flush against the numeral as a minus. A
    # lane that narrows the arm, or widens it, has to move this row.
    assert "ATTACHED" in row["note"], row["note"]
    assert "OVER-READS A BULLET GLYPH" in row["note"], row["note"]
    assert "test_never_across_fold_5080" in row["note"], row["note"]


#: Number words the registry prose may spell, keyed by the count they name. A
#: row that counts something must spell the count the SOURCE gives (below), not
#: a number someone typed once and nobody can falsify.
_COUNT_WORDS = {2: "both", 3: "three", 4: "four", 5: "five", 6: "six"}


def test_the_carve_out_row_counts_the_master_renders_and_names_both_pins() -> None:
    """The row must describe the code that SHIPPED, not the pre-#9775 tree (#10382).

    Two claims in the row's own sentence were false at the head the review read,
    and both are the declared-vs-present class this registry exists to catch:

    * the dispatcher half was routed to ONE pin,
      `test_every_render_path_carries_the_carve_out_the_guarantee_requires`, which
      never reaches a dispatcher — it ends in `_render_master_compact` direct
      calls. `render_s2_prompt` / `render_s4_prompt` are exercised only by
      `::test_the_dispatcher_prompts_carry_the_carve_out_once_and_compactly`, so
      the only assertion covering `core_only=False` (the flag whose DEFAULT
      silently moves the render under test), the `PACK KINDS` + compact-header
      proof that the compact branch was taken, and `count(...) == 1` on the real
      S2/S4 path, was invisible from the registry — and the row still claimed the
      coverage;
    * the vocabulary was said to ride "both master renders", but `_render_master`
      dispatches to THREE (`_render_master_core_only`, `_render_master_verbose`,
      `_render_master_compact`), all three carrying it since #9775. "both" is the
      arithmetic of the pre-#9775 tree, where compact carried neither block.

    The count is taken from `extractor_v2` rather than typed here, so adding or
    removing a master render without updating the row reds this test instead of
    leaving a sentence nothing can falsify.
    """
    from tortoise import extractor_v2 as v2

    rows = {r["id"]: r for r in load_registry()["mechanisms"]}
    note = rows["state_value_carve_out"]["note"]
    renders = sorted(
        name for name in dir(v2)
        if name.startswith("_render_master_") and callable(getattr(v2, name))
    )
    assert renders, "the `_render_master_*` family is the thing this row counts"
    word = _COUNT_WORDS.get(len(renders))
    assert word is not None, (
        f"the master-render family is now {len(renders)} strong ({renders}) — "
        "extend _COUNT_WORDS and the registry sentence together"
    )
    assert f"{word} master renders" in note, (
        f"the row does not spell the count of master renders that exist: "
        f"{len(renders)} ({renders}) → expected `{word} master renders`"
    )
    assert "both master renders" not in note, (
        "the row still spells the pre-#9775 arithmetic: compact carried neither "
        f"the carve-out nor the vocabulary then; all {len(renders)} master "
        "renders carry both now (~#9775, #10382 review)"
    )
    dispatcher_pin = (
        "::test_the_dispatcher_prompts_carry_the_carve_out_once_and_compactly"
    )
    assert dispatcher_pin in note, (
        "the row routes the dispatch half to a pin that never calls a "
        "dispatcher — the dispatcher guard must be NAMED in the row, or a "
        "reader is sent to the wrong test and deleting its `core_only=False` "
        "leaves every gate green (~#10382 review)"
    )
    assert (
        "::test_every_render_path_carries_the_carve_out_the_guarantee_requires"
        in note
    ), "the render-path pin the dispatcher pin complements must stay named"


#: The carve-out's BOUNDARY SENTENCE, as a literal: the clause that says what
#: may STILL be dropped once the durable values are carried. It is pinned as a
#: literal rather than through `STATE_VALUE_CARVE_OUT` because comparing the
#: constant against a render that renders the same constant is true by
#: construction — editing this sentence moved both sides together and left the
#: guard green (measured, #9775 fix round 2), which is precisely the
#: "declared-vs-present" hole the registry row exists to catch.
CARVE_OUT_BOUNDARY = "Only INCIDENTAL process logistics remain droppable"


def test_every_render_path_carries_the_carve_out_the_guarantee_requires() -> None:
    """The declared guarantee holds on EVERY path that builds a prompt (#9775).

    §7 of `EXTRACTOR-V4-ARCHITECTURE.md` declares it: *"any mechanical filter MUST
    carry this carve-out, or it will drop the facts users care about most."* The
    compact render (`TORTOISE_EXTRACTOR_PROMPT=compact`) appended nothing — no
    `STATE_VALUE_CARVE_OUT` and no user-personal-state vocabulary — so a documented
    render toggle silently removed a declared guarantee on the S2/S4 write path.
    (Measured on the capstone corpus: 1 of 14 value-bearing planted units survived,
    against a bar of 13 — #9775's own comment.)

    So the guarantee is pinned as a GUARANTEE, not as string presence on one path.
    Both halves are asserted where they are load-bearing:

    * `STATE_VALUE_CARVE_OUT` — the whole constant AND a literal of its boundary
      sentence, so editing that sentence reds here rather than shipping under an
      unchanged header. The literal is what makes the claim true: an edit to the
      constant moves BOTH sides of `v2.STATE_VALUE_CARVE_OUT in rendered`
      together, so pinning the constant alone cannot see the boundary sentence go
      (measured: that mutation left this test green);
    * the user-personal-state vocabulary it names — S2's Tier-A rule tells the
      mapper to see "the USER-PERSONAL-STATE VOCABULARY in the master list", so a
      render that omits the block leaves that instruction pointing at a section
      the prompt does not carry.

    ⚠️ The compact render's `if story:` gate is the thing that made a caller-level
    fix insufficient, so the render is exercised in THREE story shapes: a matched
    story (its granularity hint renders — the non-vacuity half), `None`, and a
    story matching no trigger. A carve-out appended INSIDE the story gate — the
    shape an earlier revision of this guard warned about — passes the first and
    reds the other two, which is exactly the regression that would re-open #9775
    in a form a single-case assertion cannot see.
    """
    from tortoise import extractor_v2 as v2

    master = v2.build_master_list()
    ups_header = "USER-PERSONAL-STATE VOCABULARY (Tier-A classification"
    ups = master.get("user_personal_state") or {}
    assert ups, (
        "the master carries no user-personal-state vocabulary, so the "
        "vocabulary half of this guard would prove nothing"
    )

    def _assert_guarantee(where: str, rendered: str, *,
                          vocabulary: bool = True) -> None:
        assert v2.STATE_VALUE_CARVE_OUT in rendered, (
            f"{where} dropped the declared state-value carve-out (~#9775): the "
            "guarantee is `STATE_VALUE_CARVE_OUT` rendered whole, not a header"
        )
        # The BOUNDARY SENTENCE, as a literal — not through the constant. This is
        # the half that says what may still be dropped, and a literal is the only
        # way to pin it: the constant comparison above tracks the constant, so an
        # edit to the sentence alone keeps it green on BOTH sides.
        assert CARVE_OUT_BOUNDARY in rendered, (
            f"{where} carries the carve-out but not its boundary sentence "
            f"({CARVE_OUT_BOUNDARY!r}) — what may still be dropped is "
            "unpinned by the constant alone (~#9775)"
        )
        if not vocabulary:
            return
        assert ups_header in rendered, (
            f"{where} dropped the user-personal-state vocabulary the carve-out "
            "names (~#9775) — S2's Tier-A rule points the mapper at that block"
        )
        for cat in ups:
            assert f"- {cat}: " in rendered, (where, cat)

    # S1's own seam (E2/D3): the carve-out rides the granularity slot. The
    # VOCABULARY half is not S1's to carry — S1 renders granularity, not the
    # master list, and its template never points at the vocabulary block — so it
    # is asserted only where a master render exists.
    _assert_guarantee("S1 (`_granularity_text`)", v2._granularity_text(master),
                      vocabulary=False)
    _assert_guarantee("the core-only master render",
                      v2._render_master_core_only(master))
    _assert_guarantee("the verbose master render",
                      v2._render_master_verbose(master))

    matched_story = "we shipped the dev: extractor change"
    matched = v2._render_master_compact(master, matched_story)
    # Non-vacuity AND scope: the story gate is still live — it compacts the HINT
    # block — while the guarantee rides through it untouched.
    assert "MEMORY GRANULARITY" in matched, (
        "the compact render no longer emits its granularity hint for a "
        "dev:-matched story, so the story-gate half of this guard is vacuous"
    )
    for story in (matched_story, None, "lunch was fine"):
        rendered = v2._render_master_compact(master, story)
        _assert_guarantee(f"the compact render (story={story!r})", rendered)


def test_the_dispatcher_prompts_carry_the_carve_out_once_and_compactly(
    monkeypatch,
) -> None:
    """…and through the dispatcher the S2/S4 write path actually CALLS (#9775).

    A fix that only touched `_render_master_compact` while the prompt path bypassed
    it cannot pass this. Two details are what make that sentence true, and both were
    measured as WRONG before this revision:

    * `core_only=False` is passed **explicitly**. `render_s2_prompt` / `render_s4_prompt`
      resolve `core_only` from `TORTOISE_CLASSIFY_LATER` when it is `None`, and with
      that flag on `_render_master` takes the CORE-ONLY branch — so the assertions
      silently tested a render that is not the one under test (measured: with the
      render fix reverted and `TORTOISE_CLASSIFY_LATER=1`, the dispatcher half
      contributed nothing and the direct assertions failed alone);
    * the COMPACT branch is PROVEN taken, not assumed: `PACK KINDS` survives
      compaction but is absent from the core-only render, and the compact master's
      header (`"…from this list. Do NOT mint kinds."`) is compact-only text — the
      verbose render interposes `(namespaced or bare form)`.

    Exactly ONCE per prompt: the seam rule is single-render, per the
    `SHORT_SESSION_FACT_RULE` precedent in `tests/test_extractor_v2.py`.
    """
    from tortoise import extractor_v2 as v2

    master = v2.build_master_list()
    marker = "STATE-VALUE CARVE-OUT"
    compact_header = "from this list. Do NOT mint kinds."
    matched_story = "we shipped the dev: extractor change"
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROMPT", "compact")
    for name, prompt in (
        ("S2 (compact, via `render_s2_prompt`)",
         v2.render_s2_prompt(master=master, story=matched_story,
                             core_only=False)),
        ("S4 (compact, via `render_s4_prompt`)",
         v2.render_s4_prompt(matched_story, {"results": []}, {},
                             master=master, core_only=False)),
    ):
        assert "PACK KINDS" in prompt, (
            f"{name} carries no PACK KINDS section, so it rendered the CORE-ONLY "
            "master and this guard is not testing the compact path at all "
            "(pass `core_only=False` explicitly — the flag default is "
            "`TORTOISE_CLASSIFY_LATER`)"
        )
        assert compact_header in prompt, (
            f"{name} does not carry the compact master header, so it rendered "
            "the VERBOSE master and the compact path is unpinned here"
        )
        assert v2.STATE_VALUE_CARVE_OUT in prompt, (
            f"{name} dropped the declared state-value carve-out (~#9775): the "
            "compact prompt reached through the dispatcher carries no guarantee"
        )
        assert CARVE_OUT_BOUNDARY in prompt, (
            f"{name} carries the carve-out but not its boundary sentence "
            f"({CARVE_OUT_BOUNDARY!r}) (~#9775)"
        )
        assert prompt.count(marker) == 1, (
            f"{name} renders the carve-out {prompt.count(marker)} times — "
            "one prompt, one copy of the guarantee"
        )


def test_the_value_fidelity_rule_rides_the_s2s4_slot_and_no_master_render() -> None:
    """The row's `never S1, never the master render` claim, pinned in BOTH directions.

    `tests/test_extractor_v4_citation_rot.py` pins never-S1 and
    `tests/test_extractor_v2.py` byte-pins the CORE-ONLY master render, but the
    DEFAULT master render (`_render_master_verbose`) was pinned by nothing, so a
    lane appending the rule there shipped a SECOND copy of the same instruction
    inside one S2/S4 prompt with every gate green (measured before this test:
    484 passed). The row claimed the whole pair was pinned when only one half was.

    So both halves are asserted here: the rule TEXT must ride the S2/S4
    `{anti_routine}` slot, and it must be absent from all three master renders —
    present-if-moved and absent-if-duplicated, neither passing by vacancy.
    """
    from tortoise import extractor_v2 as v2

    master = v2.build_master_list()
    rule = v2.VALUE_FIDELITY_RULE
    assert rule in v2._s2s4_rules(master), (
        "the S2/S4 rules block no longer carries VALUE_FIDELITY_RULE at the "
        "`{anti_routine}` slot — that slot is the ONLY place the row says it "
        "rides; restore it there before reading the absence claims below"
    )
    for name, render in (
        ("S1 (`_granularity_text`)", v2._granularity_text),
        ("core-only master", v2._render_master_core_only),
        ("verbose master", v2._render_master_verbose),
    ):
        rendered = render(master)
        assert rule not in rendered, (
            f"{name} now carries VALUE_FIDELITY_RULE — it is already emitted by "
            "`_s2s4_rules()`, so this is the same instruction twice in one "
            "prompt (the `SEAMS:` comment on `SHORT_SESSION_FACT_RULE` forbids "
            "exactly that for its own constant). Remove it there; do not narrow "
            "this assertion"
        )


# ── THE ENUMERATION: the design's own mechanism lists are compared ───────────
#
# `DECLARED_IDS` is a PIN, and a pin can only hold a set somebody already wrote.
# The membership markers close that gap for a mechanism that HAS a row; they
# cannot close it for a mechanism the design GAINS and nobody marks — the stated
# residual. One enumeration in the design is machine-readable WITHOUT a
# convention: §4.2's FOUR-DEDUP AUDIT is a table whose first cell is a mechanism
# id. So that table is compared STRUCTURALLY here, and a fifth audited dedup
# arrives as a red gate instead of as prose nobody counted. This is why the
# registry grew from eight rows to thirteen: the audit declared FOUR dedups and
# the registry carried one of them.

V4_ARCH_DOC = ARCH_DOCS / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The audited dedups -> the registry row that owns each. Two ids can share ONE
#: row, and that is not a bookkeeping shortcut: `D-b` and `D-c` are one mechanism
#: at two keys, which is how §4.2 itself lists them (`S2.2a-α EXACT DEDUP …
#: within-batch + against-store`). A mapping rather than a set, so the sharing is
#: declared rather than inferred from a count.
AUDITED_DEDUP_ROWS = {
    "D-a": "source_dedup",
    "D-b": "exact_dedup",
    "D-c": "exact_dedup",
    "D-d": "against_priors_consolidation",
}

#: A table ROW whose first cell is a bold dedup id. Anchored on the ROW START so a
#: `D-a` mentioned inside another row's prose is not a declaration, and
#: `MULTILINE` so the table can move down the document without the check losing
#: it. ⚠️ NOT anchored on the closing pipe: `| **D-e** (a note) |` is the same
#: declaration in a different cell shape, and a pattern that required the pipe
#: immediately after the id read that row as absent — measured: a fifth dedup in
#: that shape left this check GREEN, which is the one drift it exists to see.
#: ⚠️ And the id class is NOT `[a-z]`: `D-E`, `D-e2` and `D-5` are the same
#: declaration in another spelling, and a one-lowercase-letter class read them as
#: absent too — measured on the real table: appending `| **D-E** | … |` left the
#: drift check GREEN. The two readers share ONE id class so they cannot disagree
#: about what counts as an id.
_DEDUP_ID = r"D-[A-Za-z0-9_]+"
AUDITED_DEDUP_ROW = re.compile(r"^\|\s*\*\*(" + _DEDUP_ID + r")\*\*", re.MULTILINE)

#: Any id that LOOKS like a table-row declaration of a dedup, whatever the cell
#: shape (`**D-a**`, `D-a`, `**D-a** (note)`). Used only to prove the reader above
#: did not silently skip a row: an id this loose pattern finds and the strict one
#: does not is an UNREADABLE ROW, which is a violation rather than a gap.
#: ⚠️ NOT anchored on a leading pipe either: an INDENTED row
#: (`  | **D-e** | …`) and a row whose first cell lost its leading pipe
#: (`**D-e** | … | ✅ |`) are the same table row, and a `^\|` anchor in BOTH
#: readers made the cross-check share the strict reader's blind spot — measured:
#: an indented fifth dedup produced zero errors. A line that contains a pipe AND a
#: dedup id is a table row for this purpose, whatever its indentation.
AUDITED_DEDUP_ROW_LOOSE = re.compile(
    # A line that contains a pipe ANYWHERE is table-ish; the id may sit on either
    # side of it, which is why this is a lookahead and not two ordered groups.
    r"^(?=[^\n]*\|)[^\n]*?\b(" + _DEDUP_ID + r")\b", re.MULTILINE
)


def audited_dedups(doc: Path = V4_ARCH_DOC) -> list[str]:
    """The dedup ids §4.2's FOUR-DEDUP AUDIT declares, in document order.

    The table is the one place the design enumerates dedups by ID, so it is the
    one mechanism enumeration a prose document can be compared against without a
    marker convention. Reading it is the whole point: a structure that is READ is
    a declaration a check can hold; a structure that is only written down is the
    defect #5064 exists for.
    """
    return AUDITED_DEDUP_ROW.findall(doc.read_text(encoding="utf-8"))


def audited_dedup_errors(doc: Path, registry_ids: set[str],
                         mapping: dict[str, str] | None = None) -> list[str]:
    """Every violation between the design's audited-dedup table and the registry.

    FAIL CLOSED in the two directions a hand-maintained list gets wrong: a dedup
    the design DECLARES whose id maps to no row (the shape this whole issue is
    about — a declared mechanism with no verdict), and a mapping entry for a
    dedup the design no longer declares (a stale mapping reads as coverage for a
    withdrawn mechanism). An unreadable table — zero ids — is a THIRD violation,
    never an empty pass: a shape change must not silently satisfy the check.
    """
    mapping = AUDITED_DEDUP_ROWS if mapping is None else mapping
    declared = audited_dedups(doc)
    if not declared:
        return [
            f"{doc.name}: the FOUR-DEDUP AUDIT table yielded NO ids — its shape "
            "changed, so the design's dedup enumeration can no longer be "
            "compared with the registry. Restore the `| **D-x** | …` rows or "
            "update `AUDITED_DEDUP_ROW` with the shape it became (fail closed, "
            "never a silent pass)"
        ]
    errors: list[str] = []
    # A PARTIALLY readable table is the dangerous one: `not declared` only fires
    # when the reader finds NOTHING, so one unreadable row among four would read
    # as a complete enumeration. The loose pattern is the second pair of eyes.
    loose = set(AUDITED_DEDUP_ROW_LOOSE.findall(doc.read_text(encoding="utf-8")))
    for did in sorted(loose - set(declared)):
        errors.append(
            f"{did}: appears as a dedup TABLE ROW but the row reader skipped it "
            "— its cell shape is not the one `AUDITED_DEDUP_ROW` expects, so a "
            "declared dedup reads as absent. Normalise the row (or teach the "
            "reader the shape); this is a violation, not a gap, because a "
            "partially read table cannot prove it is complete"
        )
    for did in declared:
        mid = mapping.get(did)
        if mid is None:
            errors.append(
                f"{did}: declared by the design's audited-dedup table and mapped "
                "to NO registry mechanism — add a `config/v4-mechanisms.yml` row "
                f"for it and an `AUDITED_DEDUP_ROWS` entry (`{did}` -> <row id>)"
            )
        elif mid not in registry_ids:
            errors.append(
                f"{did}: mapped to `{mid}`, which is not a DECLARED mechanism — "
                "a dedup cannot be covered by a row that does not exist"
            )
    for did in sorted(set(mapping) - set(declared)):
        errors.append(
            f"{did}: `AUDITED_DEDUP_ROWS` names a dedup the design no longer "
            "declares — a stale mapping reads as coverage for a withdrawn "
            "mechanism; withdraw the entry (or restore the declaration)"
        )
    return errors


def test_the_four_audited_dedups_each_have_a_row() -> None:
    """The design's dedup ENUMERATION and the registry agree, both ways.

    This is the drift detector. Before it, adding a fifth dedup to §4.2's table
    was invisible: `DECLARED_IDS` would not change, no marker had to move, and
    the registry would keep claiming the declared set was complete. The ids are
    pinned in ORDER as well as membership, because a reordered or renamed row is
    the same silent edit.
    """
    rows = {r["id"] for r in load_registry()["mechanisms"]}
    assert audited_dedups() == ["D-a", "D-b", "D-c", "D-d"], (
        "§4.2's four-dedup audit no longer declares exactly D-a..D-d — read the "
        f"table, got {audited_dedups()!r}, and update AUDITED_DEDUP_ROWS with it"
    )
    errors = audited_dedup_errors(V4_ARCH_DOC, rows)
    assert errors == [], (
        "the design's audited dedups and the registry disagree:\n  "
        + "\n  ".join(errors)
    )


def test_a_fifth_audited_dedup_with_no_row_is_refused(tmp_path) -> None:
    """DRIFT, PROVEN: add a mechanism to the design's enumeration and the check reds.

    The real table has exactly four rows, so a check that merely counted them
    would be vacuous today and could stay green tomorrow. This fixture is the
    fake fifth declaration: `D-e` is declared by the design and mapped to no
    row, and the check must name it rather than shrug.
    """
    doc = tmp_path / "arch.md"
    doc.write_text(
        "| id | key | where | exists? |\n|---|---|---|---|\n"
        "| **D-a** | source | URL only | no |\n"
        "| **D-b** | within-batch exact | `:1` | ✅ |\n"
        "| **D-c** | vs store | `:2` | ✅ |\n"
        "| **D-d** | semantic | `:3` | ✅ |\n"
        "| **D-e** | a NEW key nobody registered | `:4` | ✅ |\n"
        # The same declaration in another ID SPELLING: an id class of one
        # lowercase letter read `D-E` as absent (measured on the real table), so
        # the fixture keeps a spelling the old class could not see.
        "| **D-E** | a NEW key in the other spelling | `:5` | ✅ |\n",
        encoding="utf-8",
    )
    rows = {r["id"] for r in load_registry()["mechanisms"]}
    errors = audited_dedup_errors(doc, rows)
    for did in ("D-e", "D-E"):
        assert any(did in e and "NO registry mechanism" in e for e in errors), (
            f"{did}: a declared dedup with no registry row was NOT refused — this "
            f"is the declared-implies-present shape the gate exists for: {errors}"
        )


def test_a_mapping_entry_the_design_no_longer_declares_is_refused(tmp_path) -> None:
    """The other drift direction: a withdrawn declaration cannot keep its row."""
    doc = tmp_path / "arch.md"
    doc.write_text(
        "| **D-a** | source |\n| **D-b** | within-batch |\n| **D-c** | store |\n",
        encoding="utf-8",
    )
    rows = {r["id"] for r in load_registry()["mechanisms"]}
    errors = audited_dedup_errors(doc, rows)
    assert any("D-d" in e and "no longer" in e for e in errors), (
        "a mapping entry for a dedup the design dropped stayed green: "
        f"{errors}"
    )


def test_an_audited_dedup_mapped_to_an_undeclared_row_is_refused(tmp_path) -> None:
    """A mapping is not evidence: the row it names has to be a declared mechanism."""
    doc = tmp_path / "arch.md"
    doc.write_text(
        "| **D-a** | source |\n| **D-b** | within-batch |\n"
        "| **D-c** | store |\n| **D-d** | semantic |\n",
        encoding="utf-8",
    )
    errors = audited_dedup_errors(
        doc,
        {"source_dedup"},
        mapping={"D-a": "source_dedup", "D-b": "a_row_that_does_not_exist",
                 "D-c": "a_row_that_does_not_exist",
                 "D-d": "a_row_that_does_not_exist"},
    )
    assert any("D-b" in e and "not a DECLARED mechanism" in e for e in errors), (
        f"a mapping to an undeclared row passed: {errors}"
    )


def test_an_unreadable_dedup_table_is_refused(tmp_path) -> None:
    """A shape change is a violation, never an empty pass (the fail-closed rule)."""
    doc = tmp_path / "arch.md"
    doc.write_text("| id | key |\n|---|---|\n| **dedup-a** | source |\n",
                   encoding="utf-8")
    errors = audited_dedup_errors(doc, {r["id"] for r in load_registry()["mechanisms"]})
    assert errors and "yielded NO ids" in errors[0], errors


def test_a_dedup_row_in_a_shape_the_reader_cannot_parse_is_refused(tmp_path) -> None:
    """A PARTIALLY readable table is the dangerous one, so an unparsed row is a violation.

    The fail-closed branch above only fires when the reader finds NO ids at all.
    One row in a cell shape the reader does not expect, beside four it does, would
    leave `declared` looking like a complete enumeration — measured on the real
    table: `| **D-e** (a note) | … |` was skipped by a pattern that required the
    closing pipe immediately after the id, and the fifth dedup went GREEN. So the
    reader is cross-checked against a loose pattern that only asks "does this row
    look like a dedup declaration?", and any id it finds that the strict reader
    skipped is an UNREADABLE ROW — a violation, because a table nobody fully read
    cannot prove it is complete.
    """
    doc = tmp_path / "arch.md"
    doc.write_text(
        "| **D-a** | source |\n| **D-b** | within-batch |\n"
        "| **D-c** | store |\n| **D-d** | semantic |\n"
        "| D-e (unbolded id) | a NEW key |\n",
        encoding="utf-8",
    )
    errors = audited_dedup_errors(doc, {r["id"] for r in load_registry()["mechanisms"]})
    assert any("D-e" in e and "skipped it" in e for e in errors), (
        "a dedup row in a shape the reader cannot parse passed as absent: "
        f"{errors}"
    )


def test_the_near_dup_three_sites_are_separately_registered() -> None:
    """§4.2's three-site split is a REGISTRY fact, not only a paragraph.

    DETECT (the mechanical guard) · VERDICT (`vet_gate.OUTCOMES`' `MERGE`, S2.2b)
    · FUSION (S3). The design's claim is *"no operation has two homes"*; a
    registry that carried only the fusion made the first two sites look like part
    of it. Each site is pinned to the thing that would have to change for the
    split to be lost:

      * DETECT -> the `near_dup_detect` row anchors `_boundary` and owns its own
        file, so folding it into the fusion row cannot pass silently;
      * VERDICT -> `MERGE` is a member of `vet_gate.OUTCOMES` AND the `vet` row
        anchors that module (an earlier revision of this test asserted only a
        tautology here — the observed value was already asserted one line up, so
        the line could never fail and nothing read the verdict site at all);
      * FUSION -> the `near_dup_merge` row anchors the fusion's own symbols.
    """
    from tortoise import vet_gate

    rows = {r["id"]: r for r in load_registry()["mechanisms"]}
    assert "_boundary" in rows["near_dup_detect"]["symbols"], rows["near_dup_detect"]
    assert "tortoise/extractor_v2.py" in rows["near_dup_detect"]["code"], (
        "the DETECT row no longer owns the file its symbol lives in"
    )
    assert "near_dup_merge" in rows["near_dup_merge"]["symbols"], rows["near_dup_merge"]
    assert rows["near_dup_detect"]["code"] != rows["near_dup_merge"]["code"], (
        "the DETECT and FUSION rows now share every declared file — the design's "
        "one-site-per-operation split is no longer legible in the registry"
    )
    assert "MERGE" in vet_gate.OUTCOMES, (
        "the VET arbiter no longer emits MERGE, so the fusion's only authorising "
        "verdict is gone while `near_dup_merge` stays `implemented`"
    )
    assert "tortoise/vet_gate.py" in rows["vet"]["code"], (
        "the VERDICT site is not in the `vet` row's declared code, so the middle "
        "site of the three has no registry home"
    )
    # `exact_dedup`'s own `code:` list is NOT asserted here: it is a fourth
    # mechanism, so a change adding the file its second leg lives in would red a
    # test named for these three sites, with no message pointing at the claim that
    # actually broke.


def test_the_failure_policy_absence_is_pinned_to_the_mint_not_the_render() -> None:
    """The `failure_policy` row's absence claim is a CALL, not a grep of its note.

    §4.4 declares the policy and said the code implemented the INVERTED one.
    `#5095` landed the **record** half — the renderer stopped inviting `new`, the
    caller-visible `link_before_create` stopped claiming *"no match"* for a
    lookup that never ran, and the run reports an `identity` block — while **I1's
    enforcement is still absent**: nothing suppresses a create, so duplicates are
    still minted.

    ⇒ The probe MOVED, and it had to: a render-only probe can no longer see the
    remaining absence, because the render is fixed. What this pins now is the
    pair that makes the row honest in BOTH directions —

    * the two CALLS that prove the record half exists (so a revert of either goes
      red here, not only in the behaviour suite); and
    * the ROW still saying `not-implemented`, because the mint gate and §4.4's
      carry-forward store genuinely do not exist. Flipping the row without
      building them would publish the exact "a declared mechanism reads like a
      shipped one" this registry exists to stop — and only a state assertion can
      see that, because the probes above stay green.
    """
    from tortoise import extractor_v2 as v2

    rendered = v2._render_search_results({"degraded": True, "reason": "S3 degraded"})
    assert "assume everything is new" not in rendered, (
        "the degraded S3 render invites the model to treat every candidate as "
        "new again — that is the defect #5095 removed (§4.4 I1), and the doc's "
        f"state-of-code paragraph must follow it (rendered: {rendered!r})"
    )
    assert "UNRESOLVED" in rendered, (
        "the degraded render no longer says the identity is UNRESOLVED, so the "
        "assertion above is not reading the state-of-code clause"
    )
    # The second call: the RECORD a caller keeps — driven through the REAL write
    # path, not the helper in isolation. A pin that only called
    # `fp.created_record(...)` stayed green when the three create sites in
    # `execute_embed` were reverted to their inline `found: False` dict, so it
    # could not see the wiring it claimed to pin (#5095 review A3).
    written = v2.execute_embed(
        {"entities": [], "events": [], "operators": [], "chain_notes": [],
         "points": [{"content": "a claim", "pointKind": "statement"}]},
        {}, session_id="s1")
    records = [r for r in written["link_before_create"]
               if str(r.get("searched_for", "")).startswith("point ")]
    assert records, "the write path emitted no record for the created Point"
    assert all(r["found"] is None for r in records), (
        "the created-candidate record asserts `found: false` for a lookup that "
        f"never ran — the caller keeps that claim (#5095): {records!r}"
    )
    # …and the row. NOT `implemented`: I1's "never mint it" needs a create gate
    # inside S5, and §4.4's carry-forward store does not exist either.
    row = {r["id"]: r for r in load_registry()["mechanisms"]}["failure_policy"]
    assert row["state"] == NOT_IMPLEMENTED, (
        "`failure_policy` was flipped to `implemented`, but nothing suppresses a "
        "create and no carry-forward store exists — see the row's note and "
        "#5095's remaining-slices list"
    )
    assert row.get("tracking_issue") == 5087, row


# ── THE DDL SCAN, as a function so it can be TESTED ─────────────────────────
#
# The pin below is only as good as the scan's reach, and reach is exactly what
# four review cycles found missing (physical lines -> string constants ->
# runtime-assembled forms). A branch no test reaches is a guard that cannot be
# SEEN to fail, so the scan is a plain function over a ROOT and each of its
# polarities has a fixture below: a literal statement, every assembly form, a
# docstring (never executed), and a mention in ordinary text.

#: `CONSTRAINT CREATE` (the graph form) or `CREATE CONSTRAINT`, up to the end of
#: the statement-ish span.
DDL = re.compile(
    r"(?i)(?:CONSTRAINT\s+CREATE|CREATE\s+(?:OR\s+REPLACE\s+)?CONSTRAINT)[^;]{0,300}"
)

#: A constraint statement that names its label is COMPLETE — reassembling it adds
#: nothing, so `_DDL = "… NODE GraphEvent"` + `g.query(_DDL)` is a statement this
#: scan fully saw and must not be called unverifiable.
DDL_LABELLED = re.compile(r"(?i)\bNODE\s+[A-Za-z_]\w*")

#: The `CODE:` docstring exclusion + the taint rule: a name bound to an
#: INCOMPLETE keyword-carrying literal (`… UNIQUE NODE ` with no label yet).
_DOC_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_ASSEMBLY_ATTRS = {"format", "format_map", "join"}


def _binding_keys(target: ast.expr) -> list[str]:
    """Every name a binding TARGET writes, in one spelling a use can be keyed by.

    The shapes the repo actually binds a DDL fragment with: a plain name
    (`_DDL = …`), an ANNOTATED name (`_DDL: str = …`), an attribute or subscript
    (`self._ddl = …`), and a tuple/list of any of those
    (`_A, _B = "… NODE ", "Point"`). The spelling is shared with
    :func:`_use_key`, so a binding and a use cannot disagree about WHICH name
    they mean — an earlier name-only allow-list (`isinstance(target, ast.Name)`)
    keyed neither `AnnAssign` nor an attribute target, so
    `_D: str = "… UNIQUE NODE "` + `g.query(_D + _L)` passed the pin with the
    constraint shipping (measured; the fixtures below cover each shape).

    An empty list means the target is a shape this scan does not resolve; the
    caller treats that as UNVERIFIABLE, never as clean.
    """
    if isinstance(target, ast.Name):
        return [f"name:{target.id}"]
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        return [f"expr:{ast.unparse(target)}"]
    if isinstance(target, ast.Starred):
        return _binding_keys(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        keys: list[str] = []
        for elt in target.elts:
            keys.extend(_binding_keys(elt))
        return keys
    return []


def _class_body_keys(tree: ast.AST) -> dict[int, list[str]]:
    """CLASS-QUALIFIED keys for bindings made in a class body, at any depth.

    A class-body binding and its use key differently — `stmt = …` is `name:stmt`,
    while every use is `Ddl.stmt`, i.e. `expr:Ddl.stmt` — so a class-level DDL
    fragment shipped unrefused (measured). The transform is mechanical, keyed on
    the LEADING component of the binding key, so it covers every shape
    `_binding_keys` can emit rather than the one that was reported:

      `name:stmt`           -> `expr:Ddl.stmt`
      `expr:parts['k']`     -> `expr:Ddl.parts['k']`
      a tuple target        -> one entry per leaf (`Ddl.stmt_a`, …)
      a NESTED class        -> `expr:Outer.Inner.stmt`

    Keyed by `id(target)` so the caller widens exactly the binding whose value is
    incomplete: widening every class-body binding regardless of value tainted enum
    members by the dozen (`OpenOutcome.DEDUP`), and a bare final name collided with
    any attribute of that name (`intent.scope` vs a class-body `scope`) — both
    measured before the key was made class-qualified.
    """
    keys: dict[int, list[str]] = {}
    # Qualified names from a parent map, so a NESTED class is `Outer.Inner` and not
    # `Inner` (measured: the inner name alone never matches `Outer.Inner.stmt`).
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node

    def qualified(node: ast.ClassDef) -> str:
        names = [node.name]
        up = parent.get(id(node))
        while up is not None:
            if isinstance(up, ast.ClassDef):
                names.append(up.name)
            up = parent.get(id(up))
        return ".".join(reversed(names))

    #: A compound statement INSIDE a class body binds just as much as a plain
    #: assignment (`class Ddl: for stmt in ["… UNIQUE NODE "]: …`), and reading only
    #: `cls.body`'s direct statements missed it — measured: that binding shipped with
    #: `assembled == []` while the identical binding at module level was refused.
    #: So the class body is descended through the compound forms; a nested CLASS is
    #: not descended (it gets its own qualified name) and neither is a method body
    #: (its `self.x` bindings and uses already share one spelling).
    _SCOPE = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

    def body_statements(nodes: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for node in nodes:
            if isinstance(node, _SCOPE):
                continue
            out.append(node)
            for name in ("body", "orelse", "finalbody"):
                inner = getattr(node, name, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    out.extend(body_statements(inner))
            for handler in getattr(node, "handlers", []) or []:
                out.extend(body_statements(handler.body))
            for case in getattr(node, "cases", []) or []:
                out.extend(body_statements(case.body))
        return out

    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        qual = qualified(cls)
        for stmt in body_statements(cls.body):
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets = list(stmt.targets)
            elif isinstance(stmt, ast.AnnAssign):
                targets = [stmt.target]
            # A `for` target and a walrus inside the class body bind too, and they
            # are not Assign/AnnAssign nodes: a class body that guards its binding
            # with a loop or a condition is the shape that shipped unrefused after
            # the first class-body fix (measured).
            for inner in ast.walk(stmt):
                if isinstance(inner, (ast.For, ast.AsyncFor, ast.NamedExpr)):
                    targets.append(inner.target)
            for target in targets:
                # EVERY node of the target shape gets a class-qualified key, so a
                # tuple target's leaves (`stmt_a`, `stmt_b`) and a subscript target
                # (`parts["k"]`) are covered without naming each shape here.
                for elt in ast.walk(target):
                    if not isinstance(
                        elt, (ast.Name, ast.Attribute, ast.Subscript, ast.Tuple, ast.List)
                    ):
                        continue
                    for key in _binding_keys(elt):
                        keys.setdefault(id(elt), []).append(
                            f"expr:{qual}.{key.split(':', 1)[1]}"
                        )
    return keys


def _use_key(node: ast.expr) -> str | None:
    """The same key for a USE of a bound name; `None` for anything else.

    A `Name` keys on its identifier; an attribute or subscript keys on its SOURCE
    TEXT. A class-body binding is the case those two disagree on: the binding is
    `stmt = …` (keyed `name:stmt`) but every use is `Ddl.stmt` (keyed
    `expr:Ddl.stmt`), so a class-level DDL fragment shipped unrefused (measured).
    :func:`_class_body_keys` therefore registers such a binding under its final
    name too, and an attribute use is matched against that.
    """
    if isinstance(node, ast.Name):
        return f"name:{node.id}"
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return f"expr:{ast.unparse(node)}"
    return None


#: "This expression STARTS a constraint statement", used to recognise a statement
#: assembled from parts — `"GRAPH.CONSTRAINT " + "CREATE x UNIQUE NODE Point"`
#: holds no complete statement in either operand, so the full-statement regex
#: cannot see it. Deliberately NOT the bare word: `_CYPHER_RESERVED` (a list of
#: reserved words) and a regex like `ADD CONSTRAINT … CHECK` both contain it, and
#: an inclusion test on the word alone tainted them (measured: two false positives
#: on the real tree). A CREATE-side spelling is what a constraint STATEMENT opens
#: with; `ADD CONSTRAINT` is a column check, not a graph constraint.
DDL_FRAGMENT = re.compile(
    r"(?i)(?:GRAPH\s*\.\s*CONSTRAINT|CONSTRAINT\s+CREATE"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?CONSTRAINT)"
)


#: A RUNAWAY guard on the assembled-text join's name-following, not a reach limit:
#: a fragment split at the KEYWORD boundary can be carried by names
#: (`_A = "CREATE "; _B = _A; … g.query(_D + "CONSTRAINT …")`), and a chain of
#: four used to be dropped SILENTLY — the text that hop carried vanished, no
#: statement was found, and the pin read the constraint as ABSENT while it shipped
#: (measured). So a chain is followed as far as the bindings go (a repeated key on
#: one path is the cycle test), and this cap only stops a pathological file:
#: reaching it is REPORTED as unverifiable, never read as an absence. Set above any
#: chain the repo carries (measured: the deepest real chain resolves inside it, so
#: the real tree reports nothing).
_MAX_RESOLVE_DEPTH = 24


#: A constraint WORD anywhere in a fragment — the GATE on the cannot-follow report.
#: The bare word is deliberately NOT a taint rule (`_CYPHER_RESERVED` and every
#: ordinary Cypher `CREATE` would red — measured, and why `DDL_FRAGMENT` names the
#: PHRASE); here it only decides whether an UNRESOLVED hop is a constraint
#: question at all, after every literal check has already come up empty.
DDL_KEYWORD = re.compile(r"(?i)(?:\bCONSTRAINT\b|GRAPH\s*\.\s*CONSTRAINT)")


def _carries_ddl_keyword(
    node: ast.expr, bindings: dict[str, ast.expr], seen: frozenset[str] = frozenset()
) -> bool:
    """Is a constraint WORD reachable from `node`, through names, cycle-safely?

    The gate on reporting a hop the join cannot follow. Reporting EVERY refused hop
    unconditionally was measured on the real tree and rejected: a self-referential
    binding (`url = url + "/x"`) is unresolvable in 73 unrelated assemblies, so the
    pin reds on string building that has nothing to do with DDL — over-blocking,
    not soundness. Conversely silence on a refused hop that DOES carry the word is
    the fail-open the join exists to close, so the word is looked for through the
    whole chain the join could not walk.

    `seen` is the same cycle test :func:`_assembled_text` applies: a key may not
    repeat along one path, which is the only thing that can actually loop.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if DDL_KEYWORD.search(child.value):
                return True
            continue
        if not isinstance(child, (ast.Name, ast.Attribute, ast.Subscript)):
            continue
        key = _use_key(child) or ""
        if key in seen or key not in bindings:
            continue
        if _carries_ddl_keyword(bindings[key], bindings, seen | {key}):
            return True
    return False


def _ddl_fragments(node: ast.expr) -> list[str]:
    """Every string literal inside `node`, however deeply nested.

    A fragment can be carried by a COLLECTION as easily as by a `+`: measured,
    `PARTS = ["CREATE CONSTRAINT pt UNIQUE NODE ", "Point"]; "".join(PARTS)`
    was neither `found` (no literal holds the whole statement) nor `assembled`
    (the `.join` branch only read constants inside the CALL), so an assembled
    `Point` constraint read as ABSENT. Walking the whole subtree closes every
    carrier shape at once — collections, dict values, nested calls.
    """
    return [
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _assembled_text(
    node: ast.expr,
    bindings: dict[str, ast.expr],
    depth: int = 0,
    truncated: list[str] | None = None,
    seen: frozenset[str] = frozenset(),
) -> str:
    """The literal text `node` carries, **in SOURCE order**, following bound names.

    `_ddl_fragments` answers "which literals are in here"; this answers "what
    will the runtime send", and the difference is the whole fail-open #9884's
    review found. A statement can be split at the KEYWORD boundary —
    `"CREATE " + "CONSTRAINT pt UNIQUE NODE Point"` — where neither operand
    contains a spelling `DDL` or `DDL_FRAGMENT` recognises, so the fragment loop
    below reports nothing and the pin reads the constraint as ABSENT while it
    ships. Joining the operands is what makes the statement visible again.

    Two properties the fragments list cannot supply:

    * **ORDER.** `ast.walk` is breadth-first, so `("CREATE " + "CONSTRAINT ") + "x"`
      yields `["x", "CREATE ", "CONSTRAINT "]` — joined in that order it matches
      nothing. The literals are ordered by (line, column), i.e. the order Python
      concatenates them; the parser merges implicit adjacency into one Constant,
      which keeps the whole span, so that case needs no handling here.
    * **NAMES.** `_D = "CREATE "; _C = "CONSTRAINT …"; g.query(_D + _C)` carries no
      literal inside the `+` at all. `bindings` maps a binding key (the same
      spelling :func:`_use_key` produces) to the bound expression, so a name is
      replaced by the text it was bound to — one constant-folding pass, following
      a name through as many bindings as it takes.

    An AMBIGUOUS key (bound twice to different values) is absent from `bindings`
    by construction, so a rebinding is never resolved to whichever value happened
    to win — it simply contributes no text, and the fragment loop and the taint
    rule remain the guard for that case.

    A hop the join REFUSES to follow is NOT the same case as one it cannot resolve,
    and both used to `continue` — so the text that hop carried vanished, the join
    returned a shorter string, no statement was found, and the pin read the
    constraint as ABSENT while it shipped (`_A = "CREATE "; _B = _A; _C = _B;
    _D = _C; g.query(_D + "CONSTRAINT …")`, measured). Two things close that, and
    the SECOND is what makes it safe:

    * a chain is followed as far as the bindings go, not three hops, so the
      measured shape resolves and the seam is FOUND (the strongest answer: the
      statement is reported, not merely doubted);
    * what still cannot be followed — a name repeating along the path (a cycle, the
      only shape that can loop) or a chain past the runaway cap — is recorded in
      `truncated` and the caller reports it as unverifiable. Reporting every
      refused hop unconditionally was measured on the real tree and rejected: it
      reds 240 unrelated assemblies (`url`, `parts`, f-string logging), which is
      over-blocking, not soundness — the caller therefore gates it on a reachable
      constraint word.
    """
    entries: list[tuple[tuple[int, int, int], str]] = []
    nodes = list(ast.walk(node))
    covered: set[int] = set()
    for child in nodes:  # outer-first (BFS), so a resolved name covers its own parts
        if id(child) in covered:
            continue
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            entries.append(((child.lineno, child.col_offset, 0), child.value))
            continue
        if not isinstance(child, (ast.Name, ast.Attribute, ast.Subscript)):
            continue
        key = _use_key(child) or ""
        bound = bindings.get(key)
        if bound is None:
            continue
        if key in seen or depth >= _MAX_RESOLVE_DEPTH:
            # This IS a bound name and the join will not follow it — a CYCLE along
            # this path, or the runaway cap — so the text it carries is unknown
            # here. Recorded, never silently dropped.
            if truncated is not None:
                truncated.append(key)
            continue
        inner = _assembled_text(bound, bindings, depth + 1, truncated, seen | {key})
        if not inner:
            continue
        entries.append(((child.lineno, child.col_offset, 0), inner))
        for part in ast.walk(child):
            covered.add(id(part))
    entries.sort(key=lambda item: item[0])
    return "".join(text for _, text in entries)


def _incomplete_ddl(value: ast.expr | None) -> bool:
    """Does `value` carry a constraint fragment whose label is NOT resolved here?

    A single literal is the common case. A COLLECTION of fragments is the same
    thing spread over elements (`PARTS = ["… UNIQUE NODE ", "Point"]`), and a
    fragment may hold only the WORD when the statement is split across operands —
    so anything that carries a `CONSTRAINT`-bearing fragment and does not carry a
    COMPLETE labelled statement is incomplete, and a use of it is unverifiable
    rather than absent.
    """
    if value is None:
        return False
    fragments = _ddl_fragments(value)
    if not any(DDL_FRAGMENT.search(f) for f in fragments):
        return False
    # ANY incomplete fragment makes the whole carrier incomplete. The earlier
    # reading ("taint unless some element is a complete statement") left a MIXED
    # collection — `[<the complete GraphEvent DDL>, "… UNIQUE NODE "]` — clean, so
    # `_TEMPLATES[1] + "Point …"` shipped through it (measured). One resolvable
    # element says nothing about the others.
    return any(
        not (DDL.search(f) and DDL_LABELLED.search(DDL.search(f).group(0)))
        for f in fragments
        if DDL_FRAGMENT.search(f)
    )


def _bound_pairs(tree: ast.AST) -> list[tuple[ast.expr, ast.expr | None, bool]]:
    """`(target, value, augments)` for every binding form, tuple targets PAIRED UP.

    `_A, _B = "… UNIQUE NODE ", "Point"` binds two values, and the half that
    carries the keyword is `_A`; pairing the whole tuple target with the whole
    tuple value would find no `ast.Constant` and taint nothing. So a `Tuple`/
    `List` target whose value is a `Tuple`/`List` of the same LENGTH is paired
    element-wise; anything else is paired whole.

    `augments` is True for an `ast.AugAssign`, and it is NOT decoration: `k += rhs`
    does not REBIND `k`, it ACCUMULATES onto whatever `k` already held, so the
    binding after it is `what k held` JOINED with `rhs` — never `rhs` alone. A
    caller that takes the pair's value as the binding loses the half the statement
    was split at (measured: `_A = "CREATE "` + `_A += "CONSTRAINT pt UNIQUE NODE
    Point"` carried no spelling either single-literal pattern sees, and the
    constraint read as ABSENT — fixture `keyword_split_augassign`).
    """
    pairs: list[tuple[ast.expr, ast.expr | None, bool]] = []
    for node in ast.walk(tree):
        augments = False
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
            augments = isinstance(node, ast.AugAssign)
        elif isinstance(node, ast.NamedExpr):
            # `if (d := "… UNIQUE NODE "):` — a walrus IS a binding.
            targets = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # `for d in ["… UNIQUE NODE "]:` — the loop target is bound to the
            # iterable's ELEMENTS, so the iterable is the value to inspect.
            targets = [node.target]
            pairs.append((node.target, node.iter, False))
            continue
        else:
            continue
        value = node.value
        for target in targets:
            if (
                isinstance(target, (ast.Tuple, ast.List))
                and isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
            ):
                pairs.extend(
                    (elt_target, elt_value, augments)
                    for elt_target, elt_value in zip(
                        target.elts, value.elts, strict=True
                    )
                )
            else:
                pairs.append((target, value, augments))
    return pairs


def _accumulated(prior: ast.expr, value: ast.expr, node: ast.expr) -> ast.BinOp:
    """`prior + value` as an AST join, so an accumulation resolves like a `+`.

    Positioned at `node` so :func:`_assembled_text`'s SOURCE ordering keeps the
    pre-augment text ahead of the RHS — the join is a synthetic node and has no
    position of its own, and an unpositioned root would sort as line 0.
    """
    joined = ast.BinOp(left=prior, op=ast.Add(), right=value)
    joined.lineno = getattr(node, "lineno", 1)
    joined.col_offset = getattr(node, "col_offset", 0)
    return joined


def ddl_scan(root: Path) -> tuple[list[str], list[str], list[str]]:
    """Every constraint-ish string, every unparsable source, every ASSEMBLED one.

    Returns `(found, unparsable, assembled)`. `found` is "a constraint, OR a
    mention of one" — the scan cannot tell those apart and does not pretend to.
    `assembled` is the cannot-tell polarity: a statement whose label the scan
    cannot resolve, reported so it can never be read as an absence.

    `unparsable` is the OTHER cannot-tell polarity: a source this scan could not
    turn into a tree at all. Both are violations at the call site — an unreadable
    file and a runtime-assembled statement are equally not an absence.
    """
    found: list[str] = []
    unparsable: list[str] = []
    assembled: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            # The SAME encoding rules the interpreter applies (PEP 263 cookie, UTF-8
            # BOM) — `_defined_names` reads its sources this way for the measured
            # reason recorded there: a plain `read_text(encoding="utf-8")` refuses a
            # legal `# -*- coding: latin-1 -*-` module, so a file Python imports
            # happily would be reported as unreadable and the pin would red with a
            # message claiming a constraint in it is INVISIBLE.
            with tokenize.open(path) as fh:
                tree = ast.parse(fh.read(), filename=rel)
        except (OSError, UnicodeError, SyntaxError, ValueError):
            unparsable.append(rel)
            continue
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, _DOC_NODES)
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        #: Keys of names bound to an INCOMPLETE statement: whether the label is
        #: supplied at the use site cannot be decided here, so a USE of one is
        #: `assembled` and never an absence.
        tainted: set[str] = set()
        class_keys = _class_body_keys(tree)
        #: Binding key -> bound expression, for the assembled-text join. A key bound
        #: TWICE to different values is dropped: which one wins is decided at
        #: runtime, so resolving to either would be a guess. `_bound_pairs` is the
        #: same enumeration the taint rule walks, and the keys are the SAME widened
        #: set (`_binding_keys` plus the class-qualified spellings), so the two
        #: cannot disagree about which bindings exist OR about how they are spelled
        #: — they used to: a class-body `kw = "CREATE "` was taint-keyed
        #: `expr:Ddl.kw` and join-keyed only as `name:kw`, so the use site found no
        #: binding, the join saw only the other operand, and a keyword-split
        #: `Ddl.kw + "CONSTRAINT …"` read as ABSENT (measured).
        bindings: dict[str, ast.expr] = {}
        rebound: set[str] = set()
        pairs = _bound_pairs(tree)
        for target, value, augments in pairs:
            if value is None:
                continue
            for key in (*_binding_keys(target), *class_keys.get(id(target), ())):
                if augments:
                    # `k += rhs` — the accumulated expression is what a use site
                    # resolves to, so the pre-augment value is JOINED with the RHS
                    # instead of the key being dropped as a rebinding. An earlier
                    # value this scan cannot resolve (`k` bound twice, or never
                    # bound here) leaves the key unresolvable rather than
                    # resolving to the RHS, which is only HALF the statement.
                    prior = None if key in rebound else bindings.get(key)
                    if prior is not None:
                        bindings[key] = _accumulated(prior, value, target)
                    continue
                if key in bindings:
                    if ast.dump(bindings[key]) != ast.dump(value):
                        rebound.add(key)
                else:
                    bindings[key] = value
        for key in rebound:
            bindings.pop(key, None)
        for target, value, _augments in pairs:
            if not _incomplete_ddl(value):
                continue
            keys = _binding_keys(target)
            if keys:
                tainted.update(keys)
                # A class-body binding is USED as `Cls.name` — a different key from
                # the `name:` spelling it binds under (`_class_body_keys`).
                tainted.update(class_keys.get(id(target), ()))
            else:  # a binding shape this scan cannot key is NOT clean
                assembled.append(
                    f"{rel}: `{ast.unparse(target)}` is bound to an incomplete "
                    "DDL statement in a shape this scan does not resolve"
                )

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                for match in DDL.finditer(node.value):
                    found.append(f"{rel}: {match.group(0).strip()}")
                continue
            fragments: list[str] = []
            #: The expression whose PARTS the runtime concatenates, when this node is
            #: one of the assembly forms. `None` for every other node, so the join
            #: below can never fire on an ordinary call that merely happens to have
            #: string arguments (`foo(_D, _C)` assembles nothing).
            assembling: ast.expr | None = None
            if isinstance(node, ast.JoinedStr):
                assembling = node
                fragments = [
                    v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
            elif isinstance(node, ast.BinOp):  # `+`, `%` and every other operator
                # A statement SPLIT across operands (`"…CONSTRAINT " + "CREATE …"`)
                # holds no complete statement in either literal, so the word is
                # what makes it reportable — an assembled statement must never
                # read as absent.
                assembling = node
                fragments = _ddl_fragments(node)
            elif isinstance(node, ast.AugAssign):
                # `_A += RHS` is an ACCUMULATION, so the text to look at is the
                # ACCUMULATED one: `_A = "CREATE "` + `_A += "CONSTRAINT pt UNIQUE
                # NODE Point"` leaves no operand carrying a spelling either
                # single-literal pattern recognises, and reading only the RHS made
                # a shipped constraint read as ABSENT (the fail-open review cycle
                # 12 found in this branch's own fix, #5064). `bindings` already
                # holds the join; the RHS alone is the fallback for an accumulator
                # whose earlier value is unresolvable here.
                accumulated = next(
                    (
                        bindings[key]
                        for key in _binding_keys(node.target)
                        if key in bindings
                    ),
                    None,
                )
                assembling = node.value if accumulated is None else accumulated
                fragments = _ddl_fragments(assembling)
                if set(_binding_keys(node.target)) & tainted:
                    assembled.append(f"{rel}: `{ast.unparse(node.target)}` += …")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _ASSEMBLY_ATTRS
            ):
                assembling = node
                fragments = _ddl_fragments(node)
            for fragment in fragments:
                statement = False
                for match in DDL.finditer(fragment):
                    statement = True
                    assembled.append(f"{rel}: {match.group(0).strip()}")
                if not statement and DDL_FRAGMENT.search(fragment):
                    # A fragment with the WORD but no statement: the statement is
                    # split across operands (`"…CONSTRAINT " + "CREATE …"`), which
                    # no single literal holds. Assembled, not absent.
                    assembled.append(
                        f"{rel}: a constraint fragment with no statement in it "
                        f"({fragment.strip()[:60]!r})"
                    )
            # THE OPERANDS JOINED, in the order the runtime concatenates them.
            # A split at the KEYWORD boundary — `"CREATE " + "CONSTRAINT pt UNIQUE
            # NODE Point"`, `"CREATE CONS" + "TRAINT …"`, or the same held by two
            # names — leaves NO operand carrying a spelling either `DDL` or
            # `DDL_FRAGMENT` recognises, so every check above is silent and the
            # constraint reads as ABSENT while it ships (the fail-open review cycle
            # 11 measured on this branch). The keyword is exactly what such a
            # statement is split AT, so the fragment loop cannot be fixed by adding
            # the bare word to `DDL_FRAGMENT`: that taints `_CYPHER_RESERVED`, a
            # regex and every ordinary Cypher `CREATE` (measured: false positives on
            # the real tree). Joining is the NARROW spelling — it fires only when the
            # parts actually form a constraint statement.
            if assembling is not None and not any(DDL.search(f) for f in fragments):
                truncated: list[str] = []
                seam = DDL.search(_assembled_text(assembling, bindings, 0, truncated))
                if seam:
                    assembled.append(
                        f"{rel}: a constraint statement SPLIT across operands — no "
                        f"single literal holds it, their concatenation does: "
                        f"{seam.group(0).strip()}"
                    )
                elif truncated and (
                    any(DDL_KEYWORD.search(f) for f in fragments)
                    or any(
                        key in bindings
                        and _carries_ddl_keyword(
                            bindings[key], bindings, frozenset({key})
                        )
                        for key in truncated
                    )
                ):
                    # The join met a binding it will NOT follow — a cycle on the path
                    # (`url = url + "/x"`) or a chain past `_MAX_RESOLVE_DEPTH` — so
                    # whether a statement is formed here is UNKNOWN. Measured before
                    # this branch existed: `_A = "CREATE "; _B = _A; _C = _B;
                    # _D = _C; g.query(_D + "CONSTRAINT …")` returned no `found` AND
                    # no `assembled`, so the pin stayed green. The GATE is the
                    # constraint word: an unresolvable hop in string building that
                    # cannot be a constraint is not this pin's business (measured:
                    # ungated, 73 unrelated assemblies red the real tree).
                    assembled.append(
                        f"{rel}: an assembly form here resolves through "
                        f"{', '.join(sorted(set(truncated)))} — a binding this scan "
                        "will not follow (a repeated binding on the path, or a chain "
                        f"past {_MAX_RESOLVE_DEPTH} names) — and a constraint word is "
                        "reachable from it, so a statement assembled here cannot be "
                        "read as absent"
                    )
            # A USE of a name bound to an incomplete statement — the label is
            # supplied HERE, which a static scan cannot resolve. `Load` only: a
            # target is a binding, not a use, and `ctx` is the shape-independent
            # test for that (the earlier `id(target)` allow-list had to name each
            # binding form and so missed `AnnAssign` and every attribute target).
            if (
                isinstance(node, (ast.Name, ast.Attribute, ast.Subscript))
                and isinstance(node.ctx, ast.Load)
                and _use_key(node) in tainted
            ):
                assembled.append(
                    f"{rel}: `{ast.unparse(node)}` carries an incomplete DDL "
                    "statement and is used as a value"
                )
    return found, unparsable, sorted(set(assembled))  # the walk visits ancestors too


def test_the_ddl_scan_sees_every_form_that_could_name_a_label(tmp_path) -> None:
    """The SCAN's own polarities, one fixture each — the pin is only as wide as this.

    The pin below reads the real tree, so nothing in `tortoise/` exercises the
    cannot-tell branch: measured, replacing `assembled` with `[]` left the whole
    suite green (87 passed) while a runtime-assembled `Point` constraint sailed
    past — the exact fail-open this branch was added to close, unguarded against
    the next conflict resolution. So the scan is driven over fixture files here:
    a literal statement (including implicit concatenation), every assembly form
    that was invisible in some earlier revision (`f-string`, `+`, `%`, `.format`,
    `.format_map`, `.join`, `+=`), every BINDING SHAPE a fragment can be carried
    by (a plain name, an annotated name, an attribute, a tuple target), a
    docstring (never executed, so not a statement), and a mention in ordinary
    text (reported, but the pin's message tells the reader it may be prose).

    The binding-shape fixtures are the ones cycle 7's review found missing: the
    taint allow-list accepted only a bare `ast.Name` target of a plain
    `ast.Assign`, so `_D: str = "… UNIQUE NODE "` + `g.query(_D + _L)` (an
    `AnnAssign`), `self.ddl = "…"` + `g.query(self.ddl + "Point")` (an attribute)
    and `_A, _B = "… NODE ", "Point"` (a tuple target) all left the pin GREEN
    while a `Point` constraint shipped — the exact false PASS it exists to stop.
    Each of those three is a fixture below, so no reduction of the allow-list
    back to `Name` targets can pass this test again.

    The `keyword_split*` fixtures are cycle 11's finding, and they are the ones
    `split_keyword` was mistaken for: a statement split at the KEYWORD boundary
    contains no spelling that either single-literal pattern sees, so the pin read
    it as ABSENT while `atomic_point_write`'s own note claimed exactly that shape
    was refused. Each of them reds on the pre-join scan (proven by disabling the
    join and by disabling name resolution: the first reds `keyword_split`, the
    second `keyword_split_fstring`).

    The two `keyword_split_augassign*` fixtures are cycle 12's finding, and they
    are the INTERSECTION of two families above, which neither covered: `+=`
    accumulations were only fixtured with the keyword already in the first
    assignment (the taint rule's case), and keyword splits only with `+`, an
    f-string and `.join`. `_A = "CREATE "` + `_A += "CONSTRAINT pt UNIQUE NODE
    Point"` therefore shipped a `Point` constraint past a GREEN pin — the row's
    note named both the form and the split, and their crossing was implemented
    nowhere. The first fixture reds while the `AugAssign` branch reads only the
    RHS; the second reds while the accumulated binding is dropped as a rebinding,
    because then the use site (`_A + "Point"`) resolves to nothing.

    Four more fixtures pin the same fail-open one layer DOWN, in the join's own
    bookkeeping: it dropped a hop it refused to follow (four bindings deep), it
    keyed a class-body binding by a different spelling than the taint rule did
    (`Ddl.kw` vs `name:kw`), and it could not follow a hop at all — each returning
    no `found` AND no `assembled` on the head cycle 12 reviewed (`([], [], [])`,
    measured). `keyword_split_cycle` pins the polarity for a hop that genuinely
    cannot be followed, and `cycle_without_a_constraint_word` is its CONTROL: the
    real tree is full of unresolvable cycles in ordinary string building
    (`url = url + "/x"`, 73 assemblies), and refusing those would red the pin for
    a question that is not about DDL at all.
    """
    forms = {
        "literal": (
            'g.query("GRAPH.CONSTRAINT CREATE pt UNIQUE "\n'
            '        "NODE Point PROPERTIES 1 h")\n',
            False,
            True,
        ),
        "fstring": (
            '_L = "Point"\ng.query(f"CREATE CONSTRAINT pt UNIQUE NODE {_L} PROPERTIES 1 h")\n',
            True,
            True,
        ),
        "plus": (
            '_L = "Point"\ng.query("CREATE CONSTRAINT pt UNIQUE NODE " + _L)\n',
            True,
            True,
        ),
        "percent": (
            '_L = "Point"\ng.query("CREATE CONSTRAINT pt UNIQUE NODE %s" % _L)\n',
            True,
            True,
        ),
        "dot_format": (
            '_L = "Point"\ng.query("CREATE CONSTRAINT pt UNIQUE NODE {}".format(_L))\n',
            True,
            True,
        ),
        "format_map": (
            '_L = "Point"\ng.query("CREATE CONSTRAINT pt UNIQUE NODE {l}".format_map({"l": _L}))\n',
            True,
            True,
        ),
        "join": (
            '_L = "Point"\ng.query("".join(["CREATE CONSTRAINT pt UNIQUE NODE ", _L]))\n',
            True,
            True,
        ),
        "augassign": (
            '_L = "Point"\nd = "CREATE CONSTRAINT pt UNIQUE NODE "\nd += _L\ng.query(d)\n',
            True,
            True,
        ),
        # The three binding SHAPES the name-only allow-list could not see. Each
        # carries the DDL keyword in one literal and the label in another, so the
        # Constant branch alone reports a fragment with no label and the pin would
        # be green without the taint branch.
        "annassign": (
            '_D: str = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '_L = "Point"\ng.query(_D + _L)\n',
            True,
            True,
        ),
        "attribute": (
            'class K:\n    def m(self):\n'
            '        self.ddl = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '        g.query(self.ddl + "Point")\n',
            True,
            True,
        ),
        "tuple_target": (
            '_A, _B = "CREATE CONSTRAINT pt UNIQUE NODE ", "Point"\n'
            'g.query(_A + _B)\n',
            True,
            True,
        ),
        "parts_list": (
            'P = ["CREATE CONSTRAINT pt UNIQUE NODE ", "Point"]\n'
            'g.query("".join(P))\n',
            True,
            True,
        ),
        "walrus": (
            'if (d := "CREATE CONSTRAINT pt UNIQUE NODE "):\n'
            '    g.query(d + "Point")\n',
            True,
            True,
        ),
        "for_target": (
            'for d in ["CREATE CONSTRAINT pt UNIQUE NODE "]:\n'
            '    g.query(d + "Point")\n',
            True,
            True,
        ),
        "split_keyword": (
            'g.query("GRAPH.CONSTRAINT " + "CREATE pt UNIQUE NODE Point")\n',
            True,
            False,  # no SINGLE literal holds the statement — that is the point
        ),
        # A split AT THE KEYWORD BOUNDARY — the shape review cycle 11 found absent
        # while `atomic_point_write`'s own note claimed "a statement SPLIT across
        # operands" is refused. Neither operand matches `DDL` OR `DDL_FRAGMENT`,
        # so every check fired on a single literal was silent and the constraint
        # read as ABSENT while it shipped. `split_keyword` above does NOT cover
        # this: its first operand matches `DDL_FRAGMENT` through the
        # `GRAPH\.CONSTRAINT` alternative, which is exactly the spelling that
        # happened to work. Each fixture below reds on the pre-join scan.
        "keyword_split": (
            'g.query("CREATE " + "CONSTRAINT pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_reverse": (
            'g.query("CONSTRAINT" + " CREATE pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_midword": (
            # The split lands INSIDE the keyword: neither `CREATE CONS` nor
            # `TRAINT …` is a spelling the single-literal patterns can see.
            'g.query("CREATE CONS" + "TRAINT pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_three_parts": (
            # Nested operands, so the join must be SOURCE-ordered: `ast.walk`
            # yields these breadth-first (`["NODE Point", "CREATE ", "CONSTRAINT"]`),
            # which joined in walk order matches nothing.
            'g.query(("CREATE " + "CONSTRAINT ") + "pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_fstring": (
            # The same split with the first part carried by a NAME inside an
            # f-string, so neither adjacency nor a single literal is enough.
            '_P = "CREATE "\ng.query(f"{_P}CONSTRAINT pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_names": (
            # No literal sits inside the `+` at all: both operands are names.
            '_D = "CREATE "\n_C = "CONSTRAINT pt UNIQUE NODE Point"\n'
            'g.query(_D + _C)\n',
            True,
            False,
        ),
        "keyword_split_in_collection": (
            'P = ["CREATE ", "CONSTRAINT pt UNIQUE NODE Point"]\n'
            'g.query("".join(P))\n',
            True,
            False,
        ),
        "keyword_split_augassign": (
            # The `+=` form CROSSED with the keyword split — the intersection the
            # row's note named ("`.join` / `+=`, a statement SPLIT across operands
            # at ANY point") and neither fixture family covered. `augassign` above
            # does NOT cover it: its first assignment already carries the whole
            # keyword, which the taint rule catches. Here `CREATE` sits in the
            # assignment and `CONSTRAINT` in the AUGMENT, so neither an operand nor
            # a single-literal pattern sees the statement and only the accumulated
            # text does — read alone, the RHS made the constraint ABSENT.
            '_A = "CREATE "\n'
            '_A += "CONSTRAINT pt UNIQUE NODE Point"\n'
            'g.query(_A)\n',
            True,
            False,
        ),
        "keyword_split_augassign_use": (
            # The same accumulation with the LABEL still to come at the use site,
            # which the augmented node cannot report on its own: the accumulation
            # must stay RESOLVABLE (`bindings` keeps the join, rather than dropping
            # the key as a rebinding) or `_assembled_text` at `g.query(_A + …)`
            # contributes nothing and the constraint reads as ABSENT again.
            '_A = "CREATE "\n'
            '_A += "CONSTRAINT pt UNIQUE NODE "\n'
            'g.query(_A + "Point")\n',
            True,
            False,
        ),
        # The same fail-open one layer DOWN, in the join itself: it dropped the one
        # thing it refused to resolve, keyed a binding by a different spelling than
        # the taint rule did, and could not follow a hop at all. Each read as
        # ABSENT on the head cycle 12 reviewed — measured `([], [], [])`, no
        # `found` and no `assembled` — keeping the pin green while a `Point`
        # constraint shipped.
        "keyword_split_name_chain": (
            # The keyword half is four bindings away. The join used to stop at three
            # hops and `continue` — the same `continue` as "this is not a binding" —
            # so the text vanished, nothing matched and nothing was reported.
            '_A = "CREATE "\n_B = _A\n_C = _B\n_D = _C\n'
            'g.query(_D + "CONSTRAINT pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_class_attr": (
            # The keyword half is a CLASS-BODY binding, USED as `Ddl.kw`. The taint
            # rule keyed the binding `expr:Ddl.kw` while the join map held only
            # `name:kw`, so the use site resolved nothing — the two disagreed about
            # the KEY SPELLING while the comment above the fill claimed they could
            # not disagree at all.
            'class Ddl:\n    kw = "CREATE "\n\n'
            'def c(g):\n    g.query(Ddl.kw + "CONSTRAINT pt UNIQUE NODE Point")\n',
            True,
            False,
        ),
        "keyword_split_cycle": (
            # A hop the join cannot follow AT ALL (a name repeating on its path)
            # whose chain does reach a constraint word: UNVERIFIABLE, never absent.
            # The control below is the other half of that gate.
            '_D = _D + "CONSTRAINT pt UNIQUE NODE Point"\ng.query(_D + "CREATE ")\n',
            True,
            False,
        ),
        "cycle_without_a_constraint_word": (
            # The CONTROL for that gate: an unresolvable cycle in ordinary string
            # building (`url = url + "/x"` is the real-tree shape, 73 of them) must
            # NOT be refused, or the real-tree pin reds unrelated assemblies for a
            # question that is not about DDL at all.
            'x = x + "hello".upper()\ng.query(x + "world")\n',
            False,
            False,
        ),
        "cypher_create_is_not_a_constraint": (
            # The CONTROL for the join: an ordinary Cypher write split at the same
            # place must NOT be refused — joining operands must catch constraint
            # statements, not every `CREATE` (the over-blocking direction that
            # made a bare-keyword test unusable on the real tree).
            'g.query("CREATE " + "(m:Point {id: 1})")\n',
            False,
            False,
        ),
        "add_constraint_is_not_a_graph_constraint": (
            # The other control: `ADD CONSTRAINT … CHECK` is a column check. Both
            # halves are here so the join cannot call it a graph constraint.
            'g.query("ALTER TABLE t " + "ADD CONSTRAINT c CHECK (x > 0)")\n',
            False,
            False,
        ),
        "class_attr_use": (
            'class Ddl:\n'
            '    stmt = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '\n'
            'def create(g):\n'
            '    g.query(Ddl.stmt + "Point")\n',
            True,
            True,
        ),
        "cls_compound_for": (
            # A class body that GUARDS its binding with a loop — the shape that
            # shipped unrefused after the first class-body fix, because only
            # direct `Assign`/`AnnAssign` statements were class-qualified.
            'class Ddl:\n'
            '    for stmt in ["CREATE CONSTRAINT pt UNIQUE NODE "]:\n'
            '        pass\n'
            '\n'
            'def c(g):\n'
            '    g.query(Ddl.stmt + "Point")\n',
            True,
            True,
        ),
        "cls_compound_if": (
            'class Ddl:\n'
            '    if True:\n'
            '        stmt = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '\n'
            'def c(g):\n'
            '    g.query(Ddl.stmt + "Point")\n',
            True,
            True,
        ),
        "cls_compound_try": (
            'class Ddl:\n'
            '    try:\n'
            '        stmt = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '    except Exception:\n'
            '        stmt = ""\n'
            '\n'
            'def c(g):\n'
            '    g.query(Ddl.stmt + "Point")\n',
            True,
            True,
        ),
        "cls_compound_with": (
            'class Ddl:\n'
            '    with open("x") as f:\n'
            '        stmt = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '\n'
            'def c(g):\n'
            '    g.query(Ddl.stmt + "Point")\n',
            True,
            True,
        ),
        "cls_tuple_target": (
            'class Ddl:\n'
            '    a, b = "CREATE CONSTRAINT pt UNIQUE NODE ", "Point"\n'
            '\n'
            'def c(g):\n'
            '    g.query(Ddl.a + "Point")\n',
            True,
            True,
        ),
        "cls_subscript": (
            'class Ddl:\n'
            '    parts = {}\n'
            '    parts["k"] = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '\n'
            'def c(g):\n'
            '    g.query(Ddl.parts["k"] + "Point")\n',
            True,
            True,
        ),
        "nested_class": (
            'class O:\n'
            '    class I:\n'
            '        stmt = "CREATE CONSTRAINT pt UNIQUE NODE "\n'
            '\n'
            'def c(g):\n'
            '    g.query(O.I.stmt + "Point")\n',
            True,
            True,
        ),
        "mixed_collection": (
            # One COMPLETE statement beside one incomplete fragment: the whole
            # carrier is unverifiable, because a resolvable element says nothing
            # about its neighbours (measured: this read as clean).
            '_T = ["GRAPH.CONSTRAINT CREATE ge UNIQUE NODE GraphEvent",\n'
            '      "CREATE CONSTRAINT pt UNIQUE NODE "]\n'
            'g.query(_T[1] + "Point")\n',
            True,
            True,
        ),
        "enum_members": (
            # The CONTROL for the class-body widening: a class body full of ordinary
            # strings must not taint its members (measured: an earlier widening did,
            # by the dozen).
            'class R(Enum):\n'
            '    DEDUP = "dedup"\n'
            '    FILED = "filed"\n',
            False,
            False,
        ),
        "complete_name": (
            # The ACCEPTING direction of the taint rule: a name bound to a COMPLETE
            # labelled statement is exactly the shipped `GraphEvent` constraint and
            # must NOT be called unverifiable (measured: unpinned, so reducing the
            # rule to `bool(match)` left the suite green while over-blocking it).
            '_D = "GRAPH.CONSTRAINT CREATE ge UNIQUE NODE GraphEvent"\n'
            'g.query(_D)\n',
            False,
            True,
        ),
        "docstring": (
            'def f():\n    """Do not issue GRAPH.CONSTRAINT CREATE: a UNIQUE NODE Point '
            'constraint would serialise writers."""\n',
            False,
            False,  # a docstring is never executed, so it is not even a mention
        ),
        "mention": (
            'NOTE = "Do not issue GRAPH.CONSTRAINT CREATE: a UNIQUE NODE Point '
            'constraint would serialise writers."\n',
            False,
            True,
        ),
    }
    for name, (source, is_assembled, is_found) in forms.items():
        probe = tmp_path / f"{name}.py"
        probe.write_text(source, encoding="utf-8")
        found, unparsable, assembled = ddl_scan(tmp_path)
        assert unparsable == [], (name, unparsable)
        if is_assembled:
            assert assembled, (
                f"the {name} form assembled a constraint statement and the scan "
                "did not refuse it — that is the fail-open this branch exists for"
            )
        else:
            assert not assembled, (name, assembled)
        # EVERY executable form is still SEEN: a refusal must never replace a report.
        assert bool(found) is is_found, (
            f"the {name} form reports found={bool(found)}, expected {is_found} — "
            "refusing a form as assembled must not hide that a constraint "
            "spelling is present, and a docstring is not a statement at all"
        )
        probe.unlink()
    # A docstring is excluded from `found` too: nothing executes it.
    (tmp_path / "doc.py").write_text(
        'def f():\n    """GRAPH.CONSTRAINT CREATE x UNIQUE NODE Point PROPERTIES 1 h"""\n',
        encoding="utf-8",
    )
    found, _, assembled = ddl_scan(tmp_path)
    assert not found and not assembled, (found, assembled)


def test_the_ddl_scan_refuses_a_source_it_cannot_read(tmp_path) -> None:
    """An unreadable source is a VERDICT, never a silent skip.

    `unparsable` is the scan's OTHER cannot-tell polarity, and nothing in the real
    tree exercises it — so without this fixture `unparsable.append(rel)` could be
    deleted and the pin would stay green on a partial read. A source that cannot
    be turned into a tree may carry the very constraint the pin claims is absent,
    so the failed read must be REPORTED, and because it was not read it must not
    also be reported as a found constraint.

    The third file is the control: a legal PEP 263 `latin-1` module (which a plain
    `read_text(encoding="utf-8")` refuses) IS read, so `unparsable` means
    "unreadable", not "not ASCII".
    """
    (tmp_path / "broken.py").write_bytes(
        b'g.query("CREATE CONSTRAINT pt UNIQUE NODE Point")\ndef f(:\n'
    )
    (tmp_path / "latin.py").write_bytes(
        b'# no coding cookie, and not valid UTF-8\nX = "caf\xe9"\n'
        b'g.query("CREATE CONSTRAINT pt UNIQUE NODE Point")\n'
    )
    found, unparsable, assembled = ddl_scan(tmp_path)
    assert found == [] and assembled == [], (found, assembled)
    assert unparsable == ["broken.py", "latin.py"], unparsable
    # The control: the SAME DDL in a module whose encoding IS declared.
    (tmp_path / "cookie.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nX = 'caf\xe9'\n"
        b'g.query("CREATE CONSTRAINT pt UNIQUE NODE Point")\n'
    )
    found, unparsable, assembled = ddl_scan(tmp_path)
    assert unparsable == ["broken.py", "latin.py"], unparsable
    assert found, (
        "a legal PEP 263 `latin-1` module was not read — the scan went blind on "
        "a file the interpreter imports happily, with no violation reported"
    )
    assert assembled == [], assembled


def test_the_atomic_write_absence_is_pinned_to_the_schema() -> None:
    """The `atomic_point_write` row's absence claim, read off the SCHEMA.

    The declaration is *"there is no uniqueness constraint on `Point`"*, and the
    thing that would falsify it is a constraint, not a paragraph. Two facts are
    asserted together: the capability is used in this repository (so the absence
    is a decision, not an impossibility), and no UNIQUE constraint names a Point
    (so the row's state is still the truth). The day one is added, this reds and
    the row must flip — the same polarity as the carve-out's render pin.

    The SCAN itself is `ddl_scan` above, and each of its polarities is proven by
    `test_the_ddl_scan_sees_every_form_that_could_name_a_label` — a real tree has
    no runtime-assembled constraint in it, so without that fixture the
    cannot-tell branch would be unreachable and could be deleted silently.
    """
    constraints, unparsable, assembled = ddl_scan(ROOT / "tortoise")
    assert unparsable == [], (
        "a source under tortoise/ could not be parsed, so a constraint written "
        "in it is INVISIBLE to this pin — that is not the same as its absence: "
        f"{unparsable}"
    )
    assert any("UNIQUE" in c.upper() for c in constraints), (
        "no UNIQUE graph constraint exists anywhere in tortoise/ — the contrast "
        "this check rests on (the capability is already used) is gone: "
        f"{constraints}"
    )
    point_constraints = [
        c for c in constraints
        if "UNIQUE" in c.upper() and re.search(r"\bPoints?\b", c)
    ]
    assert point_constraints == [], (
        "a UNIQUE constraint naming Point appears in tortoise/ — either the\n  "
        "• STATEMENT: the `atomic_point_write` row's absence claim is no longer "
        "true — flip the row to `implemented` (and close #9879); or\n  "
        "• MENTION: an explanatory string spells the DDL out while saying it is "
        "NOT created — this scan cannot tell a statement from a mention, so "
        "reword it without the DDL spelling (`a uniqueness constraint on Point`)\n  "
        "Neither is fixed by deleting this assertion:\n  "
        + "\n  ".join(point_constraints)
    )
    assert assembled == [], (
        "a graph constraint is ASSEMBLED AT RUNTIME — f-string / `+` / `%` / "
        ".format / .format_map / .join / `+=` / a statement SPLIT across operands "
        "/ a name, attribute, subscript or tuple target BOUND to an incomplete "
        "one (`AnnAssign`, walrus, `for` target, a collection of fragments) — "
        "which this static scan cannot resolve, so whether it names Point is "
        "UNKNOWN, and an unknown is not an absence. Write the statement as a "
        "literal (implicit concatenation is fine), or extend this pin to resolve "
        "the label:\n  "
        + "\n  ".join(assembled)
    )
    row = {r["id"]: r for r in load_registry()["mechanisms"]}["atomic_point_write"]
    assert row["state"] == NOT_IMPLEMENTED, row
    assert row.get("tracking_issue") == 9879, row


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

    A marked-absent row that declares its precondition must be clean, so that an
    ordering is recorded at the moment the mechanism is named rather than
    discovered at implementation time. (This was the LIVE shape until #5006
    implemented `near_dup_merge` — the registry shipped exactly this pair. The
    rule is pinned on fixtures so it survives that row flipping to
    `implemented`, which is the case it was written for.) So must a row that
    declares NO ordering (the field omitted) — and only that: an explicit
    `preconditions:` with no value is refused above, because an empty
    declaration must not read as one.
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
