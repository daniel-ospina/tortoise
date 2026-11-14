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
#:   * **False negatives — MEASURED, not assumed, and stated so the next author does
#:     not mistake this for a prover.** A green run is not a proof of prose
#:     correctness; these shapes are known to escape:
#:       * a denial whose CONTINUATION LINE STARTS A NEW BLOCK — `>` in a blockquote,
#:         `-`/`*` in a list, `|` in a table (`_doc_sentences` opens a fragment there, so
#:         `_denial_in` never sees the two halves together); a plain WORD-WRAP is caught,
#:         and `test_a_hard_wrap_is_not_a_sentence_boundary` pins that it is — an earlier
#:         revision of this list said "a hard-wrapped denial", which measurement refutes
#:         and that test contradicts (cycle-11 P2, measured);
#:       * `You cannot buy Builder.` / `Builder is not a purchasable plan.` — an
#:         inability phrasing with no `no`/`not exist` predicate;
#:       * a denial whose assertion is closed by `—`/`,` AFTER an intensifier
#:         (`…no Builder plan available — so self-hosters…`): the intensifier arms use
#:         the strict clause end, and the scope-qualifier twin of that same sentence is
#:         indistinguishable by this heuristic, so the block was paid for with the miss.
#:         `;` belongs here too as of cycle 11 — see `_CLAUSE_END_STRICT` — so a
#:         `;`-closed denial IS a miss, which is what this bullet always claimed;
#:       * a TEMPORAL qualifier FOLLOWED BY A CLAUSE BREAK (`… no Builder plan today,
#:         and the roadmap is unaffected`). ⛔ A clause-FINAL `yet`/`today`/`currently`
#:         is CAUGHT, not missed, and a cycle-19 revision of this bullet said the
#:         opposite: all three sit in `_INTENSIFIER_WORDS` beside `anymore`, whose
#:         behaviour they share (measured cycle 20: `There is no Builder plan today.`
#:         and `… anymore.` both flag, while `… today, and the roadmap…` and
#:         `… on the roadmap.` both escape). So what is left here is the SAME
#:         clause-break miss as the bullet above, plus the no-break scope spelling
#:         `… no Builder plan on the roadmap` — listed because this list is claimed to
#:         be MEASURED rather than complete (cycle-19 P2, re-measured cycle 20);
#:       * an ANAPHORIC re-assertion after a quoted record (`Previously the doc read
#:         "there is no Builder plan"; there is no such plan anymore.` / `…; that plan
#:         does not exist.`): :func:`_asserts_a_denial_outside_the_quotes` asks whether
#:         the remainder names `builder` WITH a negation, and a pronoun does not. The
#:         twin that REPEATS the noun is flagged (`…; today there is no Builder plan
#:         either.`) and is pinned live, so the residual is the anaphoric form only;
#:       * a BACKTICK-quoted mention (`` `there is no Builder plan` ``): `_plain` strips
#:         backticks before the spans are taken, so the mention reads as an assertion —
#:         true on `origin/main` too, and pre-existing; the double/typographic-quote
#:         mentions ARE exempt as of cycle 19 (see :func:`_quotes_the_whole_denial`);
#:       * an ATTRIBUTED quotation carrying NO retraction marker (`The docs say "there is
#:         no Builder plan".`, `docs/product/beta-sdk-surface.md says "there is no Builder
#:         plan".`): the cue-free mention exemption cannot tell a doc that QUOTES the
#:         current page from one that quotes a superseded claim, so both are exempt. This
#:         is the false-PASS cost of the cycle-19 exemption and it is written down here
#:         because the list is claimed to be MEASURED (round-3 review). Of the five
#:         mandated-green mention forms only this one lacks a marker (forbidden /
#:         removed / correction / `before #N`), so requiring one would close it — at the
#:         price of re-blocking an ordinary attribution, which is why the trade is named
#:         rather than taken;
#:       * a MIXED quotation pair (`The doc read “there is no Builder plan".`, one ASCII
#:         and one typographic mark): :data:`_QUOTE_PAIRS` pairs same-direction marks
#:         only, so no CLOSED span exists — but an UNCLOSED span does, opened by the
#:         typographic mark, and `_quotes_the_whole_denial`'s `match.start() <= start + 2`
#:         tolerance accepts it, so the mention is EXEMPT (green) BOTH here and at
#:         `origin/main` (cycle-20 round 3, measured: `_denial_in` is False at both).
#:         ⛔ An earlier revision of this bullet called it a false POSITIVE that was “RED
#:         here, `origin/main` green”; the unclosed-span tolerance removed that and the
#:         bullet was not updated. Recorded as a miss, not a block;
#:       * a PARENTHETICAL-aside record whose aside is COMMA-delimited —
#:         `Previously the doc said, until #12444 corrected it, that there is no Builder
#:         plan.` — BLOCKED here while `origin/main` exempts it (cycle-20 rounds 3–4,
#:         measured). ⛔ THE MECHANISM IS NOT THE CLAUSE SPLIT, and a round-3 revision of
#:         this bullet said it was: the reported arm never RUNS on this sentence, because
#:         `_REPORTED_CLAIM`'s tail is `\b\s+(?:that\s+)?` and the verb here is followed
#:         by `,` — `_REPORTED_CLAIM.search` returns None, so no clause is ever cut (the
#:         split is only a SECOND blocker, shown by patching the verb tail alone). So the
#:         round-3 conclusion that it is NOT fixable did not follow either: a cue-gated
#:         PAIRED comma pre-strip — the same device as :data:`_REPORTED_ASIDE`, applied to
#:         the whole fragment before the verb scan — was measured to exempt this record
#:         while leaving `… wrong, there is no Builder plan.` FLAGGED, with 0 pins
#:         reddened and 0 `docs/` offenders. It is NOT taken here because it moves the
#:         strip outside the reported arm (where the `and`/set arithmetic no longer bounds
#:         it), and that is a change that deserves its own cycle and its own pins. Listed
#:         as a false positive on correct prose, and recorded as a CHOICE, not a
#:         necessity;
#:       * an APOSTROPHE pseudo-span in the CUE arm: two ordinary apostrophes (an
#:         elision `'90s` opening, a plural possessive `customers'` closing) produce a
#:         CLOSED span, and a cue word beside it exempts the drift —
#:         `The docs are stale: since the '90s there is no Builder plan, and the customers'
#:         rows are wrong.` is EXEMPT here (cycle-20 round 5, measured: `_denial_in` is
#:         False). ⛔ IT IS NOT PRE-EXISTING, and a round-3/4 revision of this bullet said
#:         it was: `origin/main` returns **True** for that sentence (main has no apostrophe
#:         spans at all), so this hole was INTRODUCED by the span scanner this PR adds and
#:         a main-comparison sweep WILL see it — the earlier "so pre-existing" reasoning
#:         was inverted. The
#:         CUE-FREE mention path does NOT share this hole: it requires the denial to OPEN
#:         the span, which that pseudo-span fails (four pins). Two ordinary apostrophes
#:         were a false PASS on the MENTION path for one round and ARE closed there;
#:       * a following fragment that records a correction in a `That was corrected in
#:         #N.` shape (pronoun + auxiliary): :data:`_RETRACTION_TIE` requires the pronoun
#:         at a clause boundary, so a genuine record in that phrasing would be BLOCKED —
#:         a false positive of the bounded kind, which is why it is written down here.
#:       * a noun-before-the-name denial whose name is NOT clause-final
#:         (`There is no plan called Builder in Tortoise.`): that arm pays `_CLAUSE_END`
#:         because its scoped twin (`… called Builder on the self-hosted install.`) is
#:         indistinguishable here and is a TRUE statement (cycle 8);
#:       * a sentence that REPORTS an old denial and then re-asserts one WITHIN THE SAME
#:         CITED sentence (`Previously the page stated there is no Builder plan;
#:         #12444 tracks the correction, and today there is no Builder plan either.` —
#:         cycle-20 round 5, measured: EXEMPT here and at `origin/main`): the exemption
#:         is per SENTENCE, so a reported clause and a live one sharing one sentence are
#:         not separated here. ⛔ THE CLASS IS NARROWER THAN “IT CITES”, which a later
#:         revision of this bullet over-claimed by naming
#:         `Previously the page stated there is no Builder plan per #12444, and today
#:         there is no Builder plan either.` as the example: that sentence is FLAGGED at
#:         this head and was exempt only under `5cf012d2a53e`'s 6-character offset, so the
#:         example had been closed and the bullet was not updated. What escapes needs cue +
#:         citation AND a reported denial that ENDS ITS CLAUSE before the first break. ⛔ THE COST IS THE CITATION, NOT THE SEPARATOR (cycle-14 P2):
#:         UNCITED, both the reported and the `;`-split twin are FLAGGED — and the
#:         QUOTED twin that REPEATS THE NOUN is FLAGGED too as of cycle 18, where it used
#:         to be exempt (the quotation stood in for the citation;
#:         :func:`_asserts_a_denial_outside_the_quotes` now requires the citation when the
#:         sentence also asserts outside the quote, and both twins are pinned live).
#:         ⛔ "THE QUOTED TWIN IS FLAGGED" IS TOO WIDE AS STATED (cycle-19 P2, measured):
#:         the ANAPHORIC form escapes — see the `there is no such plan anymore` bullet
#:         above. What remains is the sentence that CITES **and** whose reported denial
#:         ends its clause before the first break: exempt with or without a quotation,
#:         which is why the residual is named rather than claimed closed.
#:       * a denial continued PAST its existential predicate (`The Builder tier does not
#:         exist because it was merged into Team.`, `No Builder plan exists in the OSS
#:         build any longer.`): the `no … exists` and `builder … does not exist` arms
#:         learned `_CLAUSE_END` in cycle 18, so their scoped twins are green and this
#:         continuation is the price. Measured cost, stated because the change was made
#:         under the doctrine that a false block is the expensive half.
#:       * a COLON-closed denial (`There is no Builder plan: it never existed.`): `:`
#:         is not in either clause end, so neither the arm nor the exemption fires. True
#:         on `origin/main` too — pre-existing, and listed here because the list is
#:         claimed to be measured (cycle-12 P2). A `:` usually INTRODUCES a scope
#:         qualifier (`…: it is self-host only`), so accepting it as terminal would cost
#:         a false block on the class this list exists to protect.
#:
#: A sentence recording the RETRACTION is exempt — but only when it QUOTES what it
#: retracts (:func:`_quotes_the_denial`) or introduces a REPORTED CLAIM *and cites it*
#: (:data:`_REPORTED_CLAIM` + :data:`_RETRACTION_EVIDENCE`). A cue word alone was a
#: bypass: it let `The docs are stale: there is no Builder plan.` escape (cycle 2); a cue
#: plus a bare `#\d+` was the same bypass one word longer (cycle 6, measured — `The docs
#: are stale per #12444: there is no Builder plan.` was exempt); and a cue plus a SPEECH
#: VERB alone was the same bypass again (cycle-12 P1, measured — `The docs are stale: the
#: page says there is no Builder plan.` was exempt, which both `origin/main` and
#: `test_a_cue_word_alone_cannot_exempt_a_denial` say must be impossible).
#: A quotation mark may sit on EITHER side of the noun too: `There is no "Builder
#: plan".` and `no plan called "Builder".` were both invisible (cycle-9 P2, measured),
#: because the quote could only wrap the WORD `builder`.
_BUILDER_PLAN = (r"['\"`\u201c\u201d\u2018\u2019]?builder['\"`\u201c\u201d\u2018\u2019]?\s+"
                 r"['\"`\u201c\u201d\u2018\u2019]?(?:plans?|tiers?)")

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
#: ⛔ WORD ORDER IS NOT THE PROPERTY (cycle-11 P2, measured). The exemption keyed on
#: two orders only, so the same #4336 restatement blocked on syntax: `— the identifier
#: is `pro` (#4336)` and `— `pro` is the identifier` were both RED while `— only the
#: `pro` identifier` was green. A lint whose stated expensive half is the false block
#: cannot discriminate on word order. Both inversions are now accepted.
_IDENTIFIER_RESTATEMENT = (
    r"(?:only|just)\s+(?:the\s+)?(?:pro|builder)\s+"
    r"(?:identifier|key|name|spelling|tier)\b"
    r"|it\s*(?:is|['\u2019]s)\s+(?:just\s+)?(?:the\s+)?(?:pro|builder)\s+"
    r"(?:identifier|key|name|spelling|tier)\b"
    r"|(?:the|its)\s+(?:identifier|key|name|spelling|tier)"
    r"(?:\s+(?:key|name|spelling))?\s*(?:is|['\u2019]s)\s+(?:the\s+)?pro\b"
    r"|(?:the\s+)?pro\s+(?:is|['\u2019]s)\s+"
    r"(?:the\s+)?(?:identifier|key|name|spelling|tier)\b"
)
#: …and the exemption is NOT a property of the comma (cycle-6 P1, measured). Bolted onto
#: the comma arm only, it protected the restatement in ONE punctuation style while the
#: repo's dominant style false-blocked: `There is no Builder plan — only the `pro`
#: identifier (#4336).` was RED for `—`, `–` and `;` — `git grep -l ' — ' -- docs`
#: counts **948** at this head (re-measured with that command; an earlier revision said
#: 944, a figure no command reproduces, and a number stated as a measurement has to be
#: runnable), and the paragraph this PR rewrote uses a dash itself. Hoisted into its own
#: lookahead so a restatement is a restatement whichever mark introduces it.
#: ⛔ …and the two lookaheads that run BEFORE the clause end must tolerate that mark
#: as well (cycle-10 P2, measured). `_BUILDER_PLAN` and `_CLAUSE_END` learned to accept a
#: closing quote; `_RESTATEMENT_TAIL` and `_INTENSIFIER` did not, so a closing mark
#: between the noun and the separator defeated BOTH — and because the clause end now
#: accepts that mark, the sentence was flagged. Composing two independently pinned
#: correct shapes was RED: `There is no "Builder plan" — only the `pro` identifier
#: (#4336).`; and the pinned intensifier class went blind to its own quoted twin:
#: `There is no "Builder plan" anymore.`
#: ⛔ AND A CITATION IS ONLY A CLAUSE END WHEN IT IS ITSELF CLAUSE-FINAL (cycle-15 P1,
#: measured). Composed into a bare `[.)]|CITATION` alternation, an INLINE citation ended
#: the clause before its qualifier arrived, so six qualified TRUE statements were RED
#: while `origin/main` was green: `There is no Builder plan (see #12444) cheaper than
#: Solo.`, `… (#12444) cheaper than Solo.`, `… (per #12444) without unlimited graphs.`,
#: `… (see #12444) in the OSS install.`, `… (tracked in #12444) for self-hosters.`,
#: `… (cf. #12444) available in your region.` — the expensive half the module's own
#: doctrine names. And a bare `(#N)` is not this repo's only citation style either
#: (`git grep -o '(see #' -- docs` counts 10), so the house phrasing has to be accepted
#: AND bounded.
_CITATION = (r"\(\s*(?:(?:see|per|tracked\s+in|cf\.?)\s+)?#\d+\s*\)"
             r"(?=\s*['\"\u201d\u2019]?\s*(?:$|[.)]))")
