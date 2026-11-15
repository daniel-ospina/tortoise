"""The verdict step over noun candidates — three bands, hard vetoes, closure
re-verification (#12296).

**Stdlib only, no model call, no graph read.** This module decides *what a
proposal means*; it never produces a proposal. The candidate set is supplied by
`extractor_v2.search_graph` (S3) and the proposal — when the resolver is armed —
by the phase-2 prompt (`extractor_v2._resolution_prompt`). Everything here is a
pure function over the mention, the candidate and a small fact dict, so every
rule below is testable without a graph or a provider.

Vocabulary
----------
Three bands, and only three::

    merge   — the mention IS this existing entity. The mention binds to it.
    review  — undecidable at this layer: HELD, never asserted new (§4.4 I1).
    new     — the run measured ABSENCE (no candidate of that name), which is the
              one state that licenses a create (#11653).

⛔ **`judge` returns only `merge` or `review`, and that is not a gap.** It takes a
(candidate, mention) PAIR, so it can answer *"is this the same entity"* and can
never answer *"does this entity exist elsewhere"* — absence is the resolver's to
measure (`resolve_entities`'s `new` population, licensed by the model positively
declaring distinct referents). `BAND_NEW` is therefore published VOCABULARY: the
name a create-side record carries, so one vocabulary covers the whole step. A
falsifier that scores `judge` "per band" will have an empty `new` row by
construction; score the create side on `resolve_entities`'s `new` list instead.

⛔ **`link` is NOT a band, and this is a deliberate record-shape decision
(issue #12296 scope 1).** Round 3 (`12186` comment `6839898289`, Half-A.1) found
exactly one non-retiring pointer in the tree — `aliases[]` on the SURVIVOR
(``entity_resolver.apply_entity_merge`` → `sdk.update_entity(survivor_id,
                     aliases=...)`), which leaves the other node live,
search-visible and still a dedup candidate (`_ENTITY_READ` filters on
`resolved_from`, `entity_resolver.py:207`). Two reasons it is not shipped as a
verdict value here:

* **a pointer is not a verdict.** `aliases[]` records *addressability* ("this
  spelling names that entity"). It carries no evidence, no score and no inverse:
  from an alias you cannot recover the variant that produced it. A band must be
  able to say *"these two are probably one thing, and here is why"*; an alias
  cannot.
* **⛔ writing `dedup_reviewed='link'` is worse than inert — it silently
  DEQUEUES.** The write is one call (`sdk.update_entity` → `_update_entity`'s
  `SET n += $props`), but the content review queue filters `dedup_reviewed IS
  NULL` (`sdk.py:24856`), so ANY non-NULL value removes the row from review with
  no verdict applied. A band that wrote `'link'` today would delete a review
  nobody counts.

So a proposal that is neither provable identity nor measured absence leaves as
`review` (held) — which is the same disposition the pointer would have produced,
minus the lost review.

Hard vetoes
-----------
The expensive direction is a FALSE MERGE: it retires a node other paths keep
reading. The bands are therefore **precision-first**, in the field's own words —
*"precision needs hard rule-based vetoes"* (arXiv:2607.26298 lesson 2, quoted
from round 1's stage-B row). A veto can only ever move a proposal from `merge`
to `review`; it never licenses `new`, because a conflict is not an absence.

Six deterministic vetoes, each with the substrate it reads. The first three are
name/kind rules over the two spellings; the last three read ``facts`` — what the
graph says about the candidate — and the final two are the closure
re-verification, described at length under "Closure re-verification" below. All
six are applied by the one function, so a reader never has to know which layer a
veto lives in to know that it fired:

``identifier_conflict``
    Both sides carry identifier-shaped tokens, the sets are disjoint, **and the
    two names are otherwise the same** (`billing v2 ledger` vs `billing v3
    ledger`). Separator and case are folded, so the #8933 class (`ERR-4032` →
    `err4032`) does NOT fire — a deliberate property: the fold that makes the
    identifier class measurable also makes its variants agree. The skeleton
    equality is the second half and it is load-bearing: without it the `#11901`
    `basename-only` variant class reads as a conflict.

``date_conflict``
    Both sides carry a year and/or a quarter token, the values are disjoint for a
    SHARED kind (or for a shared IMPLIED dimension — year / month / day — when the
    kinds do not meet, which is what makes a full date contradict a bare month),
    **and the names are otherwise the same** (`the Q3 plan` vs `the Q4 plan`).
    Compared like with like: `Q3 plan` against `2026 plan` is not a conflict —
    nothing there is contradicted, and those two spellings can name one entity.

``kind_conflict``
    Both sides carry a SPECIFIC kind and they differ. `other`/empty is the
    coalesce default (`entities.py:5086`) and is treated as *unknown*, never as a
    conflicting value — a default must not become evidence.

``target_suppressed``
    The candidate carries a rejection that named no survivor — `_pair_reviewed`'s
    documented "suppresses the candidate outright" case
    (`entity_resolver.py:236-250`): the reviewer already answered the question,
    against an ambiguous set, and re-asking it against one survivor is not a
    different question.

⛔ **On the issue's "an explicit `DIFFERENT_FROM`" veto: that predicate does not
exist in this tree.** `grep -rn 'DIFFERENT_FROM' tortoise/ tests/` finds nothing
outside this sentence;
`_VALID_EDGE_PREDICATES` (`projection/edges.py:717-730`) has no such edge. The
recorded analogue is the persisted no-merge verdict — `dedup_reviewed='reject'`
plus `dedup_target_id` / `dedup_rejected_targets` — and it is **PAIR-scoped
between two NODES**. A name-only mention has no node, so the pair form is not
evaluable at this layer: the verdict defers to it by leaving `target_suppressed`
here (the target-less form, which suppresses regardless of pair) and by never
re-offering a reviewed pair (the queue's own universe). Stated rather than
approximated: a rejection that names a survivor is honoured by the merge verb
(`sdk.py:24861-24879`), not by this module.

Closure re-verification (M-4)
-----------------------------
*"One false-positive link can silently merge unrelated entities — assuming 'A
matches B' and 'B matches C' implies 'A matches C' lets a single bad link chain
hundreds of records together, so every cross-group merge must be actively
re-verified"* (arXiv:2607.26298 lesson 3, round 1's M-4).

In this tree the chain is a `resolved_from` path, and it is **walked on the
identity path** — `follow_resolved_from` follows to a fixpoint
(`entity_resolver.py:556-577`), so a chain is silently collapsed rather than
reported. Two closure vetoes make that hazard decidable at the verdict:

``target_retired``
    The target itself carries `resolved_from`. Binding a mention to it is
    binding to a node the merge already retired — defect **D-1**
    (`12186` comment `6839898289`), which the S3 candidate-set guard also
    prevents; this is the belt-and-braces half for any caller that builds a
    candidate table by another route.

``group_join``
    The target is itself a SURVIVOR of an earlier merge (`dependents >= 1`). The
    target then stands for a group, and binding a new mention to it asserts
    "mention matches group" from a pairwise comparison that was never made
    against the group's other members. That is the transitive-closure hazard, and
    the precision-first answer is `review`: the name is HELD (`HOLD_VETOED`) and
    NOT bound — and because an entity hold is not carried forward (there is no
    queue for one; see `extractor_v2.execute_embed`), it is not re-offered either.
    The alias escape below is the one case this band can prove a membership for.

    ⛔ **Except where the group membership of THIS spelling is already proven.**
    `facts["mention_is_alias"]` is the caller's answer to "is the mention's
    folded name already in the target's `aliases[]`?" — and `aliases[]` holds
    exactly the folded spellings `apply_entity_merge` folded INTO this survivor
    (`entity_resolver.apply_entity_merge`), which the tree's own by-name resolver
    treats as a resolution (`$k IN n.aliases`). That is not an inference about the
    group; it is the record of the decision that put this spelling in it. Without
    the escape, every later re-mention of an already-folded variant is refused
    forever — and an entity hold is not carried forward, so the mention is lost
    rather than queued.
    ⛔ **Two facts, and the second is the narrower one.** `mention_is_alias`
    (`aliases[]` only — a spelling a MERGE recorded) stands the three SPELLING
    vetoes down as well as `group_join`, because re-litigating a decided identity
    loses the mention permanently (an entity hold is not carried forward).
    `mention_is_own_name` (the mention is spelled exactly like the target) stands
    `group_join` down only: a same spelling is exactly where two rows can still be
    different entities (`plan` "Acme" vs `org` "Acme"), so the spelling vetoes
    must keep firing.

⚠️ **The guard's other half is positive, and it lives in the merge verb.**
`entity_resolver.apply_entity_merge` closes BOTH legs: the survivor is hopped to
its fixpoint before writing, an already-retired candidate is refused, the
candidate's dependents are re-pointed at the final survivor, and its aliases are
unioned into the survivor's. With that, "no `resolved_from` chain this verb
creates longer than 1" is an invariant rather than a hope, and `dependents >= 1`
is a reliable reading of "this target is a group".

Contradiction test
------------------
**No recorded decision reaches this work.** V1 (`SEARCH-ARCHITECTURE.md` 1-4)
gates the index location; §12.2/§12.2c gate what earns a vector; §14.3 V3 + the
`#11395` KEEP ruling (`6781921075`) gate the embeddings' existence; §2.0 gates
the record/cache split; §7 + §17.4.6 ban the read ledger as an admission-control
signal; `#11901`'s conclusion leaves the window untouched — this module changes
no window, no index, no vector, and reads no ledger. The band structure is new
ground: nothing recorded covers it, so the external convergence (Zep's four
rungs then a select-over-supplied-list prompt, Mem0's boost that never decides
identity) is informative and not authoritative.
"""

