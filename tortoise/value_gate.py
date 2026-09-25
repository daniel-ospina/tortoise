"""S2.2b mechanical value gate — the **identifier-only** DISCARD predicate (#4899).

**What this module is.** The *predicate* half of the mechanical gate that
``EXTRACTOR-V4-ARCHITECTURE.md`` §4.3 names as ``S2.2b VET — the gate
(identifier-only DISCARD) + adversarial``. It is a **leaf**: stdlib-only, no
import of ``extractor_v2`` or ``vet_gate`` (both import *this*), so it holds no
pipeline vocabulary. It returns ``(rule_id, reason)`` and lets the caller decide
the outcome word.

**Why "identifier-only", and why that is the whole rule.**
``#2453`` (landed, regression-tested) states the droppable class exactly:

    "Only INCIDENTAL process logistics remain droppable: ids, hashes, and
     ephemeral counters **not central to a decision**."

So the predicate fires **only when the candidate's entire content is an
identifier** — a bare reference, a commit hash, a bare path, a bare id. It must
never fire because an identifier is merely *present*: measured on 80 real
production statement rows, presence-matching (the ``the ``-prefix rule this
issue first proposed) fires on **70–77%** of rows and would discard the
governing decision and the measured thresholds ``#2453`` requires be carried
verbatim. Precision is bought by requiring the row to be *nothing else*; the
recall it gives up is the semantic remainder, owned by ``#4894``.

**Why ``the <X>`` is NOT here.** An Object named ``the <X>`` cannot be judged
mechanically: ``the owner`` is a real entity with no antecedent, while
``the cycle-4 ruling`` is a definite description that is not an entity. That is
the question ``vet_gate`` describes as *"'is this an entity or a reference?'"* —
the adversarial half. A bare-identifier Object name (``#4118``) is handled here;
a definite description is left to the arbiter, and is reported, not dropped.

**Fail-open is the contract.** No identifier, no verdict. An unparseable
candidate is a ``KEEP``. ``#4899``'s three safeguards (a flag, a recorded rule
id, a recoverable counterfactual) are enforced by the caller.
"""

from __future__ import annotations

import os
import re

from .env_truthy import is_truthy

# ── The flag (#4899 safeguard 1) ────────────────────────────────────────────

#: The call-time toggle. Unset/0 ⇒ the predicate never runs and the only result
#: delta is a zeroed ``mechanical`` stats key — matching the ``TORTOISE_VET``
#: (``#5005``) and ``TORTOISE_CLASSIFY_LATER`` (``#1695``) precedent, so the
#: rate effect is measurable before it is trusted.
ENV_FLAG = "TORTOISE_VALUE_GATE"


def value_gate_enabled() -> bool:
    """Whether the mechanical gate runs. Off unless the flag says otherwise.

    Read through the declared truthy contract (``#4097``) — the same one every
    other extractor flag uses, so ``"true"``/``"1"``/``"yes"`` agree here too.
    """
    return is_truthy(os.environ.get(ENV_FLAG))


# ── The rule vocabulary (each id is recorded on every discard —
#    #4899 safeguard 2: no silent discard) ──────────────────────────────────

RULE_REFERENCE = "value.identifier_only.reference"
RULE_HASH = "value.identifier_only.hash"
RULE_PATH = "value.identifier_only.path"
RULE_UUID = "value.identifier_only.uuid"

#: Every id this module can emit, most specific first — the order that decides
#: which id is reported when a candidate is only identifiers of several shapes.
RULES: tuple[str, ...] = (RULE_UUID, RULE_HASH, RULE_REFERENCE, RULE_PATH)

