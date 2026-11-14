"""One name per tier: declared in code, agreed by every surface, pinned (#12444).

The paid mid tier is **sold** as Builder and **identified** as ``pro`` everywhere
the code and the control plane look at it. #4336 (owner-filed; landed 2026-09-21
via PR #4383) fixed that pairing — *"The tier previously called Pro is presented
to users as Builder. The internal tier key stays ``pro``."* — and it named what
must NOT change: the ``pro`` key in ``product/pricing.json``, the Stripe price-id
env keys, and the ``pro`` identifier in ``pricing.py``/``quota.py``/``billing.py``/
``hosted_api.py``.

What #12444 found is that **nothing in ``tortoise/`` stated the pairing.** The
two spellings met only in two hand-maintained JS label maps, so
``docs/product/beta-sdk-surface.md`` could assert *"there is no 'builder' plan"*
for three days AFTER the ruling landed — not a typo at authoring time, but an
unpinned hand-copied sentence whose only correct source was a browser label map.

This module is the pin, in four parts:

1. **The alias exists in code.** :data:`tortoise.pricing.TIER_DISPLAY_NAMES`
   labels every tier ``product/pricing.json`` declares and no tier it does not;
   ``pro`` renders as ``Builder``; an unlabelled tier RAISES rather than letting
   a caller render the raw identifier as if it were the product name.
2. **Every surface agrees with it.** ``website/product.html``'s ``labels`` map and
   the dashboard's ``TIER_LABELS`` must each equal the declared display names —
   turning ``test_website_static.py``'s "two hand-copies agree with each other"
   into "both agree with the artifact", which is the property that was missing.
3. **The key is not renamed.** ``pro`` stays a ``pricing.json`` tier key and
   ``builder`` is NOT one. A rename would break live Stripe price ids, every
   control-plane row carrying ``tier='pro'``, and it is the thing #4336 forbids;
   under the contradiction test the route is a reopen of #4336, never an adoption.
4. **No doc denies the plan exists.** A heuristic lint over ``docs/**`` for the
   measured denial and its close paraphrases. It is a *heuristic*, deliberately:
   it catches the sentence that was actually there and the ones a re-edit would
   reach for, not every possible way to be wrong. That is stated so nobody reads a
   green run as a proof of prose correctness — the pin's job is to make the
   measured drift impossible to reintroduce silently.

Hermetic: file reads plus one lightweight module import (``tortoise.pricing`` has
no heavy dependencies on purpose — see its module docstring). No DB, no graph, no
network, ~0.1 s.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tests.test_website_static import (
    DASHBOARD_PRICING_JS,
    PRICING_JSON,
    PRODUCT_HTML,
    PUBLIC_TIERS,
    _render_pricing_fn,
)
from tortoise.pricing import (
    TIER_DISPLAY_NAMES,
    all_tiers,
    tier_display_name,
    tier_display_names,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"


# ── surface readers ────────────────────────────────────────────────────────
# The two JS maps are hand-maintained copies. These readers are the same shape
# test_website_static.py uses (a lightweight object-literal scrape, no JS
# engine) — kept here so this pin reads every surface it compares rather than
# trusting a notification from the file that owns the surface.


def _html_labels() -> dict[str, str]:
    """``website/product.html`` → ``renderPricing()``'s ``const labels = {…}``."""
    fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
    m = re.search(r"const labels = \{([^}]*)\}", fn)
    assert m, "renderPricing() must define a `labels` display-name map"
    return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))


def _dashboard_labels() -> dict[str, str]:
    """``website/apps/dashboard/src/pricing.js`` → ``export const TIER_LABELS``."""
    js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
    m = re.search(r"export const TIER_LABELS = \{([^}]*)\}", js, re.S)
    assert m, "pricing.js must export TIER_LABELS"
    return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))


# ── 1. the alias is declared, and complete ─────────────────────────────────