from __future__ import annotations

import re

# ── Bands ────────────────────────────────────────────────────────────────

BAND_MERGE = "merge"
BAND_REVIEW = "review"
BAND_NEW = "new"

#: The three bands, and the order a caller should read them in: a `merge` is the
#: only one that writes, a `review` is the only one that waits, a `new` is the
#: only one that creates.
BANDS: tuple[str, ...] = (BAND_MERGE, BAND_REVIEW, BAND_NEW)

# ── Veto codes ───────────────────────────────────────────────────────────
#: Every veto is a REASON a proposal is not a `merge`. The vocabulary is closed
#: and published so a record can be read without the code that wrote it.

VETO_IDENTIFIER_CONFLICT = "identifier_conflict"
VETO_DATE_CONFLICT = "date_conflict"
VETO_KIND_CONFLICT = "kind_conflict"
VETO_TARGET_SUPPRESSED = "target_suppressed"
VETO_TARGET_RETIRED = "target_retired"
VETO_GROUP_JOIN = "group_join"

VETO_CODES: tuple[str, ...] = (
    VETO_IDENTIFIER_CONFLICT,
    VETO_DATE_CONFLICT,
    VETO_KIND_CONFLICT,
    VETO_TARGET_SUPPRESSED,
    VETO_TARGET_RETIRED,
    VETO_GROUP_JOIN,
)