#: The pack's OWN declared ephemeral class each rule implements — the source of
#: policy stays the pack (``#1026``), the predicate stays the engine. Pinned by
#: ``tests/test_value_gate_4899.py::test_rules_are_declared_by_their_source``,
#: which reads ``source`` and asserts ``declares`` appears in it: a rule that
#: stops being declared stops being justified, and the test fails rather than
#: the drift going silent. ``source`` is the file the *declaration* lives in —
#: for paths and ids that is the landed ``#2453`` clause in ``extractor_v2``
#: (``dev``'s ``memory_granularity`` names issue/PR numbers and commit hashes,
#: and no path class), not the pack. ``anchor`` (optional) is a second string
#: that must sit on the **same line** as ``declares`` in a non-pack source, so a
#: generic phrase cannot be satisfied by an unrelated occurrence elsewhere in a
#: large file — the review's finding on ``RULE_PATH``, whose original phrase
#: (``process logistics``) named no path class at all.
DECLARED_CLASS: dict[str, dict[str, str]] = {
    RULE_REFERENCE: {
        "source": "packs/dev/manifest.yaml",
        "declares": "issue/pr numbers",
        "label": "issue/PR number",
    },
    RULE_HASH: {
        "source": "packs/dev/manifest.yaml",
        "declares": "commit hashes",
        "label": "commit hash",
    },
    RULE_PATH: {
        "source": "tortoise/extractor_v2.py",
        "declares": "counts, paths",
        "anchor": "noise",
        "label": "path",
    },
    RULE_UUID: {
        "source": "tortoise/extractor_v2.py",
        "declares": "ids, hashes",
        "anchor": "droppable",
        "label": "id",
    },
}

# ── The identifier shapes ───────────────────────────────────────────────────

#: A bare reference — ``#4118``. Bounded to 1–6 digits so it cannot swallow a
#: run of digits, and it requires the ``#`` (a bare number is NOT adopted).
_REFERENCE = re.compile(r"#\d{1,6}(?!\d)")

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

#: A commit hash. ⚠️ Three guards, each earned from a real row or a review
#: finding:
#:   * ``{7,40}`` — a 7-char floor keeps 3/4/6/8-char hex **colour** literals
#:     (``#656970``) out of the class entirely (the sample's false positive);
#:   * ``(?=[0-9a-f]*\d)`` requires a digit, so an all-letter English word built
#:     from ``[a-f0-9]`` (``defaced``, ``deadbeef``) is not read as a hash;
#:   * ``(?=[0-9a-f]*[a-f])`` requires a hex **letter**, so an all-digit run
#:     (``1234567``, ``20260923``) is a MEASUREMENT, not a hash. Without this
#:     guard the residue-digit check below is unreachable — every decimal digit
#:     is also a hex digit, so a 7-digit number stripped to nothing and fired
#:     as a hash (the review's P1).
#: Case-insensitive, matching ``_UUID``: an uppercase SHA is still a SHA, and
#: the earlier lowercase-only form silently kept it (review cycle 2, P2).
#: The residue (an all-letter 7+ char hex) fails **open**.
_HASH = re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{7,40}\b")