class TestTheAliasIsDeclaredInCode:
    def test_pro_is_builder_and_every_other_tier_keeps_its_own_name(self):
        """The pairing #4336 ruled, stated where code can read it."""
        assert tier_display_name("pro") == "Builder"
        # Not a blanket rename: the other tiers' names equal their identifiers.
        for tier in ("free", "solo", "team"):
            assert tier_display_name(tier) == tier.capitalize()

    def test_no_tier_renders_its_raw_identifier_as_its_name(self):
        """No tier may reach a rendered surface named by its raw identifier.

        The failure this prevents is a caller falling back to the identifier, so
        ``pro`` ships to a customer who was sold ``Builder``.

        ⛔ THIS REPLACED A PIN THAT COULD NOT FAIL (cycle-4 review, measured). It read
        ``set(tier_display_names()) == set(all_tiers())`` — TRUE BY CONSTRUCTION, since
        ``tier_display_names()`` BUILDS its dict from ``all_tiers()``; it could only
        fail by raising, which ``test_an_unlabelled_tier_raises_…`` already pins. So it
        tested neither this docstring's property nor anything else. A pin that cannot
        fail is worse than none: it reads as coverage. This arm reds on the shape the
        property is about — a map entry that hands a tier back its own identifier.
        """
        for tier, name in tier_display_names().items():
            assert name, f"tier {tier!r} has an empty display name"
            assert name != tier, (
                f"tier {tier!r} renders its RAW IDENTIFIER as its display name — the "
                f"drift #4336/#12444 exist to prevent: capitalise it or alias it")

    def test_no_phantom_names(self):
        """The map may not carry a tier the artifact does not declare — a stale
        entry is how a retired tier keeps rendering on a surface."""
        assert set(TIER_DISPLAY_NAMES) == set(all_tiers())

    def test_an_unlabelled_tier_raises_instead_of_rendering_its_raw_identifier(self):
        """FAIL-CLOSED, the posture ``tier_limits`` takes on a missing limit key.

        Simulated by adding a tier to the map's source of truth without a name —
        the exact shape of a contributor adding a tier to ``pricing.json`` and
        forgetting the alias.
        """
        from tortoise import pricing as _pricing

        original = dict(_pricing.TIER_DISPLAY_NAMES)
        try:
            del _pricing.TIER_DISPLAY_NAMES["team"]
            with pytest.raises(KeyError) as exc:
                _pricing.tier_display_names()
            assert "team" in str(exc.value), (
                f"the raise must NAME the unlabelled tier, got {exc.value!r}")
        finally:
            _pricing.TIER_DISPLAY_NAMES.clear()
            _pricing.TIER_DISPLAY_NAMES.update(original)

    def test_the_free_fallback_does_not_soften_the_artifact_check(self):
        """The Free fallback covers a tier the map does not KNOW — never a
        half-updated map.

        Pinned because it is the non-obvious half of the contract: an unlabelled
        tier makes the raise fire for EVERY input, including a perfectly valid one,
        since :func:`tier_display_name` resolves through
        :func:`tier_display_names`. That is the fail-closed posture (a load-time
        error, not a silent "Free" on a paid plan), and a caller must code against
        the raise rather than assume the fallback always answers.
        """
        from tortoise import pricing as _pricing

        original = dict(_pricing.TIER_DISPLAY_NAMES)
        try:
            del _pricing.TIER_DISPLAY_NAMES["team"]
            with pytest.raises(KeyError):
                _pricing.tier_display_name("pro")
        finally:
            _pricing.TIER_DISPLAY_NAMES.clear()
            _pricing.TIER_DISPLAY_NAMES.update(original)

    def test_an_unknown_tier_resolves_to_free_like_tier_limits_does(self):
        """A legacy/missing tier must never acquire a grander name than the plan
        it is actually served — the same rule as ``tier_limits``' free fallback."""
        assert tier_display_name("bogus") == "Free"
        assert tier_display_name("") == "Free"


# ── 2. the key is not renamed (#4336) ──────────────────────────────────────


class TestTheIdentifierStaysPro:
    def test_pricing_json_still_keys_the_tier_pro(self):
        tiers = json.loads(PRICING_JSON.read_text(encoding="utf-8"))["tiers"]
        assert "pro" in tiers
        assert "builder" not in tiers, (
            "`builder` must NOT become a pricing.json tier key: the key is the "
            "billing identifier (Stripe price-id env keys, `tier='pro'` control-"
            "plane rows). #4336 keeps `pro`; Builder is the display name. Route a "
            "rename through a reopen of #4336, never through this file.")

    def test_all_tiers_keeps_its_declared_order_and_membership(self):
        """A pin that exists so a key rename reds HERE with the reason, instead of
        only in `test_pricing_tiers.py` as an unexplained list mismatch."""
        assert all_tiers() == ["free", "solo", "pro", "team", "anon"]


# ── 3. every surface agrees with the declaration ───────────────────────────


class TestEverySurfaceAgreesWithTheDeclaration:
    def test_the_dashboard_label_map_equals_the_declaration(self):
        assert _dashboard_labels() == tier_display_names()

    def test_the_marketing_page_label_map_equals_the_declaration(self):
        """``product.html`` renders the public grid only — ``anon`` is the internal
        quota tier and is deliberately absent there (it humanises to Free on the
        dashboard's current-plan card). So: every key it DOES carry must match,
        and it must cover the full public grid."""
        html_labels = _html_labels()
        declared = tier_display_names()

        assert set(html_labels) <= set(declared), (
            f"product.html renders tier(s) {sorted(set(html_labels) - set(declared))} "
            f"that product/pricing.json does not declare")
        for tier, label in html_labels.items():
            assert label == declared[tier], (
                f"`{tier}` renders as {label!r} on product.html but "
                f"{declared[tier]!r} is the declared name — one surface was "
                f"renamed and the other was not (#4336/#12444)")

        assert set(html_labels) == set(PUBLIC_TIERS)

    def test_the_three_surfaces_are_one_mapping(self):
        """The property that was missing: they agree with the ARTIFACT, not merely
        with each other. Two hand-copies can be wrong together — which is exactly
        how the beta SDK doc outlived #4336."""
        declared = tier_display_names()
        dashboard = _dashboard_labels()
        html = _html_labels()
        for tier in html:
            assert html[tier] == dashboard[tier] == declared[tier], (
                f"tier `{tier}`: product.html={html[tier]!r}, "
                f"dashboard={dashboard[tier]!r}, declared={declared[tier]!r}")