#: Kinds that mean "not stated". `other` is the write-time coalesce default
#: (`projection/entities.py:5086`), so treating it as a value would make the
#: default act as evidence on every entity that never declared a kind.
#:
#: ⛔ `unclassified` is the #1695 classify-later SENTINEL — the S2 prompt
#: *instructs* the model to emit it for pack-domain content, and it is repaired to
#: a core kind later, in `execute_embed`. It therefore reaches the verdict from the
#: raw entity refs, and it means "not yet decided" exactly as `other` does. Left
#: out, it is folded into a DECLARED value and conflicts with every real kind —
#: vetoing every true merge in classify-later mode, and an entity hold is not
#: carried forward, so each mention is dropped.
_UNSTATED_KINDS = frozenset({"", "other", "none", "unknown", "unclassified"})

# ── Token extraction ─────────────────────────────────────────────────────

_SEPARATORS = re.compile(r"[^0-9a-z]+")
#: A token is scanned as a whole: identifier-ness is a property of the TOKEN
#: (`ERR-4032`), never of a fragment (`4032`) — a bare number is a quantity or a
#: date, and folding it to an identifier would veto `the top 3 accounts` against
#: `the top 5 accounts` for the wrong reason.
#: A `#`-prefixed reference (`#11901`) — the corpus's dominant issue/PR spelling.
#: The scanner starts at an alphanumeric, so the `#` is skipped and the digits read
#: as a BARE NUMBER (excluded on purpose: a bare number is a quantity or a date). The
#: reference form is not a bare number: `#11901 plan` and `#11905 plan` name two
#: different issues, and without this they merged with no veto.
_HASH_ID = re.compile(r"#(\d{2,})")
#: A name token, a path, or a hash id — the one scanner `identifier_tokens`,
#: `date_tokens` and `skeleton` all use, so they cannot disagree about what a
#: token is.
_TOKEN_OR_HASH = re.compile(r"#\d{2,}|[A-Za-z0-9][A-Za-z0-9._\-/]*")
def _scan(text: str) -> list[str]:
    """Tokens in ``text``, with a PATH split into its components.

    ⛔ A whole path is one token (the charset includes `/`), and one token
    cannot be both a locator and an identity. Dropping it silenced BOTH vetoes for
    every path pair (`tortoise/extractor_v2.py` vs `.../extractor_v3.py` merged);
    keeping it whole folded two spellings of ONE file into disjoint "identifiers"
    while their skeletons were both ``("<id>",)``, so the `#11901`
    `basename-only` class was vetoed. Components answer both: the directory parts
    are literals and the basename is tokenised like any other name — so two
    spellings of one file stay silent, and two files in the SAME directory veto.
    ⚠️ The directory components must match for the veto: `a/x_v2.py` against
    `b/x_v3.py` is silent by design (the `basename-only` class again).

    A SLASH-WRITTEN DATE (``2026/11``, ``2026/11/12``) is NOT split: it is one
    temporal compound, and splitting it into ``2026`` + ``11`` would delete the
    `year_month`/`date` kinds and the skeleton abstraction with them, so both date
    spellings would merge with no veto (measured before the guard).
    """
    out: list[str] = []
    for tok in _TOKEN_OR_HASH.findall(str(text or "")):
        if ("/" not in tok or tok.startswith("#")
                or _temporal_compound(tok)):
            out.append(tok)
            continue
        # ⛔ A slash-written date INSIDE a path (`blog/2026/11/post.md`) must stay
        # one token: split into `2026` + `11` the `year_month`/`date` kinds vanish,
        # the skeletons then DIFFER on the peeled numbers, and the pair merges with
        # no veto. Re-join any adjacent year/month(/day) run.
        parts = [part for part in tok.split("/") if part]
        i = 0
        while i < len(parts):
            if _YEAR_PART.match(parts[i]) and i + 1 < len(parts) \
                    and _MONTH_PART.match(parts[i + 1]):
                end = i + 2
                if end < len(parts) and _DAY_PART.match(parts[end]):
                    end += 1
                out.append("/".join(parts[i:end]))
                i = end
                continue
            out.append(parts[i])
            i += 1
    return out