#: A bare path — ``a/b/c.py``, a lone ``extractor_v2.py``, or the ``file:line``
#: form the samples actually carry (``tests/_html_links.py:174-183``). Four
#: guards, each earned from a real row or a review finding:
#:   * the extension must be **alphabetic**, so a sentence-final ``review.`` and
#:     a version like ``S2.2`` do not match;
#:   * the stem must be **≥2 chars**, so the abbreviation ``e.g`` is not read as
#:     a path (``e`` before the dot is one char);
#:   * an optional ``:LINE``/``:LINE-LINE`` suffix is absorbed, or the residue
#:     ``:93`` would be left behind, carry a digit, and fail the rule **open**;
#:   * ⚠️ the slash form requires **every segment to carry a letter** and the
#:     match to **end in a dotted, extension-bearing segment**. Without the
#:     letter rule, ``2026/09/19`` (a deadline!) was removed as a path so its
#:     digits never reached the residue check, and ``CI/CD`` / ``I/O`` — not
#:     identifiers at all — were discarded outright (review cycle 2, P1).
#:     ⚠️ The dotted segment must be **inside** the match, which is why the
#:     pattern is anchored to end on it rather than using a lookahead: a
#:     start-anchored ``(?=[\w./-]*\.[A-Za-z])`` scans the whole
#:     whitespace-free run, so ``CI/CD//ab.py`` satisfied it from ``ab.py`` and
#:     still discarded the dotless ``CI/CD`` (review cycle 3, P2).
#:   * ⚠️ the final component takes **any number of dotted parts** and an
#:     extension up to 12 letters, and a *directory* segment needs no letter.
#:     Without that, whole classes of real path were silently KEPT —
#:     ``docs/4899/notes.md``, ``src/app.test.tsx``, ``archive.tar.gz``,
#:     ``index.d.ts``, ``a/b.markdown`` (review cycle 4, P2). Fail-open, so it
#:     cost no memory — but it is the class the rule exists for.
#: ⚠️ Known fail-open misses, kept deliberately: ``a//b.py`` (relaxing the
#: segment to ``/+`` re-introduces the cycle-3 P2), backslash paths
#: (``src\\main.py``), and a dot in a *middle* segment only (``a/b.py/c``).
#:   * ⚠️ **neither branch requires a letter in the final stem** — only in the
#:     *extension*. The stem guard was the same defect one level down: real
#:     tracked files (``website/404.html``, ``404.html``,
#:     ``docs/2026/09/19.md``, ``a/b/2026.py``) were silently KEPT (cycle 5, P2).
#:     The alphabetic **extension** is what protects versions and measurements
#:     (``v1.2``, ``S2.2``, ``4.2``, ``12.5ms``, ``2026/09/19``), so dropping the
#:     stem letter re-opens nothing.
#:   * the extension must be **≥2 letters**, which is what rejects ``Ph.D`` /
#:     ``Ph.D.`` (a degree, not a path). Cost: a genuine ``.c``/``.h``/``.R``
#:     path fails open — acceptable in a Python/TypeScript repository.
#:   * ⚠️ the no-slash branch also rejects a token carrying an **inner numeric
#:     segment** (``\.\d+\.``), because dropping the stem-letter rule made
#:     VERSIONS match as paths: ``10.0.0.beta``, ``12.5.rc``, ``v1.2.3.beta``.
#:     ``#2453`` lists versions in the carry-verbatim class, so that is a
#:     surface-(a) false positive, not a recall trade (review cycle 6, P1).
#:     ``404.html`` / ``2026.md`` are unaffected — a lone numeric stem is not an
#:     inner segment. Only the no-slash branch carries it: with a slash the
#:     token is a path by construction.
_PATH = re.compile(
    r"(?:"
    r"(?:[\w.@-]+/)+[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,12}\b"
    r"|"
    r"(?![\w-]*(?:\.[\w-]+)*\.\d+\.)"
    r"(?=[\w-]{2,}\.)[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,12}\b"
    r")"
    r"(?::\d+(?:-\d+)?)?"
)

#: Closed-class words that carry no claim. An identifier-only candidate is one
#: whose non-identifier residue is drawn **entirely** from this set. Deliberately
#: a small, closed list, and the rule for adding to it is: **a word may only be
#: added if it can never head a durable claim.** Function words qualify; so do
#: pure cross-reference and pure *logistics/outcome* verbs (``merged``,
#: ``landed``, ``closes``) — stating that something was merged carries no
#: claim. What stays OUT is any verb that can carry a cause, a choice or a
#: finding: ``chose``, ``fixed``, ``caused``, ``found``, ``rejected``.
#: (⚠️ An earlier version of this comment claimed the list held *only* function
#: words while the set already contained the logistics verbs — the set is
#: intended; the comment was the inaccurate part.)
_FUNCTION_WORDS = frozenset(
    {
        # articles / determiners / pronouns
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "there",
        "here",
        "we",
        "i",
        # prepositions / conjunctions / particles
        "and",
        "or",
        "but",
        "nor",
        "yet",
        "so",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "with",
        "by",
        "as",
        "into",
        "over",
        "under",
        "up",
        "down",
        "out",
        "via",
        "per",
        "than",
        "then",
        "also",
        "only",
        "just",
        "not",
        "no",
        # copulas / auxiliaries
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        # cross-reference / logistics vocabulary
        "see",
        "cf",
        "ref",
        "refs",
        "reference",
        "references",
        "aka",
        "vs",
        "id",
        "ids",
        "hash",
        "hashes",
        "sha",
        "shas",
        "commit",
        "commits",
        "pr",
        "prs",
        "issue",
        "issues",
        "note",
        "notes",
        "filed",
        "opened",
        "closed",
        "created",
        "updated",
        "merged",
        "merge",
        "landed",
        "committed",
        "pushed",
        "shipped",
        "closes",
        "closing",
        "fixes",
        "fixing",
        "resolves",
        "resolving",
    }
)

