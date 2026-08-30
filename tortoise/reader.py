"""Product reader LLM — the two-phase retrieve-then-read answer surface (#1987).

The product answer surface: an LLM reader that answers questions about
captured memory, built from the LongMemEval benchmarked two-phase reader
(presence-commit → abstain). This module OWNS all reader prompt text and
the reader class — the eval harness (`tools/longmem_eval/reader.py`) is a
thin re-export so prompt drift is impossible by construction (the #1983
inversion pattern applied to the reader). Measurements of the SHIPPED
prompt live in docs/runbook/1987-ask-abstention-check.md (the runbook's
post-#2027 numbers: graded `_abs` 26/30 = 0.867; QA spot-check aggregate
0.38 < 0.8 on the default reader model — deepseek-v4-flash, a
reader-MODEL bound, not a prompt one).

Key contracts:
  * ``LLMReader.answer`` renders the reader context via the PRODUCT
    ``tortoise.retrieval.render_context`` (never an eval import) and makes
    exactly ONE model call (``complete(system, user)`` — model-agnostic).
  * The A1 two-phase abstention clause (#1775) lives in
    ``_ABSTRACTION_FRAGMENT`` and is appended UNIVERSALLY (abstention
    questions are indistinguishable by question_type).
  * ``detect_question_type`` (deterministic, ordered precedence
    temporal-reasoning → knowledge-update → multi-session →
    single-session-preference → None) supplies the type fragments on the
    product path; callers may override with an explicit ``question_type``.
  * ``_looks_abstained`` is the best-effort heuristic abstained label
    (measurement/UX sugar, NEVER a gate — the two-phase prompt is
    authoritative). ``LLMReader.answer`` returns the raw stripped
    completion and labels abstained via ``_looks_abstained`` only —
    blank/whitespace output is NOT substituted inside the reader; the
    canonical ``NO_EVIDENCE_TEXT`` substitution is the SDK/ask-lane
    surface's responsibility (pinned in tests/test_ask_api.py).
  * ``PROBE_SYSTEM`` is the preflight ping prompt (moved from the eval's
    ``tools/longmem_eval/preflight.py``).
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from tortoise.retrieval import render_context

# ═════════════════════════════════════════════════════════════════════════
# ══ READER PROMPT CONSTANTS (OWNED here — the product is the source of
# ══ truth; tools/longmem_eval/reader.py RE-EXPORTS these (the #1987
# ══ inversion); golden-hash pinned in tests/test_reader.py) ══════════════
# ═════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to retrieved memory context "
    "from a user's long chat history. Answer the user's question using ONLY "
    "the provided memory context. The context includes dated chat sessions "
    "and, when applicable, a 'Current Date' header. When the context contains "
    "the information needed to answer, give a direct, concrete answer — do "
    "not hedge, refuse, or say you do not know. Only say you do not know when "
    "the context genuinely lacks the information. Be concise; do not mention "
    "the context."
)

# Type-specific instructions appended to the system prompt for the
# categories that fail at the READER (issue #1366: preference 43%, temporal
# 62% — evidence IS retrieved, the reader hedges/miscounts; A2 #1547: KU
# answers from the superseding point, MSR aggregation needs counting
# discipline). The generic prompt above already flips the default toward
# committing; these fragments give the reader the exact reasoning it must
# perform. A2's two new categories are expressed in ontology terms —
# subject refs (same subject+attribute across entries), supersession edges
# (the [SUPERSEDED BY]/[SUPERSES] markers = CORRECTS) and session dates
# (the (session date YYYY-MM-DD) annotation) — no parallel mechanism, no
# new fields.

_TEMPORAL_FRAGMENT = (
    "\n\nTEMPORAL REASONING INSTRUCTIONS: this question asks about elapsed "
    "time or ordering (e.g. how many days/weeks/months ago). Use the "
    "'Current Date: YYYY-MM-DD' header and the per-session 'session date "
    "YYYY-MM-DD' annotations in the context to compute the elapsed time "
    "between the relevant session date and the current date. Commit to a "
    "specific numeric answer (e.g. '3 days ago') — do not hedge or refuse "
    "when the dated evidence of the event/statement in question is present "
    "in the context. An off-by-one error in the day count is acceptable. "
    "However, if the context contains NO dated evidence of the event or "
    "statement the question asks about, say you do not know rather than "
    "guessing."
)

_PREFERENCE_FRAGMENT = (
    "\n\nPREFERENCE INSTRUCTIONS: this question asks which option the user "
    "prefers. Find the user's turns discussing the options and commit to "
    "the specific option the user stated or implied a preference for "
    "(e.g. 'X is fine but I prefer Y' → Y). Answer with that option — do "
    "not hedge, refuse, or say you do not know when the user's preference "
    "appears in the context."
)

# A2 (#1547): knowledge-update answer-from-newer + the date-conditional
# rule (review P2). Consumes the reader-visible ontology state that E5
# (#1537) + #1367/#1353 already render: the [SUPERSEDED BY]/[SUPERSES]
# markers (CORRECTS edges) and the (session date YYYY-MM-DD) annotations
# (E1). Current-value questions → newest/superseding point; point-in-time
# questions → the version whose session date is the latest on/before the
# asked date (E2E-9 chain-walk), current value rendered as context only.
# V3 restore = E5's chain-walk — NO parallel mechanism, NO valid_at/
# invalid_at windows (E6's, post-baseline). The abstention license (A1
# #1546) stays open for absent versions/dates.
_KNOWLEDGE_UPDATE_FRAGMENT = (
    "\n\nKNOWLEDGE-UPDATE INSTRUCTIONS: this question asks about a fact "
    "that may have changed across sessions. The context can contain several "
    "versions of the same fact (same subject and attribute — e.g. the gym "
    "schedule) linked by supersession edges: the replaced version carries a "
    "'[SUPERSEDED BY: <newer value>]' marker, the newer version carries "
    "'[SUPERSES: <replaced values>]', and every entry is annotated "
    "'(session date YYYY-MM-DD)'.\n"
    "- If the question asks for the CURRENT value (currently / now / these "
    "days), answer from the NEWEST, superseding version — never from a "
    "superseded one. Superseded entries are context only.\n"
    "- If the question asks what the value WAS at a specific date (what "
    "was … at/on/before <date>, back in <month>), answer from the version "
    "whose session date is the latest on or before the asked date — walk "
    "the supersession chain by session date. The current value may be "
    "mentioned only as context, never as the answer.\n"
    "- If no version's session date covers the asked date, or the newest "
    "version is absent, say you do not know rather than guessing."
)

# A2 (#1547): multi-session aggregation discipline — count distinct events
# ONCE (same subject+value restated in a later session is the SAME event),
# no double-count, reconcile conflicts by session date. Consumes the same
# ontology terms as the KU fragment (session-date annotations + supersession
# markers when present). The A1 abstention license stays open for absent
# asked information.
_MULTI_SESSION_FRAGMENT = (
    "\n\nMULTI-SESSION REASONING INSTRUCTIONS: this question spans several "
    "dated sessions in the context. Aggregate across them with counting "
    "discipline:\n"
    "- Count each distinct event or decision ONCE. The same fact restated "
    "in a later session (same subject, same value) is the SAME event — "
    "never double-count it, and do not count mentions as events.\n"
    "- Reconcile by date: when entries conflict, the value from the latest "
    "session date is the current one; earlier entries remain events of "
    "their time (entries may be linked by supersession — '[SUPERSEDED BY: "
    "…]' / '[SUPERSES: …]' markers).\n"
    "- Answer the specific question asked (which/where/when/how many) by "
    "synthesizing from the distinct events — do not dump every entry.\n"
    "- If the asked information is absent from the context, say you do not "
    "know rather than guessing."
)

# A1 (#1546 + #1762 + #1775): the universal partial-knowledge abstention
# clause — appended to EVERY question's system prompt (abstention
# questions are indistinguishable by question_type: the _abs marker lives
# only in the question_id, which never reaches the reader). It lets the
# reader derive unanswerability from the evidence: state what IS present,
# explicitly state the asked info is absent, never commit to a near-miss
# decoy. The commit-side guard keeps the #1366 fix (answer directly when
# the asked fact IS present — never abstain on present evidence).
#
# #1762 (V3 pilot finding #3): the pre-#1762 wording read the commit test
# as a literal-match requirement ("EXACT information" + "do NOT commit to
# the closest matching fact"), so a present-but-differently-phrased value
# was downgraded to "related information" and the reader over-abstained on
# FULL evidence — 4/4 fresh pilot failures were reader-side (6f9b354f:
# evidence_recall@20 = 1.0 yet "does not mention repainting…"; 8a137a7f:
# the gold string "Philips LED bulb" sat inside the hedge).
#
# #1775 (reval3, 2026-08-28): the #1762 calibration reduced but did NOT
# eliminate over-abstention on the hedge class — 4 of 6 reval3 wrongs had
# the gold string VERBATIM in a top-10 context item yet the reader
# abstained/hedged (b86304ba 'worth triple what I paid', 75499fd8 'Golden
# Retrievers like Max', ec81a493, 51a45a95). Root: the clause was a single
# pass where the abstention branch fired on PARTIAL evidence (value
# present, other details missing — "does not mention any painting of a
# sunset, nor the amount paid" while 'worth triple what I paid' sat in
# context). #1775 restructures the clause into an explicit, ORDERED
# two-phase decision: PHASE 1 (PRESENCE COMMIT) commits whenever the asked
# value is stated as the fact in any phrasing — even amid noise or with
# other details of the question missing; PHASE 2 (ABSTENTION) runs ONLY
# when Phase 1 finds no affirmative statement of the value (genuine
# absence, near-miss, decoy). The same-instance scoping keeps the
# different-value guard from licensing abstention on present same-instance
# evidence (review-gate observation on #1768). Oscillation history:
# #1366 → #1546 → #1762 → #1775 (this structural restructure is the
# follow-up the #1768 review gate tracked).
#
# #2027 (2026-08-30, the #1987 gate-d blocker): the QA spot-check scored
# 0.43 (10/21 false abstentions) because the abstention branch fired on the
# GENERIC baseline — the ask lane's detector returned None on 20/21, no
# type fragment engaged, and the reader treated 'no category matched' as
# 'abstain' even when the asked value WAS in context (d6233ab6 wrote 'It
# mentions nostalgic high school experiences (debate team, AP economics),
# but...' THEN abstained; gpt4_8279ba02 quoted the smoker-purchase session
# yet abstained instead of computing the days). #2027 applies the #1775
# two-phase design to the generic path: PHASE 1 now fires on PRESENT
# EVIDENCE regardless of fragment engagement (a category-independent
# presence-commit rule), licenses DERIVED answers (elapsed time, counts,
# totals, ordering) computed from the dated events/facts in context —
# scoped to the asked subject's events actually being present (the
# false-commit guard, 09ba9854_abs — LIVE it still false-commits under
# the compressed branch; the guard holds only on the deterministic fake,
# see the runbook) — and synthesized answers drawn from
# stated preferences; PHASE 2 abstains only on genuine absence (no turn
# mentions the asked subject), never merely because no special
# instructions were attached. The Phase-2 abstention branch was also
# COMPRESSED (#2027 measurement): with a live deepseek-v4-flash probe the
# elaborate evidence-backed abstention template + bicycle exemplar made
# the model over-produce the 'mentions X but does not contain Y' abstention
# form on present evidence (prompt-ablation: the same smoker question
# committed '10 days ago' with the generic prompt and with a compressed
# Phase 2, and abstained with the elaborate Phase 2); the compressed
# branch keeps the genuine-absence abstention form without licensing the
# hedge template — at the measured cost of the near-miss false-commit
# class ((a) 26/30 = 0.867 on the default reader model; the 0.9 figure
# was the pre-compression full-run value — see
# docs/runbook/1987-ask-abstention-check.md; the reader-model trade-off,
# not the clause).
_ABSTRACTION_FRAGMENT = (
    "\n\nPARTIAL-KNOWLEDGE ABSTENTION: decide in two phases — presence "
    "first, abstention only when the asked value is absent.\n"
    "PHASE 1 — PRESENCE COMMIT: First decide whether the context contains "
    "the asked fact — the concrete value the question asks for — not "
    "whether it echoes the question's wording. If the asked value is "
    "stated as the fact the question asks about, in any phrasing, it IS "
    "the answer: answer directly and concretely with it. The value need "
    "not be the sentence's subject: a value predicated of the asked "
    "subject or instance in any grammatical role — subject, object, "
    "complement, or apposition — is the answer. Example: 'Golden "
    "Retrievers like Max' predicates the breed on Max — answer the "
    "breed, do not call it a general statement. Do NOT abstain, do not "
    "hedge, and do not weaken your answer with unrelated material. "
    "Partial evidence is still presence: when the context states the "
    "asked value but omits other details of the question (who, when, why, "
    "how), or carries it amid noise, the answer is in the context — "
    "commit to the value; never abstain because other details are "
    "missing. These instructions apply to every question — whether "
    "or not it matches a recognized category, and whether or not any "
    "additional instructions appear above; the absence of category "
    "instructions is never a reason to abstain. When the question asks "
    "for a derived value — an elapsed time or day count, a date or "
    "order, a count, a total, a price, or a difference — and the "
    "context contains the dated events or stated facts about the asked "
    "subject needed to compute it, the answer is present: commit to "
    "the computed value (an off-by-one in elapsed-day counts is "
    "acceptable), and do not abstain merely because the number is not "
    "literally written. When the question asks what the user prefers, "
    "thinks, or would like, and the context states their relevant "
    "preferences, experiences, or prior choices, answer by drawing on "
    "them — do not abstain because the answer must be synthesized "
    "rather than quoted. For a derived or synthesized answer, still "
    "check the asked subject: commit only when the events or facts "
    "the question asks about are actually in the context; if they are "
    "absent, abstain (Phase 2). A value stated for the same subject or "
    "instance the "
    "question asks about IS the answer; a value merely mentioned "
    "for a different instance or in passing is not the answer. A "
    "mere mention is not the "

    "answer: a negated, rejected, or hypothetical mention, or a "
    "different value for the asked attribute, does not answer the "
    "question and must not be committed to. Never frame the answer value "
    "as merely related information: the 'mentions X but does not contain "
    "the asked information' formulation is forbidden when X is the "
    "answer.\n"
    "PHASE 2 — ABSTENTION (only when Phase 1 found no affirmative "
    "statement of the asked value — and never merely because the "
    "question matched no category or carried no special "
    "instructions): abstain ONLY when no turn in the context mentions "
    "the asked subject or event at all. A different value for the "
    "asked attribute is not the answer; do not commit to a near-miss "
    "decoy. Then simply state that the asked information is absent, "
    "mentioning the related facts found in the memory if any."
)

# question_type → the fragment that unlocks correct reasoning for it.
_TYPE_FRAGMENTS: dict[str, str] = {
    "temporal-reasoning": _TEMPORAL_FRAGMENT,
    "single-session-preference": _PREFERENCE_FRAGMENT,
    "knowledge-update": _KNOWLEDGE_UPDATE_FRAGMENT,
    "multi-session": _MULTI_SESSION_FRAGMENT,
}

#: The canonical no-evidence answer text (NEW product code, #1987 Task 1 —
#: pinned to the A1 abstention phrasing). NOT substituted by the reader —
#: ``LLMReader.answer`` returns the raw stripped completion and labels
#: abstained via ``_looks_abstained``; the substitution is the
#: SDK/ask-lane surface's responsibility (pinned in tests/test_ask_api.py).
#: The ``abstained`` label is best-effort heuristic sugar — the two-phase
#: prompt is authoritative.
NO_EVIDENCE_TEXT = (
    "The memory context does not contain the information needed to answer "
    "this question."
)


def build_reader_user_message(evidence: str, question: str) -> str:
    """The reader's user-message template (#1987 Task 5) — single-sourced
    so the SDK local lane and ``LLMReader.answer`` share ONE copy (no
    parallel template drift: the eval measures ``LLMReader.answer``, the
    product ships ``sdk.ask``)."""
    return f"Memory context:\n{evidence}\n\nQuestion: {question}\n\nAnswer:"

#: Official gen.py default generation length for non-CoT runs (the reader's
#: answer prompt is answered at temperature 0, max_tokens 500 — the official
#: call shape, no JSON mode; see ``build_reader_model``).
DEFAULT_READER_MAX_TOKENS = 500

#: M2 pre-flight ping prompt (moved from tools/longmem_eval/preflight.py —
#: the product owns it now; the eval preflight imports it from here).
PROBE_SYSTEM = (
    "You are the story summarizer for a durable memory. Read the conversation "
    "and produce a concise narrative digest capturing what CHANGED — the "
    "decision, the state change, the durable belief — and why. Keep the "
    "digest under 120 words; do not mention the instructions."
)


def reader_prompt_constants() -> tuple[str, dict[str, str]]:
    """The reader prompt constants (M5): the generic system prompt + the
    universal A1 abstention clause + type fragments, recorded verbatim in
    report methodology so prompt drift across run cells is human-visible in
    the report. The automated drift signal (reader_prompt_hash, run.py-owned)
    now covers the A1 clause too (#1773 closure)."""
    return _SYSTEM_PROMPT, {
        **dict(_TYPE_FRAGMENTS),
        # the universal clause appended to EVERY question's prompt; keyed
        # 'abstention' (the A1 name, #1546) — not a question_type
        "abstention": _ABSTRACTION_FRAGMENT,
    }


def system_prompt_for(question_type: str | None) -> str:
    """The reader system prompt for a question, type-tailored.

    Unknown/absent types get the hardened generic prompt; temporal-reasoning,
    single-session-preference (issue #1366), knowledge-update and
    multi-session (A2 #1547) append their reasoning instructions. A1
    (#1546 + #1762 + #1775): the partial-knowledge abstention clause is
    appended UNIVERSALLY — abstention questions are indistinguishable by
    question_type (the _abs marker lives only in the question_id, which
    never reaches the reader), so the reader must derive unanswerability
    from the evidence, never from a flag. The clause is a two-phase
    decision (#1775): PHASE 1 commits whenever the asked value is stated in
    the context (partial evidence is still presence); PHASE 2 abstains only
    on genuine absence.
    """
    return (_SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT
            + _TYPE_FRAGMENTS.get(question_type, ""))


# ── Deterministic question-type detection (Task 2) ─────────────────────────

#: TR rules (ordered): elapsed-time ("how many X ago"), "how many/long X",
#: and a between-range ("between … and …"). "how many X ago" is high-
#: precision; "was … at/before <date>" WITHOUT elapsed-time → KU.
_TR_PATTERNS = (
    re.compile(r"\b\d+\s+(days?|weeks?|months?|years?)\s+ago\b"),
    re.compile(r"\bhow (many|long)\s+(days?|weeks?|months?|years?)\b"),
    re.compile(r"\bbetween\b.{1,60}\band\b"),
)

#: KU rules: a current-value marker OR a past-tense + at/on/before/back
#: in/during + optional ONE intervening month word + a 4-digit year.
#: The relaxed form ("before March 2025") admits one month word; the bare
#: year form ("before 2025") still matches. Month-only dates ("before
#: March", no 4-digit year) deliberately fall through to None.
_KU_PATTERNS = (
    re.compile(r"\b(currently|these days|at present|right now)\b"),
    re.compile(
        r"\b(was|were|did)\b.{0,80}\b(at|on|before|back in|during)\b"
        r"(?:\s+[A-Z][a-z]+)?\s*\d{4}"),
)

#: MS rules: cross-session / aggregation markers.
_MS_PATTERNS = (
    re.compile(
        r"\b(across sessions|over time|how many times|did you ever|"
        r"have you ever|throughout)\b"),
)

#: SSP rules: preference language.
_SSP_PATTERNS = (
    re.compile(
        r"\b(prefer|preference|preferred|favorite|which (option|one)|"
        r"choose|rather|like (better|most|best))\b"),
)


def detect_question_type(question: str | None) -> str | None:
    """Deterministic, ordered precedence type detection (Task 2, #1987).

    Precedence: temporal-reasoning → knowledge-update → multi-session →
    single-session-preference → None. Returns one of the 4 fragment types
    or None (the generic baseline). ``question`` may be None/empty →
    None.
    """
    if not question or not str(question).strip():
        return None
    q = str(question)
    if any(p.search(q) for p in _TR_PATTERNS):
        return "temporal-reasoning"
    if any(p.search(q) for p in _KU_PATTERNS):
        return "knowledge-update"
    if any(p.search(q) for p in _MS_PATTERNS):
        return "multi-session"
    if any(p.search(q) for p in _SSP_PATTERNS):
        return "single-session-preference"
    return None


# ── Best-effort abstained label (Task 2) ───────────────────────────────────

#: Abstained phrases — a STRICT SUPERSET of judge.py's abstention
#: vocabulary (``_ABSTRACTION_MARKERS`` ⊆ this list). Vocabulary-only claim:
#: matching is clause-scoped, so a later-clause marker may label differently
#: from the judge's whole-answer match — the product label's committed-hedge
#: class (#2027: "…though I do not know if it changed") stays NOT abstained
#: even when the judge's whole-answer pass would flag the phrase.
_ABSTAINED_PHRASES: tuple[str, ...] = (
    "do not know", "don't know", "not know", "unanswerable", "incomplete",
    "cannot answer", "can't answer", "not enough", "does not contain",
    "doesn't contain", "not mention", "not mentioned", "no mention of",
    "asked information is absent", "information is absent", "no memory",
    "no information", "nothing related", "unable to answer", "not sure",
    "unsure",
    # #2027 (calibration): the compressed Phase-2 abstention branch's
    # canonical phrasings on genuine absence (also the judge-marker
    # vocabulary — judge ⊆ product, plan P2-32).
    "don't have that information", "don't have information",
    "absent from the context",
)


def _looks_abstained(answer: str | None) -> bool:
    """Best-effort heuristic abstained label (measurement/UX sugar — NEVER
    a gate; the two-phase prompt is authoritative). Blank/whitespace output
    is ALWAYS abstained (the deterministic case — substituted with
    ``NO_EVIDENCE_TEXT`` upstream)."""
    if answer is None:
        return True
    text = str(answer).strip()
    if not text:
        return True
    low = text.lower()
    # Clause-scoped matching (P2): a raw whole-answer substring match
    # over-labels COMMITTED answers that carry a trailing qualifier
    # ("The gym schedule is Monday, though I do not know if it changed.",
    # "…though the context does not mention his age.") as abstained — the
    # exact hedge class #2027 fought. The two-phase model emits abstention
    # as the answer's OPERATIVE clause: the whole-answer form (a marker in
    # the FIRST clause) or the Phase-2 template "[related facts], but the
    # asked value is absent" (a LATER clause referencing the asked
    # subject / absence — #2027 canonical: 'absent from the context',
    # 'asked information is absent'). Match the first clause, or a later
    # clause that references the abstention's subject ("asked"/"absent")
    # or a FINAL-clause flat refusal ("I don't know."/"I cannot answer.")
    # — a trailing confidence hedge ("though…"/possessive attribute)
    # never labels abstained.
    clauses = [c for c in re.split(r"[,;:.!?—]+", low) if c]
    if not clauses:
        # separator-only output (".", "...", "!?", "—"): no clause to
        # match — NOT abstained (preserves pre-cycle-2 behavior; a crash
        # here would escape sdk.ask()'s documented Raises contract).
        return False
    if any(p in clauses[0] for p in _ABSTAINED_PHRASES):
        return True
    for c in clauses[1:]:
        if not any(p in c for p in _ABSTAINED_PHRASES):
            continue
        if "asked" in c or "absent" in c:
            return True
        # A later-clause marker WITHOUT the "asked"/"absent" anchor: a
        # genuine whole-answer abstention only when the marker clause is
        # the FINAL clause and the answer is dominated by the abstention
        # form — a flat refusal ("I don't know.") or a clause referencing
        # the asked subject ("it does not contain the color"). NOT a
        # committing-hedge qualifier (#2027): a "though"-attached hedge
        # ("…though I do not know if it changed since") or a possessive-
        # attribute reference ("the context does not mention his age").
        # The possessive carve-out is NARROW: it exempts only the #2027
        # hedge shape — a possessive paired with "not mention"/"not
        # mentioned"/"does not contain"/"doesn't contain" ("the context
        # does not mention his age"). A flat refusal that merely mentions
        # a possessive ("I don't know the date of his birth.") is NOT exempt.
        possessive_hedge = bool(
            re.search(r"\b(?:his|her|its|their)\b", c)
            and re.search(r"(?:not mention|not mentioned|does not contain|doesn't contain)", c))
        if c == clauses[-1] and "though" not in c and not possessive_hedge:
            return True
    return False


class Reader(Protocol):
    model_id: str
    model_spec: str | None = None
    provider: str | None = None
    pinned: bool | None = None

    def answer(self, *, context_hits: list[dict[str, Any]], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str: ...

    def ping(self, probe: str) -> str: ...


class LLMReader:
    """Reader backed by an OpenAI-compatible chat model.

    Model-agnostic via ``complete(system, user)`` — the ask lane supplies
    ``build_reader_model()`` (tortoise/model_adapters.py); the eval lane
    keeps its ``OpenAICompatModel`` wiring. Renders the reader context via
    the PRODUCT ``tortoise.retrieval.render_context`` (never an eval
    import). Carries its resolved identity — ``model_spec`` (the full
    ``<provider>:<model>`` spec actually used), ``provider`` (the resolved
    endpoint provider) and ``pinned``.
    """

    def __init__(self, model, model_id: str, *, model_spec: str | None = None,
                 provider: str | None = None, pinned: bool | None = None):
        self._model = model
        self.model_id = model_id
        self.model_spec = model_spec or model_id
        self.provider = provider
        self.pinned = pinned

    def answer(self, *, context_hits: list[dict[str, Any]], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str:
        # The context carries the official gen.py shape: a "Current Date:
        # {question_date}" header + per-session date annotations (see
        # tortoise.retrieval.render_context) — temporal-reasoning questions
        # are structurally unanswerable without them (P1 #1144). The system
        # prompt is type-tailored (#1366): temporal-reasoning and
        # single-session-preference get reasoning instructions that counter
        # the reader's documented hedging/miscounting failures.
        context = render_context(context_hits, question_date=question_date)
        user = build_reader_user_message(context, question)
        raw = self._model.complete(
            system=system_prompt_for(question_type), user=user)
        return raw.strip()

    def ping(self, probe: str) -> str:
        """Minimal transport-health probe (M2 #1523 pre-flight).

        One tiny completion through the reader's OWN model (key + endpoint +
        model health only — prompt/context rendering is exercised on question
        1). HTTP status errors propagate for classification; preflight.py
        maps them via the P2 taxonomy.
        """
        raw = self._model.complete(system=PROBE_SYSTEM, user=probe)
        return raw.strip()