#: ⛔ …AND THE RESTATEMENT EXEMPTION WAS UNBOUNDED (cycle-15 P1, measured).
#: `_CLAUSE_END` is `(?!_RESTATEMENT_TAIL)(?=…)`, a lookahead anchored at ONE position —
#: immediately after `_BUILDER_PLAN`. When a restatement tail matched there the lookahead
#: failed and `_BUILDER_PLAN` was never re-tried, so **the whole rest of the sentence
#: became invisible**. On `origin/main` that unboundedness reached COMMAS only; hoisting
#: the tail into a shared lookahead for `—`/`–`/`;`/`--` (cycle-6 P1, cycle-11 P2) put it
#: on the repo's DOMINANT separator (`git grep -l ' — ' -- docs` = 948), and seven of
#: eight base-flagged live denials became exempt — including this file's own measured
#: drift reflowed with the exemption's own phrasing. The tail must now END the clause.
_RESTATEMENT_TAIL = (r"\s*['\"\u201d\u2019]?\s*(?:[—–;,]|--)?\s*(?:"
                     + _IDENTIFIER_RESTATEMENT + r")"
                     r"(?=\s*['\"\u201d\u2019]?\s*(?:$|[.)]|" + _CITATION + r"))")
_CLAUSE_END = (r"(?!" + _RESTATEMENT_TAIL + r")"
               r"(?=\s*['\"\u201d\u2019]?\s*"
               r"(?:$|[.;)]|—|–|--|;|,))")
#: ⛔ …and `_CITATION` was DEAD here and is deleted (cycle-20, measured). A cycle-16
#: revision listed it under "One pin each, so neither term can be deleted silently",
#: which is false for THIS set, and its stated reason refutes itself: `_INTENSIFIER` is
#: entirely OPTIONAL, so every `_CLAUSE_END` arm has a zero-width-intensifier
#: `_CLAUSE_END_STRICT` twin measured at the same position — which is why the base arm's
#: `_CITATION` could never be the term that matched. Measured: deleting it here leaves
#: `19 passed` and changes 0 verdicts over 1013 sentences (every `docs/` line naming
#: `builder` plus every sentence pinned in this file); deleting it from
#: `_CLAUSE_END_STRICT` alone REDS `test_the_lint_does_flag_the_real_denial`. So it is
#: load-bearing in the strict set only, and the two `_CITATION` pins below still hold.

#: A denial is often qualified by a trailing intensifier rather than nothing at all
#: (`… anymore`, `… at all`). Measured false NEGATIVES before this existed: "There is
#: no Builder plan anymore." and "There is no Builder plan any more." both read as
#: ordinary prose and both escaped the guard entirely (cycle-4 review). Kept to a
#: CLOSED set so it cannot become a wildcard that swallows the next qualifier.
#: ⛔ `whatsoever` and `of any kind` were missing from a list whose own note proves the
#: class is real (cycle-13 P2, measured: `There is no Builder plan whatsoever.` and
#: `There is no Builder plan of any kind.` both read as ordinary prose).
#: ⛔ `either` was missing from the same closed set (cycle-18 P1, measured): every
#: `… no <Builder plan> either.` read as ordinary prose — including
#: `There is no Builder plan either.` — and the same word is what let a live
#: re-assertion hide behind a quoted record (`… read "there is no Builder plan";
#: today there is no Builder plan either.`), because the trailing qualifier defeated
#: the clause end at the OUTSIDE occurrence. It is a qualifier like `anymore`, so it
#: belongs in this set and not in `_CLAUSE_END`. Residual: `… either, only the `pro`
#: identifier (#4336).` stays green, because a comma after an intensifier is a scope
#: qualifier — the same trade the rest of the class makes.
_INTENSIFIER_WORDS = (r"(?:any\s?more|anymore|at\s+all|available|for\s+sale|whatsoever|"
                     r"either|yet|today|currently|of\s+any\s+(?:kind|sort))")
#: ONE definition of the closed intensifier set, so the two places that need it cannot
#: drift: the denial arms below WRAP it, and :func:`_quotes_the_whole_denial` validates a
#: mention's out-of-span TAIL against it (round-4 review). Before this constant existed
#: the tail check would have needed its own copy of the word list — two implementations of
#: one rule, which this file removes by name.
_INTENSIFIER = (r"(?:\s*['\"\u201d\u2019]?\s+" + _INTENSIFIER_WORDS + r")?")

