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

import json
import re
from pathlib import Path

import pytest

from tortoise.pricing import (
    TIER_DISPLAY_NAMES,
    all_tiers,
    tier_display_name,
    tier_display_names,
)
from tests.test_website_static import (
    DASHBOARD_PRICING_JS,
    PRICING_JSON,
    PRODUCT_HTML,
    PUBLIC_TIERS,
    _render_pricing_fn,
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

    def test_every_declared_tier_has_a_display_name(self):
        """No tier may reach a rendered surface without one.

        The failure this prevents is a caller falling back to the identifier, so
        ``pro`` ships to a customer who was sold ``Builder``.
        """
        assert set(tier_display_names()) == set(all_tiers())

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
_BUILDER_PLAN = r"['\"`]?builder['\"`]?\s+(?:plan|tier)"

#: The assertion must END its clause. Without this the patterns match the HEAD of a
#: qualified true statement ("there is no Builder plan cheaper than Solo"), which is
#: the cycle-2 false block. `$` inside a lookahead is the end of the STRING, which is
#: what a line-based scan gives us.
_CLAUSE_END = r"(?=\s*(?:$|[.;,)]|—|–|--|;))"

_DENIAL_PATTERNS = (
    # "there is no 'builder' plan" — asserted, unqualified absence.
    re.compile(r"\bthere(?:'s| is| are)\s+no\s+" + _BUILDER_PLAN + _CLAUSE_END, re.I),
    # "no 'builder' plan exists" (with an explicit non-existence predicate)
    re.compile(r"\bno\s+(?:such\s+(?:thing\s+as\s+(?:a\s+)?)?)?" + _BUILDER_PLAN +
               r"\s+exists\b", re.I),
    # "no such thing as a builder tier"
    re.compile(r"\bno\s+such\s+(?:thing\s+as\s+(?:a\s+)?)?" + _BUILDER_PLAN, re.I),
    # "the builder plan does not exist"
    re.compile(r"\bbuilder\b[^.]{0,40}\b(?:does|do)\s+not\s+exist\b", re.I),
    # "Builder is not a plan/tier" — predicate nominative, clause-final, so a plan
    # name used as an OBJECT ("the Builder tier is not a tier you can downgrade
    # to") does not match.
    re.compile(r"\bbuilder\s+(?:plan\s+|tier\s+)?(?:is|are)\s+not\s+(?:a\s+)?"
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
    if not any(pattern.search(sentence) for pattern in _DENIAL_PATTERNS):
        return False
    return not _quotes_a_retracted_claim(sentence)


def _doc_sentences(text: str) -> list[tuple[int, str]]:
    """(line number, sentence) pairs — line numbers so a failure is actionable."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Sentence boundaries inside a line; a markdown hard-wrap joins back up
        # because the denial forms above never span a wrap in practice, and a
        # false negative on an exotic wrap is preferable to a lint nobody trusts.
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if sentence:
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
            # A doc recording the retraction — cue AND evidence (#12444 cited).
            "docs/product/beta-sdk-surface.md previously read 'there is no "
            "\"builder\" plan'; #12444 corrected it.",
            "The denial is no longer present since 2026-09-21.",
        )
        for sentence in correct:
            assert not _denial_in(sentence), (
                f"the lint false-blocks correct prose: {sentence!r}")

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

    def test_the_tests_assert_the_decision_not_the_patterns(self):
        """The structural half: no test above may bypass :func:`_denial_in`.

        Cycle 2 proved this matters — with the tests matching `_DENIAL_PATTERNS`
        directly and the loop applying the exemption at a separate call site,
        appending `" (stale)"` to every denial kept the tests green while the loop
        reported nothing. Assert on the decision, never on its ingredients.
        """
        source = Path(__file__).read_text(encoding="utf-8")
        # Scan the tests ABOVE this one (this check's own source necessarily names
        # the ingredients it bans).
        body = source[
            source.index("class TestNoDocDeniesABuilderPlanExists"):
            source.index("    def test_the_tests_assert_the_decision_not_the_patterns")]
        bypasses = [line.strip() for line in body.splitlines()
                    if "_DENIAL_PATTERNS" in line or "_RETRACTION_CUES" in line]
        assert not bypasses, (
            f"a test asserts against the lint's INGREDIENTS rather than its "
            f"decision (`_denial_in`), so it can stay green while the guard is "
            f"bypassed: {bypasses}")

    def test_no_doc_asserts_a_builder_plan_does_not_exist(self):
        offenders: list[str] = []
        for doc in sorted(DOCS_ROOT.rglob("*.md")):
            text = doc.read_text(encoding="utf-8", errors="replace")
            if "builder" not in text.lower():
                continue
            for lineno, sentence in _doc_sentences(text):
                low = sentence.lower()
                if "builder" not in low:
                    continue
                if "plan" not in low and "tier" not in low and "buys" not in low:
                    continue  # "buys" covers the "a builder buys pro or team" shape
                if _denial_in(sentence):
                    offenders.append(
                        f"{doc.relative_to(REPO_ROOT)}:{lineno}: "
                        f"{sentence.strip()}")
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
