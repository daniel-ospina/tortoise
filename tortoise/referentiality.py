"""The referentiality test — is this surface a REFERENT or a MENTION? (#11021)

**The one question this module asks:** *does this phrase name something, or does it
only point at something the sentence already said?* A phrase that names something is
an **entity** and may get a node. A phrase that only points is a **mention**: it is
recorded against the claim that used it (``surface`` + ``span`` + ``kind_guess``) and
never promoted to an ``:Object``.

**Why a predicate at all.** ``EXTRACTOR-V4-ARCHITECTURE.md`` §4.2 asks the question and
``vet_gate`` names the reason it has never had an answer: *"it cannot answer 'is this an
entity or a reference?'"*. Without one, every grammatical subject the extraction model
emits is minted — measured 2026-09-24, **62.3 % of Objects are ``^(the )<X>`` definite
descriptions** (4,900 of 7,859). ``apply_vet``'s Rule 2 (*"a referenced entity is never
removed"*) means no discard rule can ever move that share: every extracted Object is
named by the claim that produced it, so the repair is **not creating it in the first
place** (``tests/test_e1_e2_measurement.py`` pins the 0.0). That is this module's job.

**What this module can decide, and what it cannot — stated exactly.**
It decides only the class where **the referent is not recoverable from the phrase at
all**:

- ``referentiality.pronoun`` — a pronoun or a bare demonstrative (``it``, ``they``,
  ``this``, ``those``, ``something`` …). The phrase carries no lexical content; its
  referent lives in the preceding sentence by construction.
- ``referentiality.deictic`` — a determiner plus a purely relational head
  (``the former``, ``the latter``, ``the above``, ``the same``, ``the following`` …).
  Relational by definition: the phrase is *about its own relation to other text*.
- ``referentiality.generic_head`` — a determiner plus a **discourse-generic** head with
  no distinctive modifier (``the thing``, ``the problem``, ``the situation``,
  ``the rest``). A generic head names no referent; any modifier that follows it is what
  would name one, and then the phrase is not this class.

⚠️ **It deliberately does NOT decide ``the <X>`` in general, and it must not.**
``value_gate`` records the measurement that settled this (``#2453``/``#4899``):
``the owner`` is a real entity with no antecedent, while ``the cycle-4 ruling`` is a
definite description that is not one — and the two are **structurally identical**.
Measured on this repo's own labels: ``the admin-merge rail`` (70 references, a
must-keep referent) and ``the parity harness`` (a ``not_an_entity`` row) are both
``the <content-noun>`` with a compound modifier. No surface rule separates them; that
difference is *meaning*, and meaning is the arbiter's (``tortoise/s2_decision_seam.py``,
``#11021``'s ``ref-v1``), never a second classifier invented here. This module is the
**cue stage**: it removes the class that needs no judgement, and hands the rest
untouched to the seam.

⚠️ **It does NOT decide the bare-pointer class either** — a surface that is *nothing
but* an identifier (``#4118``, ``tests/x.py``, ``feat/branch``). ``#2453`` lands that as
a mechanical **``DISCARD``** (``value_gate.RULE_REFERENCE`` / ``RULE_PATH``, ON at the
default since ``#10996``/``#11171``). Duplicating it here would give one surface two
owners deciding two different outcomes, so this module skips any surface the value gate
already claims — see :func:`judge`, which consults that predicate rather than copying it.

**The policy source is a recorded decision, not a preference** (``#11021``'s
``OVERRIDES:`` line): a *surface cue* now feeds a channel decision. ``#1026`` refused
manifest-declared **name patterns that discard artefacts**; nothing is discarded here —
the mention is recorded on its claim — and the cue feeds a selection rather than a
refusal. If ``#1026`` is read as forbidding even the cue, the route is a **reopen of
``#1026``**, not an adoption.

**Fail-open, in the one direction that is safe.** No cue, no mention: the surface is
treated as a REFERENT (it mints) and keeps the gate's verdict. A wrong *mention* loses a referent (the
fearful direction); a wrong *entity* only adds noise. So every rule here demands a
**whole-phrase** match — an unnoticed modifier means the phrase is not this class — and
an unparseable surface is treated as a referent, so it mints.

**Flag.** :data:`ENV_FLAG` — see :func:`enabled`. **Default OFF, and that is a
recorded decision, not caution.** ``#5087``'s plan R1 rules: *"measure on the 25
labelled rows plus the 12 most-referenced rows that a name-pattern rule would have
deleted **before arming**; fail-open to entity until measured"*. That measurement does
not exist yet — ``#10996`` owns the 25-row precision number and this issue owns the
12-row arm, and neither is committed as a run — so arming now would reverse R1. ⚠️ This
is **not** a contradiction of ``#10996``'s corrected precedent (unset must not mean
"never runs"): that flip was legitimate because its rate HAD been measured and committed
(``docs/evidence/9889/e1-gate-drops.json``). The two rules agree once the distinction is
stated — **measured ⇒ arm ; unmeasured ⇒ fail open** — and ``=1`` arms this one for the
run that produces the measurement.
"""

