"""Deterministic first-stage pre-screen for the banded semantic judge (#5106).

#5085 wired the additive **banded semantic judge** (blind paraphrase probes →
one binary preservation question per unit, repeated in both prompt orders,
earned agreement → the owner's four bands).  The owner's decision comment on
#5085 asked for an optional **deterministic NLI/entailment pre-screen** as the
first stage — the SummaC / SummaC-ZS / SummaC-Conv, FactCC, AlignScore
lineage — **but only if it could be added without a heavy new dependency**.
This module is the design + the implementation of that stage.

## The dependency decision (#5106 D15 — recorded on the issue)

The contradiction test runs FIRST, and the recorded decision bounds what may be
adopted:

* **REFUSED — a new declared transformer dependency** (`torch` /
  `transformers` / `onnxruntime` added to ``pyproject.toml``).  It contradicts
  the owner's ruling outright; the route would be a *reopen*, never an
  adoption.  Nothing here adds a dependency.
* **PERMITTED — reuse of the already-declared ``embeddings`` extra.**  The
  stack is *already in the lock*: `sentence-transformers 5.7.0` pulls
  `torch 2.13.0` + `transformers 5.14.1` (``uv.lock``), and the PRODUCT already
  ships a cross-encoder — ``tortoise/rerank.py::CrossEncoderScorer``, whose own
  docstring records *"ships in the ``embeddings`` extra, NO new third-party
  dependency"*.  An NLI checkpoint loads through that same runtime, so the
  dependency delta is **zero** and the decision's condition is satisfied.  The
  only new artifact is a model-weight download at first use.
* **PERMITTED — a lexical/statistical stage** with no model at all.

The issue's sketch proposed a *new* extra (``nli``).  This module deliberately
does **not** add one: its contents would duplicate ``embeddings`` byte-for-byte
and would be the change's only dependency-surface delta.

## Where the stage sits

``judge.BandedSalienceJudge.judge_units(probes, memory, prescreen=...)`` runs
the screen FIRST and short-circuits **only the two confident ends**:

* ``same_fact``    — the memory states the probe's claim (owner band ≥ 0.80)
* ``likely_not``   — the memory contradicts the probe (owner band < 0.50)

Every other outcome is ``ABSTAIN``, and the unit goes to the LLM judge exactly
as it does today.  **The screen fails OPEN**: a missing extra, an unavailable
model, or any exception inside a stage returns ``ABSTAIN`` for that unit — no
unit is ever dropped, and the arm never fails closed.

Three properties are load-bearing and enforced here rather than promised:

1. **The blindness guard is NOT exempted.**  ``judge_units`` runs ``_guard``
   over the **full** probe dict *before* screening; the screen consumes the
   same probe surface and never sees an anchor.  A leak still fails the run
   (plan §J4).
2. **``likely_not`` requires a CONTRADICTION, not the absence of entailment.**
   The #5106 sketch mapped ``P(entail) ≤ 0.05 → likely_not``.  With a 3-class
   NLI head (*contradiction / entailment / neutral*) low entailment also covers
   **NEUTRAL**, and short-circuiting a neutral verdict to ``likely_not`` is
   exactly "launder an uncertain verdict into a confident band".  So the
   confident-not end requires ``P(contradiction) ≥ 0.95 AND P(entailment)
   ≤ 0.05``.
3. **A model verdict alone cannot buy ``same_fact``.**  NLI detectors
   systematically miss copied-number/arithmetic errors and subtle
   inconsistencies (TRUE arXiv 2204.04991; EMNLP 2023.emnlp-main.105;
   PrefixNLI arXiv 2511.01359), and this corpus's gold deliberately carries
   date/number/name/ownership-critical units where a reworded figure IS
   corruption (#5085's anchor-fidelity split).  Both stages therefore carry a
   deterministic **claim-critical guard**: every number/date/entity token in
   the probe must be present in some memory note before ``same_fact`` may be
   short-circuited.  The model may skip work; a deterministic check must still
   consent.

## Measurement (the acceptance criterion)

The judge **batches**: one call per (session, prompt-order, sample) covers every
probe of that session, so a per-unit screen saves **0 judge calls** unless a
whole session's probes are screened out (then ``orders × samples``).  Its real
savings are prompt tokens + determinism on the confident ends.  ``audit_receipt``
recomputes the screen over any judged receipt and reports units screened, judge
calls removed, probe characters removed, and **agreement** with the judge's
earned band on the short-circuited units — the accuracy-loss number.

## MEASURED — and the measurement says DO NOT ENABLE the ``nli`` stage

Run offline with ``runner prescreen-audit`` over the three judged #5085 receipts
(runs c / d / f; 270 units), on 2026-09-26:

| stage | units screened | of which ``same_fact`` | judge calls saved | agreement with the judge's earned band |
|---|---|---|---|---|
| ``lexical`` | **0 / 270** | 0 | **0** | n/a (never fires) |
| ``nli`` | **71 / 270** | **0** | **0** | **29 / 71 = 0.408** |

The paraphrases are paraphrases: the ``lexical`` stage demands containment 1.0,
and measured mean max-containment per probe is 0.29 (max 0.75 corpus-wide), so
it is **inert by construction** — safe, and worth nothing.

The ``nli`` stage is not merely inert — it is **harmful**.  It fires only ever at
the ``likely_not`` end, and **26 of the 71 units it short-circuits are judged
``same_fact`` by the LLM** (7 + 10 + 9 across c/d/f): a preserved fact would be
recorded as lost.  Two independent causes, both measured:

1. **The two ends are pooled over DIFFERENT pairs.**  ``entail`` is a max over
   the forward (note ⇒ probe) pairs only, while ``contradict`` is a max over
   *both* directions.  In a session of 10–27 notes one note almost always
   supplies a pair the checkpoint calls a contradiction, so ``contradict
   <= 0.05`` — half of the ``same_fact`` conjunction — is essentially
   unsatisfiable: ``same_fact`` fired **0 times in 270 units**.  On run d, the
   same 90 units yielded ``entail >= 0.95`` (forward) for 50 of them, against
   ``contradict >= 0.95`` over the code's **both-directions** pooling for
   **85** — both ends claim nearly the same corpus.  (Forward-only
   contradiction alone already reaches 0.95 on 63 of the 90.)
2. **The default checkpoint confuses NEUTRAL with CONTRADICTION.**
   ``cross-encoder/nli-deberta-v3-small`` scores the textbook neutral pair
   ("The cat is on the mat." / "The dog is in the yard.") as ``contradiction
   0.9998``.  This reproduces through the raw ``transformers`` path with the
   documented ``DebertaV2Tokenizer`` / ``DebertaV2ForSequenceClassification``
   classes and a correct ``id2label``, so it is a property of the weights, not a
   loading or label-order defect.  A probe whose fact was never stored is
   exactly a NEUTRAL pair, so this is the dominant source of the false
   ``likely_not`` verdicts.

Neither cause is fixable by tuning the 0.95/0.05 ends.  And no tuning of any
kind can move the second column that matters: **the judge batches per session,
so the screen saves 0 judge calls** unless a whole session is screened out,
which never happened on this corpus.  Recommended posture: **keep the stage OFF,
reuse it only as an instrument**, and re-measure with ``prescreen-audit`` before
believing any number about it.

Hermetic by construction: importing this module costs no DB/network/LLM; both
model paths are lazy.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from tests.eval.write_path import judge

# ── Stage names (the CLI/receipt vocabulary) ────────────────────────────────

PRESCREEN_OFF = "off"
PRESCREEN_LEXICAL = "lexical"
PRESCREEN_NLI = "nli"
PRESCREEN_NAMES = (PRESCREEN_OFF, PRESCREEN_LEXICAL, PRESCREEN_NLI)

# ── Verdicts ────────────────────────────────────────────────────────────────
# The confident ends reuse the JUDGE's band vocabulary (owner bands, #5085
# D14) — there is ONE declaration of those names, in judge.py.  A second
# vocabulary here would be a duplicated contract that could drift.
ABSTAIN = "abstain"
SAME_FACT = judge.BAND_SAME_FACT
LIKELY_NOT = judge.BAND_LIKELY_NOT
SCREEN_VERDICTS = (ABSTAIN, SAME_FACT, LIKELY_NOT)

# Confidence-end thresholds.  The 0.95/0.05 ends are this issue's own numbers;
# nothing here softens an owner band.  They are the two ends of ONE confidence
# scale, not a threshold per class: ``HIGH`` is the confidence a verdict must
# reach to be short-circuited, and ``LOW`` is the suppression bound the
# OPPOSITE class must stay under at the same time.  ``same_fact`` needs
# ``P(entail) >= HIGH`` AND ``P(contradict) <= LOW``; ``likely_not`` needs
# ``P(contradict) >= HIGH`` AND ``P(entail) <= LOW``.  (Naming them per class
# would be wrong: the low bound applies to entailment in the not-branch and to
# contradiction in the same-fact branch.)
DEFAULT_HIGH_THRESHOLD = 0.95
DEFAULT_LOW_THRESHOLD = 0.05
# The lexical stage's containment floors.  ``same_fact`` demands EVERY probe
# content token be present (containment 1.0): the deliberately one-sided,
# conservative end — a genuine paraphrase has low overlap and therefore
# ABSTAINS to the LLM judge rather than being claimed by the screen.  The
# contradiction end needs the same content words with a flipped polarity.
LEXICAL_SAME_FACT_CONTAINMENT = 1.0
LEXICAL_CONTRADICTION_CONTAINMENT = 0.8
# A probe shorter than this cannot be screened honestly (too few content
# tokens to establish containment) — abstain.
LEXICAL_MIN_PROBE_TOKENS = 3

# The NLI default: an SNLI/MultiNLI cross-encoder with three heads
# (contradiction / entailment / neutral).  It is NOT a dependency — it loads
# through the already-declared ``embeddings`` extra (see the module docstring).
DEFAULT_NLI_MODEL = "cross-encoder/nli-deberta-v3-small"
# Documented label order for the ``cross-encoder/nli-deberta-v3-*`` family.
# The loader prefers the checkpoint's own ``config.id2label`` when the runtime
# exposes it, so a differently-ordered checkpoint cannot be silently misread.
DEFAULT_NLI_LABELS = ("contradiction", "entailment", "neutral")
NLI_MAX_LENGTH = 512

#: #5106 measurement, 2026-09-26 — recomputed OFFLINE with no LLM calls via
#: ``runner prescreen-audit --prescreen nli`` over the three judged #5085
#: receipts (runs c/d/f, 270 units).  It rides in ``audit()`` so it is visible
#: to anyone reading a receipt or an audit report, not only to a reader of this
#: file.  Numbers are point-in-time and checkpoint-specific: re-measure (and
#: update this line) before relying on them for a decision.
NLI_MEASURED_NOTE = (
    "#5106, 2026-09-26, corpus = the 7-session / 90-unit frozen write-path "
    "corpus (#5085 runs c/d/f, 270 units): this stage short-circuited 71/270 "
    "units, ALL at the likely_not end and NONE at same_fact, and saved 0 LLM "
    "judge calls (the judge batches one call per (session, order, sample), so "
    "only a fully screened session removes calls and none ever was). "
    "Agreement with the judge's earned band was 29/71 = 0.408; 26 of the 71 "
    "were judged same_fact by the LLM, i.e. a preserved fact would have been "
    "recorded as lost. Two measured causes: (1) entail is pooled forward-only "
    "while contradict is pooled over both directions, so in a 10-27 note "
    "session a contradictory pair is almost always available and the "
    "same_fact conjunction is unsatisfiable (0 fires / 270 units); (2) the "
    "default checkpoint scores textbook NEUTRAL pairs as contradiction at "
    "~0.999. ENABLING THIS STAGE IS NOT RECOMMENDED — it is retained as an "
    "instrument, off by default, to be re-measured (with --prescreen-model) "
    "before any use."
)

# ``'`` is IN the token class on purpose: a contraction must stay ONE token or
# the contraction cues in ``NEGATION_CUES`` ("won't", "isn't", …) can never
# match — ``"won't"`` would tokenize as ``won``+``t``, the polarity flip would
# be invisible, and a contradiction could be laundered into ``same_fact``.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/'-]*")
#: The SAME token grammar as ``_TOKEN_RE``, scanned with ``IGNORECASE`` over the
#: RAW text so a token's criticality can be judged IN PLACE (``raw[0].isupper()``)
#: without lowercasing first.  Derived from ``_TOKEN_RE.pattern`` so the grammar
#: cannot drift.  The scan is NOT byte-equivalent to ``tokens()`` for the few
#: codepoints whose ``str.lower()`` changes shape (U+0130 ``İ`` is the realistic
#: one) — which is why ``_critical_tokens`` intersects with ``set(tokens(...))``
#: and the guard's invariant then holds BY CONSTRUCTION.
_TOKEN_CASED_RE = re.compile(_TOKEN_RE.pattern, re.IGNORECASE)

# Negation cues: the lexical stage's polarity signal.  A contradiction is
# frequently the SAME content words with the polarity flipped, which is the
# only case the zero-dependency stage is allowed to call confidently.
NEGATION_CUES = frozenset({
    "no", "not", "never", "none", "nobody", "nothing", "neither", "nor",
    "without", "cannot", "cant", "won't", "wont", "don't", "dont", "doesn't",
    "doesnt", "didn't", "didnt", "isn't", "isnt", "wasn't", "wasnt", "aren't",
    "arent", "weren't", "werent", "hasn't", "hasnt", "haven't", "havent",
    "shouldn't", "shouldnt", "wouldn't", "wouldnt", "couldn't", "couldnt",
    "unable", "fail", "failed", "fails", "rejected", "reverted", "abandoned",
    "disabled", "removed", "cancelled", "canceled",
})

# Function words excluded from the CONTENT-token overlap.  The list is
# deliberately small and English-only: over-pruning would let a note with the
# wrong content words reach full containment, which is the failure the
# conservative floor exists to prevent.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "as", "at",
    "by", "for", "from", "in", "into", "of", "on", "onto", "to", "with",
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "has", "have", "had", "it", "its", "this", "that", "these",
    "those", "we", "our", "they", "their", "he", "she", "his", "her", "i",
    "you", "your", "will", "would", "can", "could", "should", "may", "might",
    "must", "so", "because", "when", "while", "after", "before", "also",
})

# Capitalized tokens that are function words or generic role nouns — they are
# not entities, and treating them as claim-critical would make the guard fire
# on ordinary sentence structure.
ENTITY_STOPWORDS = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "we", "they", "it",
    "he", "she", "i", "you", "and", "but", "or", "if", "when", "while",
    "after", "before", "note", "notes", "memory", "user", "assistant",
    "session", "today", "yesterday", "tomorrow", "ok", "okay", "our",
})

# Spelled-out numerals are claim-critical exactly like digits.  The corpus's own
# gold uses them for number-critical units ("batches of five thousand events
# each", "a sixty second backoff") — 18 of the 90 verbatim anchors carry a
# word numeral and NO digit — so a guard that only looked for `str.isdigit()`
# was vacuous on precisely the class it exists to protect.  The list is
# deliberately generous: a false positive only makes the guard STRICTER
# (an extra abstain), which is the fail-open direction.
NUMBER_WORDS = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion", "trillion", "dozen",
    "half", "double", "triple", "quadruple", "quarter",
})


# ── Token helpers (deterministic, dependency-free) ──────────────────────────


def tokens(text: str) -> list[str]:
    """All lowercase tokens (numbers and whole contractions included), in order."""
    # The curly apostrophe is normalized first so ``won’t`` and ``won't``
    # tokenize identically — the corpus and the cues mix both.
    return _TOKEN_RE.findall((text or "").replace("\u2019", "'").lower())


def content_tokens(text: str) -> set[str]:
    """Content tokens = tokens minus the stopword list."""
    return {t for t in tokens(text) if t not in STOPWORDS}


def _critical_sequence(text: str) -> list[str]:
    """The claim-critical tokens of ``text``, IN ORDER (duplicates kept).

    Order matters: a set comparison cannot see a PERMUTATION, and permuting
    entities or numbers reverses attribution while satisfying every set test
    ("Maya owns 20 percent and Priya owns 30 percent" vs the two swapped is not
    the same claim).  The sequence is what ``same_fact`` compares.

    Criticality is decided **in place** on the raw text, and every emitted
    token is checked against ``tokens()`` first, so the guard's invariant —
    ``set(_critical_sequence(t)) <= set(tokens(t))`` — holds BY CONSTRUCTION.
    Extracting spans with their own regexes did not do that: ``"…owned by
    Priya."`` yielded the span ``priya`` while ``tokens()`` yields ``priya.``,
    so the guard abstained even on a byte-identical restatement (42 of the 270
    measured probes carried such a span).  The membership check also absorbs
    the handful of codepoints where a case-insensitive scan and a
    lowercase-first scan disagree (U+0130 ``İ`` is the only realistic one): a
    critical token that ``tokens()`` would never emit can only force a spurious
    abstain.
    """
    normalized = (text or "").replace("\u2019", "'")
    valid = set(tokens(text))
    out: list[str] = []
    for match in _TOKEN_CASED_RE.finditer(normalized):
        raw = match.group(0)
        token = raw.lower()
        if token not in valid:
            continue
        core = token.strip("._/'")
        if not (any(ch.isdigit() for ch in token) or core in NUMBER_WORDS):
            if not raw[0].isupper():
                continue
            if core in ENTITY_STOPWORDS:
                continue
        out.append(token)
    return out


def _critical_tokens(text: str) -> set[str]:
    """The claim-critical tokens of ``text``, as a set (see the sequence form)."""
    return set(_critical_sequence(text))


def numeric_tokens(text: str) -> set[str]:
    """Numeric tokens — digits OR spelled-out numerals — in ``tokens()`` form."""
    return {
        t for t in _critical_tokens(text)
        if any(ch.isdigit() for ch in t) or t.strip("._/'") in NUMBER_WORDS
    }


def entity_tokens(text: str) -> set[str]:
    """Capitalized entity tokens, in ``tokens()`` form, minus generic nouns.

    Sentence-initial capitalization is deliberately **not** skipped wholesale.
    The gold carries names that legitimately open a sentence ("Maya owns the
    rollout"), and dropping those would blind the claim-critical guard to
    exactly the entity swap it exists to catch.  Function words and generic
    role nouns are excluded by ``ENTITY_STOPWORDS`` instead — which is why the
    exclusion is a LIST rather than a positional rule.
    """
    return {t for t in _critical_tokens(text) if not any(ch.isdigit() for ch in t)}


def claim_critical_tokens(text: str) -> set[str]:
    """Numbers + dates + named entities — the unit's corruption-critical span.

    Silently rewording one of these is corruption, not understanding (the
    #5085 anchor-fidelity split), and NLI detectors are known to miss exactly
    this class — so ``same_fact`` may not be short-circuited unless these
    survive verbatim in some note.

    The set is built in the **SAME token vocabulary as ``tokens()``**, because
    the guard compares it against a note's ``tokens()``.  It was not, and the
    guard therefore abstained even on a byte-identical restatement whenever a
    critical span carried trailing punctuation or a letter suffix (``Priya.``,
    ``3am.``, ``95th``) — 42 of the 270 measured probes.  That bug was
    fail-OPEN (it only forced extra abstains, never a wrong band); this makes
    the deterministic consent the docstring promises actually work.
    """
    return numeric_tokens(text) | entity_tokens(text)


def negation_parity(text: str) -> int:
    """0 for an even number of negation cues, 1 for an odd number."""
    count = sum(1 for t in tokens(text) if t in NEGATION_CUES)
    return count % 2


def containment(probe_tokens: set[str], note_tokens: set[str]) -> float:
    """Fraction of the probe's tokens present in the note (0.0 when empty)."""
    if not probe_tokens:
        return 0.0
    return len(probe_tokens & note_tokens) / len(probe_tokens)


#: English INFLECTIONAL suffixes — the ONLY differences ``_is_inflection``
#: forgives.  Derivational endings (``ly``/``er``/``est``/``ion``/``ions``) are
#: deliberately EXCLUDED: they form a DIFFERENT lexeme, so forgiving them
#: re-opened the very hole this guard exists to close — ``mission`` is not
#: ``miss``, and a note that restated the claim and appended ``", no mission"``
#: was scored ``likely_not``, recording a preserved fact as lost.  A narrower
#: suffix set can only cause extra abstains.
_INFLECTION_SUFFIXES = frozenset({"s", "es", "ed", "d", "ing"})


def _is_inflection(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are the same word up to a common suffix.

    Used ONLY by the lexical stage's ``likely_not`` branch, to tell a note that
    restates the probe in another inflection (``locked`` vs ``lock``) from one
    that APPENDS new content (``rollback`` vs ``rollout``).  Both directions
    are accepted, and the test is deliberately narrow — an unrecognised
    inflection merely abstains.
    """
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 3 or not long.startswith(short):
        return False
    return long[len(short):] in _INFLECTION_SUFFIXES


# ── The seam ────────────────────────────────────────────────────────────────


@runtime_checkable
class PreScreen(Protocol):
    """First-stage interface: ``screen(probe, notes) -> verdict``.

    Implementations MUST fail open: any internal failure returns ``ABSTAIN``,
    never a confident band.  ``audit()`` returns a JSON-serializable snapshot
    of bounded counters for the receipt.
    """

    name: str

    def available(self) -> bool: ...

    def screen(self, probe: str, notes: Sequence[str]) -> str: ...

    def audit(self) -> dict: ...


@dataclass
class _Counters:
    """Bounded per-stage bookkeeping (never grows with corpus size)."""

    screened: int = 0
    same_fact: int = 0
    likely_not: int = 0
    abstained: int = 0
    unavailable: int = 0
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "screened": self.screened,
            "same_fact": self.same_fact,
            "likely_not": self.likely_not,
            "abstained": self.abstained,
            "unavailable": self.unavailable,
            "errors": self.errors,
        }


class LexicalPreScreen:
    """Zero-dependency stage: token overlap + negation polarity + claim guard.

    **Deliberately asymmetric.**  It short-circuits ``same_fact`` only when the
    note carries EVERY probe content token *and* every claim-critical token
    *and* the same negation parity — i.e. the note restates the probe's own
    wording.  It short-circuits ``likely_not`` only on an explicit **polarity
    contradiction** (the same content words with the negation flipped).  A
    genuine paraphrase has low lexical overlap and therefore ABSTAINS to the
    LLM judge: the stage is not allowed to guess.

    The embedding-cosine variant of this option is deliberately NOT
    implemented: sentence embeddings score negated pairs at cosine ~0.99
    (PMC9563701) — a contradiction is *maximally* similar by cosine, so the
    signal is unsafe at both confident ends.
    """

    name = PRESCREEN_LEXICAL

    def __init__(self) -> None:
        self._c = _Counters()

    def available(self) -> bool:
        return True

    def audit(self) -> dict:
        return {"name": self.name, "available": True, **self._c.snapshot()}

    def screen(self, probe: str, notes: Sequence[str]) -> str:
        try:
            verdict = self._screen(probe, notes)
        except Exception:  # fail OPEN — never a confident band on an error
            self._c.errors += 1
            verdict = ABSTAIN
        if verdict == ABSTAIN:
            self._c.abstained += 1
        else:
            self._c.screened += 1
            if verdict == SAME_FACT:
                self._c.same_fact += 1
            else:
                self._c.likely_not += 1
        return verdict

    def _screen(self, probe: str, notes: Sequence[str]) -> str:
        probe_content = content_tokens(probe)
        if len(probe_content) < LEXICAL_MIN_PROBE_TOKENS:
            return ABSTAIN
        probe_critical = claim_critical_tokens(probe)
        probe_parity = negation_parity(probe)
        probe_critical_seq = _critical_sequence(probe)
        # The probe's CLAIM content, with negation cues set aside — the thing a
        # contradiction must restate (allowing inflection).
        probe_claim_content = probe_content - NEGATION_CUES
        note_tokens = [set(tokens(note)) for note in notes]
        note_content = [content_tokens(note) for note in notes]
        note_critical_seq = [_critical_sequence(note) for note in notes]
        # Polarity is read off the RAW note, never off a deduplicated token
        # set: folding to a set would collapse a doubled negation ("not …
        # never", an even count) into a single cue and invert the parity.
        note_parity = [negation_parity(note) for note in notes]

        # Confident YES first: one note stating the probe's claim is enough —
        # a second note contradicting it does not un-state the claim.
        for all_tokens, content, critical_seq, parity in zip(
            note_tokens, note_content, note_critical_seq, note_parity, strict=True
        ):
            if containment(probe_content, content) < LEXICAL_SAME_FACT_CONTAINMENT:
                continue
            if not probe_critical <= all_tokens:
                continue  # a number/date/entity did not survive → not confident
            if critical_seq != probe_critical_seq:
                # The note must carry the SAME critical spans IN THE SAME ORDER.
                # A set test is not enough twice over: a note that ADDS one
                # (probe "…twenty seconds…" vs "…twenty five seconds…" extends
                # the numeral without changing any probe token) and a note that
                # PERMUTES them ("Maya owns 20 percent and Priya owns 30" vs
                # the two swapped reverses attribution) both pass a set test
                # while stating a different claim.
                continue
            if parity != probe_parity:
                continue
            return SAME_FACT

        # Confident NO: the same content words with a flipped polarity.
        for all_tokens, content, _critical_seq, parity in zip(
            note_tokens, note_content, note_critical_seq, note_parity, strict=True
        ):
            if containment(probe_content, content) < LEXICAL_CONTRADICTION_CONTAINMENT:
                continue
            if not probe_critical <= all_tokens:
                continue
            if parity != probe_parity:
                # A parity flip is only a contradiction when the note restates
                # the SAME claim with the polarity reversed.  ``negation_parity``
                # reads the WHOLE note, so a note that restates the claim and
                # merely APPENDS an unrelated negation ("… and no errors
                # occurred") also flips the note-wide parity — and calling that
                # ``likely_not`` would record a preserved fact as lost, the
                # exact laundering this stage forbids.  Any content the probe
                # does not carry (allowing inflection) therefore abstains.
                extra = {
                    t for t in (content - NEGATION_CUES)
                    if not any(_is_inflection(t, p) for p in probe_claim_content)
                }
                if extra:
                    continue
                return LIKELY_NOT
        return ABSTAIN


class _NliScorer(Protocol):
    """A loaded 3-class NLI model: ``probabilities(pairs) -> list[dict]``."""

    def probabilities(self, pairs: Sequence[tuple[str, str]]) -> list[dict[str, float]]: ...


class _CrossEncoderScorer:
    """Real scorer over the already-declared ``embeddings`` extra.

    ``premise`` / ``hypothesis`` follow the sentence-transformers pair order.
    The label order is taken from the checkpoint's own ``id2label`` when it is
    exposed, falling back to the documented default — a differently-ordered
    checkpoint can therefore never be silently misread.
    """

    def __init__(self, model_name: str, max_length: int = NLI_MAX_LENGTH) -> None:
        from sentence_transformers import CrossEncoder  # lazy: embedding extra

        self._model = CrossEncoder(model_name, max_length=max_length)
        self.labels = self._resolve_labels()

    def _resolve_labels(self) -> tuple[str, ...]:
        config = getattr(getattr(self._model, "model", None), "config", None)
        id2label = getattr(config, "id2label", None)
        if isinstance(id2label, dict) and id2label:
            try:
                ordered = tuple(
                    str(id2label[i]).strip().lower() for i in sorted(id2label)
                )
            except (KeyError, TypeError):
                ordered = ()
            if set(ordered) == set(DEFAULT_NLI_LABELS):
                return ordered
        return DEFAULT_NLI_LABELS

    def probabilities(self, pairs: Sequence[tuple[str, str]]) -> list[dict[str, float]]:
        import torch  # lazy: embedding extra

        logits = self._model.predict(list(pairs))
        rows = []
        for row in logits:
            tensor = torch.tensor([float(x) for x in row])
            probs = torch.softmax(tensor, dim=0).tolist()
            rows.append(dict(zip(self.labels, probs, strict=True)))
        return rows


class NliPreScreen:
    """Deterministic-ish entailment stage over the declared `embeddings` extra.

    Reads the claim in the only direction that means preservation —
    ``premise = memory note``, ``hypothesis = probe`` — and pools with
    ``max`` over notes (the SummaC-ZS sentence-level pooling), while reading
    contradiction from BOTH directions.  ``same_fact`` additionally requires
    the claim-critical guard; ``likely_not`` requires a contradiction verdict,
    never merely the absence of entailment.

    ⛔ **MEASURED HARMFUL — NOT RECOMMENDED FOR ENABLING.**  Over the three
    judged #5085 receipts (270 units) this stage short-circuited 71 units,
    always at the ``likely_not`` end, **26 of them judged ``same_fact`` by the
    LLM**, and saved **0** judge calls.  Agreement with the judge's earned band
    was 29/71 = 0.408.  The two causes — the forward-only ``entail`` max paired
    against a both-directions ``contradict`` max (making ``same_fact``
    unsatisfiable: 0 fires in 270 units), and the default checkpoint scoring
    NEUTRAL pairs as ``contradiction`` ≈ 0.999 — are recorded in the module
    docstring.  Nothing here is "wrong" in the sense of a crash; it is a stage
    whose confident end is not confident.  It stays OFF by default and exists
    as an *instrument*: point ``--prescreen-model`` at a better checkpoint and
    re-measure with ``runner prescreen-audit`` before trusting any of it.

    **No dependency is declared or required.**  The stage loads through the
    already-declared ``embeddings`` extra; when that extra (or the weights) is
    absent the stage reports ``available: false`` and every unit ABSTAINS to
    the LLM judge.
    """

    name = PRESCREEN_NLI

    def __init__(
        self,
        *,
        model: str = DEFAULT_NLI_MODEL,
        scorer: _NliScorer | None = None,
        high_threshold: float = DEFAULT_HIGH_THRESHOLD,
        low_threshold: float = DEFAULT_LOW_THRESHOLD,
    ) -> None:
        self.model = model
        self.high_threshold = float(high_threshold)
        self.low_threshold = float(low_threshold)
        self._scorer = scorer
        self._attempted = False
        self._load_error: str | None = None
        self._c = _Counters()

    # -- loading (lazy; never raises) ---------------------------------------
    def _load(self) -> _NliScorer | None:
        if self._scorer is not None:
            return self._scorer
        if self._attempted:
            return None
        self._attempted = True
        try:
            self._scorer = _CrossEncoderScorer(self.model)
        except Exception as exc:  # missing extra, no weights, offline, …
            self._load_error = f"{type(exc).__name__}: {exc}"
            return None
        return self._scorer

    def available(self) -> bool:
        return self._load() is not None

    def audit(self) -> dict:
        snapshot = {
            "name": self.name,
            "available": self.available(),
            "model": self.model,
            "high_threshold": self.high_threshold,
            "low_threshold": self.low_threshold,
            # The measurement travels WITH the artifact: a reader of a receipt
            # or an audit report cannot enable this stage without seeing that
            # it was measured and that enabling it is not recommended.
            "recommended": False,
            "measured": NLI_MEASURED_NOTE,
            **self._c.snapshot(),
        }
        if self._load_error is not None:
            snapshot["load_error"] = self._load_error
            snapshot["note"] = (
                "the NLI stage did not load — the ``embeddings`` extra (which "
                "already carries sentence-transformers/torch) is not installed "
                "or the weights are unavailable; every unit ABSTAINED to the "
                "LLM judge (fail-open, no dependency was added)"
            )
        return snapshot

    # -- screening ----------------------------------------------------------
    def screen(self, probe: str, notes: Sequence[str]) -> str:
        scorer = self._load()
        if scorer is None:
            self._c.unavailable += 1
            return ABSTAIN
        try:
            verdict = self._screen(scorer, probe, notes)
        except Exception:  # fail OPEN — never a confident band on an error
            self._c.errors += 1
            verdict = ABSTAIN
        if verdict == ABSTAIN:
            self._c.abstained += 1
        else:
            self._c.screened += 1
            if verdict == SAME_FACT:
                self._c.same_fact += 1
            else:
                self._c.likely_not += 1
        return verdict

    def _screen(self, scorer: _NliScorer, probe: str, notes: Sequence[str]) -> str:
        clean_notes = [n for n in notes if isinstance(n, str) and n.strip()]
        if not clean_notes:
            return ABSTAIN
        # premise = the memory note (the SOURCE), hypothesis = the probe (the
        # CLAIM): only "the note entails the probe" means the claim is stated.
        forward = [(note, probe) for note in clean_notes]
        backward = [(probe, note) for note in clean_notes]
        forward_probs = scorer.probabilities(forward)
        backward_probs = scorer.probabilities(backward)
        entail = max((p.get("entailment", 0.0) for p in forward_probs), default=0.0)
        contradict = max(
            (p.get("contradiction", 0.0) for p in (*forward_probs, *backward_probs)),
            default=0.0,
        )
        if (
            entail >= self.high_threshold
            and contradict <= self.low_threshold
            and self._claim_critical_present(probe, clean_notes)
        ):
            return SAME_FACT
        if (
            contradict >= self.high_threshold
            and entail <= self.low_threshold
        ):
            return LIKELY_NOT
        return ABSTAIN

    @staticmethod
    def _claim_critical_present(probe: str, notes: Sequence[str]) -> bool:
        """The deterministic consent: numbers/entities must survive verbatim."""
        critical = claim_critical_tokens(probe)
        if not critical:
            return True
        return any(critical <= set(tokens(note)) for note in notes)


def build_prescreen(name: str, **kwargs) -> PreScreen | None:
    """Resolve a ``prescreen`` name to a stage (``None`` for ``off``)."""
    if name == PRESCREEN_OFF:
        return None
    if name == PRESCREEN_LEXICAL:
        return LexicalPreScreen()
    if name == PRESCREEN_NLI:
        return NliPreScreen(**kwargs)
    raise ValueError(
        f"unknown prescreen {name!r} — one of {PRESCREEN_NAMES}"
    )


# ── Measurement: recompute the screen over a judged receipt ─────────────────


def _judge_band(unit: dict) -> str:
    """The unit's band as the RECEIPT recorded it (judged, not re-screened)."""
    probability = unit.get("probability")
    if isinstance(probability, (int, float)) and not isinstance(probability, bool):
        return judge.band_for_probability(float(probability))
    return str(unit.get("band") or "")


def audit_receipt(semantic: dict, screen: PreScreen) -> dict:
    """What the screen WOULD have done on a judged receipt (#5106 measurement).

    The reference verdict is the LLM judge's own earned band, so this reports
    the screen's **accuracy loss**: agreement on the units it short-circuits.
    Judge-call savings are computed with the batching rule the judge actually
    uses — one verdict call per (session, prompt-order, sample) — so a partial
    screen saves calls only when a whole session's probes are removed.

    The reference must be **independent**, so it is only valid on a receipt
    produced WITHOUT the pre-screen.  A unit the receipt already short-circuited
    carries the SCREEN's own verdict as its band (``source == "prescreen"``),
    and comparing the re-run against it scores the screen against itself — it
    reports a false 100%.  Such units are excluded from the agreement statistic
    and counted in ``reference_screened_excluded``, with ``reference_note`` set.
    """
    units = semantic.get("units") or []
    memory_by_session = semantic.get("memory_by_session") or {}
    samples = int(semantic.get("samples") or judge.DEFAULT_JUDGE_SAMPLES)
    orders = semantic.get("orders") or list(judge.PROMPT_ORDERS)
    calls_per_session = len(orders) * samples

    screened_units: list[dict] = []
    judged_screened: list[dict] = []
    pre_screened_in_receipt = 0
    per_session: dict[str, list[dict]] = {}
    chars_removed = 0
    for unit in units:
        if not isinstance(unit, dict):
            continue
        session_id = str(unit.get("session_id") or "")
        probe = unit.get("probe")
        notes = memory_by_session.get(session_id) or []
        if not isinstance(probe, str) or not probe:
            verdict = ABSTAIN
        else:
            verdict = screen.screen(probe, notes)
        # The receipt's own provenance: a ``prescreen`` unit's stored band IS
        # the screen's verdict, so it can never serve as an independent
        # reference.
        judge_source = str(unit.get("source") or "judge")
        if judge_source == "prescreen":
            pre_screened_in_receipt += 1
        record = {
            "session_id": session_id,
            "unit_id": unit.get("unit_id"),
            "screen_band": verdict,
            "judge_band": _judge_band(unit),
            "judge_source": judge_source,
        }
        per_session.setdefault(session_id, []).append(record)
        if verdict != ABSTAIN:
            screened_units.append(record)
            chars_removed += len(probe) if isinstance(probe, str) else 0
            if judge_source != "prescreen":
                judged_screened.append(record)

    fully_screened = [
        sid for sid, rows in per_session.items()
        if rows and all(r["screen_band"] != ABSTAIN for r in rows)
    ]
    agreed = [r for r in judged_screened if r["screen_band"] == r["judge_band"]]
    confusion: dict[str, dict[str, int]] = {}
    for record in judged_screened:
        confusion.setdefault(record["screen_band"], {})
        confusion[record["screen_band"]][record["judge_band"]] = (
            confusion[record["screen_band"]].get(record["judge_band"], 0) + 1
        )
    return {
        "screen": screen.name,
        "units_total": len(units),
        "units_screened": len(screened_units),
        "units_screened_fraction": (
            round(len(screened_units) / len(units), 6) if units else None
        ),
        "screened_same_fact": sum(
            1 for r in screened_units if r["screen_band"] == SAME_FACT
        ),
        "screened_likely_not": sum(
            1 for r in screened_units if r["screen_band"] == LIKELY_NOT
        ),
        # Batching: 0 unless a session's probes are ALL screened out.
        "sessions_total": len(per_session),
        "sessions_fully_screened": len(fully_screened),
        "judge_calls_per_session": calls_per_session,
        "judge_calls_saved": len(fully_screened) * calls_per_session,
        "judge_calls_total_without_screen": len(per_session) * calls_per_session,
        "paraphrase_calls_saved": 0,
        "paraphrase_note": (
            "the paraphrase stage still runs for every unit — the screen "
            "consumes the probe, it cannot replace its synthesis"
        ),
        "probe_chars_removed": chars_removed,
        # Agreement is over units the LLM actually judged AND that the re-run
        # screen short-circuited — the accuracy-loss denominator.
        "agreement": round(len(agreed) / len(judged_screened), 6)
        if judged_screened else None,
        "agreement_n": len(agreed),
        "agreement_denominator": len(judged_screened),
        "reference_screened_excluded": len(screened_units) - len(judged_screened),
        "receipt_pre_screened_units": pre_screened_in_receipt,
        "reference_note": (
            "this receipt was produced WITH the pre-screen: units it had "
            "already short-circuited carry the SCREEN's verdict, not the LLM "
            "judge's, and are EXCLUDED from the agreement statistic (scoring "
            "them would report a false 100%). Audit a screen-OFF receipt to "
            "measure accuracy loss."
            if pre_screened_in_receipt else None
        ),
        "confusion": confusion,
        "disagreements": [
            r for r in judged_screened if r["screen_band"] != r["judge_band"]
        ][:20],
        "screen_audit": screen.audit(),
    }
