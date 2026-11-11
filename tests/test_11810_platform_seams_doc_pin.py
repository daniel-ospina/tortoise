"""#11810 review (P2) — `platform-seams.md` and the shipped install message cannot drift apart.

WHY THIS PIN EXISTS. `docs/research/2026-09-01-gbrain-learnings/platform-seams.md` §3
cites the `tortoise install cursor` message as the evidence that the cloud-agent
exclusion is **HOSTED**-scoped, and that record is what a future lane cites for
#11807. The r6 review measured the drift in the DOC direction: the record quoted
*"a session running in a cloud agent VM"* — the UNSCOPED universal #11806 carried
and this PR removes — while the shipped message says *"in a HOSTED cloud agent VM"*.
A fragment in double quotes that is not in its source reads as evidence FOR the
over-assertion it sits next to.

WHY ITS OWN FILE, REGISTERED IN `tier1` (as well as `api` and `core`). The guarded
artifact is under `docs/`, and `docs/` is in `NON_PYTHON_PREFIXES`, so a
**docs-only** diff selects NO surface and takes the tier1-only early return. The pin
originally shipped inside `tests/test_capture_install.py` — which is registered on
`api` + `core` and NOT in `tier1` — so on a docs-only edit of this very record it ran
**zero times**: a lane doing #11807's doc half could restore the unscoped quote,
merge green, and only red `main` post-merge (the `push` build takes the full
selection). That is the #4309/#8474/#8614 class, and every sibling doc pin
(`test_10000_cache_projection_doc_pin.py`, `test_10191_record_move_doc_pin.py`,
`test_8093_registry_namespace_doc.py`, …) is dual/triple-registered for exactly this
reason.

`api` is registered for the message-direction arm: a change to
`tortoise/__main__.py` matches `SOURCE_PATTERNS["api"]`, which REPLACES the `core`
fallback — measured, `printf 'tortoise/__main__.py\\n' | tools/ci_selection.py
--changed-files -` → `surfaces: ['api']`.

⚠️ Not claimed: that `api` (or `core`) is what makes the pin RUN. Review r10 (P3)
measured the opposite and this file now states it: `select()` unions `tier1` into
**every** PR selection (`files = set(tier1)`, then the surface's members), so this
file's `tier1` row ALONE selects the pin on both the docs-only diff and the
`__main__.py` diff, and the two surface rows add no coverage. They are kept
belt-and-braces as the durable home — `tier1` is a curated smoke list, and one
edit there would drop the pin silently — not because either row carries an arm.
`tests/test_ci_selection.py` measures both facts instead of asserting them in prose.

WHAT IT ASSERTS, in BOTH directions:

  (1) the record quotes the HOSTED-scoped fragment, and the unscoped WORDS do not
      reappear **in any form** — the r6 defect;
  (2) the shipped message really contains the fragment the record quotes — so
      dropping `hosted` from the message (or restoring the unscoped quote in the
      record) REDs instead of passing silently.

`_SHIPPED_FRAGMENT` is the single string both arms bind to, so the two cannot be
corrected in opposite directions without this file noticing.

⛔ BOTH ARMS MATCH ON `_normalised()` — ONLY THE WORDS ARE THE CLAIM. Three
successive rounds shipped a form-exactness hole here, each one level deeper: r7
matched `("…")` only (a re-emphasis walked through); r8's bare-words fix was
still whitespace- and case-exact (a hard wrap inside the phrase walked through —
this record is hard-wrapped, line 315 is 95 columns); r9 stripped whitespace and
case but not PUNCTUATION, so a delimiter INSIDE the phrase walked through. Every
one of these is the same words and the same claim.

So `_normalised()` reduces text to its word sequence — HTML tags are stripped,
HTML entities are decoded, anything else that is not a letter or digit becomes a
separator, and case is folded. That closes the SEPARATOR-VALUED and
ENTITY/TAG-VALUED spellings of the same words **where the separator or the markup
falls BETWEEN words** (or wraps the phrase): reflow, tab/`\r\n`, literal nbsp,
zero-width space, case, hyphenation, markdown emphasis around or between words
(whole-phrase `*"…"*`, or in-phrase `**cloud agent VM**`, `` `cloud agent VM` ``,
`*cloud*`, `~~…~~`, `_…_`), and `&nbsp;` / `&#32;` / `&mdash;` / `<span>` / `<b>`
— the entity and tag forms render as the identical sentence, so a pin that only
blocked the literal characters would call the rendered artifact safe.
⚠️ "Between words" is load-bearing, not a hedge: a separator or tag INSIDE a word
(`clou<ZWSP>d`, `clou-`+`d`, `cl<b>ou</b>d`) does not respell the phrase, it
SPLITS it, so it is missed — the residual bullet below states this accurately and
this paragraph defers to it (review r12 P3 #3, which measured the unconditional
reading as the over-claim).

The PRESENCE arms read that ONE rendering. The ABSENCE arm reads `_renderings()`
— a union over every strip — because no single tag strip is complete and a single
one can be made to HIDE the banned phrase (both directions measured; see
`_renderings`).

WHAT IT STILL DOES NOT CATCH, stated so the docstring is not the next over-claim:

  * a PARAPHRASE, a synonym, a different word order, or a JOINED token
    (`cloudagent`) — beyond any string guard. `test_capture_install.py` keeps an
    ENUMERATED list for that and says so; #11812 holds the structural form.
  * a delimiter INSIDE a word (`clou\u200bd`, `clou\u00add`, `cl<wbr>oud`) splits
    the word rather than separating it, so the fragment is no longer a word
    subsequence. No editor produces these; measured as missed, recorded rather
    than claimed.
  * a HOMOGLYPH (`\u0441loud` rendering as `cloud`). That needs a confusables
    table; it is out of proportion for a docs pin, so it is a recorded residual
    rather than a fix. (Measured: entities/tags now caught, homoglyphs not.)
  * this arm is deliberately OVER-BROAD in the safe direction: it bans the words
    wherever they appear in any order-adjacent form, so an HONEST correction that
    quotes the retracted wording to reject it — this record's own house style,
    see line 309 — REDs. Reword such an entry instead of reproducing the phrase
    verbatim. Likewise the presence arm answers "are these words in this file",
    not "is this an actual quote", so it cannot tell a quote from prose.

MUTATION (each verified RED at this head, restoring byte-identical):
  * restore the unscoped quote at `platform-seams.md:315`      → (1) RED
  * re-add with OTHER delimiters, whole-phrase (`*"…"*`)      → (1) RED (r8)
  * re-add with a LINE BREAK / double space / capital           → (1) RED (r8)
  * re-add with in-phrase `**`, `` ` ``, `*`, `~~`, `_`         → (1) RED (r9)
  * re-add with a zero-width space or a hyphen                 → (1) RED (r9)
  * re-add with `&nbsp;`/`&#32;`/`&mdash;`/`<span>`/`<b>`        → (1) RED (r10)
  * correct quote with `**hosted**` emphasised → now PASSES    → was RED (P3)
  * drop `hosted` from `tortoise/__main__.py`'s message         → (2) RED
  * delete the record's quoted sentence                         → (1) RED
  * widen the tag strip back to `<[^>]+>` and inject one
    "a hook count of < 5 is suspect" before §3's quote          → new tag test RED
  * `_renderings` → `[_normalised(text)]` (the `_TAG` reading only)
    → the bare-attribute sample REDs               (r12 P3 #1)
  * `_renderings` → the bare-attribute reading only
    → the prose-forgery sample REDs                (r12 P3 #1)
  * revert `_TAG` to the permissive `_TAG_WITH_BARE_ATTRS` form
    → the forged-tag PRESENCE assert REDs          (r12 P3 #1)
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLATFORM_SEAMS_DOC = (_REPO_ROOT / "docs" / "research"
                       / "2026-09-01-gbrain-learnings" / "platform-seams.md")
#: The fragment the record quotes AND the message must carry. One constant, so a
#: correction applied to one side only cannot satisfy both arms.
_SHIPPED_FRAGMENT = "a session running in a hosted cloud agent VM"
_UNSCOPED_FRAGMENT = "a session running in a cloud agent VM"
_PY = sys.executable

#: A tag's attributes AS MARKUP — `name=value`, quoted or bare. NOT `[^<>\n]*`:
#: a bare word list is PROSE, and `if a<b and c>d then …` is an ordinary sentence,
#: so a permissive attribute group forges a "tag" out of it and deletes
#: everything between the two comparison signs. The ABSENCE leg survives that either
#: way (it unions three strips, so no single one can hide a phrase), but the
#: PRESENCE legs read ONE strip, and with the permissive form a record whose quote
#: sits between a prose `<` and `>` false-REDs — a correct record failing the pin.
#: Measured both ways; pinned by
#: `test_a_stray_angle_bracket_cannot_swallow_the_quoted_sentence`.
_TAG_ATTRS_ASSIGNED = (r"(?:\s+[A-Za-z_:][-A-Za-z0-9_:.]*\s*=\s*"
                       r"(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))*")

#: …and a TAG-SHAPED strip, deliberately not `<[^>]+>`. The fully loose form spans
#: any `<` … `>` anywhere in a 50 KB document, so one ordinary sentence — "a hook
#: count of < 5 is suspect" — swallows everything up to the next `>`. Measured on
#: THIS record with that sentence injected IMMEDIATELY BEFORE §3's quote (line
#: 315): the loose form matches a single 2,481-byte span that CONTAINS the quoted
#: words, so it deletes them and false-REDs the presence arm on a record that
#: states the right thing; `_TAG` matches nothing there.
#: ⚠️ The measured reason `_TAG` is preferable is NOT that it keeps prose
#: angle-brackets — it does not. `<harness>` and `<ws>` are tag-SHAPED, so BOTH
#: forms strip them. Over this record `<[^>]+>` strips 314 bytes (28 spans) and
#: `_TAG` 179 (23 spans), and the whole 135-byte difference is five spans that
#: are not tags: `<state.db>`, `<session_id>`, `<lookup_key>`, `<message shown to
#: user when blocked>` and `<CustomMessage, "customType"|…>`.
#: `_TAG` cannot cross a `<`, a `>`, or a NEWLINE at all (measured:
#: `re.fullmatch(_TAG, "<a\nb>")` and `"<a\r\nb>"` are both non-matches, since an
#: attribute must be an ASSIGNMENT). That bound, not prose preservation, is the
#: property it buys.
_TAG = rf"</?[A-Za-z][A-Za-z0-9-]*{_TAG_ATTRS_ASSIGNED}\s*/?>"
#: A strip that ADMITS bare (boolean) attributes, e.g. `<span hidden>`. Kept as a
#: second absence rendering, never as the only one — see `_renderings`.
#: ⚠️ Its bound is NOT `_TAG`'s (r13, measured): it cannot cross a `<` or a `>`, and
#: its attribute part consumes at most ONE **newline**, and only immediately after
#: the tag name — after that one `\s`, `[^<>\n]*` accepts any amount of HORIZONTAL
#: whitespace (`<a  b>`, `<a b c d>` and `<a\tb\tc>` all match), so "one
#: whitespace character" would be the wrong quantifier. `re.fullmatch` matches
#: `"<a\nb>"`, does not match `"<a\r\nb>"`, and
#: `"<b\nhere is a session running in a hosted cloud agent VM >"` matches with the
#: quoted words INSIDE the match. So it is tag-shaped, not line-bounded, and it can
#: swallow a line. That is tolerable only because the absence leg unions it with
#: `_TAG` and a no-strip reading: this form can never be the sole judge. To forbid
#: the newline here too, narrow `\s` to `[ \t]` — a behaviour change, deliberately
#: not made, and no longer load-bearing for the absence leg.
_TAG_WITH_BARE_ATTRS = r"</?[A-Za-z][A-Za-z0-9-]*(\s[^<>\n]*)?/?>"


def _normalised(text: str, tag_pattern: str | None = _TAG) -> str:
    """The WORDS of `text`, for BOTH arms — markup, punctuation and case gone.

    `tag_pattern=None` strips NO markup (the third absence rendering).

    HTML tags are removed and HTML entities decoded BEFORE the word collapse, so
    `&nbsp;`, `&#32;`, `&mdash;`, `<span>` and `<b>` — which RENDER as the same
    sentence — cannot carry a banned phrase past the guard. Everything that is
    then not a letter or digit becomes a separator, and case is folded, which
    makes reflow, tab/`\\r\\n`/literal nbsp, zero-width space, hyphenation and
    markdown emphasis INDISTINGUISHABLE from the plain words, including a
    delimiter in the MIDDLE of the phrase.

    What it deliberately does NOT do, so this docstring cannot become the next
    over-claim: no accent folding, no synonym or word-order handling (a
    paraphrase is out of reach of any string guard), and no HOMOGLYPH folding —
    `\\u0441loud` renders as `cloud` but needs a confusables table, which is out of
    proportion here and is a recorded residual.

    One function for every arm, so they cannot drift apart — the failure this
    file exists to catch, one level up. The PRESENCE arms read it once; the
    ABSENCE arm reads it through `_renderings`, which applies it to each of three
    tag strips and takes the union.
    """
    if tag_pattern is not None:
        text = re.sub(tag_pattern, " ", text)
    return re.sub(r"[\W_]+", " ", html.unescape(text).lower()).strip()


def _renderings(text: str) -> list[str]:
    """Every render-identical reading of `text` — the ABSENCE leg judges ALL of them.

    ⚠️ No single tag strip is complete, and each one is fail-OPEN in the opposite
    direction from the other (both measured, review r12 P3 #1):

      * a strip admitting BARE attributes reads prose as a tag — `if a<b and … c>d`
        loses everything between the comparison signs, banned phrase included;
      * `_TAG`, whose attributes must be ASSIGNED, keeps that prose but leaves a
        bare-attribute tag's words INTERPOSED, so
        `A <span hidden>session running in a cloud agent VM` is never adjacent to
        the words that make it the banned phrase.

    The UNION closes both: a phrase counts as present if ANY reading shows it, so
    no single strip can hide it. The third reading strips nothing at all, which
    covers prose that forges a tag no pattern anticipates (a cost of 3
    normalisations over a 50 KB record — the pin runs 3 tests).
    """
    return [_normalised(text, p) for p in (_TAG, _TAG_WITH_BARE_ATTRS, None)]


def _absent(fragment: str, text: str) -> bool:
    """True only if `fragment` survives in NO rendering — the fail-closed question.

    `any`, not `all`: the absence leg must RED if any reading reveals the words,
    which is why over-stripping one rendering can no longer manufacture a PASS.
    """
    needle = _normalised(fragment)
    return not any(needle in rendering for rendering in _renderings(text))


def _install_cursor_stdout(tmp_path) -> str:
    """The REAL `tortoise install cursor` output, on an isolated HOME.

    Not a source scrape: the pin is on what a customer is told, so it reads the
    same stdout `test_capture_install.py` reads. `TORTOISE_DB_URI=""` keeps it
    DB-free so the tier1 (URI-less) legs can run it.
    """
    root = tmp_path / "proj"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }
    r = subprocess.run(
        [_PY, "-m", "tortoise", "install", "cursor", "--dir", str(root)],
        env=env,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_the_record_quotes_the_HOSTED_scoped_fragment():
    """Direction 1 — the record's quote, not the message's.

    Mutation: drop `hosted` from the quoted fragment (the r6 drift), or restore
    the unscoped quote beside it — both RED here."""
    doc = _PLATFORM_SEAMS_DOC.read_text(encoding="utf-8")
    normalised_doc = _normalised(doc)
    assert _normalised(_SHIPPED_FRAGMENT) in normalised_doc, (
        "the platform-seams record must quote the HOSTED-scoped message")
    # …and the unscoped WORDS may not reappear at all, in ANY form. Four rounds
    # shipped a form-exact leg here and each false-passed the mutation it exists to
    # catch — r7 a whole-phrase re-emphasis (`*"…"*`), r8 a hard wrap inside the
    # phrase on this 95-column-wrapped record, r9 a delimiter in the MIDDLE of the
    # phrase (`**cloud agent VM**`, `` `cloud agent VM` ``, `*cloud*`, `~~…~~`,
    # `_…_`, a zero-width space, a hyphen), r10 render-identical markup
    # (`&nbsp;`, `&#32;`, `&mdash;`, `<span>`, `<b>`). Matching on the word sequence
    # closes the spelling class instead of enumerating it; the docstring's residual
    # list names what it still cannot see (paraphrase, homoglyph) and the two places
    # it is deliberately over-broad. The assert goes through `_absent`, not a single
    # rendering, so a tag strip that swallows prose cannot hide the words either.
    assert _absent(_UNSCOPED_FRAGMENT, doc), (
        "the record quotes the unscoped universal as though it were the message")


def test_a_stray_angle_bracket_cannot_swallow_the_quoted_sentence():
    """The strip itself, pinned — review r10 measured this fail-open direction.

    `_normalised` is the only thing between the record and both arms, so its tag
    strip is load-bearing: the loose `<[^>]+>` runs from ANY `<` to the next `>`,
    so one ordinary half-sentence ("a hook count of < 5 is suspect") deletes
    everything up to the following `>`, including a correctly quoted sentence,
    and the pin would RED a record that is right. Hermetic: no file, no install.

    Mutation: widen `_TAG` back to `<[^>]+>` — this REDs (measured; the loose
    strip removes the quoted words from the sample below)."""
    sample = ('a hook count of < 5 is suspect. §3 says '
              '"a session running in a hosted cloud agent VM" and <b>bold</b>.')
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(sample), (
        "a stray `<` in ordinary prose must not eat the quoted sentence")
    # …and the banned words must be SEEN when they sit inside the region a forged
    # tag would delete. Before review r12 P3 #2 this assert ran on a sample holding
    # only the hosted variant, so it could not fail under any mutant; placing the
    # unscoped phrase between the `<` and the `>` gives it the tooth the loose
    # strips (`<[^>]+>` and this file's own permissive form) actually bite on.
    prose_forgery = ('a hook count of < 5 is suspect — "a session running in a cloud agent VM" '
                     'would be banned. §3 quotes '
                     '"a session running in a hosted cloud agent VM" and <b>bold</b>.')
    assert not _absent(_UNSCOPED_FRAGMENT, prose_forgery), (
        "a forged tag must not swallow the banned phrase out of the absence leg")
    # …and the PRESENCE leg reads ONE strip (`_TAG`), so the same forgery must not
    # delete the quote there either. This is what makes `_TAG`'s ASSIGNED-attribute
    # narrowing load-bearing rather than decorative: the absence union survives the
    # permissive form, this assert does not.
    forged_around_the_quote = ('if a<b and §3 says "a session running in a hosted cloud agent VM" '
                               'c>d, the quote must survive.')
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(forged_around_the_quote), (
        "a prose `a<b … c>d` must not be read as a tag and eat the quote")


def test_no_single_tag_strip_can_hide_the_unscoped_phrase():
    """Review r12 P3 #1 — the attribute group, in BOTH fail-open directions.

    The absence leg takes the union (`_renderings`) precisely because each single
    strip hides the banned phrase in the case the other catches. Both samples are
    measured fail-opens of a single strip, and each is the other's mutant:

      * PROSE FORGERY — `if a<b and … c>d` is an ordinary sentence, but a strip
        admitting bare attributes reads `<b and … c>` as a tag and deletes the
        banned phrase between the comparison signs → false PASS;
      * BARE ATTRIBUTE — `A <span hidden>session running in a cloud agent VM`
        renders as the banned sentence, and `_TAG` alone leaves `<span hidden>`
        interposed, so the words are never adjacent → false PASS.

    Mutation: make `_renderings` return `[_normalised(text)]` (the `_TAG` reading
    only) → the bare-attribute sample REDs; return only the bare-attribute reading
    → the prose sample REDs. Dropping the no-strip reading too REDs the prose arm
    of the stray-`<` test above."""
    prose_forgery = ('if a<b and the record says "a session running in a cloud agent VM" c>d, '
                     'the pin false-PASSes.')
    bare_attribute = ('A <span hidden>session running in a cloud agent VM</span> is still the '
                      'unscoped universal, rendered.')
    tag_wrapped = 'A <span class="x">session</span> running in a cloud agent VM, split by markup.'
    for name, sample in (("prose-forgery", prose_forgery),
                         ("bare-attribute", bare_attribute),
                         ("tag-wrapped", tag_wrapped)):
        assert not _absent(_UNSCOPED_FRAGMENT, sample), (
            f"the absence leg must see the unscoped phrase in the {name} sample")


def test_the_shipped_message_carries_the_fragment_the_record_quotes(tmp_path):
    """Direction 2 — the message the record cites.

    Mutation: drop `hosted` from `tortoise/__main__.py`'s install message — the
    record's quoted fragment is then no longer produced, and this REDs."""
    stdout = _install_cursor_stdout(tmp_path)
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(stdout), stdout