from __future__ import annotations

import os
import re

from .env_truthy import is_truthy

# ── The flag ────────────────────────────────────────────────────────────────

#: The call-time toggle. **Default OFF** — plan R1: *fail-open to entity until
#: measured*, and this cue's own measurement on our data does not exist yet (see the
#: module docstring; ``#10996`` owns the 25-row precision number, this issue owns the
#: 12-row arm). ``=1`` arms it for the run that produces that measurement. The
#: declared truthy contract (``#4097``) decides the value, and the unmeasured default
#: is the fail-open one: everything is an entity, i.e. exactly today's behaviour.
ENV_FLAG = "TORTOISE_REFERENTIALITY"


def enabled() -> bool:
    """Whether the referentiality test runs. **Off** unless the flag arms it."""
    return is_truthy(os.environ.get(ENV_FLAG))


# ── The rule vocabulary (every held mention records which rule held it) ─────

RULE_PRONOUN = "referentiality.pronoun"
RULE_DEICTIC = "referentiality.deictic"
RULE_GENERIC_HEAD = "referentiality.generic_head"
#: NOT a cue: the marker on a mention the extraction MODEL emitted itself. It is
#: declared here because this module owns the vocabulary a held mention carries,
#: and a `by_rule` key nobody declared is a rule that cannot be argued with.
RULE_CHANNEL = "referentiality.channel"

#: Every id a held mention can carry. The three cue rules are tested in this
#: order by :func:`judge`; ``RULE_CHANNEL`` is applied by the caller, never by
#: the cue.
RULES: tuple[str, ...] = (RULE_PRONOUN, RULE_DEICTIC, RULE_GENERIC_HEAD,
                          RULE_CHANNEL)

# ── The declared word sets ──────────────────────────────────────────────────
#
# Each set is CLOSED and small on purpose. A word joins it when its referent is
# not in the phrase: pronouns, relational heads, and discourse-generic nouns. A
# DOMAIN noun is never a member, however unspecific it feels in one sentence —
# `test`, `harness`, `doc`, `rail`, `constraint`, `finding`, `rule`, `gap`,
# `probe` and `criterion` are all real referents in this corpus (`#11021`'s
# 12-row must-keep arm), and adding one to this set is how a lane would start
# deleting them.

#: Personal, possessive, reflexive, indefinite and bare demonstrative pronouns.
#: A demonstrative DETERMINER (`this constraint`) is not here — only the bare
#: form is a pronoun; `this constraint` names one.
PRONOUNS: frozenset[str] = frozenset({
    "i", "me", "my", "mine", "myself",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "we", "us", "our", "ours", "ourselves",
    "they", "them", "their", "theirs", "themselves",
    "this", "that", "these", "those",
    "something", "anything", "everything", "nothing",
    "someone", "anyone", "everyone", "nobody", "somebody", "anybody", "everybody",
    "none", "such",
})

#: Determiners that may open a deictic or generic-head phrase. Possessives are
#: included because `the` is not the only way a definite description is built.
DETERMINERS: frozenset[str] = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "such",
    "some", "any", "all", "both", "each", "every", "no", "another", "other",
    "my", "our", "your", "its", "their", "his", "her", "whose",
    "whatever", "whichever", "which", "what",
})

#: Purely RELATIONAL heads: the phrase is about its own relation to other text.
DEICTIC_HEADS: frozenset[str] = frozenset({
    "former", "latter", "above", "below", "aforementioned", "aforesaid",
    "same", "following", "preceding", "opposite", "reverse",
})

#: Discourse-generic heads — a noun that names a slot in the discourse rather
#: than a thing in the world.
#:
#: ⛔ **Every member is MEASURED, and the excluded ones are named.** The review of
#: this lane found the first draft carrying **domain nouns**, which is the exact
#: direction this module must never fail in: a wrong *mention* loses a referent.
#: Measured over this repo's `docs/**/*.md` + `tortoise/*.py` at ``277a559b1``,
#: case-insensitive, whole-word, for the bare surface `the <head>`:
#:
#: =======================  ======  ==========================================
#: head                     count   verdict
#: =======================  ======  ==========================================
#: ``the point``               893   ⛔ EXCLUDED — `Point` is the core node type
#: ``the question``            398   ⛔ EXCLUDED — a seam question is a typed object
#: ``the number``              357   ⛔ EXCLUDED — almost always "the number of X"
#: ``the step``                311   ⛔ EXCLUDED — a pipeline step is a real unit
#: ``the result``              193   ⛔ EXCLUDED — a measurement is a referent
#: ``the case``                143   ⛔ EXCLUDED — "the case where …" introduces a case
#: ``the time``                103   ⛔ EXCLUDED — a timestamp is a referent
#: ``the part``                 47   ⛔ EXCLUDED — a component is a referent
#: ``the instance``            134   ⛔ EXCLUDED — a deployed instance is a referent
#: ``the detail``               51   ⛔ EXCLUDED — `the detail has label_breaches`
#: ``the thing``               100   ✅ kept — anaphoric to the text, not a thing named
#: ``the problem``             111   ✅ kept — anaphoric to the text
#: =======================  ======  ==========================================
#:
#: The rule the measurement produced: **a head is generic only when the phrase
#: names nothing this corpus has a NODE TYPE for.** Adding `point`, `step`,
#: `result`, `case`, `time`, `number` or `part` back would hold referents in this
#: repo (all of them are ontology-adjacent), and the 25-row label set could not
#: catch it — it has no generic-head row at all.
GENERIC_HEADS: frozenset[str] = frozenset({
    "thing", "things", "stuff", "way", "ways", "kind", "kinds", "sort", "sorts",
    "one", "ones", "rest", "remainder", "lot", "lots", "bit", "bits",
    "piece", "pieces", "amount", "amounts", "situation", "situations",
    "problem", "problems", "matter", "matters", "example", "examples",
    "reason", "reasons", "difference", "differences",
})