#: …and AFTER an intensifier only TERMINAL punctuation may end the assertion
#: (cycle-5 review, measured). `_CLAUSE_END`'s comma arm re-entered through the new
#: intensifier and false-blocked the SCOPE-qualifier class the module's own doctrine
#: protects — `There is no Builder plan available, so self-hosters use the OSS build.`
#: and `There is no Builder plan at all, in the OSS install.` were both red. A comma
#: after an intensifier qualifies; it does not close. ⛔ The same holds for `—`/`–`/`;`
#: (cycle-6 P1): they are NOT terminal either, and their pinned comma twins proved each
#: one a false block. So the strict set is terminal punctuation and an issue citation —
#: and NOT a comma, dash or semicolon, whose cost is stated as a false NEGATIVE in the
#: class docstring above rather than paid for with a false block.
#: ⛔ THE SET STILL CARRIED THE SEMICOLON (cycle-11 P2, measured), so this comment
#: contradicted the regex underneath it and the `;` twin of the scope-qualifier class
#: was RED on one separator while its `,` and `—` twins were pinned green. `;` is now
#: out of the strict set, which is what the sentence above always claimed.
#: ⛔ THE `(?!_RESTATEMENT_TAIL)` PREFIX HERE IS CURRENTLY UNREACHABLE, AND IS KEPT
#: DELIBERATELY (cycle-16 P2, measured). The strict terminals are `$`/`.`/`)`/a
#: clause-final citation, and `_RESTATEMENT_TAIL` can only begin with `[—–;,]`, `--` or
#: the first letter of a restatement — disjoint sets — so the two can never match at the
#: same position: 0 firings over a 105,600-sentence grid, and deleting the prefix changes
#: 0 verdicts on that grid and 0 of the 111 files under `docs/`. It is kept because the
#: strict set has already been widened by accident once (the `;` above), and the next
#: widening is the one that would make this reachable. Stated so a reader does not take
#: it as active protection: the intensifier arms do NOT honour the restatement bound.
_CLAUSE_END_STRICT = (r"(?!" + _RESTATEMENT_TAIL + r")"
                      r"(?=\s*['\"\u201d\u2019]?\s*(?:$|[.)]|" + _CITATION + r"))")

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
    re.compile(r"\bthere\s+(?:isn['\u2019]t|aren['\u2019]t|is\s+not|are\s+not)\s+(?:(?:an?|any)\s+)?" +
               _BUILDER_PLAN + _CLAUSE_END, re.I),
    re.compile(r"\bthere\s+(?:isn['\u2019]t|aren['\u2019]t|is\s+not|are\s+not)\s+(?:(?:an?|any)\s+)?" +
               _BUILDER_PLAN + _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "there exists no builder plan"
    re.compile(r"\bthere\s+exists?\s+no\s+" + _BUILDER_PLAN + _CLAUSE_END, re.I),
    re.compile(r"\bthere\s+exists?\s+no\s+" + _BUILDER_PLAN +
               _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "no 'builder' plan exists" (with an explicit non-existence predicate)
    #: ⛔ THIS ARM AND ITS PREDICATE TWIN CARRIED NO CLAUSE END (cycle-18 P1, measured),
    #: so the scope-qualifier class they exist beside was RED while the `there is no …`
    #: twin of the same sentence was green: `No Builder plan exists in the OSS build.`
    #: and `The Builder tier does not exist on the self-hosted install.` were both
    #: flagged (true on `origin/main` too — pre-existing, fixed here because it is the
    #: class this file's own doctrine calls the expensive half). Both arms now end their
    #: clause and carry the intensifier twin every other arm has; the cost is the
    #: documented miss in the class docstring (a denial continued past its predicate:
    #: `The Builder tier does not exist because it was merged into Team.`).
    re.compile(r"\bno\s+(?:such\s+(?:thing\s+as\s+(?:a\s+)?)?)?" + _BUILDER_PLAN +
               r"\s+exists\b" + _CLAUSE_END, re.I),
    re.compile(r"\bno\s+(?:such\s+(?:thing\s+as\s+(?:a\s+)?)?)?" + _BUILDER_PLAN +
               r"\s+exists\b" + _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "no such thing as a builder tier" — and this arm needs `_CLAUSE_END` like every
    # other existential arm (cycle-13 P2, measured): without it, three extra words
    # defeated the qualified-statement protection `_CLAUSE_END` exists for, so
    # `There is no such thing as a Builder plan cheaper than Solo.` and `… , only the
    # `pro` identifier (#4336).` were both RED. The `-end` cannot rescue the second —
    # the restatement exemption is a lookahead on a clause end this arm did not use.
    re.compile(r"\bno\s+such\s+(?:thing\s+as\s+(?:a\s+)?)?" + _BUILDER_PLAN +
               _CLAUSE_END, re.I),
    re.compile(r"\bno\s+such\s+(?:thing\s+as\s+(?:a\s+)?)?" + _BUILDER_PLAN +
               _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "the builder plan does not exist" — INCLUDING its contracted spelling. `isn't` was
    # added in cycle 4 for exactly this class and its sibling verb was missed then
    # (cycle-6 P2, measured: "The Builder tier doesn't exist." escaped).
    re.compile(r"\bbuilder\b[^.]{0,40}\b(?:does|do)(?:n['\u2019]t|\s+not)\s+exist\b"
               + _CLAUSE_END, re.I),
    re.compile(r"\bbuilder\b[^.]{0,40}\b(?:does|do)(?:n['\u2019]t|\s+not)\s+exist\b"
               + _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "Builder is not AVAILABLE as a plan" — the predicate nominative displaced by an
    # adjective. Clause-final so "the Builder tier is not available in your region"
    # (a true, scoped statement) does not match; the `as a plan/tier` continuation is
    # optional because that is the denial's own tail (cycle-6 P2, measured).
    #: ⛔ …and each of the three arms below carries an INTENSIFIER TWIN (cycle-11 P2,
    #: measured). The older arms learned in cycle 4 that one trailing qualifier defeats
    #: a clause-final pattern; the arms added in cycles 6/7 were born without that twin,
    #: so `We don't offer a Builder plan anymore.`, `Builder is not available anymore.`
    #: and `There is no plan called Builder anymore.` all read as ordinary prose again —
    #: the same class, one word deep.
    re.compile(r"\bbuilder\s+(?:plan\s+|tier\s+)?(?:is|are)(?:n['\u2019]t|\s+not)\s+"
               r"(?:available|offered|sold|purchasable|buyable)\b"
               r"(?:\s+as\s+(?:a\s+)?(?:plan|tier))?" + _CLAUSE_END, re.I),
    re.compile(r"\bbuilder\s+(?:plan\s+|tier\s+)?(?:is|are)(?:n['\u2019]t|\s+not)\s+"
               r"(?:available|offered|sold|purchasable|buyable)\b"
               r"(?:\s+as\s+(?:a\s+)?(?:plan|tier))?"
               + _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "we don't offer a Builder plan" — the negation carried by the VERB. Measured
    # escaping (cycle-6 P2).
    re.compile(r"\b(?:do|does)(?:n['\u2019]t|\s+not)\s+(?:offer|sell|have)\s+"
               r"(?:a\s+|the\s+|any\s+)?" + _BUILDER_PLAN + _CLAUSE_END, re.I),
    re.compile(r"\b(?:do|does)(?:n['\u2019]t|\s+not)\s+(?:offer|sell|have)\s+"
               r"(?:a\s+|the\s+|any\s+)?" + _BUILDER_PLAN +
               _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # "there is no plan called Builder" / "no such plan as Builder" — the noun BEFORE
    # the name. Measured escaping (cycle-6 P2). `tier` is the same noun: every other arm
    # in this tuple spells `tiers?`, and matching only `plan` left `There is no tier
    # called Builder.` green (cycle-11 P2, measured).
    re.compile(r"\bno\s+(?:such\s+)?(?:plan|tier)\s+(?:called|named|as)\s+"
               r"['\"`\u201c\u2018]?builder\b" + _CLAUSE_END,
               re.I),
    re.compile(r"\bno\s+(?:such\s+)?(?:plan|tier)\s+(?:called|named|as)\s+"
               r"['\"`\u201c\u2018]?builder\b" + _INTENSIFIER + _CLAUSE_END_STRICT,
               re.I),
    # "Builder is not a plan/tier" — predicate nominative, clause-final, so a plan
    # name used as an OBJECT ("the Builder tier is not a tier you can downgrade
    # to") does not match.
    # `isn't` counts: "Builder isn't a plan." is the same predicate nominative and was
    # a measured false negative (cycle 4). It stays clause-final, so a plan name used
    # as an OBJECT ("the Builder tier isn't a tier you can downgrade to") is unmoved.
    re.compile(r"\bbuilder\s+(?:plan\s+|tier\s+)?(?:is|are)(?:n['\u2019]t|\s+not)\s+(?:a\s+)?"
               r"(?:plan|tier)" + _CLAUSE_END, re.I),
    #: …and its INTENSIFIER twin, which this arm — like the three cycle-6/7 arms — was
    #: born without (round-2 review, measured): `Builder is not a plan anymore.` and
    #: `Builder is not a plan either.` read as ordinary prose. The rule the file states
    #: for every other arm ("one trailing qualifier defeats a clause-final pattern")
    #: applies here too, and the twin is what lets the outside-quotation rule see a
    #: second assertion in the SAME arm's vocabulary.
    re.compile(r"\bbuilder\s+(?:plan\s+|tier\s+)?(?:is|are)(?:n['\u2019]t|\s+not)\s+(?:a\s+)?"
               r"(?:plan|tier)" + _INTENSIFIER + _CLAUSE_END_STRICT, re.I),
    # the measured sentence's own tail: "a builder buys pro or team"
    re.compile(r"\bbuilder\s+buys\s+(?:the\s+)?(?:pro|team|builder)\b", re.I),
)

#: A retraction RECORD, not the claim. `#4336` protects historical records that state
#: the name "as it was at the time", and quoting the superseded claim is this repo's
#: convention for recording a supersession — so a pin that reds that document punishes
#: ⛔ …and `before #N` was not the only way this repo dates a supersession (cycle-12
#: P1, measured): the cue list spelled only `before`/`prior to`, so `Until #12444 the
#: page said "there is no Builder plan".` — the module's OWN protected convention, quote
#: the superseded claim and cite the issue — was RED, because the closing-quote
#: tolerance in `_CLAUSE_END` now reaches the claim and no cue matched. `git grep -niE
#: 'until #|prior to #|through #' -- 'docs/*.md'` is 30+ files, so this is the house
#: phrasing, not an edge case. ⛔ …and `until\s+then` was hoisted INSIDE the
#: citation-requiring group, where it could only match the nonsense `until then #12444`,
#: so `Until then the page said "there is no Builder plan".` — 52 matching lines across
#: 47 `docs/` files (`git grep -ci 'until then' -- docs | awk -F: '{s+=$2} END{print
#: s}'`) — stayed RED (cycle-13 P2, measured). The bare temporal cue is its own
#: alternation now. ⛔ `before then` was added with it and REMOVED again (cycle-14 P2,
#: measured): `git grep -li 'before then' -- docs` is 0 files, and a cue is the WEAKER
#: half of an exemption, so an unpinned cue with no corpus behind it is new false-pass
#: surface for nothing — `Before then the page said "there is no Builder plan".` was
#: exempt.
_RETRACTION_CUES = re.compile(
    r"\b(?:previously|formerly|used\s+to|no\s+longer|retract\w*|stale\w*|"
    r"corrected|corrects|outdated|supersed\w*|"
    r"(?:before|until|through|prior\s+to|up\s+to)\s+#\d+|"
    r"until\s+then)\b", re.I)

#: …but a cue word alone is not a record — it is a bypass: it exempted
#: `The docs are stale: there is no Builder plan.`, a live denial (cycle 2). The
#: same-sentence path therefore needs an ATTRIBUTION (:func:`_quotes_the_denial` or
#: :data:`_REPORTED_CLAIM`); the forward-window path needs this CITATION half plus a
#: tie. A quoted single word is NOT evidence — the denial's own subject is usually
#: quoted (`"builder"`).
_RETRACTION_EVIDENCE = re.compile(
    r"#\d+"                                                   # cites the retraction
    r"|\b(?:19|20)\d\d-\d\d-\d\d\b",                        # dates the record
    re.I)

#: …AND the record must TIE BACK to the denied claim (cycle-5 review, measured). A
#: following fragment carrying a cue and evidence about a DIFFERENT correction exempted
#: a whole document: `There is no Builder plan.` + `The stale `org` wording was
#: corrected in #12444.` returned NO offenders — i.e. the forward window had become the
#: cycle-2 "dress the denial" bypass, two sentences wide. The record must name the
#: builder claim or refer back to the sentence it follows.
#: ⛔ A BARE PRONOUN WAS TOO LOOSE (cycle-6 P2, measured): it bound to whatever noun
#: followed it, so `That Team row was corrected in #12444.` and `The pricing row was
#: corrected in #12444; it reads right now.` both exempted a live denial. The tie now
#: requires `builder`, or a pronoun standing at a CLAUSE BOUNDARY — end of the fragment
#: or immediately before punctuation (`#12444 corrected it.`) — which is how the
#: pinned records read. Stated residual: `That was corrected in #12444.` (pronoun +
#: auxiliary) is a plausible record this rejects, so it is a false POSITIVE risk of the
#: documented, bounded kind; it is not reachable from the denial itself.
_RETRACTION_TIE = re.compile(
    r"\bbuilder\b|\b(?:it|this|that|these|the\s+above)\b(?=\s*(?:[.,;:!?)]|$))",
    re.I)


#: The ATTRIBUTION half. Cycle-6 review measured why the same-sentence exemption
#: cannot accept a bare citation: a cue plus `#\d+` was enough, so `The docs are stale
#: per #12444: there is no Builder plan.` — the cycle-2 dressed denial with a citation
#: bolted on — was exempt. A sentence that ASSERTS the denial while citing an issue is
#: not a record OF it.
#:
#: ⛔ THE SHAPE IS STRUCTURAL, AND THREE REGEX ATTEMPTS AT IT EACH BOUGHT A DEFECT
#: (cycles 7 and 8, all measured). What makes a record a record is that THE DENIAL
#: ITSELF IS THE QUOTED MATTER — not that some quotation appears near a speech verb:
#:   * "a speech verb and some quote" exempted `The doc previously said "pro"; there is
#:     no Builder plan.` — `said "pro"` satisfied it;
#:   * a tempered backreference to the opening mark exempted `said "pro" and "builder
#:     is a plan"; there is no Builder plan`, and let two CONTRACTIONS fence a
#:     quotation (`stated there isn't a Builder plan and it doesn't exist`), while
#:     blocking `read “There is no Builder plan.”` because `“` and `”` are different
#:     characters and a backreference demands the same one;
#:   * requiring the span to CLOSE blocked a record whose quoted claim ends in a
#:     period, because the sentence splitter breaks INSIDE the quotation.
#: So the rule is a scanner, not a regex: find the quoted spans (matched pairs,
#: including the asymmetric typographic ones), and require ONE span carrying BOTH the
#: Builder phrase and a negation. A drift sentence cannot put the denial in quotes; a
#: record writes it there. An UNCLOSED pair runs to the end of the fragment, which is
#: exactly what the splitter leaves when the claim's own period ends the sentence.
_QUOTE_PAIRS = (('"', '"'), ('`', '`'), ('\u201c', '\u201d'), ('\u2018', '\u2019'))
#: ⛔ MIXED MARKS ARE DELIBERATELY NOT PAIRS (round-3 review, measured, and REFUSED
#: after trying it). Adding `('"', '\u201d')` / `('\u201c', '"')` fixes the real false
#: block `The doc read “there is no Builder plan".` — RED here, `origin/main` green —
#: BUT it makes the SCANNER emit a spurious UNCLOSED span for every ordinary quotation
#: (opening `"` looks for a `”` that never comes, so the span runs to the end of the
#: fragment). :func:`_asserts_a_denial_outside_the_quotes` masks every span, so that
#: unclosed tail then swallowed a live denial and turned
#: `Until then the doc said "no plan called Builder" and there is no Builder plan.`
#: — a PINNED true positive — GREEN. A false block traded for a false PASS on the exact
#: drift this file guards is the wrong trade, so the pairs stay same-direction. ⛔ The
#: mixed-mark mention is NOT a false positive, which an earlier revision of this comment
#: claimed: the unclosed span the typographic mark opens is accepted by
#: `_quotes_the_whole_denial`, so it is EXEMPT here and at `origin/main` (cycle-20 round 3,
#: measured — see the class docstring bullet). Repairing it properly means a scanner that
#: pairs an opening mark with the NEAREST mark from its own closer set, which is a change
#: to `_quoted_span_offsets`'
#: contract and belongs in its own cycle with its own pins, not bolted onto this one.
#: ⛔ `_QUOTED_NEGATION` and `_BUILDER_PHRASE` lived here and are GONE (cycle-17 P1,
#: measured). Both became dead when `_quotes_the_denial` moved its gate from "the span
#: holds a Builder phrase and a negation" to "the span IS a denial, by `_DENIAL_PATTERNS`"
#: — the narrower pair refused to recognise the predicate-nominative and
#: noun-before-name denials the lint itself flags, and blocked retraction records in this
#: file that quote one. A dead term left behind reads as protection it does not provide;
#: removing it is this file's own cycle-16 rule for `before then`.


def _quoted_span_offsets(text: str, *, apostrophe: bool = True) -> list[tuple[int, int, bool]]:
    """Every quoted span as ``(inner_start, inner_end, closed)`` in ``text``.

    THE ONE implementation of "what is quoted here": :func:`_quoted_spans` reads it as
    strings, and :func:`_asserts_a_denial_outside_the_quotes` needs the same spans as
    OFFSETS so it can mask them. Two implementations of this rule would be two
    definitions of "quoted" — the failure this whole exemption exists to avoid.

    ⛔ ``apostrophe=False`` EXISTS FOR ONE CALLER, AND IT CLOSES A MEASURED HOLE
    (round-3 review, P1). The ``'`` pass opens at any `'` not preceded by an alphanumeric
    and closes at the next one not followed by one, so an ELISION opens and a plural
    POSSESSIVE closes — one CLOSED span wrapping an entire denial:

        Since the '90s there is no Builder plan, and the customers' rows are wrong.
        spans -> [(11, 58, True)]      # the "quotation" IS the denial
        _denials_in_document(...) -> []   # the guard's real scan saw NOTHING

    while `origin/main` FLAGS it. Two ordinary apostrophes made the #12444 drift
    re-introducible verbatim. The apostrophe is right for the RECORD arm (a retraction
    written `'there is no "builder" plan'` is a quotation), so it stays on by default;
    it is wrong for the cue-free MENTION exemption, and that is the one caller that
    turns it off.
    """
    spans: list[tuple[int, int, bool]] = []
    for open_mark, close_mark in _QUOTE_PAIRS:
        i = 0
        while True:
            start = text.find(open_mark, i)
            if start < 0:
                break
            end = text.find(close_mark, start + len(open_mark))
            if end < 0:
                spans.append((start + len(open_mark), len(text), False))
                break
            spans.append((start + len(open_mark), end, True))
            i = end + len(close_mark)
    i = 0
    while apostrophe and i < len(text):
        if text[i] == "'":
            before = text[i - 1] if i else " "
            if not before.isalnum():                      # an opener, not `isn't`
                j = i + 1
                close = -1
                while j < len(text):
                    if text[j] == "'":
                        after = text[j + 1] if j + 1 < len(text) else " "
                        if not after.isalnum():           # a closer, not `don't`
                            close = j
                            break
                    j += 1
                spans.append((i + 1, close if close >= 0 else len(text), close >= 0))
                i = (close + 1) if close >= 0 else len(text)
                continue
        i += 1
    return spans


def _quoted_spans(text: str) -> list[tuple[str, bool]]:
    """Every quoted span in ``text`` as ``(inner, closed)``.

    Matched pairs, plus the asymmetric typographic pairs; a single ``'`` is a quotation
    only when it is not inside a word, so ``isn't`` cannot open one. An unclosed pair
    runs to the end of the fragment — the splitter's output when the claim's own period
    ends the sentence — and is reported as NOT closed so the caller can require that it
    still OPENS with the claim (cycle-9 P2, measured: a cue plus one stray `"` before a
    live denial exempted it, because the denial was "inside" a span that never closed).
    """
    return [(text[start:end], closed)
            for start, end, closed in _quoted_span_offsets(text)]


def _quotes_the_denial(sentence: str) -> bool:
    """Is the DENIAL itself the quoted matter? Recognised by THIS MODULE'S OWN DENIAL SET.

    ⛔ THE GATE IS `_DENIAL_PATTERNS`, NOT `_BUILDER_PHRASE` (cycle-17 P1, measured).
    Gating on the phrase (which requires `builder` IMMEDIATELY followed by `plan`/`tier`)
    was narrower than the lint itself: `Builder is not a plan.`, `There is no plan called
    Builder` and every other predicate-nominative / noun-before-name claim IS a denial
    here, so a record that quoted one was BLOCKED — three of the six records cycle 17
    pinned, none of them flagged by `origin/main` OR by this branch's own parent. A lint
    whose stated expensive half is the false block cannot refuse to recognise the very
    shapes it calls denials.

    ⛔ AND THE `13 of 52` FIGURE THIS PARAGRAPH USED TO CARRY WAS NOT A MEASUREMENT
    (cycle-18 P1, measured). It described a population that does not exist. The
    reproducible figures, each against the commit it belongs to:

        at `77deb656`   correct tuple 66 · cue-bearing 18 · parent `77deb656^` blocks 6
                        (the six cycle-17 pins ARE the whole delta: three quoted,
                        three reported) — and the PARENT's own tuple is 60 / 12, of
                        which it blocks 0, which is the same statement twice
        `origin/main`   correct tuple 21 · cue-bearing 3

    ⛔ THE TUPLE GROWS WITH EVERY FIX, so the "at this head" row this paragraph carried
    in cycle 19 is the one figure that re-stales by construction: RE-RUN the probe
    rather than quoting a head count. Parse the tuple with `ast.literal_eval`, count
    `_RETRACTION_CUES` matches, and evaluate `_denial_in` per sentence, against
    `git show 77deb656^:tests/test_tier_naming.py` and
    `git show origin/main:tests/test_tier_naming.py`. Every count above is a command's
    output, not a recall.


    ⛔ AND A QUOTATION MUST NOT SHEATHE A DENIAL THE SENTENCE ASSERTS OUTSIDE IT
    (cycle-18 P1, measured, two lanes independently): once ANY span is a denial, a
    trailing `… and there is no Builder plan` became invisible —
    `Until then the doc said "no plan called Builder" and there is no Builder plan.`
    was EXEMPT while `origin/main` FLAGS it. A record QUOTES the denial; a drift ASSERTS
    it. This function only answers "is the quoted matter itself a denial"; the outside
    rule lives ONCE, in :func:`_quotes_a_retracted_claim` via
    :func:`_asserts_a_denial_outside_the_quotes` — both arms, offset-masked, so a span
    whose text repeats cannot blank the wrong occurrence and no joiner (`;`, `,`, `—`,
    `and`) can carry a second assertion past the check.

    ⛔ An UNCLOSED span counts only when the denial OPENS it (allowing for the opening
    mark itself), i.e. the span is the claim. A stray unmatched `"` earlier in the
    sentence must not make everything after it "quoted" (cycle-9 P2, measured).

    ⛔ "A NEGATION SOMEWHERE IN THE SPAN" IS NOT "THE DENIAL IS QUOTED" (cycle-11 P2,
    measured): `no downtime is guaranteed; the Builder plan is unlimited` asserted the
    tier EXISTS while carrying a `no`, so an unrelated negation exempted the live denial
    beside it. Requiring a `_DENIAL_PATTERNS` match in the span is what keeps that
    closed — it is the whole protection, and it does not need a second gate.
    """
    for span, closed in _quoted_spans(sentence):
        plain_span = _plain(span)
        denial = next((m for m in map(lambda p: p.search(plain_span), _DENIAL_PATTERNS)
                       if m is not None), None)
        if denial is None:
            continue
        if closed or denial.start() <= 2:
            # ⛔ `closed or` IS DELIBERATE HERE, AND IT IS NOT THE MENTION RULE
            # (round-4 review, measured). A CLOSED span is a real quotation, so the
            # denial may sit anywhere inside it — that is what makes the pinned record
            # `Previously the page read "There is no plan called Builder".` a record
            # rather than a denial (dropping `closed` reds it). The cost is stated rather
            # than hidden: two ordinary APOSTROPHES also produce a CLOSED span (elision
            # opens, plural possessive closes), so a cue word plus such a span can still
            # exempt the drift —
            #
            #   The docs are stale: since the '90s there is no Builder plan, and the
            #   customers' rows are wrong.
            #
            # is exempt here AND at `origin/main` (pre-existing, not a regression). It is
            # listed in the class docstring's MEASURED false negatives. The CUE-FREE
            # mention path does NOT have this hole: it requires the denial to OPEN the
            # span, which a pseudo-span of that shape fails.
            # ⛔ NOTHING OUTSIDE THE QUOTATIONS MAY BE A DENIAL — enforced ONCE, in
            # `_quotes_a_retracted_claim` (`_asserts_a_denial_outside_the_quotes`), on
            # BOTH arms and by OFFSET. This function answers only "is the quoted matter
            # itself a denial"; a second copy of the outside rule lived here as a
            # string-replacement mask and was deleted in the round-2 review: two
            # implementations of one rule is exactly what this file forbids, the
            # replacement form can blank the WRONG occurrence when a span repeats, and
            # it governed only this arm — the reported arm walked straight past it, so
            # `… said "no plan called Builder"; there is no Builder plan.` stayed exempt
            # through a `;`, a `,` and a `—` (all three FLAGGED at `origin/main`).
            return True
    return False


#: The SPEECH-VERB half only. What it introduces is decided by `_DENIAL_PATTERNS`
#: (:func:`_reports_a_denial`), never by a hand-rolled shape.
_REPORTED_CLAIM = re.compile(
    r"\b(?:read|said|says|claimed|asserted|asserts|stated|wrote|worded)\b\s+"
    r"(?:that\s+)?",
    re.I)


#: Where the REPORTED CLAUSE ends. A denial beginning after one of these is a SECOND
#: claim, not the one the speech verb introduces — so the stretch a verb can introduce is
#: everything UP TO its first break.
#:
#: ⛔ THIS REPLACES A FIXED 6-CHARACTER OFFSET (cycle-20, measured). That bound could not
#: tell a harmless adverbial from a detour, so three correct retraction records were
#: BLOCKED here while `origin/main` exempts all three:
#: `Previously the doc said flatly that there is no Builder plan; #12444 corrected it.`
#: and its `claimed (wrongly)` / `said throughout` twins (the denial begins at 12–16 and
#: the offset was 6). `and`/`but` are in the set for the reason the offset existed:
#: `said the naming was wrong and there is no Builder plan` asserts a denial and must stay
#: FLAGGED, which is the pin below. `so`/`which`/`therefore` and friends are here because
#: one bare `so` re-opens that same detour a word further out. A CLOSED set, like
#: `_INTENSIFIER_WORDS`, so it cannot become a wildcard.
#:
#: ⛔ `--` AND THE SUBORDINATING CONNECTIVES ARE IN THE SET FOR THE SAME REASON (cycle-20
#: round 2, measured). The first cut of this constant carried only `[;:,—–]` and
#: `and/but/so/which/therefore/thus/hence/whereas`, and that was FOUR FALSE PASSES: `--`
#: is a clause break everywhere else in this file (`_RESTATEMENT_TAIL`, `_CLAUSE_END`)
#: and 65 `docs/` files use it, so `Until #12444 the docs said the naming was wrong --
#: there is no Builder plan.` was exempt here while `origin/main` and the pre-fix head
#: both FLAG it — likewise `while`, `although`/`though`, and a connective `yet`.
#:
#: ⛔ A BREAK BEFORE THE DENIAL IS NOT HARMLESS, AND AN EARLIER CUT CLAIMED IT WAS
#: (cycle-20 round 3, measured). Widening this set DOES add false blocks when the break is
#: an ASIDE inside the reported material, because the split keeps only the PREFIX and
#: `reported` collapses to `''` — the exact opposite of the sentence that used to sit
#: here ("A break AFTER the denial is harmless … so widening this set cannot add a false
#: block"), which was true of breaks after the denial and false of breaks before it:
#: `Previously the doc said -- until #12444 corrected it -- that there is no Builder
#: plan.` and `… said yet again that there is no Builder plan; #12444 corrected it.` are
#: CORRECT records that `origin/main` exempts and the round-2 terms BLOCKED.
#: :data:`_REPORTED_ASIDE` strips a matched pair / the adverbial before the split, so both
#: are exempt again without giving back the assertions those terms were added for.
#:
#: ⛔ …BUT THE STRIP MUST BE CUE-GATED, OR IT SWALLOWS THE ASSERTION (cycle-20 round 4,
#: measured). An ungated pair strip defeats the `--`/`—` members of
#: `_REPORTED_CLAUSE_END`, because the pair's own delimiters are consumed and the denial
#: behind the aside is glued into the reported clause — so the PINNED live denial simply
#: gains a parenthetical and escapes:
#: `Until #12444 the doc said the naming was wrong -- and the page still carries it --
#: there is no Builder plan.` was EXEMPT, as was its `—` twin and `… wrong yet again
#: there is no Builder plan.` — all three FLAGGED by `origin/main` and by the pre-fix head.
#: So an aside must be identified by POSITION, NOT by vocabulary (cycle-20 round 5,
#: measured — and the round-4 cue gate was a ONE-WORD BYPASS): keying the strip on
#: `_RETRACTION_CUES` meant any sentence could wrap a cue word in a pair and have its
#: delimiter consumed — `… wrong -- stale -- there is no Builder plan.` read EXEMPT for
#: 20 of the 20 pinned joins (every `_REPORTED_CLAUSE_END` member behind `-- <cue> --`),
#: all of them FLAGGED by the pre-fix head. It also BLOCKED correct records whose aside
#: carried no cue: the dash twin of every round-1 pin — `Previously the doc claimed —
#: wrongly — that there is no Builder plan; #12444 corrected it.` — was FLAGGED here
#: while `origin/main` exempts it. Position fixes both directions at once, because a
#: genuine aside in these records LEADS the reported material and a second clause cannot:
#: a pair that leads is stripped, a pair anywhere after it is a clause break. (A cue word
#: alone is not a record — the file's own doctrine at the `_RETRACTION_CUES` block.)
_REPORTED_ASIDE = re.compile(
    r"^(?:\s*(?:--[^-]*--|\u2014[^\u2014]*\u2014|yet\s+again))+\s*", re.I)
_REPORTED_CLAUSE_END = re.compile(
    r"[;:,\u2014\u2013]|--|\b(?:and|but|so|which|therefore|thus|hence|whereas|"
    r"while|although|though|yet|since|because)\b",
    re.I)

#: ⛔ `_REPORTED_CLAUSE_BREAK` LIVED HERE AND IS GONE (round-2 review, measured). It
#: was added for a real finding — a reported denial whose match STARTED inside a
#: quotation and finished after it (`… said "the Builder plan is unlimited"; the Builder
#: tier does not exist.`) — but that class is now closed one level UP and for BOTH arms
#: by :func:`_asserts_a_denial_outside_the_quotes`, which masks the spans by offset and
#: asks the denial set about the remainder. Measured on 576 sentences (every `docs/` line
#: containing `builder` plus every sentence pinned in this file): deleting the rule
#: changes **0** sentence-level and **0** document-level verdicts, so keeping it would be
#: a second implementation of one rule that the suite cannot tell is missing — the shape
#: this file has removed twice already (`_BUILDER_PHRASE`, `before then`). The FINDING is
#: kept where it belongs: its two sentences are pinned live below, and removing the
#: outside-quotation rule reds them.
#:
#: ⛔ `_REPORTED_CLAUSE_END` IS A DIFFERENT RULE, NOT THAT TERM RETURNING (cycle-20,
#: measured): `_REPORTED_CLAUSE_BREAK` asked whether a MATCH crossed a break, to bound
#: where a denial could END. `_REPORTED_CLAUSE_END` bounds where the reported CLAUSE
#: ends, so a denial BEGINNING after the verb's first break is a second claim. Named
#: apart so a reader cannot take one for the other.


def _reports_a_denial(sentence: str) -> bool:
    """Does a SPEECH VERB introduce a denial — recognised by `_DENIAL_PATTERNS`?

    ⛔ THE TAIL WAS A HAND-ROLLED SHAPE (cycle-17 P1, measured): it required
    `there is|was|are no` or `no` + `builder`, so three of the six records cycle 17 pinned
    were BLOCKED — `said there's no Builder plan`, `said there isn't a Builder plan`,
    `said the Builder tier does not exist` — none of them flagged by `origin/main`. (The
    `23 of 52` figure an earlier revision carried alongside this one is refuted in
    :func:`_quotes_the_denial`; the population is 18 cue-bearing sentences, not 36.)
    Reusing the denial set makes the exemption's recognition the SAME set as the lint's,
    which is the only way the two cannot drift.

    ⛔ AND THE DENIAL MUST NOT STRADDLE A CLAUSE BREAK (cycle-18 P1, measured, the OTHER
    lane's finding on this PR). Bounding the match's START is not enough: the
    `builder … (does|do) not exist` arm's free `[^.]{0,40}` window begins at a `Builder`
    INSIDE a quoted claim and finishes its predicate AFTER the closing quote and a
    semicolon, so `Until #12444 the doc said "the Builder plan is unlimited"; the
    Builder tier does not exist.` was EXEMPT — an assertion that the tier EXISTS
    followed by a live denial, and `origin/main` FLAGS it. The match may not cross `;`
    `:` `—` `–` or a quotation, and the opening mark itself is skipped so a denial may
    still begin immediately after it.
    """
    plain = _plain(sentence)
    for verb in _REPORTED_CLAIM.finditer(plain):
        # ⛔ NO `lstrip` OF THE OPENING MARK HERE (round-2 review, measured): it was
        # added so a quoted reported claim still counted under the offset bound, it left
        # the suite at 19 passed when deleted, and no `docs/` verdict or pinned case
        # depends on it — an unpinned term with no corpus behind it, which is this file's
        # own cycle-14 rule for removing surface rather than carrying it.
        # ⛔ AN ASIDE IS NOT A CLAUSE BREAK (cycle-20 round 3, measured — see
        # `_REPORTED_ASIDE`): a matched `--…--` / `—…—` pair, or the adverbial `yet again`,
        # sits INSIDE the reported material, so it is removed before the clause is cut.
        # Without this the split keeps only the prefix and `reported` is empty, which
        # BLOCKED two correct records while `origin/main` exempts both.
        rest = _REPORTED_ASIDE.sub(" ", plain[verb.end():])
        # ⛔ …AND ONLY UP TO THE FIRST CLAUSE BREAK (cycle-20, measured — see
        # `_REPORTED_CLAUSE_END`): a denial beginning after one is a second claim, so
        # `said the naming was wrong and there is no Builder plan` stays FLAGGED while
        # `said flatly that there is no Builder plan` is the record it is.
        reported = _REPORTED_CLAUSE_END.split(rest, 1)[0]
        for pattern in _DENIAL_PATTERNS:
            if pattern.search(reported) is not None:
                return True
    return False


def _asserts_a_denial_outside_the_quotes(sentence: str) -> bool:
    """Is there a DENIAL — by this lint's own set — outside every quoted span?

    ⛔ A QUOTATION IS NOT A LICENCE FOR A SECOND ASSERTION (cycle-18 P1, measured). The
    same-sentence exemption accepts a quoted claim without a citation, because the
    quotation IS the evidence that the sentence records a supersession. That made a
    live re-assertion FREE as long as one was also quoted:

        Previously the doc read "there is no plan called Builder"; today there is no
        plan called Builder.

    was exempt here while `origin/main` FLAGGED it (the narrower phrase gate refused to
    recognise the quoted claim at all, so the sentence was reported by accident) — so
    cycle 17's widening introduced this escape for the shapes it recognised. The
    unquoted twin is pinned live; this is the twin that was not.

    THE REMAINDER IS JUDGED BY THE LINT'S OWN DENIAL SET, NOT BY "builder + a negation"
    (round-2 review, measured). The broader test false-blocked the file's own canonical
    records with a clarifying tail — `Previously the page read "there is no Builder
    plan"; Builder is not the name we sell, pro is.` and
    `… ; it is not the name we use for Builder anymore.` were RED here and GREEN at
    `origin/main` — the expensive half, on the very sentences this exemption exists for.
    Masking by OFFSET (the one implementation of "what is quoted", from
    :func:`_quoted_span_offsets`) and asking `_DENIAL_PATTERNS` about the remainder keeps
    every sheltering shape flagged and every clarifying tail green.
    """
    chars = list(sentence)
    for start, end, _closed in _quoted_span_offsets(sentence):
        for i in range(start, end):
            chars[i] = " "
    outside = _plain("".join(chars))
    return any(pattern.search(outside) for pattern in _DENIAL_PATTERNS)


def _quotes_the_whole_denial(sentence: str) -> bool:
    """Does the sentence only MENTION the denial — every denial inside a CLOSED quotation?

    ⛔ A DOC THAT PRINTS THE FORBIDDEN SENTENCE IS NOT ASSERTING IT (cycle-19 P1,
    measured). :func:`_quotes_a_retracted_claim` reaches a quoted claim only through
    :data:`_RETRACTION_CUES` — a CLOSED cue list — so a mention introduced by any verb
    outside that list was FLAGGED, while `origin/main` was GREEN on every one of them
    because it had no closing-quote tolerance at all:

        Never write "there is no Builder plan".
        The forbidden sentence is "there is no Builder plan".
        Correction: the page said "there is no Builder plan".
        The old wording, "there is no Builder plan", was removed in #12444.
        A doc must never say “there is no Builder plan”.

    The fourth is the worst: it QUOTES the superseded claim AND cites the retraction,
    which is precisely the convention this exemption exists to protect. All of them are
    the module's own expensive half (a lint that blocks correct prose) and they were a
    REGRESSION against `origin/main`, so the exemption cannot be gated on a cue word: a
    MENTION is a mention whatever verb introduces it.

    The rule is structural, and it needs no cue. The denial must START inside a CLOSED
    quotation — its own assertion (the `there is no` lead-in) then sits OUTSIDE it — and
    nothing outside the spans may assert a denial, so `There is no "Builder plan".`
    stays RED (the match starts at `There`, before the opening mark) and a live
    re-assertion beside a quoted record stays RED (via
    :func:`_asserts_a_denial_outside_the_quotes`).

    ⛔ `start`, NOT WHOLE-MATCH CONTAINMENT (round-3 review, P2). The intensifier arms
    CONSUME the closing mark and the qualifier, so `match.end()` lands past the span end
    and whole-match containment rejected a genuine mention — `Never write "there is no
    Builder plan" either.` was RED here and GREEN at `origin/main`. Requiring only the
    match's START to be inside the span is the same test applied to the part that carries
    the assertion; anything the match reaches BEYOND the span is still governed by
    :func:`_asserts_a_denial_outside_the_quotes`, which is why the relaxation cannot
    become a licence (its pins stay red).

    The spans come from `_plain` (the same coordinate space as the matches, so markdown
    stripping cannot shift them).

    ⛔ START-ONLY CONTAINMENT IS NOT ENOUGH — THE TAIL MATTERS (round-4 review, P1,
    measured). Relaxing whole-match containment to "the match STARTS inside the span"
    (round 3) let a denial whose SUBJECT is quoted and whose PREDICATE is live pass, and
    :func:`_asserts_a_denial_outside_the_quotes` CANNOT catch it: masking the span deletes
    the SUBJECT, so the remainder has no `builder` and no pattern can fire. Four
    regressions against both the parent and `origin/main`:

        The tier called "Builder" does not exist.        -> EXEMPT (parent FLAGS it)
        The page calls the tier "Builder"; the plan does not exist.  -> EXEMPT
        Notes say "Builder" plan does not exist.          -> EXEMPT

    The tail after the span may therefore be ONLY the closing mark and a closed-set
    intensifier — never a predicate. That is the whole bound, and it is why the relaxation
    is safe.

    ⛔ AND THE SPANS INCLUDE APOSTROPHES, ANCHORED (round-4 review, P2, measured).
    `apostrophe=False` closed the elision false pass but ALSO blocked every genuine
    single-quote mention — `Never write 'there is no Builder plan'.` became RED at this
    head while green at the parent and at `origin/main`, a false block in the class this
    unit set out to unblock. Apostrophe spans are back, and the same opening anchor that
    rejects the elision span rejects nothing else: a real single-quote mention opens with
    its denial.

    ⛔ AN UNCLOSED SPAN COUNTS WHEN THE DENIAL OPENS IT (round-4 review, P2, measured).
    `_doc_sentences` splits INSIDE a quotation when the quoted claim's own period ends the
    sentence, so the guard's real scan handed this function
    `Never write "there is no Builder plan.` with an UNCLOSED span — which the `closed`
    filter dropped, leaving the exact sentence this exemption exists for FLAGGED through
    `_denials_in_document` while the pin (which called :func:`_denial_in` on the UNSPLIT
    sentence) stayed green. That pin-versus-scan divergence is this module's own cycle-2
    defect; the rule now matches :func:`_quotes_the_denial`'s.
    """
    plain = _plain(sentence)
    spans = _quoted_span_offsets(plain)
    if not spans:
        return False
    matches = [match for pattern in _DENIAL_PATTERNS for match in pattern.finditer(plain)]
    if not matches:
        return False
    def _tail_is_only_a_qualifier(start: int, end: int, match) -> bool:
        # ⛔ THE DENIAL MUST OPEN THE SPAN — and CLOSEDNESS IS NOT PART OF THE TEST
        # (round-4 review, P1 + P2). The rule used to branch on `closed`; that parameter
        # is now DEAD and was removed rather than left in place (this file's cycle-14/16
        # rule), because the anchor answers both cases:
        # "The match starts inside the span" is not enough in either direction. A span
        # that STARTS before the denial and runs past it is a pseudo-quotation (an
        # elision's `'` opening and a possessive's `'` closing), and a span that ENDS
        # before the denial finishes is a quoted SUBJECT with a live PREDICATE. Both are
        # assertions. Requiring the denial to open the span rejects both, and it is the
        # rule `_quotes_the_denial` already used for unclosed spans.
        if not (start <= match.start() <= end and match.start() <= start + 2):
            return False
        # …and whatever the match reaches BEYOND the span may be only a closing mark plus
        # a closed-set intensifier. A predicate here is an ASSERTION, not a mention.
        tail = plain[end:match.end()].strip(" \t'\"`\u201c\u201d\u2018\u2019")
        return not tail or re.fullmatch(_INTENSIFIER_WORDS, tail, re.I) is not None
    if not all(any(_tail_is_only_a_qualifier(start, end, match)
                   for start, end, _closed in spans) for match in matches):
        return False
    return not _asserts_a_denial_outside_the_quotes(sentence)


def _quotes_a_retracted_claim(sentence: str) -> bool:
    """Is this sentence itself a QUOTED record of the retraction?

    Requires the cue AND either the DENIAL ITSELF being the quoted matter
    (:func:`_quotes_the_denial`) or a REPORTED CLAUSE introducing it AND citing it. The
    block comment above records the three regex forms this replaced and the bypass each
    one allowed.

    ⛔ A QUOTATION IS NOT A LICENCE FOR A SECOND ASSERTION (cycle-18 P1, measured). The
    quoted arm needs no citation, because the quotation IS the evidence that the
    sentence records a supersession — which made a live re-assertion FREE as long as one
    was also quoted: `Previously the doc read "there is no plan called Builder"; today
    there is no plan called Builder.` was exempt at this head while `origin/main`
    FLAGGED it, so cycle 17's widening introduced the escape for the shapes it began to
    recognise. A sentence carrying a claim that is NOT the quoted matter must cite what
    it retracts, exactly like the reported-clause arm.

    ⛔ A SPEECH VERB IS NOT A CITATION (cycle-12 P1, measured). The reported-clause arm
    used to stand alone, so `The docs are stale: the page says there is no Builder plan.`
    was exempt — a cue plus a verb, no citation, no quotation, no tie: the cycle-2
    dressed denial reopened three words wide, and `origin/main` flags it.
    """
    if not _RETRACTION_CUES.search(sentence):
        return False
    # ⛔ ONE RULE, BOTH ARMS, BEFORE EITHER ARM'S OWN TEST (round-2 review, measured).
    # Consulted only on the QUOTED arm — a record that carries no quotation at all is a
    # reported record, and its denial is "outside" by definition — this is the rule that
    # makes a quotation refuse to sheathe an asserted denial, whatever joiner introduces
    # it: `… said "no plan called Builder"; there is no Builder plan.` (also `,` and `—`)
    # was exempt while `origin/main` FLAGS it, because the reported arm ran its own
    # clause-break test instead of this one.
    if _quoted_span_offsets(sentence) and _asserts_a_denial_outside_the_quotes(sentence):
        return False
    if _quotes_the_denial(sentence):
        return True
    return bool(_RETRACTION_EVIDENCE.search(sentence) and _reports_a_denial(sentence))


def _is_a_correction_record(fragment: str) -> bool:
    """Is ``fragment`` the SECOND half of a record — a cue plus a CITATION?

    The forward-window exemption's half: ``#12444 corrected it.`` names no claim (the
    claim is the fragment before it), so it cannot carry an attribution; it must carry
    a cue AND a citation, and then tie back (:data:`_RETRACTION_TIE`).
    """
    return bool(_RETRACTION_CUES.search(fragment)
                and _RETRACTION_EVIDENCE.search(fragment))


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
    if _quotes_the_whole_denial(sentence):
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
    following fragment must ITSELF carry a cue AND a citation (:func:`_is_a_correction_record`)
    AND tie back to this claim (:data:`_RETRACTION_TIE`), so `The docs are stale: <denial>`
    cannot buy an exemption by borrowing a cue from the next sentence, and neither can a
    genuine correction OF SOMETHING ELSE (measured, cycle 5).

    The citation half is PINNED, not merely stated (cycle-11 P2, measured): replacing
    :func:`_is_a_correction_record`'s body with ``return True`` left this suite fully
    GREEN, because every pinned record carried a citation and nothing pinned the case
    that has the cue and the tie but no citation.
    """
    sentences = _doc_sentences(text)
    out: list[tuple[int, str]] = []
    for i, (lineno, sentence) in enumerate(sentences):
        if "builder" not in sentence.lower():
            continue
        # ⛔ THE WORD PRE-FILTER USED TO REQUIRE `plan`/`tier`/`buys` TOO, AND THAT MADE
        # AN ARM UNREACHABLE (cycle-18 P1, measured). `builder … does not exist` is the
        # only arm needing neither word, and it is the plainest form of the class this
        # file added in cycle 6, so `Builder does not exist.` answered `_denial_in`=True
        # and `_denials_in_document`=[] — the guard could never report it, which is the
        # divergence this module's own doctrine forbids. `builder` alone is the filter
        # the arms' actual ingredients reduce to; anything narrower is a second
        # definition of them, and re-derives the same defect one word later.
        if not _denial_in(sentence):
            continue
        following = sentences[i + 1][1] if i + 1 < len(sentences) else ""
        if _is_a_correction_record(following) and _RETRACTION_TIE.search(following):
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
            # ⛔ THE AFFIRMATIVE IN BARE FORM (cycle-8 P1, measured): an OPTIONAL negation
            # in the negated-auxiliary arm made `There is a Builder plan.` a denial — the
            # exact sentence the class docstring says must stay writable.
            "There is a Builder plan.",
            "There is a Builder plan, and a Team plan.",
            "There is a Builder tier, which is the `pro` tier.",
            # RETRACTION RECORDS in the two shapes the repo writes them (cycle-8 P2):
            # the typographic PAIR (`“…”` — a backreference demands the same mark and
            # cannot see it), and a quoted claim whose own period ends the sentence, so
            # the splitter hands the fragment an UNCLOSED quotation.
            "... read \"There is no Builder plan.\"; #12444 corrected it.",
            "Previously the doc read \u201cThere is no Builder plan.\u201d; #12444 "
            "corrected it.",
            # …and a record quoting the claim in THIS repo's markdown dialect (cycle 9:
            # the span was compared RAW, so `**Builder**` defeated it — a false block on
            # the same dialect `_plain` exists for).
            "The doc previously read \"There is no **Builder** plan.\"; #12444 "
            "corrected it.",
            "The doc previously read \"There is no [Builder](https://x.test) plan.\"; "
            "#12444 corrected it.",
            # A quoted TRUE statement stays green (cycle-9 P2's other direction).
            "There is no \"Builder plan\" cheaper than Solo.",
            # Cycle-10 P2: two independently pinned correct shapes COMPOSED — the
            # quoted noun form inside the #4336 restatement — were RED because the two
            # lookaheads that run FIRST did not tolerate the mark the clause end does.
            "There is no \"Builder plan\" \u2014 only the `pro` identifier (#4336).",
            # The #4336 restatement in its most natural form: a comma followed by the
            # IDENTIFIER restatement qualifies the claim instead of ending it (cycles
            # 4 and 5 — `only|just` alone exempted the drift, so the noun is required).
            "There is no Builder plan, only the `pro` identifier (#4336).",
            "Builder isn't a plan, it is the `pro` identifier.",
            # SCOPE-qualified statements behind an intensifier (cycle 5: the intensifier
            # re-admitted the comma arm and red both of these).
            "There is no Builder plan available, so self-hosters use the OSS build.",
            "There is no Builder plan at all, in the OSS install.",
            # …and the SAME two sentences with the dash the repo actually writes
            # (cycle-6 P1: the exemption was bolted onto the comma arm, so `—`/`–`/`;`
            # false-blocked the restatement and the scope qualifier alike). The 948-file
            # measurement for this style lives ONCE, in the `_IDENTIFIER_RESTATEMENT`
            # block above — it is not restated here (cycle-12 P2: the previous sentence
            # claimed to state it once while stating it twice).
            "There is no Builder plan available — so self-hosters use the OSS build.",
            "There is no Builder plan at all — in the OSS install.",
            "There is no Builder plan — only the `pro` identifier (#4336).",
            "There is no Builder plan – only the `pro` identifier (#4336).",
            "There is no Builder plan; only the `pro` identifier (#4336).",
            # ⛔ WORD ORDER IS NOT THE PROPERTY (cycle-11 P2, measured): the same #4336
            # restatement in the orders a doc author actually writes was RED, because
            # `_IDENTIFIER_RESTATEMENT` keyed on ONE syntax.
            "There is no Builder plan — the identifier is `pro` (#4336).",
            "There is no Builder plan; the identifier is `pro`.",
            "There is no Builder plan — the tier key is `pro` (#4336).",
            "There is no Builder plan — `pro` is the identifier.",
            # …and the `;` twin of the scope-qualifier class: `_CLAUSE_END_STRICT` still
            # carried a semicolon while its own comment said it did not (cycle-11 P2).
            "There is no Builder plan available; so self-hosters use the OSS build.",
            "There is no Builder plan at all; in the OSS install.",
            "Builder isn't a plan — it is the `pro` identifier.",
            # CONTRASTED spelling (cycle 7): the contraction a doc that writes `isn't`
            # naturally continues with — this was RED before the tail accepted it.
            "Builder isn't a plan, it's the `pro` identifier.",
            "Builder isn't a plan, it\u2019s the `pro` identifier.",
            # SCOPED noun-before-the-name (cycle 7): the new arm had NO clause end, so
            # these true statements were RED — the very class `_CLAUSE_END` exists for.
            "There is no plan called Builder on the self-hosted install.",
            "There is no such plan as Builder for end-customers, only graphs.",
            # …and the SAME qualified-statement protection for the `no such thing as`
            # arm, which had no clause end at all (cycle-13 P2, measured).
            "There is no such thing as a Builder plan cheaper than Solo.",
            "There is no such thing as a Builder plan, only the `pro` identifier (#4336).",
            # ⛔ A CITATION IS A CLAUSE END ONLY WHEN IT IS CLAUSE-FINAL (cycle-15 P1,
            # measured): composed unbounded, an inline citation ended the clause before
            # its qualifier arrived, so the qualified-true-statement class this pin list
            # exists for was RED while `origin/main` was green.
            "There is no Builder plan (see #12444) cheaper than Solo.",
            "There is no Builder plan (#12444) cheaper than Solo.",
            "There is no Builder plan (per #12444) without unlimited graphs.",
            "There is no Builder plan (see #12444) in the OSS install.",
            "There is no Builder plan (tracked in #12444) for self-hosters.",
            "There is no Builder plan (cf. #12444) available in your region.",
            # ⛔ THE SCOPE-QUALIFIER CLASS THE EXISTENTIAL ARMS WERE MISSING (cycle-18 P1,
            # measured): `no … exists` and `builder … does not exist` carried no
            # `_CLAUSE_END`, so these three TRUE statements were RED at this head while
            # their `there is no …` twins above were green. Each arm now ends its clause.
            "No Builder plan exists in the OSS build.",
            "No Builder plan exists for self-hosters.",
            "The Builder tier does not exist on the self-hosted install.",
            # …and the intensifier twin of the `no … exists` arm, whose terminal-only
            # clause end keeps the comma-scope qualifier green.
            "No Builder plan exists at all, in the OSS install.",
            # ⛔ `either` joined the closed intensifier set (cycle-18 P1, measured): it is
            # the qualifier that let a live re-assertion hide beside a quoted record, so
            # the restatement must stay green WITH it.
            "There is no Builder plan either, only the `pro` identifier (#4336).",
            # A RECORD whose quoted claim uses SINGLE quotes (cycle-18 P2, measured):
            # nothing pinned `_quoted_spans`' apostrophe pass, so deleting it left the
            # suite green and this conventional record RED.
            "Previously the page read 'there is no Builder plan'; #12444 corrected it.",
            # …and one whose exemption depends on the APOSTROPHE pass specifically: no
            # speech verb (so `_reports_a_denial` cannot carry it) and no citation, so the
            # single-quoted span IS the record. Pinned because deleting that pass left the
            # suite green while this conventional record went RED.
            "Stale as of yesterday, the page displayed 'there is no Builder plan'.",
            # ⛔ A RECORD WITH A CLARIFYING TAIL IS STILL A RECORD (round-2 review,
            # measured): the outside-quotation rule must ask "is there a DENIAL out
            # there", not "is there a builder and a negation". Both of these were RED
            # while the broader test was in place and are GREEN at `origin/main`.
            'Previously the page read "there is no Builder plan"; Builder is not the name '
            'we sell, pro is.',
            'Previously the page read "there is no Builder plan"; it is not the name we '
            'use for Builder anymore.',

            # …and THREE of the CUE list's members, each pinned through a record that
            # depends on that specific cue (`formerly`, `stale\w*`, `up to #` — the
            # cycle-18 review measured that each could be deleted with the suite at 19
            # passed). ⛔ THE OTHER ALTERNATIVES ARE NOT PINNED AND THE COMMENT MUST NOT
            # CLAIM THEY ARE (round-2 review, measured: `used to`, `no longer`,
            # `retract\w*`, `corrects`, `outdated`, `supersed\w*`, `before #N`,
            # `prior to #N` and the date half of `_RETRACTION_EVIDENCE` each delete
            # green, and the first two are exercised by `docs/` records, so they are
            # kept — a cue that a real record uses is not free surface, it is the
            # exemption's weaker half earning its place; the unpinned ones are the
            # remaining risk, stated here rather than implied away).
            # ⛔ UNQUOTED on purpose: a QUOTED mention is exempt without any cue once
            # `_quotes_the_whole_denial` exists, so a quoted form would pin the mention
            # rule instead of the cue it is here for (measured: with `formerly` removed
            # from `_RETRACTION_CUES`, the quoted twin still passed).
            'Formerly, per #12444, the doc said there is no Builder plan.',
            'Stale per #12444, the doc said there is no Builder plan.',
            'Up to #12444 the doc said there is no Builder plan.',
            # A RECORD written without quotation marks (cycle 7): a reported clause is
            # the same record, and rejecting it was a false positive.
            "Previously this doc asserted there is no Builder plan; #12444 corrected it.",
            "Before 2026-09-21 the doc read there is no Builder plan; #12444 corrected it.",
            # …and the same record dated with `until`, which is how this repo writes it
            # (`git grep -niE 'until #|prior to #|through #' -- 'docs/*.md'` = 30+ files).
            # `before #N` was the ONLY form the cue list spelled, so this record was RED
            # only because `_CLAUSE_END` had learned to reach the closing quote
            # (cycle-12 P1, measured).
            'Until #12444 the page said "there is no Builder plan".',
            'Through #12444 this page asserted "there is no Builder plan".',
            # A SCOPED, true statement with the displacing adjective (cycle 6).
            "The Builder tier is not available in your region.",
            # ⚠️ DOCUMENTED RESIDUAL (false positive, cycle 4): a comma-SCOPE qualifier
            # still ends the clause, so "There is no Builder plan, and no Team plan
            # either, in the OSS install." is flagged. Tolerated on purpose — every
            # further comma shape that would exempt it (`and no`, `nor`) trades this
            # block for a false NEGATIVE on a live compound denial, and the class that
            # provoked the exception above is the restrictive `only`/`just` restatement,
            # which is measured. The message names the fix ("keep the substance"), so a
            # docs author who hits it is redirected, not blocked by silence.
            # ⛔ …and the SAME bound narrows the COMMA restatement itself when the
            # sentence continues (cycle-16 P2, measured): `_RESTATEMENT_TAIL` must now
            # END the clause, so `There is no Builder plan, only the `pro` identifier,
            # and the Team plan sits above it.` is flagged at this head while
            # `origin/main` exempted it. Coherent with the pin that makes
            # `… , only the `pro` identifier, because Builder was discontinued.` a
            # DENIAL — the two are the same shape — but it is a measured block and so it
            # is written down here rather than discovered by a docs author. NOT added to
            # this tuple: it is the documented residual, not a case the lint accepts.
            # A doc recording the retraction — cue AND evidence (#12444 cited).
            "docs/product/beta-sdk-surface.md previously read 'there is no "
            "\"builder\" plan'; #12444 corrected it.",
            "The denial is no longer present since 2026-09-21.",
            # ⚠️ Of the three cases here, `The denial is no longer present since
            # 2026-09-21.` raises no `_DENIAL_PATTERNS` hit at all, so IT needs nothing.
            # ⛔ The other TWO both MATCH and are green ONLY because of the exemption
            # (cycle-13 P2, measured: the earlier revision of this comment said the
            # first case matched nothing — true on `origin/main`, invalidated by the
            # closing-quote tolerance THIS PR gave `_CLAUSE_END`, which now reaches
            # `…'there is no "builder" plan';…`). A maintainer who trusted that would
            # reshape the exemption without touching the case that depends on it.
            # THIS case pins the SENTENCE-level path, while
            # `test_a_retraction_record_split_across_blocks_is_exempt` pins the
            # across-fragments one.
            'Until then the page said "there is no Builder plan".',
            # ⛔ THE EXEMPTION NOW RECOGNISES WHAT THE LINT RECOGNISES (cycle-17 P1,
            # measured). Gating the quoted span on `_BUILDER_PHRASE` and the reported
            # clause on a hand-rolled `there is no … builder` tail refused to recognise
            # the predicate-nominative, noun-before-name and contracted shapes THIS LINT
            # calls denials — six records, three per arm, each BLOCKED at `77deb656^`
            # and green at this head and at `origin/main`. ⛔ The `13 of 52` / `23 of 52`
            # figures an earlier revision stated here were not measurements and are
            # retracted (cycle-18 P1) — and so is the `blocked 0 of those 18` this
            # comment carried until cycle 19, which contradicted its own next clause.
            # Measured: at `77deb656` the tuple is 66 sentences / 18 cue-bearing and the
            # parent blocks 6 of them (the six pins below are the whole delta). ⛔ The
            # "at this head" figure a cycle-19 revision carried here ("76 / 23, the
            # parent blocks 10") was stale at the head that shipped it — re-measured
            # cycle 20: 88 / 25 — and the paragraph above already forbids quoting a
            # head count, because the tuple grows with every fix. Two pins per arm, so
            # a re-narrowing cannot be silent.
            'Previously the doc read "Builder is not a plan."; #12444 corrected it.',
            'Until #12444 the page read "Builder is not a plan".',
            'Previously the page read "There is no plan called Builder".',
            "Previously the doc said there's no Builder plan; #12444 corrected it.",
            "Previously the doc said there isn't a Builder plan; #12444 corrected it.",
            # ⛔ AN ASIDE INSIDE THE REPORTED CLAUSE IS NOT A CLAUSE BREAK (cycle-20 round
            # 3, measured): both are exempt at `origin/main` and were BLOCKED by the
            # round-2 terms until `_REPORTED_ASIDE` stripped the matched pair / adverbial.
            # The lone-`--` twin stays FLAGGED and is pinned in the live-denial tuple, so
            # this pair cannot be satisfied by dropping `--` from the break set.
            'Previously the doc said -- until #12444 corrected it -- that there is no '
            'Builder plan.',
            'Previously the doc said — until #12444 corrected it — that there is no '
            'Builder plan.',
            'Previously the doc said yet again that there is no Builder plan; #12444 '
            'corrected it.',
            # ⛔ THE DASH TWIN MUST STAY EXEMPT (cycle-20 round 5, measured): the round-4
            # CUE gate blocked these while `origin/main` exempts them, and a later
            # revision of the class docstring claimed the class was pre-existing when it
            # was introduced here. Position — a pair that LEADS the reported material — is
            # what makes them asides.
            'Previously the doc claimed — wrongly — that there is no Builder plan; '
            '#12444 corrected it.',
            'Previously the doc said -- flatly -- that there is no Builder plan; #12444 '
            'corrected it.',
            "Previously the doc said the Builder tier does not exist; #12444 corrected it.",
            'The doc previously read "There is no Builder plan."; #12444 corrected it.',
            # ⛔ A MENTION OF THE FORBIDDEN SENTENCE IS NOT AN ASSERTION OF IT
            # (cycle-19 P1, measured). The exemption above reaches a quoted claim only
            # through the CLOSED `_RETRACTION_CUES` list, so a doc that PRINTS the
            # sentence it forbids was flagged — a regression against `origin/main`,
            # which was green on every one of these because it had no closing-quote
            # tolerance. The style-guide form (`never write …`) and the cited-retraction
            # form (`…, was removed in #12444`) are both ordinary correct prose.
            'Never write "there is no Builder plan".',
            'The forbidden sentence is "there is no Builder plan".',
            'Correction: the page said "there is no Builder plan".',
            'The old wording, "there is no Builder plan", was removed in #12444.',
            'A doc must never say \u201cthere is no Builder plan\u201d.',
            'The phrase \u201cthere is no **Builder** plan\u201d is forbidden in this doc.',
            # ⛔ THE SINGLE-QUOTE TWIN OF EVERY MENTION ABOVE (round-4 review, P2,
            # measured): turning apostrophe spans off wholesale blocked them, so the
            # double-quote form was pinned green while the single-quote form was RED —
            # a false block introduced by one round and closed by the next.
            "Never write 'there is no Builder plan'.",
            "Never write 'there is no Builder plan' either.",
            # ⛔ …and a mention with an INTENSIFIER TRAILER (round-3 review, P2, measured):
            # the intensifier arms CONSUME the closing mark and the qualifier, so
            # whole-match containment pushed `match.end()` past the span and rejected a
            # genuine mention — RED here, GREEN at `origin/main`. The exemption now
            # requires only the match's START to be inside the span.
            'Never write "there is no Builder plan" either.',
            'Never write "there is no Builder plan" anymore.',
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
                # ⛔ BLOCK BOUNDARIES, EXERCISED (cycle-18 P2, measured): `_BLOCK_START`
                # was INERT — disabling it left this suite at 19 passed and changed no
                # verdict over `docs/`, because the three records above are separated by
                # BLANK LINES (which flush a paragraph) and the ones here by a block
                # marker whose trailing line still JOINS the item, so the clause end
                # fails either way. The shape that actually depends on it is a live denial
                # as the FIRST of two list items — it is pinned in the live tuple below
                # (`Plans we sell:\n- there is no Builder plan\n- the Team grid…`), which
                # REDS when the rule is switched off. These two stay as record-shape
                # coverage, not as a pin of that rule.
                "Previously:\n- there is no \"builder\" plan\n#12444 corrected it.\n",
                "Previously:\n## \"there is no builder plan\"\n#12444 corrected it.\n",
                # ⛔ THE TIE'S `builder` ALTERNATIVE (cycle-18 P2, measured): the
                # forward-window record below ties by naming the claim, not by a pronoun
                # at a clause boundary, so deleting `\bbuilder\b` from `_RETRACTION_TIE`
                # used to leave the suite green and this record RED.
                "There is no Builder plan.\n\n#12444 corrected the Builder naming.\n",
                # ⛔ A MENTION WHOSE QUOTED CLAIM ENDS THE SENTENCE (round-4 review, P2,
                # measured). The style-guide sentence is exactly what a doc writes, and
                # `_doc_sentences` cuts INSIDE the quotation when the claim's own period
                # ends it — so the span the scan hands the exemption is UNCLOSED and was
                # filtered out, leaving the sentence FLAGGED through the guard's real
                # path while the `_denial_in` pin (which runs on the UNSPLIT sentence)
                # stayed green. That is the module's own pin-versus-scan defect (cycle 2),
                # so the case is pinned HERE, on the scan.
                'Never write "there is no Builder plan."\n',
                '"Never write" is a style rule: "there is no Builder plan."\n',
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
                # Cycle-6 P2: the tie bound to whatever noun FOLLOWED the pronoun, so a
                # correction of a DIFFERENT row exempted the denial again.
                "There is no Builder plan.\n\nThat Team row was corrected in "
                "#12444.\n",
                "There is no Builder plan.\n\nThe pricing row was corrected in "
                "#12444; it reads right now.\n",
                # …and a cited-but-DRESSED denial in one sentence: a bare citation is
                # not an attribution, so the cycle-2 bypass cannot be re-bought with a
                # `#\d+` bolted onto it (cycle 6).
                "The docs are stale per #12444: there is no Builder plan.\n",
                "The doc was outdated until 2026-09-21; there is no Builder plan.\n",
                # Cycle-12 P1: the reported-clause arm stood alone, so a cue plus a bare
                # SPEECH VERB exempted a live denial — no citation, no quotation, no tie.
                # `origin/main` flags this sentence and `test_a_cue_word_alone_cannot_
                # exempt_a_denial` says it must.
                "The docs are stale: the page says there is no Builder plan.\n",
                "The stale `org` wording says there is no Builder plan.\n",
                # …and the report-then-re-assert shape the class docstring used to list as
                # an escape: the citation requirement added in cycle 12 closed it, so it
                # is a live denial now and belongs HERE, not on the miss list
                # (cycle-13 P2, measured).
                "Previously the page stated there is no Builder plan; today there is no "
                "Builder plan either.\n",
                # Cycle-7 P2: a speech verb plus SOME quote satisfied the attribution,
                # so an unrelated quotation exempted a live denial.
                'The doc previously said "pro"; there is no Builder plan.\n',
                'Per #12444 the doc previously said "pro" and there is no Builder '
                'plan.\n',
                # Cycle-8 P2: the quoted span merely had to CONTAIN `builder`, so a
                # second quotation, an apostrophe pair, or a bare `"builder"` satisfied
                # it while the denial sat outside every quotation.
                'The doc previously said "pro" and "builder is a plan"; there is no '
                'Builder plan.\n',
                "The doc previously stated there isn't a Builder plan and it "
                "doesn't exist.\n",
                'said "builder"; there is no Builder plan.\n',
                # …and the span must carry a NEGATION too: a quotation that merely
                # NAMES the tier is not a record of its absence.
                'The doc previously said "the Builder plan is unlimited"; there is no '
                'Builder plan.\n',
                # Cycle-11 P2: a negation ANYWHERE in the span satisfied the record
                # test, so a quotation that merely carries an unrelated `no` (and
                # asserts the tier EXISTS) exempted the live denial beside it. The
                # span must itself BE a denial.
                'The doc previously said "no downtime is guaranteed; the Builder plan '
                'is unlimited"; there is no Builder plan.\n',
                # Cycle-11 P2: the forward-window exemption's CITATION half was stated
                # but never pinned — a cue plus the tie was enough here. Replacing
                # `_is_a_correction_record` with `return True` left the suite green.
                "There is no Builder plan.\n\nWe corrected it.\n",
                # Cycle-9 P2: an UNCLOSED quotation earlier in the sentence made
                # everything after it "quoted", so a live denial merely FOLLOWING one
                # was exempt. An unclosed span now counts only if the claim OPENS it.
                'Previously the wording read "pro and team are the only paid tiers, so '
                'there is no Builder plan.\n',
                # ⛔ THE QUOTED TWIN OF THE CITED-DRESSED DENIAL (cycle-18 P1, measured):
                # the quoted arm needed no citation, so a live re-assertion riding one
                # quotation was exempt — the unquoted twin above is pinned, this is the
                # twin that was not, and `origin/main` flags all three.
                'Previously the doc read "there is no plan called Builder"; today there '
                'is no plan called Builder.\n',
                'Previously the page read "there is no Builder plan"; today there is no '
                'Builder plan either.\n',
                # …and the SAME arm's own intensifier tail (`today` interposed between the
                # predicate and `either` is the documented miss; `either` alone is pinned).
                'Previously the doc read "Builder is not a plan"; Builder is not a plan '
                'either.\n',
                # ⛔ THE CITATION IS NOT THE LEVER — THE JOINER IS (round-2 review,
                # measured): the reported arm's own clause-break test never saw a `;`,
                # `,` or `—` after the quoted claim, so all three stayed exempt at this
                # head while `origin/main` flags them. They are pinned live, which is
                # what the outside-quotation rule has to keep true.
                'Until #12444 the doc said "no plan called Builder"; there is no '
                'Builder plan.\n',
                'Until #12444 the doc said "no plan called Builder", there is no '
                'Builder plan.\n',
                'Until #12444 the doc said "no plan called Builder" \u2014 there is no '
                'Builder plan.\n',
                # ⛔ THE SCAN, NOT JUST THE DECISION (cycle-18 P1, measured): the
                # `builder … does not exist` arm needs neither `plan` nor `tier`, and the
                # scan's word pre-filter required one of them, so `_denial_in` answered
                # True and the GUARD never saw the sentence. Pinned through the guard.
                "Builder does not exist.\n",
                "Note: Builder doesn't exist.\n",
                # ⛔ THE BLOCK-BOUNDARY RULE'S OWN PIN (cycle-18 P2, measured): a live
                # denial as the FIRST of two list items. With `_BLOCK_START` disabled the
                # two items JOIN into one sentence whose clause end no longer fires, so
                # the denial disappears entirely — measured, the only shape found that
                # depends on the rule at all.
                "Plans we sell:\n- there is no Builder plan\n- the Team grid follows.\n",
                # ⛔ THE REPORTED DETOUR, PINNED (cycle-20, measured): the reported
                # arm accepts a denial only BEFORE its first clause break, so this
                # sentence stays FLAGGED because `and` ends the reported clause. Widen
                # that set to exclude `and` and it is exempt — the same detour the
                # replaced 6-character offset existed to stop, now bounded by
                # `_REPORTED_CLAUSE_END`.
                "Until #12444 the doc said the naming was wrong and there is no Builder "
                "plan.\n",
                # ⛔ EVERY MEMBER OF `_REPORTED_CLAUSE_END` IS PINNED, ONE FOR ONE
                # (cycle-20 round 2, measured). The set opened with `and` alone pinned, so
                # deleting `;`/`:`/`,`/`—`/`–`/`--`/`so` still left `19 passed` while each
                # one turned a FLAGGED denial into an exempt one — the cycle-11 rule this
                # file restates below, and the reason the first cut of that constant shipped
                # four false passes on `--`/`while`/`although`/`yet`. Every sentence below
                # reads True at this head, at the pre-fix head (`5cf012d2a53e`) and at
                # `origin/main`, so no new behaviour is demanded — only recorded.
                # Punctuation, one pin each:
                "Until #12444 the doc said the naming was wrong; there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong: there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong, there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong — there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong – there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong -- there is no Builder "
                "plan.\n",
                # …and every connective, one pin each:
                "Until #12444 the doc said the naming was wrong but there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong so there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong which is why there is no "
                "Builder plan.\n",
                "Until #12444 the doc said the naming was wrong therefore there is no "
                "Builder plan.\n",
                "Until #12444 the doc said the naming was wrong thus there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong hence there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong whereas there is no "
                "Builder plan.\n",
                "Until #12444 the doc said the naming was wrong while there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong although there is no "
                "Builder plan.\n",
                "Until #12444 the doc said the naming was wrong though there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong yet there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong since there is no Builder "
                "plan.\n",
                "Until #12444 the doc said the naming was wrong because there is no "
                "Builder plan.\n",
                # ⛔ AN UNGATED ASIDE STRIP SWALLOWS THE ASSERTION (cycle-20 round 4,
                # measured): each of these is one of the pinned denials above with a
                # NON-retraction parenthetical inserted, and each was EXEMPT until
                # `_REPORTED_ASIDE` was cue-gated. `origin/main` flags all three, so a
                # regression here is invisible to a main-comparison sweep.
                "Until #12444 the doc said the naming was wrong -- and the page still "
                "carries it -- there is no Builder plan.\n",
                "Until #12444 the doc said the naming was wrong — the page was never "
                "fixed — there is no Builder plan.\n",
                "Until #12444 the doc said the naming was wrong yet again there is no "
                "Builder plan.\n",
                # ⛔ A CUE WORD IS NOT AN ASIDE (cycle-20 round 5, measured): the round-4
                # gate keyed the strip on `_RETRACTION_CUES`, so wrapping ANY cue in a
                # pair defeated all 20 `_REPORTED_CLAUSE_END` joins at once — 20 of 20
                # pinned denials read EXEMPT behind `-- <cue> --`. Position, not
                # vocabulary, decides; this pin is the bypass.
                "Until #12444 the doc said the naming was wrong -- stale -- there is no "
                "Builder plan.\n",
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
            # Cycle-6 P2, each measured escaping: the docs' own typographic quotes,
            # the sibling verb of `isn't`, verb-carried negation, the displaced
            # predicate, the noun-before-the-name shape, and a bare issue citation.
            "There is no \u201cbuilder\u201d plan.",
            # Typographic APOSTROPHE, in every arm that spells a contraction (cycle 7:
            # only the `doesn\u2019t exist` arm carried it, so these three escaped).
            "The Builder tier doesn\u2019t exist.",
            "There isn\u2019t a Builder plan.",
            "Builder isn\u2019t a plan.",
            "There aren\u2019t any Builder plans.",
            "There are not Builder plans.",
            # Cycle-9 P2: a closing quotation mark may follow the phrase, which the
            # clause ends did not accept even though `_BUILDER_PLAN` was widened to
            # typographic quotes in cycle 6 for exactly this family.
            'There is no "Builder plan".',
            "There is no \u201cBuilder plan\u201d.",
            'There is no plan called "Builder".',
            'We do not offer a "Builder plan".',
            # …and the quoted twin of the pinned intensifier class, which the same gap
            # made invisible (cycle-10 P2).
            'There is no "Builder plan" anymore.',
            'There is no "Builder plan" at all.',
            "The Builder tier doesn't exist.",
            "We don't offer a Builder plan.",
            "Builder is not available as a plan.",
            "There is no such plan as Builder.",
            "There is no plan called Builder.",
            # ⛔ `_CLAUSE_END`'s CLOSING-QUOTE TOLERANCE, PINNED (cycle-18 P2, measured):
            # removing it left the suite at 19 passed AND turned this measured drift
            # green — the comma arm of the clause end could not see past the `"`.
            'There is no "Builder plan", only pro and team.',
            # ⛔ the `builder buys …` arm, PINNED (cycle-18 P2, measured): deleting it
            # left the suite green, so the drift's own example sentence had no pin.
            "a builder buys pro or team.",
            # ⛔ `either` as an intensifier (cycle-18 P1, measured).
            "There is no Builder plan either.",
            # …and the predicate-nominative arm's intensifier twin, which the round-2
            # review found missing entirely (the arm was born without one, like the
            # cycle-6/7 arms before it).
            "Builder is not a plan anymore.",
            "Builder is not a plan either.",
            # …and the intensifier twins of the two existential arms widened in cycle 18,
            # so neither arm can be narrowed back without a red.
            "No Builder plan exists anymore.",
            "The Builder tier doesn't exist anymore.",
            "No Builder plan exists whatsoever.",
            # ⛔ The widened restatement must still NAME the identifier's VALUE
            # (cycle-12 P2, measured): requiring only the noun let `Builder is the name
            # of a plan` and `Builder is the tier you cannot buy` exempt a live denial.
            'There is no "builder" plan — Builder is the name for the `pro` tier.',
            "There is no Builder plan — Builder is the tier you cannot buy.",
            "There is no Builder plan — Builder is the name of a plan.",
            "There is no such thing as a Builder plan.",
            # The closed intensifier set was missing the two most common siblings
            # (cycle-13 P2, measured).
            "There is no Builder plan whatsoever.",
            "There is no Builder plan of any kind.",
            # ⛔ …and the two SHELTERING shapes (cycle-18 P1 + P2, measured): a denial
            # may not be REPORTED across a clause break, and a quotation may not SHEATHE
            # a denial the sentence asserts outside it. `origin/main` FLAGS both; this
            # head exempted both.
            'Until #12444 the doc said "the Builder plan is unlimited"; the Builder tier '
            'does not exist.',
            'Until then the doc said "no plan called Builder" and there is no Builder plan.',
            # ⛔ …and the MENTION exemption added in cycle 19 must not become a new
            # sheath (cycle-19 P1, measured in both directions): a sentence that prints
            # the forbidden sentence and THEN asserts it is still a denial. Without these
            # the new exemption could be widened into a licence and no test would red.
            'Never write "there is no Builder plan"; there is no Builder plan.',
            'The doc said "there is no Builder plan"; a builder buys pro or team.',
            # ⛔ APOSTROPHES MUST NOT BRIDGE A DENIAL (round-3 review, P1, measured):
            # an elision opened a "quotation" and a plural possessive closed it, so one
            # CLOSED span wrapped the whole denial and `_denials_in_document` returned
            # `[]` for the live drift — while `origin/main` FLAGS it. Two ordinary
            # apostrophes made the drift re-introducible verbatim.
            "Since the '90s there is no Builder plan, and the customers' rows are wrong.",
            "In the '90s there is no Builder plan, and the builders' rows are wrong.",
            "'Twas noted there is no Builder plan, and the docs' row is wrong.",
            # ⛔ A LIVE PREDICATE BESIDE A QUOTED SUBJECT IS NOT A MENTION (round-4 review,
            # P1, measured). Requiring only the match's START to be inside the span let
            # every one of these through, because masking the span deletes the SUBJECT and
            # the outside rule then has no `builder` to match — so the exemption became a
            # licence for the drift. All five were FLAGGED at the parent and at
            # `origin/main`. The tail after a span may be only a closing mark and a
            # closed-set intensifier.
            'The tier called "Builder" does not exist.',
            'The tier called \u201cBuilder\u201d does not exist.',
            'The **"Builder"** tier does not exist.',
            'The page calls the tier "Builder"; the plan does not exist.',
            'Notes say "Builder" plan does not exist.',
            'The doc says "there is no Builder plan" and there is no Builder plan.',
            # ⛔ THE TWO CLAUSE ENDS DO NOT EACH CARRY A `_CITATION` PIN (cycle-20,
            # measured). A cycle-16 revision claimed "One pin each, so neither term can
            # be deleted silently"; measurement falsifies it for `_CLAUSE_END`, whose
            # `_CITATION` was dead surface and is now deleted (see that constant). The
            # strict set's copy IS pinned: removing it alone reds this test. What these
            # two sentences actually pin is the CITATION-accepted clause end itself, on
            # both arms, which is the class the cycle-11 rule demanded (an exemption half
            # that is stated but not pinned left the suite green when it was removed).
            "Builder is not a plan (see #12444).",
            "There is no Builder plan at all (#12444).",
            # ⛔ THE RESTATEMENT EXEMPTION MUST END THE CLAUSE (cycle-15 P1, measured):
            # unbounded, it made the whole REST of the sentence invisible, and on the
            # repo's dominant `—` it exempted this file's OWN measured drift reflowed
            # with the exemption's phrasing — base FLAGGED, this head EXEMPT.
            'the tiers are free, solo, pro, team, anon — there is no "builder" plan — '
            "the identifier is pro, so buy pro or team",
            "There is no Builder plan — only the `pro` identifier, because Builder was "
            "discontinued.",
            "There is no Builder plan; only the `pro` identifier, so buy pro or team.",
            # ⛔ The three arms added in cycles 6/7 were born WITHOUT the intensifier
            # twin the older arms learned for in cycle 4 — one trailing qualifier
            # defeated all three (cycle-11 P2, measured).
            "We don't offer a Builder plan anymore.",
            "We don't offer a Builder plan at all.",
            "Builder is not available anymore.",
            "Builder is not sold at all.",
            "There is no plan called Builder anymore.",
            # …and the noun-before-name arm matched `plan` only, while every other arm
            # spells `tiers?` (cycle-11 P2, measured).
            "There is no tier called Builder.",
            "There is no such tier as Builder.",
            # …and a citation written in the repo's house style (`git grep -o '(see #'
            # -- docs` = 10) escaped the clause end, which accepted only a bare `(#N)`.
            "There is no Builder plan (see #12444).",
            "There is no Builder plan (per #12444).",
            "There is no Builder plan (tracked in #12444).",
            "There is no Builder plan (#12444).",
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
            # …and the ingredients the cycle-6/8 review ADDED (cycle-11 P2, measured):
            # this guard did not ban them, so the next test to reach for one could pin
            # the prose around the decision and stay green — the cycle-2 defect this
            # test exists to prevent, one function later.
            "_is_a_correction_record",
            "_quotes_the_denial",
            "_REPORTED_CLAIM",
            "_reports_a_denial",
            # …and the rest of the composition surface the same review left reachable
            # (cycle-12 P2, measured: an injected test pinning `_BUILDER_PHRASE` ran
            # green, so the guard was silent on an ingredient added by this commit).
            # ⛔ `_BUILDER_PHRASE` and `_QUOTED_NEGATION` were DELETED in cycle 17 and are
            # no longer banned — banning a name that does not exist is not a guard.
            "_quoted_spans",
            # …and the OFFSETS view the cycle-9/18 scanner added, which the set above
            # missed (cycle-19 P2, measured: an injected test pinning
            # `_quoted_span_offsets` ran GREEN, so the guard was silent on the ingredient
            # the scanner work introduced — the same defect this set was extended for in
            # cycles 6/8/11/12/13). The rest of the scanner/instrument surface is banned
            # with it rather than left reachable for the next author to rediscover.
            "_quoted_span_offsets",
            # ⛔ `_REPORTED_CLAUSE_BREAK` and `_NEGATION_WORD` were DELETED in the
            # round-2 review and are no longer banned — banning a name that does not
            # exist is not a guard (the same rule that removed `_BUILDER_PHRASE` and
            # `_QUOTED_NEGATION` here in cycle 17). ⛔ `_REPORTED_DENIAL_OFFSET` was
            # REPLACED in cycle 20 by `_REPORTED_CLAUSE_END`, so the ban moves with it:
            # the deleted name is dropped and the live ingredient takes its place.
            "_REPORTED_CLAUSE_END",
            "_QUOTE_PAIRS",
            "_BLOCK_START",
            "_asserts_a_denial_outside_the_quotes",
            "_quotes_the_whole_denial",
            "_CITATION",
            "_RESTATEMENT_TAIL",
            "_RETRACTION_TIE",
            # …and the four that compose straight into `_DENIAL_PATTERNS`, which the
            # comment above claimed to have covered and had not (cycle-13 P2, measured:
            # injected tests pinning `_INTENSIFIER`, `_CLAUSE_END` and
            # `_IDENTIFIER_RESTATEMENT` all ran green — and `_IDENTIFIER_RESTATEMENT` is
            # the exact constant the cycle-12 fix edited).
            "_INTENSIFIER",
            "_CLAUSE_END",
            "_CLAUSE_END_STRICT",
            "_IDENTIFIER_RESTATEMENT",
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