#: A `YYYY-MM` (or `YYYY/MM`) month reference, and the `YYYY-Qn` / `Qn-YYYY`
#: compounds. Excluded from identifiers (they are temporal) and compared by
#: `date_tokens`, so `2026-11 report` / `2026-12 report` reports `date_conflict` —
#: the rule that actually fired — instead of `identifier_conflict`.
_YEAR_MONTH_RAW = re.compile(
    r"^(?:1[89]\d\d|2\d\d\d)[-/](?:0?[1-9]|1[0-2])$")
#: `YYYY-Qn` — a QUARTER, not a month. Kept out of `_YEAR_MONTH_RAW` so the two
#: kinds cannot both claim it: filed under `year_month` it made
#: `the 2026-11 report` vs `the 2026/Q4 report` a same-kind contradiction, which
#: is the like-with-like rule this module exists to keep.
_YEAR_QUARTER_RAW = re.compile(r"^(?:1[89]\d\d|2\d\d\d)[-/]q[1-4]$")
#: A full `YYYY-MM-DD` / `YYYY/MM/DD`. Same rule: temporal, not an identifier, so
#: two different dates are reported under the date rule that fired.
_YEAR_MONTH_DAY_RAW = re.compile(
    r"^(?:1[89]\d\d|2\d\d\d)[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])$")
#: The quarter-first spelling of the same compound (`Q3-2026`).
_QUARTER_YEAR_RAW = re.compile(r"^q[1-4][-/](?:1[89]\d\d|2\d\d\d)$")
#: The parts a slash-written date is rebuilt from inside a path (`blog/2026/11/x`).
_YEAR_PART = re.compile(r"^(?:1[89]\d\d|2\d\d\d)$")
_MONTH_PART = re.compile(r"^(?:0?[1-9]|1[0-2])$")
_DAY_PART = re.compile(r"^(?:0?[1-9]|[12]\d|3[01])$")


def _pad_temporal(tok: str, parts: int) -> str:
    """`2026/1` and `2026-01` are the SAME month — zero-pad the value.

    The acceptance patterns deliberately admit both spellings as ONE kind
    (`0?[1-9]`), and then comparing the raw strings reports `2026/1` against
    `2026/01` as a `date_conflict`: the same defect the identifier class was
    cured of, where *"the fold that makes the class measurable also makes its
    variants agree"*. The value a veto compares must therefore be the folded one,
    not the emission-time spelling.
    """
    bits = re.split(r"[-/]", str(tok or "").casefold(), maxsplit=parts - 1)
    if len(bits) != parts:
        return str(tok or "").casefold().replace("/", "-")
    year, rest = bits[0], bits[1:]
    nums = []
    for bit in rest:
        try:
            nums.append(f"{int(bit):02d}")
        except (TypeError, ValueError):
            return str(tok or "").casefold().replace("/", "-")
    return "-".join([year, *nums])


def _date_dimensions(tokens: dict[str, set[str]]) -> dict[str, set[str]]:
    """What the temporal tokens IMPLY, by dimension: ``year``, ``month``, ``day``.

    `date_tokens` keys by the SPELLING's kind, so the recorded reason can name the
    rule that fired. The veto then has to compare what those kinds STATE, because
    kinds alone never meet across a date and a month: `2026-11-12` against
    `2026-12` reached `merge` — they are different kinds, so the per-kind loop
    looked at neither — and merging retires a node for a month the candidate
    explicitly contradicts.

    `quarter` is deliberately absent: a quarter CONTAINS months, so `2026-Q4`
    against `2026-11` states less and contradicts nothing (the like-with-like rule
    `test_a_year_quarter_compound_is_a_DATE_not_an_identifier` pins). A month is
    not containment — `2026-12` against `2026-11-12` says a different month.
    """
    dims: dict[str, set[str]] = {}

    def add(dim: str, value: str) -> None:
        if value:
            dims.setdefault(dim, set()).add(value)

    for tok in tokens.get("year", set()):
        add("year", tok)
    for tok in tokens.get("year_month", set()):
        year, _, month = tok.partition("-")
        add("year", year)
        add("month", month)
    for tok in tokens.get("date", set()):
        year, _, rest = tok.partition("-")
        month, _, day = rest.partition("-")
        add("year", year)
        add("month", month)
        add("day", day)
    for tok in tokens.get("month", set()):
        add("month", tok)
    return dims