#: Modifiers that carry no reference of their own — they may sit between the
#: determiner and the head without making the phrase referential
#: (`the whole thing`, `the main problem`, `the other part`).
GENERIC_MODIFIERS: frozenset[str] = frozenset({
    "whole", "entire", "main", "only", "very", "other", "others",
    "first", "second", "third", "last", "next", "previous",
})

#: Tokens that are stripped from the edge of a surface before judging — the
#: model's own punctuation is not part of the phrase.
_EDGE = " \t\n\r.,;:!?\"'`()[]{}*_—–-"

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d+")


def _tokens(surface: str) -> list[str]:
    """Lower-cased word tokens, edge punctuation stripped.

    A surface that yields no word token at all (`#4118`, `---`) is **not** this
    module's class: it is the value gate's pointer class, or it is unjudgeable,
    and both directions here are "leave it alone".
    """
    return _WORD.findall(str(surface or "").strip(_EDGE).lower().replace("’", "'"))


def judge(surface: object) -> tuple[str, str] | None:
    """Is ``surface`` a MENTION rather than a referent?

    Returns ``(rule_id, reason)`` when the phrase's referent is not recoverable
    from the phrase, and ``None`` when it is a referent (or when this module
    declines to judge). A member of :data:`RULES` is always the first element of
    a returned pair, so a held mention always names the rule that held it.

    **Whole-phrase, never substring.** Each rule below consumes the phrase and
    fails if any token is left over, so an unnoticed modifier makes the phrase
    referential. That is the fail-open direction this module declares: a missed
    mention mints one node (noise), an over-eager one loses a referent.

    **The value gate keeps its own class.** A surface the identifier-only
    predicate would DISCARD is skipped here (see :func:`_claimed_by_value_gate`),
    so one phrase never has two owners deciding two different outcomes.
    """
    tokens = _tokens(str(surface or ""))
    if not tokens:
        return None
    if _claimed_by_value_gate(str(surface)):
        return None

    phrase = " ".join(tokens)

    # 1. A pronoun / bare demonstrative — the whole phrase, alone.
    if len(tokens) == 1 and tokens[0] in PRONOUNS:
        return (RULE_PRONOUN,
                f"pronoun '{phrase}' carries no lexical content — its referent "
                f"is in the surrounding text, not in the phrase")

    # A phrase that does not OPEN with a determiner is a bare name
    # (`config/ci-surfaces.yml`, `Alice`, `guard 2`) — needed for the classes
    # below to be about determiner phrases rather than about names.
    if tokens[0] not in DETERMINERS:
        return None
    rest = tokens[1:]
    if not rest:
        return None

    # 2. A purely relational head: `the former`, `the above`, `the same`.
    #    Judged on the UNSTRIPPED tail — `same` is a head AND could be read as a
    #    modifier, and stripping it first made `the same` unjudgeable.
    if len(rest) == 1 and rest[0] in DEICTIC_HEADS:
        return (RULE_DEICTIC,
                f"'{phrase}' is relational — it names its position in the text, "
                f"not a thing in the world")

    # 3. A discourse-generic head with no distinctive modifier:
    #    `the thing`, `the problem`, `the rest`, `the whole thing`.
    stripped = [t for t in rest if t not in GENERIC_MODIFIERS]
    if len(stripped) == 1 and stripped[0] in GENERIC_HEADS:
        return (RULE_GENERIC_HEAD,
                f"generic head '{stripped[0]}' in '{phrase}' names a slot in the "
                f"discourse, not a referent")

    return None


def _claimed_by_value_gate(surface: str) -> bool:
    """Whether ``#2453``'s identifier-only predicate already owns this surface.

    Consulted, never copied: ``value_gate`` is the single home of that rule
    (``#1509`` §2.4 — no parallel layer doing the same thing), and it returns a
    rule id for a surface that is *nothing but* an identifier. A pointer that
    predicate will DISCARD is not this module's to hold as a mention, and a
    second decision on the same phrase is how two owners disagree.
    """
    from . import value_gate

    return value_gate.identifier_only(str(surface or "")) is not None