# ── 4. no doc may deny the plan exists ─────────────────────────────────────

#: The denial this pin exists for, plus the paraphrases a re-edit reaches for.
#:
#: ⚠️ A heuristic, and stated as one — in BOTH directions, with the measured limits
#: of each written down rather than discovered by the next author:
#:
#:   * **False positives are the expensive half.** A lint that blocks CORRECT docs
#:     is worse than the drift it guards, and it is the half that is easy to get
#:     wrong: two review cycles each found one. The patterns therefore demand an
#:     unqualified assertion of NON-EXISTENCE — not a bare token sequence, and not
#:     a qualified true statement:
#:       * `no Builder tier at all` / `require no Builder tier` / `is needed` — a
#:         statement about a REQUIREMENT, not the plan's absence (cycle 1).
#:       * `there is no Builder plan cheaper than Solo`, `… without unlimited
#:         graphs`, `… in your region` — a QUALIFIED true statement (cycle 2).
#:         Hence :data:`_CLAUSE_END`: the assertion must END its clause.
#:       * `The Builder plan exists` — the AFFIRMATIVE (cycle 2). A denial regex
#:         whose `no` is optional matches the sentence a correct doc must write;
#:         `no` is therefore mandatory in every pattern that spells it.
#:   * **False negatives.** A hard-wrapped denial, or a shape below, slips through;
#:     a green run is not a proof of prose correctness.
#:
#: A sentence recording the RETRACTION is exempt — but only with EVIDENCE that it is
#: a record (:func:`_quotes_a_retracted_claim`). A cue word alone was a bypass: it
#: let `The docs are stale: there is no Builder plan.` escape (cycle 2), and a cue
#: word plus a quoted single word was still one.
_BUILDER_PLAN = r"['\"`]?builder['\"`]?\s+(?:plans?|tiers?)"

#: The assertion must END its clause. Without this the patterns match the HEAD of a
#: qualified true statement ("there is no Builder plan cheaper than Solo"), which is
#: the cycle-2 false block.
#:
#: ⛔ A COMMA IS NOT ALWAYS A CLAUSE END (cycle-4 review, measured). "There is no
#: Builder plan, **only the `pro` identifier** (#4336)" is the #4336 restatement in
#: its most natural form — and a bare `,` in this set flagged it, which is the
#: EXPENSIVE half of the trade (a lint that blocks correct prose is worse than the
#: drift it guards).
#:
#: ⛔ `only|just` ALONE WAS TOO LOOSE (cycle-5 review, measured): it exempted
#: `there is no "builder" plan, only pro and team` — the MEASURED drift reflowed with
#: a comma, i.e. the exact sentence this pin exists for — and `, only a paid one`,
#: `, only the Team plan`. The exception therefore keys on the IDENTIFIER
#: RESTATEMENT: the trailing clause must name `pro`/`builder` **as an identifier**
#: (`identifier`, `key`, `name`, `spelling`, `tier`), or say it IS one. That is the
#: #4336 sentence and nothing else:
#:   * exempt — `…, only the `pro` identifier (#4336)`, `…, it is the `pro` identifier`
#:   * denied — `…, only pro and team` (drift), `…, only a paid one`, `…, only the Team plan`
#: A comma followed by anything else still ends the clause (`…, so a builder buys pro`).
_IDENTIFIER_RESTATEMENT = (
    r"(?:only|just)\s+(?:the\s+)?(?:pro|builder)\s+"
    r"(?:identifier|key|name|spelling|tier)\b"
    r"|it\s+is\s+(?:just\s+)?(?:the\s+)?(?:pro|builder)\s+"
    r"(?:identifier|key|name|spelling|tier)\b"
)
_CLAUSE_END = (r"(?=\s*(?:$|[.;)]|—|–|--|;|,(?!\s*(?:" + _IDENTIFIER_RESTATEMENT + r"))))")

#: A denial is often qualified by a trailing intensifier rather than nothing at all
#: (`… anymore`, `… at all`). Measured false NEGATIVES before this existed: "There is
#: no Builder plan anymore." and "There is no Builder plan any more." both read as
#: ordinary prose and both escaped the guard entirely (cycle-4 review). Kept to a
#: CLOSED set so it cannot become a wildcard that swallows the next qualifier.
_INTENSIFIER = r"(?:\s+(?:any\s?more|anymore|at\s+all|available|for\s+sale))?"