_WORD = re.compile(r"[A-Za-z][A-Za-z'’_-]*")


def _strip_identifiers(text: str) -> tuple[str, list[str]]:
    """``text`` with every identifier span removed, plus the shapes removed.

    ⚠️ **The order is load-bearing, not cosmetic.** ``UUID`` must be stripped
    before ``HASH``: a dashed UUID read as a hash splits into sub-runs
    (``550e8400`` is an 8-char hex run), the tail survives, and the verdict
    inverts from DISCARD to KEEP. ``HASH`` must precede ``REFERENCE`` so an
    identifier like ``#1234abcd`` is read as one span: ``REFERENCE`` first takes
    only ``#1234`` and leaves ``abcd`` as a content word, turning a DISCARD into
    a KEEP. Only the *reporting* order is free (the tuple below picks the most
    specific id). An earlier version of this docstring claimed the order
    affected nothing but reporting — that was false (review cycle 2, P2), and
    acting on it would silently kill the whole UUID class.
    """
    found: list[str] = []

    def _drop(pattern: re.Pattern[str], rule_id: str):
        nonlocal text
        if pattern.search(text):
            found.append(rule_id)
            text = pattern.sub(" ", text)

    _drop(_UUID, RULE_UUID)
    _drop(_HASH, RULE_HASH)
    _drop(_REFERENCE, RULE_REFERENCE)
    _drop(_PATH, RULE_PATH)
    return text, found


def identifier_only(text: object) -> str | None:
    """The rule id when ``text`` is **nothing but** identifiers, else ``None``.

    ``None`` is the fail-open answer and covers both "no identifiers at all"
    and "identifiers plus any content word". Only a candidate whose whole
    residue is closed-class filler earns a rule id.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    residue, found = _strip_identifiers(raw)
    if not found:
        return None  # no identifier ⇒ this rule never fires
    # A candidate built only from digits and separators is a MEASUREMENT, not an
    # identifier: ``#2453`` carries a recorded measurement verbatim, so a bare
    # number, ratio, date or time must fail open. The one shape that
    # legitimately carries no letter is a ``#`` reference (``#4118``).
    # ⚠️ Only reachable because ``_HASH`` now requires a hex letter — a
    # digit-only run used to strip to nothing before reaching here (review P1).
    if RULE_REFERENCE not in found and not any(c.isalpha() for c in raw):
        return None
    for word in _WORD.findall(residue):
        if word.lower() not in _FUNCTION_WORDS:
            return None  # a content word survives ⇒ KEEP
    if any(ch.isdigit() for ch in residue):
        return None  # a bare number is not adopted ⇒ KEEP
    return next((r for r in RULES if r in found), found[0])


def mechanical_verdict(text: object) -> dict | None:
    """The verdict for ``text``, or ``None`` to keep it.

    Returns ``{"rule_id", "reason"}`` — deliberately **not** an outcome word:
    the caller owns the vocabulary (``vet_gate.DISCARD``), which is what keeps
    this module a leaf. ``reason`` quotes the candidate so the counterfactual
    the caller records is readable without the original session.
    """
    rule_id = identifier_only(text)
    if rule_id is None:
        return None
    shown = str(text or "").strip()
    if len(shown) > 120:
        shown = shown[:117] + "..."
    return {
        "rule_id": rule_id,
        "reason": (
            f"identifier-only ({DECLARED_CLASS[rule_id]['label']}) — no claim attached: {shown!r}"
        ),
    }