def _temporal_compound(tok: str) -> bool:
    """Is ``tok`` a date written as one hyphenated compound?

    One predicate for both halves — `identifier_tokens` must not call it an
    identifier and `skeleton(replace_dates=True)` must abstract it — so the two
    rules cannot disagree about the same spelling and report an `identifier`
    conflict for a date that contradicts. Case-folded here because the callers
    pass the RAW token (`2026-Q3`) while the patterns are written lowercase.
    """
    folded = str(tok or "").casefold()
    return bool(_YEAR_MONTH_RAW.match(folded)
                or _YEAR_QUARTER_RAW.match(folded)
                or _QUARTER_YEAR_RAW.match(folded)
                or _YEAR_MONTH_DAY_RAW.match(folded))
_YEAR = re.compile(r"\b(1[89]\d\d|2\d\d\d)\b")
_QUARTER = re.compile(r"\bq([1-4])\b")
#: Tokens that LOOK identifier-shaped (letter+digit) but are temporal, so they
#: belong to `date_tokens` and not here: `Q3`/`q4` and a bare four-digit year. Left
#: in, `the Q3 plan` complained about a contradicting IDENTIFIER — the right band
#: reached by the wrong reason, and a record whose reason names the wrong rule is
#: worse than no record.
_TEMPORAL_TOKENS = re.compile(r"^(q[1-4]|1[89]\d\d|2\d\d\d)$")
#: Leading articles dropped from a `skeleton`, so the skeleton-equality
#: precondition sees the same name `normalize_entity_name` treats as identical.
_ARTICLES = frozenset({"the", "a", "an"})
_MONTHS = {
    name: idx
    for idx, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def identifier_tokens(name: str) -> set[str]:
    """Identifier-shaped tokens in ``name``, separator- and case-folded.

    A token qualifies when it carries a DIGIT **and** (a letter or a separator) —
    so `ERR-4032`, `v2`, `abc123` and `v2.1` qualify, while `4032`, `billing` and
    `the` do not. A `#`-prefixed reference (`#11901`) qualifies too, folded onto
    its digits: the hash form is identifier-shaped, while the bare digits are a
    quantity or a date. ⛔ A DATE never qualifies — `2026-11`, `2026-11-12`,
    `2026-Q3` and `Q3-2026` are excluded here and compared by `date_tokens`, so a
    date disagreement is recorded under the date rule that fired instead of under
    this one. Folding drops separators and case, which is what makes `ERR-4032`
    and `err4032` the SAME identifier: the #8933 separator class is a *variant*
    class, and a veto that fired across it would refuse exactly the merges the
    fold exists to enable.
    """
    out: set[str] = set()
    text = str(name or "")
    # `#11901` → `11901`: the hash form is identifier-shaped, the bare digits are
    # not (the scanner's note below). Folded onto the digits, so the same reference
    # written with an inner separator still agrees.
    out.update(_HASH_ID.findall(text))
    for tok in _scan(text):
        has_digit = any(ch.isdigit() for ch in tok)
        if not has_digit:
            continue
        has_alpha = any(ch.isalpha() for ch in tok)
        has_sep = any(ch in tok for ch in "-_./")
        if not (has_alpha or has_sep):
            continue
        if _temporal_compound(tok):
            # A date is compared by `date_tokens`, so the record names the rule
            # that fired instead of claiming an identifier conflict.
            continue
        folded = _SEPARATORS.sub("", tok.casefold())
        if not folded or _TEMPORAL_TOKENS.match(folded):
            continue
        out.add(folded)
    return out


def date_tokens(name: str) -> dict[str, set[str]]:
    """Temporal tokens in ``name``, keyed by KIND: ``year``, ``quarter``,
    ``month`` and — when both a year and a quarter are present — ``year_quarter``.

    Keyed by kind on purpose: the veto compares like with like, so `Q3 plan`
    against `2026 plan` is not a conflict (one side states a quarter, the other a
    year; nothing is contradicted), while `Q3 plan` against `Q4 plan` is.

    ⛔ Kind is where a reason is NAMED, not where the comparison ends.
    `_date_dimensions` then compares what the kinds IMPLY (year / month / day),
    because two kinds never meet otherwise: a full date (`date`) and a bare month
    (`year_month`) share no key, so a per-kind loop looked at neither and
    `2026-11-12` against `2026-12` reached `merge` — a retired node for a month the
    candidate contradicts. A month that AGREES still states less and contradicts
    nothing. Values are zero-padded (`_pad_temporal`): the acceptance patterns
    admit `2026/1` and `2026/01` as ONE kind (`0?[1-9]`), so they cannot be two
    values — the class is made measurable by a fold, and its variants must agree.
    """
    text = str(name or "").casefold()
    years = set(_YEAR.findall(text))
    quarters = {f"q{q}" for q in _QUARTER.findall(text)}
    months = {f"{m:02d}" for label, m in _MONTHS.items() if re.search(rf"\b{label}\b", text)}
    out: dict[str, set[str]] = {}
    if years:
        out["year"] = years
    if quarters:
        out["quarter"] = quarters
    if months:
        out["month"] = months
    # `2026-11` / `2026/11`: a month reference, compared by its own kind so a
    # disagreement is reported as a DATE conflict (the rule that fired). `2026-11`
    # against `2026-12` contradicts; `2026-11` against `2026` does not (one side
    # states a month, the other only a year) — the same like-with-like rule as
    # year vs quarter.
    year_months = {tok for tok in _scan(name)
                   if _YEAR_MONTH_RAW.match(tok.casefold())}
    if year_months:
        # Case-folded like every sibling kind: `_YEAR_MONTH_RAW` matched on the
        # folded token, so an un-folded set here made `2026-Q4` vs `2026-q4` a
        # contradiction while `year`, `quarter` and `year_quarter` all agreed.
        # Zero-padded for the same reason, one level down: `2026/1` and `2026/01`
        # are ONE kind by the acceptance pattern, so they cannot be two values.
        out["year_month"] = {_pad_temporal(tok, 2) for tok in year_months}
    year_quarters = {tok for tok in _scan(name)
                     if _YEAR_QUARTER_RAW.match(tok.casefold())}
    if year_quarters:
        out["year_quarter"] = out.get("year_quarter", set()) | {
            tok.casefold().replace("/", "-") for tok in year_quarters}
    quarter_years = {tok for tok in _scan(name)
                     if _QUARTER_YEAR_RAW.match(tok.casefold())}
    if quarter_years:
        out["year_quarter"] = out.get("year_quarter", set()) | {
            "-".join(reversed(tok.casefold().replace("/", "-").split("-")))
            for tok in quarter_years}
    full_dates = {tok for tok in _scan(name)
                  if _YEAR_MONTH_DAY_RAW.match(tok.casefold())}
    if full_dates:
        # Its own kind: `2026-11-12` against `2026-12-12` contradicts, while a full
        # date against a bare month states LESS — so it contradicts nothing when
        # the implied month and year AGREE, and `date_conflict` when they do not
        # (`_date_dimensions` does that comparison; the kind alone never would).
        out["date"] = {_pad_temporal(tok, 3) for tok in full_dates}
    if years and quarters:
        out["year_quarter"] = {f"{y}-{q}" for y in years for q in quarters}
    return out


def skeleton(
    name: str, *, replace_ids: bool = False, replace_dates: bool = False
) -> tuple[str, ...]:
    """The name's folded token sequence, with the named token class abstracted.

    This is what makes the identifier and date vetoes PRECISE rather than
    trigger-happy: the rule is not *"the tokens disagree"* but *"the tokens
    disagree and nothing else does"*. Without it, the `#11901` variant class
    `basename-only` (`tortoise/sdk.py` → `sdk.py`) reads as two disjoint identifier
    sets — a genuine variant vetoed as a conflict — because a path's stem really
    does carry digits. With it, the skeletons differ (`tortoise <ID> py` vs
    `<ID> py`) and the veto stays silent, while `billing v2 ledger` /
    `billing v3 ledger` both reduce to `billing <ID> ledger` and the conflict is
    real.
    """
    out: list[str] = []
    for tok in _scan(name):
        if tok.startswith("#"):
            # A hash reference is an identifier token under `replace_ids`, and its
            # digits otherwise — the same shape `identifier_tokens` produces, so a
            # `#`-id pair whose only difference is the number reduces to equal
            # skeletons and the identifier veto can fire.
            out.append("<id>" if replace_ids
                       else _SEPARATORS.sub("", tok.casefold()))
            continue
        folded = _SEPARATORS.sub("", tok.casefold())
        if not folded:
            continue
        if replace_ids and folded in identifier_tokens(tok):
            out.append("<id>")
        elif replace_dates and (_TEMPORAL_TOKENS.match(folded)
                               or folded in _MONTHS
                               or _temporal_compound(tok)):
            out.append("<date>")
        else:
            out.append(folded)
    # ⛔ A leading ARTICLE is not part of the skeleton (#11901's `article-drop`
    # class, and the same fold `normalize_entity_name` applies for identity).
    # Kept, it made the skeleton-equality precondition fail on the corpus's
    # dominant variant class, so BOTH vetoes went silent exactly where they were
    # needed: `billing v2 ledger` vs `the billing v3 ledger` merged, and so did
    # `the Q3 plan` vs `Q4 plan`, `#11901 plan` vs `the #11905 plan`.
    if out and out[0] in _ARTICLES:
        out = out[1:]
    return tuple(out)


def _fold_kind(kind) -> str:
    """The BARE, case-folded kind — namespace prefix dropped.

    ⛔ Not a detail: `core:person` and `ops:person` are the same kind surface,
    and the tree already keys on the bare form (`extractor_v2._norm_kind`,
    *"The model may emit 'plan' where the backend stores 'core:plan'; both must
    resolve to the same key or link-before-create misses and a duplicate :Object
    is created"*). A veto comparing the SPELLED kind would refuse exactly the
    cross-namespace resolutions the phase-2 prompt exists to decide — measured,
    it turned `tests/test_11653_s3_hold_not_add.py::
    TestTheOtherArms::test_the_resolver_may_still_pick_one_of_the_collision`
    red. The namespace is an emission-time spelling, not identity.
    """
    return str(kind or "").strip().rsplit(":", 1)[-1].strip().casefold()


# ── Vetoes ───────────────────────────────────────────────────────────────


def hard_vetoes(mention: dict, candidate: dict, facts: dict | None = None) -> list[str]:
    """The vetoes that refuse ``candidate`` as the entity ``mention`` names.

    ``mention`` / ``candidate`` are ``{name, kind}`` dicts — the same shape the
    resolution table carries (`extractor_v2._resolution_prompt`). ``facts`` is
    what the graph adds about the CANDIDATE, all optional so a caller with no
    graph read gets the name/kind vetoes only::

        {"retired": bool,        # candidate carries `resolved_from`
         "dependents": int,      # nodes whose `resolved_from` is the candidate
         "suppressed": bool,     # `dedup_reviewed='reject'` naming no survivor
         "mention_is_alias": bool,  # the mention's folded name is one of the
                                    # candidate's `aliases[]` — a spelling a MERGE
                                    # RECORDED, so it stands the three SPELLING
                                    # vetoes AND `group_join` down. ⛔ `aliases[]`
                                    # ONLY: the candidate's own name is the
                                    # separate `mention_is_own_name`, because a
                                    # same spelling is where a `plan` "Acme" and
                                    # an `org` "Acme" are still different
                                    # entities. ⛔ DERIVED by the caller's shim
                                    # (`extractor_v2._verdict_facts`), never by
                                    # the facts reader: only the shim sees the
                                    # mention. A caller that derives it itself must
                                    # honour the same limit — phase 1 does, from
                                    # `mode == "alias"` AND a fold that differs
                                    # from the row's own name, because
                                    # `_absorb_spellings` used to alias every row
                                    # with its own name (round-5 review).
         "mention_is_own_name": bool}  # the mention is spelled exactly like the
                                    # candidate. Not an inference about a group, so
                                    # it stands `group_join` down only — but it is
                                    # not a reviewer's record either, so the
                                    # spelling vetoes still run.

    Returns the veto codes (empty = no objection). **Order is the published
    ``VETO_CODES`` order**, so two runs over the same inputs produce the same
    record — a record whose reason list reorders between runs cannot be compared.
    """
    facts = facts or {}
    got: list[str] = []

    m_ids = identifier_tokens(mention.get("name", ""))
    c_ids = identifier_tokens(candidate.get("name", ""))
    # ⛔ The RECORDED-ALIAS fact stands the three SPELLING vetoes down, because it
    # is the tree's own record that the two spellings are one entity
    # (`apply_entity_merge` writes `aliases[]` only after a reviewer approved the
    # merge). Re-litigating that does not just refuse a merge: an entity hold is not
    # carried forward, so every later re-mention of the alias is held and LOST.
    # Measured on a real graph — `billing v2 ledger` merged into
    # `billing v3 ledger`, then a re-mention of the v2 spelling was vetoed
    # `identifier_conflict` — and it made `_find_existing_entity`'s kind-agnostic
    # alias rung self-defeating, since the rung exists precisely because the kind
    # spelled on a retired variant need not match the survivor's.
    # ⛔ `mention_is_alias` is ALIASES ONLY (`extractor_v2._verdict_facts`). The
    # candidate's OWN name is the separate `mention_is_own_name`, and it must NOT
    # stand these down: a same spelling is where a `plan` "Acme" and an `org`
    # "Acme" are still different entities.
    recorded_identity = bool(facts.get("mention_is_alias"))
    if (
        m_ids
        and c_ids
        and not (m_ids & c_ids)
        and not recorded_identity
        and skeleton(mention.get("name", ""), replace_ids=True)
        == skeleton(candidate.get("name", ""), replace_ids=True)
    ):
        got.append(VETO_IDENTIFIER_CONFLICT)

    m_dates = date_tokens(mention.get("name", ""))
    c_dates = date_tokens(candidate.get("name", ""))
    if not recorded_identity and skeleton(
        mention.get("name", ""), replace_dates=True
    ) == skeleton(candidate.get("name", ""), replace_dates=True):
        # Like-with-like first: the reason then names the rule that fired.
        for kind in ("year_quarter", "quarter"):
            mine, theirs = m_dates.get(kind), c_dates.get(kind)
            if mine and theirs and not (mine & theirs):
                got.append(VETO_DATE_CONFLICT)
                break
        else:
            # Then the dimensions those kinds SHARE. Without this, a full date and
            # a bare month never meet (`2026-11-12` vs `2026-12` merged: different
            # kinds, so the loop above compared nothing) — and merging retires a
            # node for a month the candidate contradicts.
            m_dims = _date_dimensions(m_dates)
            c_dims = _date_dimensions(c_dates)
            for dim in ("year", "month", "day"):
                mine, theirs = m_dims.get(dim), c_dims.get(dim)
                if mine and theirs and not (mine & theirs):
                    got.append(VETO_DATE_CONFLICT)
                    break

    m_kind, c_kind = _fold_kind(mention.get("kind")), _fold_kind(candidate.get("kind"))
    if (
        not recorded_identity
        and m_kind not in _UNSTATED_KINDS
        and c_kind not in _UNSTATED_KINDS
        and m_kind != c_kind
    ):
        got.append(VETO_KIND_CONFLICT)

    if facts.get("suppressed"):
        got.append(VETO_TARGET_SUPPRESSED)
    if facts.get("retired"):
        # D-1 at the candidate-table layer. The S3 guard
        # (`extractor_v2._current_entity_priors`) already rewrites such a row onto
        # its survivor, so this fires only for a table built another way — and
        # when it does, the correct answer is a refusal, not a hop: a caller
        # that reached here lost the evidence the hop needs.
        got.append(VETO_TARGET_RETIRED)
    if (int(facts.get("dependents") or 0) >= 1
            and not facts.get("mention_is_alias")
            and not facts.get("mention_is_own_name")):
        # The alias escape: the survivor's `aliases[]` already records that this
        # exact spelling was folded into it, so no group-level inference is being
        # made. Refusing here would refuse the one case the tree proved, and the
        # refusal is permanent (an entity hold is not carried forward).
        got.append(VETO_GROUP_JOIN)

    return [code for code in VETO_CODES if code in got]


def judge(mention: dict, candidate: dict, facts: dict | None = None) -> dict:
    """The band for one (mention, candidate) proposal.

    ``{"band": ..., "vetoes": [...], "reasons": [...]}`` where ``reasons`` are
    readable sentences — the record a later reader needs in order to know *why*
    without re-running the rule.

    ⛔ A veto produces ``review``, NEVER ``new``: a contradiction between two
    spellings is evidence that they are different, which says nothing about
    whether the entity exists somewhere else. Conflating the two is how a
    precision fix becomes a duplicate-manufacturing fix (§4.4 I1).
    """
    vetoes = hard_vetoes(mention, candidate, facts)
    band = BAND_REVIEW if vetoes else BAND_MERGE
    return {"band": band, "vetoes": vetoes, "reasons": [VETO_REASONS[v] for v in vetoes]}


#: One readable sentence per veto code. Kept beside the codes rather than at the
#: call sites: a second description of the same veto is how two readers start
#: disagreeing about what fired.
VETO_REASONS: dict[str, str] = {
    VETO_IDENTIFIER_CONFLICT: (
        "the two spellings carry disjoint identifier tokens — the identifier is "
        "a contradicting identity, so this is not a merge"
    ),
    VETO_DATE_CONFLICT: (
        "the two spellings carry disjoint date values — for a shared kind, or for "
        "a shared implied dimension (year / month / day) when the kinds do not "
        "meet — so the dates contradict, and this is not a merge"
    ),
    VETO_KIND_CONFLICT: (
        "the two spellings declare different kinds — a typed identity conflict, "
        "so this is not a merge"
    ),
    VETO_TARGET_SUPPRESSED: (
        "the candidate carries a rejection that named no survivor, which "
        "suppresses it outright rather than for one pair"
    ),
    VETO_TARGET_RETIRED: (
        "the candidate carries resolved_from — a merge already retired it, so "
        "binding here would bind to a retired carrier"
    ),
    VETO_GROUP_JOIN: (
        "the candidate is itself a merge survivor, so binding to it asserts "
        "membership of a group this mention was never compared against "
        "(cross-group merge re-verification, M-4) — no alias of the candidate "
        "carries this spelling"
    ),
}


# ── Closure re-verification (M-4) ────────────────────────────────────────


def chain_length(node_id: str, edges) -> int:
    """How many `resolved_from` hops separate ``node_id`` from its fixpoint.

    ``edges`` is an iterable of ``(retired_id, survivor_id)`` pairs. The walk is
    cycle-guarded (`follow_resolved_from`'s own rule, `entity_resolver.py:552`)
    because a corrupt record must not hang a report.

    ⛔ A CYCLE HAS NO FIXPOINT, so it returns ``-1`` rather than a hop count: the
    question is *"how far from the survivor"*, and on a cycle there is no
    survivor. Returning a number there would under-count exactly the corrupt
    record this helper exists to surface (and a caller reading it as a bound would
    be reading a value that means the opposite).

    No production caller: it is the measurement the closure invariant is checked
    with (`apply_entity_merge` enforces the bound by construction), so a lane
    asking "is this chain longer than 1?" reads it against a graph, not a hope.
    """
    survivor_of = {str(a): str(b) for a, b in edges if b is not None}
    current, seen = str(node_id), {str(node_id)}
    hops = 0
    while True:
        nxt = survivor_of.get(current)
        if not nxt:
            return hops
        if nxt in seen:
            return -1
        current, hops = nxt, hops + 1
        seen.add(nxt)