#: …and AFTER an intensifier only TERMINAL punctuation may end the assertion
#: (cycle-5 review, measured). `_CLAUSE_END`'s comma arm re-entered through the new
#: intensifier and false-blocked the SCOPE-qualifier class the module's own doctrine
#: protects — `There is no Builder plan available, so self-hosters use the OSS build.`
#: and `There is no Builder plan at all, in the OSS install.` were both red. A comma
#: after an intensifier qualifies; it does not close.
_CLAUSE_END_STRICT = r"(?=\s*(?:$|[.;)]|—|–|--|;))"

#: Markdown down to prose, applied BEFORE matching. The drift has to be caught in the
#: style the DOC ITSELF uses: this repo renders the tier as ``**Builder**`` and the
#: paragraph this pin was written for says "the **Builder** and **Team** plans".
#: Matching only quoted/backticked spellings missed every emphasised denial —
#: `There is no **Builder** plan.` and `There is no [Builder](url) plan.` both passed
#: (cycle-3 review), which is a false NEGATIVE on the exact sentence a re-edit would
#: write in this file's own dialect.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")   # [text](url) -> text
_MD_EMPHASIS = re.compile(r"[*_`]+")                 # ** * _ __ ` -> nothing


def _plain(sentence: str) -> str:
    """Strip markdown emphasis and link syntax so patterns see the words."""
    return _MD_EMPHASIS.sub("", _MD_LINK.sub(r"\1", sentence))

_DENIAL_PATTERNS = (
    # "there is no 'builder' plan" — asserted, unqualified absence.
    re.compile(r"\bthere(?:'s| is| are)\s+no\s+" + _BUILDER_PLAN + _CLAUSE_END, re.I),
    # …the same arm qualified by a trailing intensifier, where only TERMINAL punctuation
    # may end the assertion (a comma after an intensifier is the scope-qualifier shape).
    re.compile(r"\bthere(?:'s| is| are)\s+no\s+" + _BUILDER_PLAN + _INTENSIFIER +
               _CLAUSE_END_STRICT, re.I),
    # "Tortoise has no Builder plan" / "we offer no Builder tier" — the same asserted
    # absence with the vendor as the subject. Measured false negative (cycle 4).
    re.compile(r"\b(?:has|have|offer|offers|sell|sells)\s+no\s+" + _BUILDER_PLAN +
               _CLAUSE_END, re.I),
    re.compile(r"\b(?:has|have|offer|offers|sell|sells)\s+no\s+" + _BUILDER_PLAN +
               _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "there isn't a builder plan" / "there is not a builder plan" — the same
    # assertion in NEGATED-AUXILIARY form, so `no` never appears and the `there is
    # no` arm cannot reach it. Measured as a false NEGATIVE (review); `not` alone is
    # not a match, the subject must still be bare `there`. Both clause-end forms,
    # matching the convention above: the intensifier variant is TERMINAL-only.
    re.compile(r"\bthere\s+(?:isn't|aren't|is\s+not|are\s+not)\s+an?\s+" +
               _BUILDER_PLAN + _CLAUSE_END, re.I),
    re.compile(r"\bthere\s+(?:isn't|aren't|is\s+not|are\s+not)\s+an?\s+" +
               _BUILDER_PLAN + _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "there exists no builder plan"
    re.compile(r"\bthere\s+exists?\s+no\s+" + _BUILDER_PLAN + _CLAUSE_END, re.I),
    re.compile(r"\bthere\s+exists?\s+no\s+" + _BUILDER_PLAN +
               _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "no 'builder' plan exists" (with an explicit non-existence predicate)
    re.compile(r"\bno\s+(?:such\s+(?:thing\s+as\s+(?:a\s+)?)?)?" + _BUILDER_PLAN +
               r"\s+exists\b", re.I),
    # "no such thing as a builder tier"
    re.compile(r"\bno\s+such\s+(?:thing\s+as\s+(?:a\s+)?)?" + _BUILDER_PLAN, re.I),
    # "the builder plan does not exist"
    # "builder plan doesn't exist" — the contracted form, a measured false
    # NEGATIVE (review): `does|do` + `n't` shares the negated-existence
    # predicate but never spells `not`.
    re.compile(r"\bbuilder\b[^.]{0,40}\b(?:does|do)(?:n't|\s+not)\s+exist\b", re.I),
    # "Builder is not a plan/tier" — predicate nominative, clause-final, so a plan
    # name used as an OBJECT ("the Builder tier is not a tier you can downgrade
    # to") does not match.
    # `isn't` counts: "Builder isn't a plan." is the same predicate nominative and was
    # a measured false negative (cycle 4). It stays clause-final, so a plan name used
    # as an OBJECT ("the Builder tier isn't a tier you can downgrade to") is unmoved.
    re.compile(r"\bbuilder\s+(?:plan\s+|tier\s+)?(?:is|are)(?:n't|\s+not)\s+(?:a\s+)?"
               r"(?:plan|tier)" + _CLAUSE_END, re.I),
    # the measured sentence's own tail: "a builder buys pro or team"
    re.compile(r"\bbuilder\s+buys\s+(?:the\s+)?(?:pro|team|builder)\b", re.I),
)

#: A retraction RECORD, not the claim. `#4336` protects historical records that state
#: the name "as it was at the time", and quoting the superseded claim is this repo's
#: convention for recording a supersession — so a pin that reds that document punishes
#: exactly the behaviour it wants.
_RETRACTION_CUES = re.compile(
    r"\b(?:previously|formerly|used\s+to|no\s+longer|retract\w*|stale\w*|"
    r"corrected|corrects|outdated|supersed\w*|before\s+#\d+)\b", re.I)

#: …but a cue word alone is not a record — it is a bypass: it exempted
#: `The docs are stale: there is no Builder plan.`, a live denial (cycle 2). Exemption
#: requires ONE piece of evidence that the sentence CITES what it retracts: an issue/
#: PR, a date, or an attribution verb introducing a quotation. A quoted single word is
#: NOT enough — the denial's own subject is usually quoted (`"builder"`).
_RETRACTION_EVIDENCE = re.compile(
    r"#\d+"                                                   # cites the retraction
    r"|\b(?:19|20)\d\d-\d\d-\d\d\b"                         # dates the record
    r"|\b(?:read|said|says|claimed|asserted|stated|wrote|worded)\b[^.]{0,40}"
    r"['\"“”`]",                                            # quotes it AS a claim
    re.I)

#: …AND the record must TIE BACK to the denied claim (cycle-5 review, measured). A
#: following fragment carrying a cue and evidence about a DIFFERENT correction exempted
#: a whole document: `There is no Builder plan.` + `The stale `org` wording was
#: corrected in #12444.` returned NO offenders — i.e. the forward window had become the
#: cycle-2 "dress the denial" bypass, two sentences wide. The record must name the
#: builder claim or refer back to the sentence it follows.
_RETRACTION_TIE = re.compile(r"\bbuilder\b|\b(?:it|this|that|these|the\s+above)\b",
                             re.I)


def _quotes_a_retracted_claim(sentence: str) -> bool:
    """Is this sentence a RECORD of the retraction rather than a live denial?"""
    return bool(_RETRACTION_CUES.search(sentence)
                and _RETRACTION_EVIDENCE.search(sentence))


def _denial_in(sentence: str) -> bool:
    """Does ``sentence`` ASSERT that a Builder plan/tier does not exist?

    **THE single decision.** The scan loop and every test below call this, and no
    test may assert against :data:`_DENIAL_PATTERNS` directly. That is a cycle-2
    review finding, and it is the structural half of this pin: the first cut had the
    tests matching the *patterns* while the loop applied the *exemption* at a
    separate call site, so appending `" (stale)"` to every denial in the
    true-positive tuple left the tests green while the loop reported nothing. A pin
    whose tests can diverge from the guard is not a pin.
    """
    if not any(pattern.search(_plain(sentence)) for pattern in _DENIAL_PATTERNS):
        return False
    return not _quotes_a_retracted_claim(sentence)


#: A markdown block that starts a NEW block, so it is not glued onto the previous
#: paragraph (a list item or a heading is its own unit).
_BLOCK_START = re.compile(r"^\s*(?:[#>&\-*+|]|\d+[.)])\s")


def _doc_sentences(text: str) -> list[tuple[int, str]]:
    """(line number, sentence) pairs, with hard-wrapped paragraphs JOINED.

    A markdown hard-wrap is a formatting accident, not a sentence boundary. The first
    cut split per PHYSICAL LINE and never rejoined, so ``_CLAUSE_END``'s ``$`` meant
    "end of this line" — and a correct, qualified statement wrapped just before its
    qualifier was reported as a denial (cycle-3 review):

        "… the fact that there is no Builder plan
         cheaper than Solo on any surface we publish today."

    That is the same false-positive class the patterns were narrowed to fix, arriving
    through formatting, and the reported remedy (weaken a correct sentence) is worse
    than the drift. Lines are therefore accumulated into paragraphs and split on
    sentence punctuation; each sentence reports the line it STARTS on, which is where
    a reader looks for it.
    """
    out: list[tuple[int, str]] = []
    para: list[tuple[int, str]] = []

    def flush() -> None:
        if not para:
            return
        joined = ""
        starts: list[tuple[int, int]] = []   # (char offset, line number)
        for lineno, line in para:
            starts.append((len(joined), lineno))
            joined += line.strip() + " "
        for match in re.finditer(r"[^.!?]+[.!?]*", joined):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            lineno = starts[0][1]
            for offset, line_no in starts:
                if offset <= match.start():
                    lineno = line_no
            out.append((lineno, sentence))

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            flush()
            para = []
            continue
        if para and _BLOCK_START.match(line):
            flush()
            para = []
        para.append((lineno, line))
    flush()
    return out


def _denials_in_document(text: str) -> list[tuple[int, str]]:
    """Every ``(line, sentence)`` in ``text`` that ASSERTS the plan does not exist.

    ⛔ THE SINGLE SCAN — :meth:`TestNoDocDeniesABuilderPlanExists
    .test_no_doc_asserts_a_builder_plan_does_not_exist` and the tests ABOUT the scan
    all call THIS, so a test can never describe a scan the guard does not perform
    (the cycle-2 defect, restated at document granularity).

    The exemption's window is the sentence PLUS its immediately following fragment,
    because :func:`_doc_sentences` starts a new fragment at every block start — a
    fenced block, a blockquote, a list item — so a genuine retraction record routinely
    lands in two pieces: the quoted claim, then the sentence that corrects it.
    MEASURED before this window existed (cycle-4 review): all three shapes of the
    convention this module documents were flagged — ```` ```\nthere is no "builder"
    plan\n``` ````, a `>` blockquote, and an in-line quotation of the superseded claim
    whose split strands the cue in the next fragment. The lint was punishing the very
    behaviour its own exemption was written to protect.

    The window is FORWARD ONLY and is not a concatenation of the window's text: the
    following fragment must ITSELF carry a cue AND evidence
    (:func:`_quotes_a_retracted_claim`) AND tie back to this claim
    (:data:`_RETRACTION_TIE`), so `The docs are stale: <denial>` cannot buy an
    exemption by borrowing a cue from the next sentence, and neither can a genuine
    correction OF SOMETHING ELSE (measured, cycle 5).
    """
    sentences = _doc_sentences(text)
    out: list[tuple[int, str]] = []
    for i, (lineno, sentence) in enumerate(sentences):
        low = sentence.lower()
        if "builder" not in low:
            continue
        if "plan" not in low and "tier" not in low and "buys" not in low:
            continue  # "buys" covers the "a builder buys pro or team" shape
        if not _denial_in(sentence):
            continue
        following = sentences[i + 1][1] if i + 1 < len(sentences) else ""
        if _quotes_a_retracted_claim(following) and _RETRACTION_TIE.search(following):
            continue   # the retraction record's SECOND half, tied to THIS claim
        out.append((lineno, sentence))
    return out


class TestNoDocDeniesABuilderPlanExists:
    def test_the_lint_does_not_flag_correct_prose(self):
        """The false-positive half, pinned — because a lint that blocks correct
        docs is worse than the drift it guards. Both review cycles found one, so
        the cases are grouped by the CLASS each belongs to."""
        correct = (
            # A statement about a REQUIREMENT, not the plan's absence (cycle 1).
            "Self-hosted installs require no Builder tier at all.",
            "There is no Builder plan requirement in the OSS path.",
            "No Builder tier is needed to run the local engine.",
            "No Builder tier is required for self-hosting.",
            # A QUALIFIED true statement (cycle 2).
            "There is no Builder plan cheaper than Solo.",
            "There is no Builder plan without unlimited graphs.",
            "If there is no Builder plan in your region, contact us.",
            "The Builder tier is not a tier you can downgrade to.",
            # The AFFIRMATIVE — the sentence a correct doc must be able to write
            # (cycle 2: an optional `no` made the denial regex match this).
            "The Builder plan exists and is called `pro`.",
            "A Builder tier exists for teams that outgrow Solo.",
            "The Builder plan and the Team plan are both unlimited.",
            # The doc's OWN rendering style, which the lint must not punish.
            "The **Builder** and **Team** plans are both unlimited.",
            "**Builder *is* the `pro` tier** — the identifier is `pro`.",
            "The **Builder** plan exists and is called `pro`.",
            # The #4336 restatement in its most natural form: a comma followed by the
            # IDENTIFIER restatement qualifies the claim instead of ending it (cycles
            # 4 and 5 — `only|just` alone exempted the drift, so the noun is required).
            "There is no Builder plan, only the `pro` identifier (#4336).",
            "Builder isn't a plan, it is the `pro` identifier.",
            # SCOPE-qualified statements behind an intensifier (cycle 5: the intensifier
            # re-admitted the comma arm and red both of these).
            "There is no Builder plan available, so self-hosters use the OSS build.",
            "There is no Builder plan at all, in the OSS install.",
            # ⚠️ DOCUMENTED RESIDUAL (false positive, cycle 4): a comma-SCOPE qualifier
            # still ends the clause, so "There is no Builder plan, and no Team plan
            # either, in the OSS install." is flagged. Tolerated on purpose — every
            # further comma shape that would exempt it (`and no`, `nor`) trades this
            # block for a false NEGATIVE on a live compound denial, and the class that
            # provoked the exception above is the restrictive `only`/`just` restatement,
            # which is measured. The message names the fix ("keep the substance"), so a
            # docs author who hits it is redirected, not blocked by silence.
            # A doc recording the retraction — cue AND evidence (#12444 cited).
            "docs/product/beta-sdk-surface.md previously read 'there is no "
            "\"builder\" plan'; #12444 corrected it.",
            "The denial is no longer present since 2026-09-21.",
            # ⚠️ Neither case above NEEDS the exemption — their plain text raises no
            # `_DENIAL_PATTERNS` hit at all, so deleting `_quotes_a_retracted_claim`
            # wholesale left both green (measured, review). THIS case is the one the
            # pattern DOES match, and only the documented exemption rescues it — it
            # pins the SENTENCE-level path, while
            # `test_a_retraction_record_split_across_blocks_is_exempt` pins the
            # across-fragments one.
            'The doc previously read "There is no Builder plan."; #12444 corrected it.',
        )
        for sentence in correct:
            assert not _denial_in(sentence), (
                f"the lint false-blocks correct prose: {sentence!r}")

    def test_a_retraction_record_split_across_blocks_is_exempt(self):
        """The retraction CONVENTION the exemption exists for, through the REAL scan.

        ⛔ MEASURED DEFECT (cycle-4 review): the false-positive pin called
        ``_denial_in(sentence)`` on whole sentences, while the guard calls
        :func:`_doc_sentences` FIRST — which starts a new fragment at every block
        start. So the split was invisible to the pin and the three shapes below were
        all flagged, i.e. the lint reddened the documented convention of recording a
        supersession. This test runs :func:`_denials_in_document`, the guard's own
        path, so the pin and the scan cannot disagree again.
        """
        # Retraction record — all three shapes of it.
        for record in (
                "Before #12444 this document read:\n\n```\nthere is no \"builder\" plan\n"
                "```\n\n#12444 corrected it.\n",
                "Previously:\n\n> there is no \"builder\" plan.\n\n#12444 corrected it.\n",
                "`docs/product/beta-sdk-surface.md` said \"there is no builder plan.\" "
                "#12444 corrected it.\n",
        ):
            assert not _denials_in_document(record), (
                f"the lint false-blocks a retraction RECORD — the convention this "
                f"module documents — through the guard's own scan: {record!r}")

        # …and the forward window is not a bypass: the SECOND fragment must carry both
        # the cue and the evidence, so a live denial cannot borrow either one.
        for live in (
                "There is no Builder plan.\n\n#12444 tracks the naming.\n",
                "There is no Builder plan.\n\nThat statement was corrected.\n",
                "There is no Builder plan.\n\nThis is stale.\n",
                # Cycle-5 P2: a real retraction record — of a DIFFERENT correction —
                # exempted the live denial above it, two sentences wide.
                "There is no Builder plan.\n\nThe stale `org` wording was corrected "
                "in #12444.\n",
                "There is no Builder plan. See #12444, which corrected the `org` "
                "wording.\n",
        ):
            assert _denials_in_document(live), (
                f"a live denial was exempted by a following fragment that is not a "
                f"retraction record (cue AND evidence required): {live!r}")

    def test_the_lint_does_flag_the_real_denial(self):
        """The true-positive half, pinned so the false-positive fix cannot have
        been bought by disarming the guard."""
        denials = (
            'the tiers are free, solo, pro, team, anon — there is no "builder" '
            "plan; a builder buys pro or team",
            "There is no 'builder' plan.",
            "There's no builder tier.",
            "No builder plan exists.",
            "The builder tier does not exist.",
            "Builder is not a plan.",
            # Intensifier-qualified and vendor-subject denials — measured false
            # NEGATIVES before the arms were widened (cycle-4 review).
            "There is no Builder plan anymore.",
            "There is no Builder plan any more.",
            "Tortoise has no Builder plan.",
            "We offer no Builder tier at all.",
            "Builder isn't a plan.",
            # The drift re-written in this repo's own markdown emphasis style — the
            # style the corrected doc itself uses, so the likeliest re-edit form, and
            # the one the first cut could not see at all (cycle-3 review).
            # The MEASURED drift reflowed with a comma — the shape the loose
            # `only|just` exception exempted, i.e. the P1 of cycle 5. Pinned here so
            # the exception can never widen onto it again.
            'there is no "builder" plan, only pro and team',
            "There is no Builder plan, only a paid one.",
            "There is no Builder plan, only the Team plan.",
            "There is no **Builder** plan.",
            "There is no *Builder* plan.",
            "No **Builder** plan exists.",
            "Builder is not a **plan**.",
            "There is no [Builder](https://example.test) plan.",
            # Negated-auxiliary, existential and contracted-predicate forms —
            # each measured as a false NEGATIVE before the arms above (review).
            "There isn't a Builder plan.",
            "There is not a Builder plan.",
            "There are no Builder plans.",
            "There exists no Builder plan.",
            "Builder plan doesn't exist.",
        )
        for sentence in denials:
            assert _denial_in(sentence), (
                f"the lint MISSES a real denial, so it no longer guards #12444: "
                f"{sentence!r}")

    def test_a_cue_word_alone_cannot_exempt_a_denial(self):
        """The BYPASS, pinned (cycle-2 review). The first cut exempted any sentence
        containing a retraction cue, so the drift was re-introducible by appending
        one word — measured: 3 of 3 real denials escaped. Exemption now requires
        EVIDENCE that the sentence cites what it retracts."""
        for denial in (
            "There is no Builder plan.",
            "No Builder plan exists.",
            "The builder tier does not exist.",
        ):
            for dressed in (
                f"The docs are stale: {denial[0].lower()}{denial[1:]}",
                f"{denial} That statement was corrected.",
                f"{denial} (previously there was one)",
                f"{denial} (stale)",   # the cycle-2 probe, verbatim
            ):
                assert _denial_in(dressed), (
                    f"a cue word exempted a live denial, so #12444's drift is "
                    f"re-introducible by adding one word: {dressed!r}")

    def test_a_hard_wrap_is_not_a_sentence_boundary(self):
        """The lint reads PARAGRAPHS, not physical lines (cycle-3 review).

        Both halves, because one fix cannot be bought with the other: a wrap before
        the qualifier must not invent a denial, and a wrap inside the denial must not
        hide one.
        """
        qualified = (
            "Self-hosting is free, and the hosted grid is explicit about the fact that there is\n"
            "no Builder plan cheaper than Solo on any surface we publish today.\n")
        for lineno, sentence in _doc_sentences(qualified):
            assert not _denial_in(sentence), (
                f"a hard wrap invented a denial at line {lineno}: {sentence!r}")

        denial = "The old doc said there is\nno builder plan.\n"
        assert any(_denial_in(sentence)
                   for _, sentence in _doc_sentences(denial)), (
            "a denial wrapped across two lines is not caught, so a re-edit can \n"
            "reintroduce the drift by rewrapping the paragraph")

    def test_the_tests_assert_the_decision_not_the_patterns(self):
        """The structural half: no test in this class may bypass :func:`_denial_in`.

        Cycle 2 proved this matters — with the tests matching `_DENIAL_PATTERNS`
        directly and the loop applying the exemption at a separate call site,
        appending `" (stale)"` to every denial kept the tests green while the loop
        reported nothing. Assert on the decision, never on its ingredients.

        An **AST walk**, not a source scan: the first cut sliced the file's text from
        the class header to THIS method's own `def`, so (a) a test appended anywhere
        after it escaped the guard entirely and (b) every docstring mentioning a
        pattern name tripped it (cycle-3 review, both measured). Walking the parsed
        class visits every test regardless of position, ignores prose, and can ban the
        exemption's ingredients too.
        """
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef)
                   and node.name == "TestNoDocDeniesABuilderPlanExists")
        banned = {
            "_DENIAL_PATTERNS",          # the raw regexes
            "_RETRACTION_CUES",          # the exemption's cue half
            "_RETRACTION_EVIDENCE",      # …and its evidence half
            "_quotes_a_retracted_claim",  # …and the composed exemption
        }
        offenders: list[str] = []
        for node in cls.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name == "test_the_tests_assert_the_decision_not_the_patterns":
                continue
            referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            referenced |= {n.attr for n in ast.walk(node)
                           if isinstance(n, ast.Attribute)}
            hits = sorted(banned & referenced)
            if hits:
                offenders.append(f"{node.name} -> {hits}")
        assert not offenders, (
            f"test(s) assert against the lint's INGREDIENTS rather than its "
            f"decision (`_denial_in`), so they can stay green while the guard is "
            f"bypassed: {offenders}")

    def test_no_doc_asserts_a_builder_plan_does_not_exist(self):
        offenders: list[str] = []
        for doc in sorted(DOCS_ROOT.rglob("*.md")):
            text = doc.read_text(encoding="utf-8", errors="replace")
            if "builder" not in text.lower():
                continue
            for lineno, sentence in _denials_in_document(text):
                offenders.append(
                    f"{doc.relative_to(REPO_ROOT)}:{lineno}: {sentence.strip()}")
        assert not offenders, (
            "doc(s) assert that a Builder plan does not exist, contradicting #4336 "
            "(Builder IS the `pro` tier) and #12444:\n  "
            + "\n  ".join(offenders)
            + "\n\nFix the naming, keep the substance. A doc that RECORDS the "
              "retraction is exempt — but only with a cue AND a citation of what it "
              "retracts (an issue/PR, a date, or an attribution verb introducing the "
              "quoted claim). A cue word alone does not exempt a live denial.")

    def test_the_beta_sdk_tenancy_passage_keeps_its_substance(self):
        """#12444 corrected the naming in this passage and must not have softened
        the tenancy rule with it: an end-customer is a memory graph inside the
        builder's account, never an organisation account, and the unlimited graph
        count is the proof cited."""
        doc = (DOCS_ROOT / "product" / "beta-sdk-surface.md").read_text(
            encoding="utf-8")
        assert "An end-customer never has an organisation" in doc
        assert "they are a memory graph inside the builder's account" in doc
        assert "max_graphs_per_team" in doc
        # The corrected naming, and the decisions it rests on.
        assert "Builder *is* the `pro` tier" in doc
        assert "#4336" in doc
        assert "#12444" in doc
