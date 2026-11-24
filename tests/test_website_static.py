"""Static parity tests for the pricing surface — issue #749.

The pricing section is the biggest revenue surface and had NO functional
test. product.html claims its `PRICING` object is a "data mirror of
product/pricing.json — single source", but a broken shape (missing key, NaN
price, undefined popular) throws mid-map, the grid renders blank, and CI stays
green (anti-#728).

Repo-local, zero network, stdlib only (the test_waitlist_form.py pattern):

1. Parse the PRICING object out of website/product.html (lightweight JS-object
   → JSON normalizer; no JS engine).
2. Assert every tier in product/pricing.json exists in the HTML mirror with
   price/popular/features/excluded and matching numeric limits
   (None → "∞" mapping).
3. Reimplement fmtPrice() math in Python → annual contract
   (per-month × 12 == billed total — the rounding-once invariant),
   price 0 → 'Free forever', 20% discount.
4. Element inventory (anti-#728): renderPricing/renderSelfHost/setBilling
   reference #pricing-grid / #pricing-usage-line / #selfhost-section /
   #btn-monthly / #btn-annual, and those element ids exist in the page; init
   wiring honors display.annual_default.
5. Copy pin for the `04 · Automate` beat (#3432, #8466): the paragraph is
   pinned so a paraphrase cannot slip past. The availability premise that used
   to sit over it is retired — outbound webhook push shipped (#2081, PR #8104)
   — see `TestAutomateBeatCopy`.
6. `/investors` Progress cell order + pinned bullet copy (PR #9283): the section
   must OPEN on what exists (`Product & distribution` left of `Early demand`),
   every bullet in both cells is pinned verbatim — curly quotation marks
   included — and the retired `20+ prospective beta testers` line must stay
   gone. The verbatim quotation is an ACCEPTED RISK recorded by the owner, not
   a defect to "fix" — see `TestInvestorsProgressCopy`.

7. Product-root marker contract (PR #9652 review, P1): the string that proves
   the tortoise host serves product.html at `/` is pinned in TWO surfaces that
   drift independently — `PRODUCT_ROOT_MARKER` in
   `tests/e2e/test_legal_pages.py` (exercised by the pre-merge `legal-e2e` job
   and by post-deploy `verify-legal`) and the `grep -qF` in the post-deploy DNS
   preflight (`.github/workflows/deploy-pages.yml`), which NO pre-merge leg
   reads. Both must be structural (not hero prose) and must agree; see
   `TestProductRootMarkerContract`.

8. Company-root marker contract (PR #9813 review, P1): the SAME contract one
   page over. `premiselabs.co/` is pinned by `COMPANY_ROOT_MARKER`
   (`data-page="company"` on index.html's `<html>`), because the pin it
   replaced was the company hero's prose — which PR #9813's hero rewrite
   deleted, reddening the served-root suite on a correct page; see
   `TestCompanyRootMarkerContract`.

9. Document outline (PR #9813 review, P2): no page may skip a heading level
   (h1 -> h3), and the company front page must keep a real `h2` for the
   Tortoise section; see `TestPageHeadingOutline`.

10. Short-viewport beats (PR #9813 review, P2): the beats whose content is
    taller than a landscape phone (`#beat-cta`, `#beat-tortoise` on index.html,
    `#beat-hero` on product.html) are fixed full-height overlays with centred
    content, so each must be covered by an `@media (max-height: ...)` rule that
    lets the stack scroll instead of clipping — AND must be able to RECEIVE the
    gesture, which `pointer-events: none` on `.beat` otherwise makes
    impossible; see `TestShortViewportBeatsScrollInsteadOfClipping`.

Run:  TORTOISE_TEST_CARVE_OUT=1 python -m pytest tests/test_website_static.py -v
      (a URI-less run fails at session setup by construction — epic #1647 P4;
      this file is static, offline and stdlib-only, so the carve-out lane fits.)
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import zlib
from html import unescape
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlsplit

# `pytest` is deliberately not imported (#8066): this file's only use was the
# `pytest.skip` that made the tortoise-decide gate green-while-blind.

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # tests/ and tools/ are not installed packages
    sys.path.insert(0, str(REPO_ROOT))

PRODUCT_HTML = REPO_ROOT / "website" / "product.html"
PRICING_JSON = REPO_ROOT / "product" / "pricing.json"
# The ONE pricing source (#13645). Both product pages load this file; the tier
# table used to be inlined in product.html, and `/tortoise-for-product-teams`
# renders the same prices, so a second copy would be two prices that can drift
# with nothing red. See `TestPricingSourceIsShared`.
PRICING_SOURCE = REPO_ROOT / "website" / "assets" / "tortoise-pricing.js"

#: The hosts this site serves itself from. An asset referenced through one of
#: these is SAME-ORIGIN — the browser loads the same file a relative path would —
#: so the absolute spelling must not be read as a foreign asset and skipped
#: (#13696 round 9, P3). `tortoise.premiselabs.co` is the PRODUCT host and serves
#: this same `website/` tree (`TestSiteIconAndSocialPreviewContract.SITE_ORIGINS`
#: pins the same pair), so
#: leaving it out read `https://tortoise.premiselabs.co/assets/x.js` as foreign
#: and left a `$25/mo` in it green — measured, #13696 round 10.
SITE_HOSTS = frozenset({"premiselabs.co", "tortoise.premiselabs.co"})
# The shared consent module (#658/#736). It GAINED a behaviour for #13645 — the
# `tortoise:consent` event — and the niche page's whole funnel reads it, so the
# seam is pinned here: see `TestConsentSeam`.
CONSENT_JS = REPO_ROOT / "website" / "consent.js"

#: The consent seam's calls, each anchored as a STATEMENT in the file's own code.
#: ⛔ Every one of these replaced a `str.find`/`in` substring test, which three
#: successive spellings defeated: a whole-line comment, a trailing comment, a
#: `//` glued to a colon, and a STRING LITERAL
#: (`throw new Error("opt_in_capturing() is handled by the caller")`). All four
#: left a deleted call green (#13696 rounds 6, 8, 9). A pattern anchored to the
#: line's own code cannot be satisfied by prose, a string, or a fragment of a
#: larger expression.
OPT_IN_STMT = re.compile(
    r"^[ \t]*window\.posthog\.opt_in_capturing\(\)[ \t]*;?", re.M)
LOADERS_STMT = re.compile(r"^[ \t]*loadConsentedAnalytics\(\)[ \t]*;?", re.M)
GRANTED_STMT = re.compile(
    r"""^[ \t]*announceConsent\((["'])granted\1\)[ \t]*;?""", re.M)
#: `;?` — the terminator is OPTIONAL on purpose. A statement's identity here is
#: the `^[ \t]*` anchor plus the full call text; requiring `;` red a call that is
#: present and ASI-terminated with "no longer calls `opt_in_capturing()`", which
#: is false and misleads the next reader (#13696 round 10, measured: dropping
#: only the semicolon failed this row while every other row was green).
#: The page's consent gate, as the statement it is: `if (!consented()) return;`.
NOT_CONSENTED_STMT = re.compile(
    r"^[ \t]*if \(!consented\(\)\)[ \t]*return\b", re.M)
# The niche product page (#13645) — a GTM experiment added ALONGSIDE
# `/tortoise`, served at the company root (`premiselabs.co/tortoise-for-product-
# teams`), not under the `/tortoise` prefix.
PRODUCT_TEAMS_HTML = REPO_ROOT / "website" / "tortoise-for-product-teams.html"
# The investor one-pager (PR #9283): its Progress section carries owner copy and
# a left/right cell order that is pure DOM order — see TestInvestorsProgressCopy.
INVESTORS_HTML = REPO_ROOT / "website" / "investors.html"
# #4336: the dashboard's display-name map — the second hand-maintained copy of
# the tier names (product.html renderPricing() carries the first).
DASHBOARD_PRICING_JS = (REPO_ROOT / "website" / "apps" / "dashboard"
                         / "src" / "pricing.js")

# Internal quota tiers that are NOT public offerings — they must not leak
# onto the pricing page (anon = unclaimed zero-email teams, raised to free
# on claim, #1082). Mirror/parity assertions operate on the public set.
INTERNAL_TIERS = frozenset({"anon"})
PUBLIC_TIERS = frozenset({"free", "solo", "pro", "team"})


# ── JS-object extraction (no JS engine, no network) ─────────────────────────


def _extract_pricing_object() -> dict:
    """Parse the `window.TORTOISE_PRICING = {...}` object from the ONE source.

    ⛔ It is NOT in product.html any more (#13645). The tier table moved to
    `website/assets/tortoise-pricing.js` because `/tortoise-for-product-teams`
    renders the same prices, and the owner's decision named a second copy as
    the defect: two prices that can drift with nothing red.
    `TestPricingSourceIsShared` holds both pages to this one file; everything
    below (`TestMirrorNumericParity`, `TestStaticPricingFallback`) now reads the
    shared source, so the mirror parity it asserts is a property of the ONE table
    every page renders. (`tests/test_tier_naming.py` does NOT read this file — it
    holds the tier NAMES across `product/pricing.json`, product.html's `labels`
    map and the dashboard's `TIER_LABELS`, so it is not part of this list.)

    Raises AssertionError (with the source context) when the block cannot be
    located — a renamed/moved constant must fail loudly, not skip.
    """
    src = PRICING_SOURCE.read_text(encoding="utf-8")
    start = src.index("window.TORTOISE_PRICING = {")
    obj_start = src.index("{", start)
    # The object closes on a line of its own (`};` at column 0); every nested
    # object is indented, so the first unindented `\n};` marks the end and the
    # object's own `}` is the character before it.
    end = src.index("\n};", obj_start)
    return json.loads(_normalize_js_object(src[obj_start:end + 2]))


def _normalize_js_object(src: str) -> str:
    """Quote bare identifier keys + drop trailing commas so json.loads works.

    Handles nested objects/arrays and string literals (single or double
    quoted, with backslash escapes). Only the object's OWN keys are quoted —
    values pass through verbatim.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(src[i:j])
            i = j
        elif c.isalpha() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            ident = src[i:j]
            k = j
            while k < n and src[k] in " \t\n":
                k += 1
            out.append(f'"{ident}"' if k < n and src[k] == ":" else ident)
            i = j
        elif c == ",":
            k = i + 1
            while k < n and src[k] in " \t\n":
                k += 1
            if k < n and src[k] in "}]":
                i = k  # trailing comma before } or ] — drop it
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


# ── fmtPrice() mirror (website/product.html, verbatim semantics) ────────────


def _fmt_price(price, mode: str, discount_pct: int) -> tuple[str, str]:
    """Python mirror of the page's fmtPrice(): (big, total) display strings.

    Annual: effective PER-MONTH price big, total billed small; round ONCE
    (per-month) then derive the total so the two never disagree
    (e.g. $7/mo × 12 must equal $84, not $86).
    """
    if mode == "annual":
        if price == 0:
            return "$0", "Free forever"
        disc = discount_pct / 100
        per_month = max(1, round(price * 12 * (1 - disc) / 12))
        total = per_month * 12
        return f"${per_month}", f"${total} billed annually"
    # The Free tier is free in BOTH modes — a $0 tier reading "Billed monthly"
    # contradicts the same card one click away, and the static card's own
    # "Free forever". Mirrors `website/assets/tortoise-pricing.js`.
    if price == 0:
        return "$0", "Free forever"
    return f"${price}", "Billed monthly"


# ── the SAME contract, executed from the JS both pages load ─────────────────

#: `window.tortoiseFmtPrice = function (…) {` in the shared source.
FMT_PRICE_HEAD_RE = re.compile(
    r"window\.tortoiseFmtPrice\s*=\s*function\s*\([^)]*\)\s*\{")


def _fmt_price_js_body() -> str:
    """The body of the shared formatter — the code BOTH pages actually execute.

    `_code_only`, so a COMMENT edit cannot red a row whose subject is the math.
    """
    code = _code_only(PRICING_SOURCE.read_text(encoding="utf-8"))
    m = FMT_PRICE_HEAD_RE.search(code)
    assert m, (
        "the shared source no longer defines `window.tortoiseFmtPrice` — the "
        "rounding contract has no home, so each page would re-implement it")
    return _balanced_block(code, m.start())


def _js_round(value: float) -> int:
    """`Math.round`: JS sends halves UP, where Python's `round` is half-to-EVEN
    (`round(20.5)` is 20 here and 21 in a browser)."""
    return math.floor(value + 0.5)


# ── a reader for the ONE formatter: statements and expressions EXECUTED ─────
#
# ⛔ The reason this exists at all (#13696 round 16, P1, measured): the previous
# reader took `mode` as its OWN Python argument and returned HARDCODED strings.
# It evaluated only the three `var` bindings and compared two return templates
# as TEXT — so the source's own `if (mode === 'annual')` guard and its two
# `return { big: …, total: … }` templates were never read. Both mutants left
# all 167 rows GREEN:
#
#   * `if (mode === 'annual') {` → `if (true) {` — a MONTHLY visitor then sees
#     `$20` / `$240 billed annually` on both pages, identically, so the
#     page-vs-page compare and the static-card compare (annual default) agree;
#   * `total: '$' + total + ' billed annually'` → `… ' billed yearly'`.
#
# Restating the dispatch or the output strings in Python cannot disagree with
# itself, which is why a row built that way verifies its re-implementation. So
# the guard, the statements and both templates are now READ OUT of
# `website/assets/tortoise-pricing.js` and EXECUTED. Anything this reader does
# not model is REFUSED with the construct named — never skimmed past — so a
# rewrite reds with a message a human can act on instead of passing blind.

#: The only CALLS the formatter may contain. `Math` is the only JS-ism its
#: arithmetic needs; a call this table does not name is refused.
JS_FMT_CALLS = {"Math.round": _js_round, "Math.max": max}

#: JS tokens the formatter's own statements are written in.
JS_FMT_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<str>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")"
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<op>===|!==|==|!=|\+|\*|/|\(|\)|,|\{|\}|:|-)"
    r"|(?P<ident>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)"
    r")")

#: `text`'s bracket group starting at `text[i]` — the opener included, the
#: closer matched by depth — and the index just past it. Quote-aware.
_JS_OPENERS = {"(": ")", "[": "]", "{": "}"}


def _js_unmodelled(construct: str) -> str:
    return (
        "the shared formatter contains "
        f"`{construct.strip()[:80]}`, which this reader does not model — teach "
        "it here rather than letting the agreement row pass blind over code it "
        "cannot execute (#13696 round 16)")


def _js_bracket_group(text: str, i: int) -> tuple[str, int]:
    """`(inner, index_after)` for the bracket group opening at `text[i]`."""
    if i >= len(text) or text[i] not in _JS_OPENERS:
        raise AssertionError(_js_unmodelled(text[i:i + 40]))
    close = _JS_OPENERS[text[i]]
    depth = 0
    quote = ""
    j = i
    while j < len(text):
        ch = text[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch in _JS_OPENERS:
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                if ch != close:
                    raise AssertionError(_js_unmodelled(text[i:j + 1]))
                return text[i + 1:j], j + 1
        j += 1
    raise AssertionError(
        f"unbalanced `{text[i]}` in the shared formatter: {text[i:i + 80]!r}")


def _js_statement_end(text: str, i: int) -> int:
    """Index of the `;` that terminates the statement starting at `text[i]`
    (bracket- and quote-aware, so a `;` inside `(…)` or a string is not it)."""
    depth = 0
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch in _JS_OPENERS:
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return i
        i += 1
    raise AssertionError(
        "the shared formatter has an unterminated statement: "
        f"{text.strip()[:120]!r}")


def _js_statements(inner: str) -> list[tuple]:
    """Parse the formatter's statement block into `if` / `var` / `return` nodes.

    Only the three shapes the formatter is written in are modelled: a
    `var NAME = EXPR;`, a `return EXPR;`, and an `if (COND)` whose body is a
    `{ … }` block or a single statement. Anything else is refused by name.
    """
    statements: list[tuple] = []
    i, n = 0, len(inner)
    while True:
        while i < n and (inner[i].isspace() or inner[i] == ";"):
            i += 1
        if i >= n:
            return statements
        rest = inner[i:]
        if re.match(r"if\s*\(", rest):
            cond, j = _js_bracket_group(inner, i + rest.index("("))
            while j < n and inner[j].isspace():
                j += 1
            if j < n and inner[j] == "{":
                block, j = _js_bracket_group(inner, j)
                statements.append(("if", cond, _js_statements(block)))
            else:
                end = _js_statement_end(inner, j)
                statements.append(("if", cond, _js_statements(inner[j:end + 1])))
                j = end + 1
            i = j
            continue
        m = re.match(r"var\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=", rest)
        if m:
            j = i + m.end()
            end = _js_statement_end(inner, j)
            statements.append(("var", m.group(1), inner[j:end]))
            i = end + 1
            continue
        if re.match(r"return\b", rest):
            j = i + len("return")
            end = _js_statement_end(inner, j)
            statements.append(("return", inner[j:end]))
            i = end + 1
            continue
        raise AssertionError(_js_unmodelled(rest[:60]))


def _js_str(value: object) -> str:
    """How JS renders a value inside `'…' + value` (`20`, not `20.0`)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    raise AssertionError(
        f"the shared formatter concatenates {value!r} into a price string — "
        "this reader does not model that value")


def _js_add(left: object, right: object) -> object:
    """JS `+`: concatenation when EITHER side is a string, numeric otherwise."""
    if isinstance(left, str) or isinstance(right, str):
        return _js_str(left) + _js_str(right)
    if isinstance(left, bool) or isinstance(right, bool):  # pragma: no cover
        raise AssertionError(_js_unmodelled(f"{left!r} + {right!r}"))
    return left + right  # type: ignore[operator]


class _JsExpression:
    """A recursive-descent evaluator for the formatter's own expressions.

    It EVALUATES the source text — `'$' + perMonth` becomes the string a browser
    would print — so a changed literal or a changed operator is measured, not
    pattern-matched. Precedence and semantics are the JS ones this contract
    uses: `+` concatenates when a side is a string, `/` is float division,
    `===`/`!==` compare values.
    """

    def __init__(self, expr: str, scope: dict[str, object]) -> None:
        self.text = expr
        self.scope = scope
        self.tokens: list[tuple[str, str]] = []
        i = 0
        while expr[i:].strip():
            m = JS_FMT_TOKEN_RE.match(expr, i)
            if m is None:
                raise AssertionError(_js_unmodelled(expr[i:i + 40]))
            kind = m.lastgroup
            assert kind is not None
            self.tokens.append((kind, m.group(kind)))
            i = m.end()
        self.i = 0

    def _peek(self, ahead: int = 0) -> tuple[str | None, str | None]:
        j = self.i + ahead
        return self.tokens[j] if j < len(self.tokens) else (None, None)

    def _take(self, kind: str, value: str | None = None) -> str:
        got_kind, got = self._peek()
        if got_kind != kind or (value is not None and got != value):
            raise AssertionError(_js_unmodelled(self.text))
        self.i += 1
        assert got is not None
        return got

    def parse(self) -> object:
        value = self._comparison()
        if self.i != len(self.tokens):
            raise AssertionError(_js_unmodelled(self.text))
        return value

    def _comparison(self) -> object:
        left = self._additive()
        while self._peek()[0] == "op" and self._peek()[1] in (
                "===", "!==", "==", "!="):
            op = self._take("op")
            right = self._additive()
            left = (left == right) if op in ("===", "==") else (left != right)
        return left

    def _additive(self) -> object:
        left = self._multiplicative()
        while self._peek()[0] == "op" and self._peek()[1] in ("+", "-"):
            op = self._take("op")
            right = self._multiplicative()
            if op == "+":
                left = _js_add(left, right)
                continue
            if isinstance(left, bool) or isinstance(right, bool) or not isinstance(
                    left, (int, float)) or not isinstance(right, (int, float)):
                raise AssertionError(_js_unmodelled(self.text))
            left = left - right
        return left

    def _multiplicative(self) -> object:
        left = self._primary()
        while self._peek()[0] == "op" and self._peek()[1] in ("*", "/"):
            op = self._take("op")
            right = self._primary()
            if not isinstance(left, (int, float)) or isinstance(left, bool) \
                    or not isinstance(right, (int, float)) \
                    or isinstance(right, bool):
                raise AssertionError(_js_unmodelled(self.text))
            left = left * right if op == "*" else left / right
        return left

    def _primary(self) -> object:
        kind, value = self._peek()
        if kind == "num":
            self._take("num")
            assert value is not None
            return int(value) if "." not in value else float(value)
        if kind == "str":
            self._take("str")
            assert value is not None
            if "\\" in value:
                raise AssertionError(_js_unmodelled(value))
            return value[1:-1]
        if (kind, value) == ("op", "("):
            self._take("op", "(")
            inner = self._comparison()
            self._take("op", ")")
            return inner
        if (kind, value) == ("op", "{"):
            return self._object_literal()
        if kind == "ident":
            self._take("ident")
            assert value is not None
            if value in ("true", "false"):
                return value == "true"
            if self._peek() == ("op", "("):
                return self._call(value)
            if value not in self.scope:
                raise AssertionError(_js_unmodelled(value))
            return self.scope[value]
        raise AssertionError(_js_unmodelled(self.text))

    def _call(self, name: str) -> object:
        fn = JS_FMT_CALLS.get(name)
        if fn is None:
            raise AssertionError(_js_unmodelled(f"{name}(…)"))
        self._take("op", "(")
        args: list[object] = []
        if self._peek() != ("op", ")"):
            args.append(self._comparison())
            while self._peek() == ("op", ","):
                self._take("op", ",")
                args.append(self._comparison())
        self._take("op", ")")
        assert callable(fn)
        return fn(*args)

    def _object_literal(self) -> dict[str, object]:
        self._take("op", "{")
        obj: dict[str, object] = {}
        while self._peek() != ("op", "}"):
            key = self._take("ident")
            self._take("op", ":")
            obj[key] = self._comparison()
            if self._peek() == ("op", ","):
                self._take("op", ",")
        self._take("op", "}")
        return obj


#: The value `_js_run` returns when a branch reaches no `return`.
JS_FMT_FELL_THROUGH = object()


def _js_run(statements: list[tuple], scope: dict[str, object]) -> object:
    """Run the parsed formatter; the value of the FIRST `return` reached."""
    for stmt in statements:
        kind = stmt[0]
        if kind == "if":
            _, cond_src, block = stmt
            if _JsExpression(cond_src, scope).parse():
                value = _js_run(block, scope)
                if value is not JS_FMT_FELL_THROUGH:
                    return value
        elif kind == "var":
            _, name, expr_src = stmt
            scope[name] = _JsExpression(expr_src, scope).parse()
        elif kind == "return":
            return _JsExpression(stmt[1], scope).parse()
        else:  # pragma: no cover — `_js_statements` only produces those three
            raise AssertionError(f"unexpected statement {kind!r}")
    return JS_FMT_FELL_THROUGH


def _js_fmt_price(price: int, mode: str, discount_pct: int) -> tuple[str, str]:
    """EXECUTE the shared formatter — the code BOTH pages run — and return the
    `(big, total)` pair it produces for a visitor in `mode`.

    The dispatch is the SOURCE's: `if (mode === 'annual') { … }` is evaluated
    against `mode`, and the pair comes from the source's own `return { big: …,
    total: … }` templates. Nothing here restates the guard, the branch chosen,
    or either output string — so a mutated guard, a swapped branch or an edited
    template all disagree with the Python mirror and red the rows that use this.

    Measured at `3e381c6194` (#13696 round 16, P1): with the previous reader
    (which took `mode` as its own argument and returned hardcoded strings)
    `if (mode === 'annual')` → `if (true)` and `' billed annually'` →
    `' billed yearly'` each left **all 167 rows GREEN**.
    """
    body = _fmt_price_js_body()
    inner, _ = _js_bracket_group(body, body.index("{"))
    program = _js_statements(inner)
    assert program, (
        "the shared formatter's body is empty — the display contract has no "
        f"implementation (#13645):\n{body}")
    out = _js_run(program, {"price": price, "mode": mode,
                            "discountPct": discount_pct})
    assert out is not JS_FMT_FELL_THROUGH, (
        f"the shared formatter returned nothing for mode={mode!r}, ${price} — "
        "a visitor in that mode would render `undefined` (#13645). Its own "
        f"statements decide this, so the guard must branch on `mode`:\n{body}")
    assert isinstance(out, dict), (
        f"the shared formatter returns {out!r} instead of a `{{ big, total }}` "
        f"pair (#13645):\n{body}")
    missing = {"big", "total"} - set(out)
    assert not missing, (
        f"the shared formatter's return object lost {sorted(missing)!r}, so a "
        f"page would render `undefined` (#13645):\n{body}")
    return _js_str(out["big"]), _js_str(out["total"])


def _render_pricing_fn(html: str) -> str:
    """Extract the renderPricing() function body from product.html."""
    m = re.search(r"function renderPricing\(\) \{.*?\n    \}", html, re.S)
    assert m, "renderPricing() not found in product.html"
    return m.group(0)


def _function_refs(html: str, fn_name: str, page: str = "product.html") -> str:
    """`page` labels the failure. A message that hard-codes one file name sends
    the next reader to a page that is fine, now that the extractor serves both
    product pages (#13696 review 2, finding 3)."""
    m = re.search(rf"function {fn_name}\(.*?\n    \}}", html, re.S)
    assert m, f"{fn_name}() not found in {page}"
    return m.group(0)


def _balanced_block(text: str, start: int) -> str:
    """`text[start:]` up to and including the `}` that closes its first `{`.

    QUOTE-AWARE, because every body this is used on holds strings: a quoted `}`
    (or `{`) must not move the depth. A `${…}` interpolation inside a template
    literal is balanced by construction, so treating a backtick literal as an
    opaque string is enough — none of these consumers needs a JS parser.
    """
    open_at = text.find("{", start)
    assert open_at != -1, (
        f"no `{{` at or after offset {start}: {text[start:start + 80]!r}")
    depth = 0
    quote = ""
    i = open_at
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise AssertionError(
        f"unbalanced `{{` at offset {open_at}: {text[open_at:open_at + 80]!r}")


#: The block that CHOOSES the initial billing mode, at the page's top level.
_PRICING_INIT_RE = re.compile(
    r"^[ \t]*if[ \t]*\([ \t]*PRICING[ \t]*\)[ \t]*\{", re.M)

#: A `function` head, whose body the stray-call scan must not read as top level.
_FUNCTION_HEAD_RE = re.compile(r"\bfunction\b[^\n{]*\{")

#: A single px length inside a `border` shorthand (`1px`, `2.5px`) — the only
#: unit the CTA-baseline compensation relation can do arithmetic on. A border
#: written in `rem`/`em`/`thin` does not match, so the relation FAILS rather
#: than comparing a number to a keyword (#14406 review round 11).
_BORDER_WIDTH_PX = re.compile(r"([0-9]+(?:\.[0-9]+)?)px")


def _without_function_bodies(code: str) -> str:
    """`code` with every `function …(…) { … }`, head included, removed.

    What survives is the page's TOP-LEVEL code — the statements a browser runs
    at parse time. A `function` whose signature spans lines is not matched, so
    its body stays in the result: that direction is fail-CLOSED (a call inside
    it would red the row below with a message naming the rule), never a silent
    pass.
    """
    out: list[str] = []
    i = 0
    while True:
        m = _FUNCTION_HEAD_RE.search(code, i)
        if m is None:
            out.append(code[i:])
            return "".join(out)
        out.append(code[i:m.start()])
        block = _balanced_block(code, m.start())
        i = m.start() + len(block)


def _pricing_init_code(html: str, page: str = "product.html") -> str:
    """The `if (PRICING) { … }` BLOCK that decides which prices a visitor sees FIRST.

    `setBilling` is itself compared by `test_the_two_pages_render_from_the_same_code`,
    but the call that CHOOSES its argument sits at the page's top level, outside
    every captured function — so a page could keep the function identical and
    still open on the other billing mode. Measured before this helper existed
    (#13696 review 2, finding 4): flipping the campaign page's init to
    `setBilling('monthly')` left all 135 rows GREEN while that page opened on
    $19/$25/$149 against `/tortoise`'s $15/$20/$119 annual.

    ⛔ Round 11 (#13696, P1): this used to return the ONE LINE carrying both
    `PRICING.display.annual_default` and `setBilling` and compare that stripped
    line. Keeping that line BYTE-IDENTICAL and appending
    `if (PRICING) setBilling('monthly');` after the block left all **158 rows
    GREEN** at `59bc263091` while the campaign page opened on $19/$25/$149
    against `/tortoise`'s $15/$20/$119 — the same divergence, one statement
    later. So the WHOLE block is compared, and a second top-level `setBilling(…)`
    call is refused: the block is the only statement allowed to choose the
    initial mode.
    """
    blocks: list[str] = []
    stray: list[str] = []
    for script in _inline_scripts(html):
        found = [_balanced_block(script, m.start())
                 for m in _PRICING_INIT_RE.finditer(script)]
        blocks.extend(found)
        rest = script
        for block in found:
            rest = rest.replace(block, "", 1)
        # `_code_only`, not the comment-stripped script: this consumer asks
        # "is the call there at all", where a mention in prose or in a trailing
        # comment is indistinguishable from the call (see `_code_only`).
        stray.extend(ln.strip()
                     for ln in _code_only(_without_function_bodies(rest)).splitlines()
                     if re.search(r"\bsetBilling\s*\(", ln))
    assert blocks, (
        f"{page} no longer wires its billing default from the shared table "
        "(`if (PRICING) { … setBilling(…) … }`). Without it the "
        "page opens on whatever `setBilling` was left at, not the mode "
        "`pricing.json` names, so every price on it is quoted in the wrong "
        "billing period (#13645).")
    assert len(blocks) == 1, (
        f"{page} wires the billing default {len(blocks)} time(s): {blocks!r}. "
        "Two wiring blocks can disagree, and only the last one runs.")
    # Indentation is not code, so the comparison is over stripped lines.
    block = "\n".join(ln.strip() for ln in blocks[0].splitlines() if ln.strip())
    assert "PRICING.display.annual_default" in block and "setBilling(" in block, (
        f"{page}'s `if (PRICING) {{ … }}` block no longer chooses the mode from "
        "`PRICING.display.annual_default`, so the page opens on the wrong "
        f"billing period with every row green (#13645):\n{block}")
    assert not stray, (
        f"{page} calls `setBilling(…)` at TOP LEVEL, outside the init block:\n  "
        + "\n  ".join(stray)
        + "\nThe init block is the only statement allowed to choose the "
        "initial billing mode: a later top-level call WINS, so the page opens "
        "on the other mode than `/tortoise` while the compared block is "
        "byte-identical (#13696 round 11, measured green). Move the call inside "
        "the block, or leave the mode to the monthly/annual buttons' `onclick`.")
    return block


def _code_only(code: str) -> str:
    """`code` with every JS comment removed — block AND line, whole-line
    or trailing.

    Whole-line stripping alone is not enough where the result feeds a PRESENCE
    check: a trailing `// opt_in_capturing() is now handled inside …` satisfied
    `str.find` while the real call was gone (#13696 round 7). `_strip_comment_lines`
    stays the tool for the price channels, where a trailing comment is meant to
    be scanned WITH the code (it fails closed there — the line is read as
    written); this helper is for the rows that ask "is the call there at all",
    where a mention is indistinguishable from the call and so must not count.

    `(?<!:)` keeps a `//` that is part of a URL scheme (`https://…`) intact.
    """
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", code)


def _strip_comment_lines(code: str) -> str:
    """`code` with every WHOLLY-comment line removed and every comment SPAN cut.

    ⛔ SPANS, not lines. Dropping a line because its stripped form merely
    STARTS with `/*` also drops the code that follows the closing `*/` on that
    same line, and continuing past `*/` when a block closes mid-line drops the
    remainder with it — so `/* note */ labels.pro = 'Growth';` and a block whose
    last line closes and then runs code were both invisible to every consumer
    of this helper. Measured (`#13696` review, round 6): with the line-based
    rule, a `$25/mo` literal behind a leading `/* x */` left BOTH
    `test_no_page_quotes_a_price_inside_its_own_script` and
    `test_the_two_pages_render_from_the_same_code` green while the page quoted a
    price `/tortoise` does not. The tail after `*/` is now KEPT as code.

    Line-based for the trailing `//` case only, on purpose. A regex `//`-strip
    would cut the `https://` inside the self-host anchor and hide every
    mutation past it; a `//` at the START of a stripped line really is wholly a
    comment, so that branch stays a per-line drop.
    """
    out: list[str] = []
    in_block = False
    for line in code.splitlines():
        stripped = line.strip()
        if in_block:
            end = stripped.find("*/")
            if end == -1:
                continue
            in_block = False
            # The block closes here; anything after it is CODE on this line.
            # NO indent is inherited: the leading whitespace on this line
            # belongs to the comment's own continuation, not to the code.
            line, stripped = _keep_code_indent(
                line, stripped, end + 2, keep_indent=False)
            if not stripped:
                continue
        if stripped.startswith("/*"):
            end = stripped.find("*/")
            if end == -1:
                in_block = True
                continue
            # A one-line block comment leading a line of code: keep the code.
            line, stripped = _keep_code_indent(line, stripped, end + 2)
            if not stripped:
                continue
        if stripped.startswith("//"):
            continue
        out.append(line.rstrip())
    return "\n".join(out)


def _keep_code_indent(line: str, stripped: str, cut: int,
                      keep_indent: bool = True) -> tuple[str, str]:
    """`(line_with_comment_cut, code)`, keeping the line's own INDENT.

    `_render_code` compares two pages' function bodies as STRINGS and its
    contract says the pages may explain themselves differently — so cutting a
    comment must not move the code. For a comment that OPENS on this line the
    indent is the original line's leading whitespace, unchanged by inserting the
    comment after it:

        `        /* c */ code` → `        code`
        `/* why */ run(x)` → `run(x)`

    Taking it from the text after the comment instead would ADD an indent to a
    line that had none; taking it from nowhere would drop the indent of an
    indented line and red the render-equality row against a page whose code is
    identical (#13696 round 8, P3 — measured both ways).

    `keep_indent=False` is for a block comment that CLOSED on this line: its
    leading whitespace indents the comment's continuation (` */ run(x)`), not
    the code, so the code takes no indent from it.
    """
    code = stripped[cut:].strip()
    indent = '' if not keep_indent else line[:len(line) - len(line.lstrip())]
    return indent + code, code


def _render_code(html: str, fn_name: str, page: str = "product.html") -> str:
    """One page's render function with its COMMENTS removed.

    The two product pages explain the same code with different comments — and
    legitimately so: `product.html` cites `#4815` for the labels resolver, the
    campaign page cites the funnel. What must not differ is the CODE, so the
    comparison is taken over it.

    `_code_only`, not `_strip_comment_lines`: this consumer compares two pages
    as STRINGS, so a TRAILING `// note` on one page is a difference that is not
    a difference in code. `_strip_comment_lines` deliberately keeps a trailing
    `//` (the price channels scan it — fail-closed there), which made a
    comment-only edit red this row (#13696 round 9, P2). The two consumers want
    opposite things, so they use opposite strippers.
    """
    return "\n".join(
        # Each line STRIPPED, blanks dropped. Both normalise something that is
        # not a difference in code: a cut comment leaves a blank line behind,
        # and cutting a LEADING `/* … */` leaves its indent behind
        # (`      /* c */ run();` → `       run();`) — which made a comment-only
        # edit to ONE page red this row while the render was byte-identical
        # (#13696 round 10, measured: the two bodies differed by one space).
        # Indentation is not code; the comparison is over the statements.
        ln.strip()
        for ln in _code_only(_function_refs(html, fn_name, page)).splitlines()
        if ln.strip()
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PRICING object parses + tier inventory
# ═══════════════════════════════════════════════════════════════════════════════


class TestPricingObjectShape:
    def test_pricing_json_parses_with_required_structure(self):
        data = json.loads(PRICING_JSON.read_text(encoding="utf-8"))
        for key in ("$schema", "status", "billing", "tiers", "display"):
            assert key in data, f"pricing.json missing top-level key: {key}"
        assert data["status"] == "current"
        assert data["billing"]["model"] == "per-team"
        for tier in ("free", "solo", "pro", "team"):
            assert tier in data["tiers"], f"pricing.json missing tier: {tier}"

    def test_pricing_object_extracts_from_product_html(self):
        pricing = _extract_pricing_object()
        assert set(pricing.keys()) == {"display", "tiers"}
        # Only public tiers render on the pricing page; internal quota tiers
        # (anon — unclaimed zero-email teams, #1082) must not leak.
        assert set(pricing["tiers"].keys()) == PUBLIC_TIERS
        assert not (INTERNAL_TIERS & set(pricing["tiers"].keys()))

    def test_tier_sets_mirror_bidirectionally(self):
        html_tiers = set(_extract_pricing_object()["tiers"].keys())
        json_tiers = set(json.loads(
            PRICING_JSON.read_text(encoding="utf-8"))["tiers"].keys())
        assert html_tiers == json_tiers - INTERNAL_TIERS == PUBLIC_TIERS
        assert not (INTERNAL_TIERS & html_tiers)

    def test_every_tier_has_complete_render_inventory(self):
        """A missing key (price/popular/features/excluded) throws mid-map and
        the grid renders blank — every tier must carry the full inventory."""
        pricing = _extract_pricing_object()
        for name, tier in pricing["tiers"].items():
            for key in ("price", "popular", "features", "excluded",
                        "graphs", "users", "ops", "nodes", "keys", "overage"):
                assert key in tier, f"PRICING.tiers[{name}] missing '{key}'"
            assert isinstance(tier["features"], list) and tier["features"]
            assert isinstance(tier["excluded"], list)
            assert isinstance(tier["popular"], bool)

    def test_exactly_one_popular_tier(self):
        pricing = _extract_pricing_object()
        popular = [n for n, t in pricing["tiers"].items() if t["popular"]]
        assert popular == ["pro"], f"expected exactly pro as popular, got {popular}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Mirror parity: HTML PRICING ↔ product/pricing.json
# ═══════════════════════════════════════════════════════════════════════════════


def _mirror_data() -> tuple[dict, dict]:
    """Load both sides of the mirror: the HTML PRICING object + pricing.json."""
    html = _extract_pricing_object()
    js = json.loads(PRICING_JSON.read_text(encoding="utf-8"))
    return html, js


class TestMirrorNumericParity:
    def test_price_mirrors_price_usd_monthly(self):
        html, js = _mirror_data()
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            assert html["tiers"][name]["price"] == tier["price_usd_monthly"], (
                f"tier {name}: HTML price {html['tiers'][name]['price']} != "
                f"pricing.json {tier['price_usd_monthly']}")

    def test_limit_fields_mirror(self):
        """graphs/users/ops/nodes/keys map to pricing.json limits with
        None (unlimited) rendered as '∞'."""
        html, js = _mirror_data()
        field_map = {
            "graphs": "max_graphs_per_team",
            "users": "max_users_per_team",
            "ops": "included_write_ops_per_month",
            "nodes": "max_graph_nodes",
            "keys": "max_api_keys",
        }
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            for html_key, json_key in field_map.items():
                expected = tier[json_key]
                if expected is None:
                    expected = "∞"
                assert html["tiers"][name][html_key] == expected, (
                    f"tier {name}: HTML {html_key}={html['tiers'][name][html_key]} "
                    f"!= pricing.json {json_key}={tier[json_key]}")

    def test_overage_flag_mirrors(self):
        html, js = _mirror_data()
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            assert html["tiers"][name]["overage"] is tier["overage"], (
                f"tier {name}: HTML overage {html['tiers'][name]['overage']} "
                f"!= pricing.json {tier['overage']}")

    def test_excluded_mirrors_overage(self):
        """Overage tiers carry no exclusions; non-overage tiers exclude the
        paid overage line."""
        html, js = _mirror_data()
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            excluded = html["tiers"][name]["excluded"]
            if tier["overage"]:
                assert excluded == [], f"tier {name}: overage tier must not exclude anything"
            else:
                assert excluded == ["Overage"], f"tier {name}: expected Overage exclusion"

    def test_display_mirrors(self):
        html, js = _mirror_data()
        html_disp, js_disp = html["display"], js["display"]
        for key in ("annual_discount_pct", "annual_default",
                    "license_self_hosted", "overage_line"):
            assert html_disp[key] == js_disp[key], (
                f"display.{key} drift: HTML {html_disp[key]!r} != "
                f"pricing.json {js_disp[key]!r}")

    def test_feature_copy_pins_tier_facts(self):
        """Spot-check that the displayed feature strings encode the JSON
        facts (catches a mirror that parses but renders stale copy)."""
        html, js = _mirror_data()
        free = html["tiers"]["free"]
        assert any("1 graph" in f for f in free["features"])
        assert any("2 API keys" in f for f in free["features"])
        assert any("10,000 write ops/mo" in f for f in free["features"])
        pro = html["tiers"]["pro"]
        assert any("Unlimited graphs" in f for f in pro["features"])
        assert any("Usage-based overage" in f for f in pro["features"])
        team = html["tiers"]["team"]
        assert any("invites + RBAC" in f for f in team["features"])
        # JSON-side facts agree (belt and braces)
        assert js["tiers"]["free"]["max_api_keys"] == 2
        assert js["tiers"]["pro"]["max_graphs_per_team"] is None
        assert js["tiers"]["team"]["max_users_per_team"] is None


class TestDisplayLabelParity:
    """#4336: the user-facing tier name lives in TWO hand-maintained maps —
    product.html renderPricing()'s `labels` and the dashboard's
    `TIER_LABELS` (pricing.js). Nothing else ties them together, so a rename
    applied to one and not the other renders two names for one tier.
    (The internal tier KEYS stay `pro` — only the display value changes.)"""

    def _html_labels(self) -> dict:
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        m = re.search(r"const labels = \{([^}]*)\}", fn)
        assert m, "renderPricing() must define a `labels` display-name map"
        return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))

    def _dashboard_labels(self) -> dict:
        js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
        m = re.search(r"export const TIER_LABELS = \{([^}]*)\}", js, re.S)
        assert m, "pricing.js must export TIER_LABELS"
        return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))

    def test_labels_maps_agree_for_public_tiers(self):
        html_labels = self._html_labels()
        js_labels = self._dashboard_labels()
        for tier in PUBLIC_TIERS:
            assert html_labels.get(tier) == js_labels.get(tier), (
                f"display-label drift for tier {tier}: product.html "
                f"{html_labels.get(tier)!r} != pricing.js {js_labels.get(tier)!r}")

    def test_pro_displays_as_builder_and_key_is_unchanged(self):
        assert self._dashboard_labels()["pro"] == "Builder"
        assert self._html_labels()["pro"] == "Builder"
        # The tier KEY stays `pro` (parity, Stripe ids, pricing.json).
        assert "pro" in _extract_pricing_object()["tiers"]
        assert "pro" in json.loads(
            PRICING_JSON.read_text(encoding="utf-8"))["tiers"]


class TestDashboardPlanPricesAreThePriceOfRecord:
    """#11981 raised `solo` from $9 to $19 in `product/pricing.json` and left the
    dashboard e2e's plan-button pin at the old literal, so the required
    `dashboard-e2e` job went red on `main` for every PR from that landing onward
    (measured: run 43142101648 — `Solo · $9/mo` not found, the failure snapshot
    renders `Solo · $19/mo`).

    The dashboard does not carry a second price table: `pricing.js` imports
    `product/pricing.json` at build time and the dialog renders those numbers. A
    spec (or any other surface) that RESTATES a price therefore holds a copy that
    can disagree with the shipped artifact, and when it does the disagreement
    reds a browser job on `main` instead of failing a two-second static check.
    These two rows pin the construction AND the restatements.
    """

    _MAIN_JSX = (REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx")

    def _prices(self) -> dict:
        data = json.loads(PRICING_JSON.read_text(encoding="utf-8"))
        return {tier: t["price_usd_monthly"] for tier, t in data["tiers"].items()}

    def _label_to_tier(self) -> dict:
        """Display label -> tier key, from the dashboard's own TIER_LABELS.

        Derived, not restated: `TestDisplayLabelParity` already pins that map
        against product.html's, so this row cannot introduce a third copy.
        """
        js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
        m = re.search(r"export const TIER_LABELS = \{([^}]*)\}", js, re.S)
        assert m, "pricing.js must export TIER_LABELS"
        pairs = dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))
        return {label: tier for tier, label in pairs.items() if tier in PUBLIC_TIERS}

    def test_the_dialog_renders_the_price_of_record_by_construction(self):
        """The chain the e2e keys on: pricing.json -> planOptions() -> button."""
        js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
        assert "from '../../../../product/pricing.json'" in js, (
            "pricing.js must import the price of record, never a local copy")
        assert re.search(r"price:\s*t\.price_usd_monthly", js), (
            "planOptions() must read `price_usd_monthly` out of pricing.json")
        jsx = self._MAIN_JSX.read_text(encoding="utf-8")
        assert re.search(r"\{p\.label\}\s*·\s*\$\{p\.price\}/mo", jsx), (
            "the paid-new-org dialog must render `<label> · $<price>/mo` from a "
            "`planOptions()` entry (the accessible name the e2e asserts)")

    def test_no_spec_restates_a_plan_price_that_disagrees_with_the_record(self):
        """A spec may pin a plan price only at the price of record. Fails on the
        tree BETWEEN #11981 and this PR: #11981 raised `solo` 9 -> 19 while the e2e
        spec still asserted `Solo · $9/mo`, so they agreed BEFORE #11981 (verified:
        `git show a3d20763ab09^:product/pricing.json` -> solo 9) and disagreed after
        it — which is why the fix is to read the record, not to bump the literal."""
        prices = self._prices()
        labels = self._label_to_tier()
        offenders: list[str] = []
        for spec in sorted((REPO_ROOT / "tests" / "e2e").rglob("*.py")):
            text = spec.read_text(encoding="utf-8")
            # ⛔ COMMENTS ARE DOCUMENTATION, NOT RESTATEMENTS. Scan only non-comment
            # lines: this PR's own e2e comment quotes `Solo · $19/mo` to record the
            # #11981 regression it fixed, and a bare scan reds it the moment `solo`
            # moves — forcing the next author to FALSIFY a historical measurement
            # (or reword it to dodge this regex) on a line that asserts nothing.
            # Same split the runtime-URL guard states one file over: "bare-host
            # prose in a comment that explains the history is documentation, and
            # counting it would make this test red on its own rationale (measured:
            # it did, before this split)". Blanking keeps one entry per line, so
            # offsets (and therefore the reported line) do not shift.
            scannable = "\n".join(
                "" if line.lstrip().startswith("#") else line
                for line in text.splitlines())
            for label, tier in labels.items():
                for m in re.finditer(
                        rf"{re.escape(label)}\s*·\s*\$(\d+)/mo", scannable):
                    got = int(m.group(1))
                    if got != prices[tier]:
                        line_no = scannable[:m.start()].count("\n") + 1
                        offenders.append(
                            f"{spec.relative_to(REPO_ROOT)}:{line_no}: "
                            f"{label} · ${got}/mo "
                            f"!= pricing.json tier {tier!r} ${prices[tier]}/mo")
        assert not offenders, (
            "a spec restates a plan price that disagrees with the price of "
            "record (product/pricing.json) — read the price instead of "
            "restating it, or correct the literal: " + "; ".join(offenders))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. fmtPrice() math
# ═══════════════════════════════════════════════════════════════════════════════


class TestFmtPriceMath:
    def _discount(self) -> int:
        html, _ = _mirror_data()
        return html["display"]["annual_discount_pct"]

    def test_monthly_billing(self):
        # ⛔ $0 IS EXCLUDED, deliberately. A free tier is not "billed monthly" in
        # either mode — see `test_free_is_forever_free_in_BOTH_modes` (#13696
        # review cycle 2, P2): the same card read "Billed monthly" on the Monthly
        # tab and "Free forever" on Annual, and nothing is billed on it.
        for price in (9, 25, 149):
            big, total = _fmt_price(price, "monthly", 20)
            assert big == f"${price}"
            assert total == "Billed monthly"

    def test_free_is_forever_free(self):
        big, total = _fmt_price(0, "annual", 20)
        assert (big, total) == ("$0", "Free forever")

    def test_free_is_forever_free_in_BOTH_modes(self):
        """The Free card must not contradict itself across a one-click toggle.

        #13696 review cycle 2 (P2, measured in node): `_fmt_price(0, 'monthly')`
        returned `('$0', 'Billed monthly')` while the annual branch returned
        `('$0', 'Free forever')` — so the Free card read "Billed monthly" on the
        Monthly tab, which is false (nothing is billed on a free tier) and
        contradicts the static card's own `Free forever` one line away. Both the
        shared formatter and this mirror now carry the free case in both modes.
        """
        for mode in ("annual", "monthly"):
            assert _fmt_price(0, mode, 20) == ("$0", "Free forever"), (
                f"the Free tier reads a different copy in {mode} mode")
        # …and the EXECUTED source agrees, so the mirror cannot drift from it.
        for mode in ("annual", "monthly"):
            assert _js_fmt_price(0, mode, 20) == ("$0", "Free forever"), (
                f"the shared source renders the Free tier differently in {mode} "
                "mode")

    def test_annual_contract_rounding_once_invariant(self):
        """The page's own invariant: round per-month ONCE, derive the total —
        $7/mo × 12 must equal $84, not $86."""
        disc = self._discount()
        for price in (9, 25, 149):
            _, total = _fmt_price(price, "annual", disc)
            billed = int(total.split(" ")[0].lstrip("$"))
            assert billed == max(1, round(price * 12 * (1 - disc / 100) / 12)) * 12

    def test_annual_prices_across_tiers(self):
        disc = self._discount()
        assert disc == 20  # the -20% badge on the page
        cases = {
            0: ("$0", "Free forever"),
            9: ("$7", "$84 billed annually"),    # 9×12×0.8/12 = 7.2 → 7
            25: ("$20", "$240 billed annually"),  # 25×0.8 = 20 exactly
            149: ("$119", "$1428 billed annually"),  # 119.2 → 119
        }
        for price, expected in cases.items():
            assert _fmt_price(price, "annual", disc) == expected

    def test_per_month_never_drops_below_1(self):
        assert _fmt_price(1, "annual", 20)[0] == "$1"
        # Even a 99% discount floor keeps $1/mo (max(1, ...))
        assert max(1, round(1 * 12 * 0.01 / 12)) == 1


class TestSharedFormatterMathIsExecuted:
    """The JS the pages RUN is executed, not merely found.

    ⛔ #13696 round 11, P2. `TestFmtPriceMath` above exercises `_fmt_price` —
    the Python MIRROR — and the shared-source row asserted only that
    `website/assets/tortoise-pricing.js` DEFINES `window.tortoiseFmtPrice`. So
    nothing tied either to the arithmetic both pages actually execute, and the
    PR makes that one file the place EVERY product page formats through: one
    silent edit there quotes wrong money on both, which is the invariant this
    PR declares. Measured mutants, both left all **158 rows GREEN** at
    `59bc263091`:

      * `var disc = discountPct / 100;` → `var disc = 0 / 100;` — both pages
        then rendered annual $9/$25/$149 (totals $108/$300/$1788) beside the
        static no-JS cards' $7/$20/$119;
      * `var total = perMonth * 12;` → `Math.round(price * 12 * (1 - disc))` —
        `$15/mo` printed beside `$182 billed annually`.

    The rows below EXECUTE the source — its `if (mode === 'annual')` guard, its
    `var` statements and its two `return { big: …, total: … }` templates — so
    the two implementations are held to EACH OTHER. A formula rewrite, a
    swapped branch or an edited template reds even where the text still looks
    right.

    ⛔ #13696 round 16, P1 (measured at `3e381c6194`): the previous reader took
    `mode` as its OWN Python argument and returned HARDCODED strings, so the
    guard and the return templates were never read and three mutants left all
    **167 rows GREEN** — a monthly visitor was mis-priced with the suite green:

      * `if (mode === 'annual')` → `if (true)` — Monthly then rendered
        `$20` / `$240 billed annually` on both pages, identically;
      * `total: '$' + total + ' billed annually'` → `'… billed yearly'`;
      * `big: '$' + perMonth` → `big: '~$' + perMonth`.

    Restating the dispatch or an output string in Python cannot disagree with
    itself, so those rows verified their own re-implementation. Everything the
    rows now measure comes out of the source, and a construct the reader does
    not model is REFUSED by name (`test_the_reader_refuses_code_it_cannot
    _execute`) rather than skimmed past.
    """

    def test_the_shared_math_agrees_with_the_python_mirror(self):
        disc = _mirror_data()[0]["display"]["annual_discount_pct"]
        cases = {
            0: ("$0", "Free forever"),
            1: ("$1", "$12 billed annually"),      # the max(1, …) floor
            9: ("$7", "$84 billed annually"),      # 7.2 → 7
            25: ("$20", "$240 billed annually"),   # 25 × 0.8 exactly
            149: ("$119", "$1428 billed annually"),  # 119.2 → 119
        }
        for price, expected in cases.items():
            from_source = _js_fmt_price(price, "annual", disc)
            from_mirror = _fmt_price(price, "annual", disc)
            assert from_source == from_mirror == expected, (
                f"the formatter both pages LOAD and the Python mirror that "
                f"pins its cases disagree at ${price}: source={from_source!r} "
                f"mirror={from_mirror!r} expected={expected!r}. One of them "
                "now quotes a different number than the other, and the page "
                "runs the first (#13645, #13696 round 11).")

    def test_the_two_implementations_agree_across_the_price_range(self):
        """The five pinned cases are also the five the mirror was written from.

        A formula can agree on those and diverge elsewhere — so the agreement
        is measured over a range, which is what makes the row a comparison of
        two implementations rather than of two literals.
        """
        disc = _mirror_data()[0]["display"]["annual_discount_pct"]
        offenders = [
            (price, _js_fmt_price(price, "annual", disc),
             _fmt_price(price, "annual", disc))
            for price in range(0, 500)
            if _js_fmt_price(price, "annual", disc)
            != _fmt_price(price, "annual", disc)]
        assert not offenders, (
            "the shared formatter's math and the Python mirror disagree "
            f"({len(offenders)} of 0..499): {offenders[:5]}")

    def test_monthly_comes_from_the_sources_own_final_return(self):
        """The MONTHLY pair is the source's last statement, executed.

        This is the row that would have caught the round-16 P1 on its own: when
        the reader took `mode` as its own argument, `if (mode === 'annual')` →
        `if (true)` left this row GREEN while every monthly visitor was quoted
        the annual price. It now reds because the SOURCE picks the branch and
        the source's own template prints the pair.
        """
        assert _js_fmt_price(25, "monthly", 20) == ("$25", "Billed monthly")
        for price in (0, 9, 25, 149):
            assert _js_fmt_price(price, "monthly", 20) == \
                _fmt_price(price, "monthly", 20)

    def test_the_reader_refuses_code_it_cannot_execute(self):
        """Fail CLOSED: a construct the reader does not model is named, not
        skimmed past.

        `_js_fmt_price` is only honest while an unreadable statement is an
        ERROR. If the reader ever guessed instead, a rewritten formatter would
        be graded by whatever residue happened to parse — the exact shape that
        let three mutants through at `3e381c6194`.
        """
        for construct in (
                "'$' + String(perMonth)",     # a call outside JS_FMT_CALLS
                "perMonth ?? 0",              # an operator not modelled
                "tier[0]",                    # a member expression
        ):
            try:
                _JsExpression(construct, {"price": 25}).parse()
            except AssertionError as exc:
                assert "does not model" in str(exc), (construct, str(exc))
            else:  # pragma: no cover - this is the failure
                raise AssertionError(
                    f"the reader accepted `{construct}`, which it does not "
                    "execute — an unreadable formatter would then be graded "
                    "by whatever residue parses (#13696 round 16)")
        for statement in ("for (var i = 0; i < 2; i++) {}",
                          "while (true) {}"):
            try:
                _js_statements(statement)
            except AssertionError as exc:
                assert "does not model" in str(exc), (statement, str(exc))
            else:  # pragma: no cover - this is the failure
                raise AssertionError(
                    f"the reader accepted the statement `{statement}` — an "
                    "unmodelled statement must red, not be skipped")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. renderPricing / setBilling / renderSelfHost element inventory (anti-#728)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRenderElementInventory:
    def test_render_pricing_references_grid_and_usage_line(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        fn = _render_pricing_fn(html)
        assert "getElementById('pricing-grid')" in fn
        assert "getElementById('pricing-usage-line')" in fn
        # Every tier is mapped — a missing tier renders a blank card slot
        for name in ("free", "solo", "pro", "team"):
            assert f"'{name}'" in fn or f'"{name}"' in fn

    def test_render_pricing_has_no_undefined_name_path(self):
        """#4815: `names` and `labels` are separate hand-maintained lists, so a
        tier in one but not the other used to print the literal string
        "undefined" in customer copy — and an empty metered set dangled the
        " — applies to " separator."""
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))

        # Exactly one name resolver, and it carries the fallback...
        assert "const displayName = (n) => labels[n] ||" in fn, (
            "renderPricing() must resolve display names through one helper "
            "with a fallback")
        # ...so no render site indexes `labels` bare (no fallback → undefined).
        resolver = "(n) => labels[n] ||"
        assert "labels[" not in fn.replace(resolver, "", 1), (
            "a bare `labels[...]` lookup renders `undefined` when the tier is "
            "missing from the map — every use must go through displayName()")
        # The sentence must not render its separator with nothing after it.
        assert "overageTiers.length" in fn, (
            "the overage line must be skipped (not left dangling) when no "
            "tier is metered")

    def test_dashboard_plan_grid_discloses_overage(self):
        """#4815: `planOptions()` computed an `overage` flag and the plan
        grids rendered nothing, so a metered tier was invisible on the card
        the user upgrades from — the card Solo is bought on. The string must
        come from pricing.json's own `display.overage_line`, never a second
        hardcoded copy of the price.

        The assertions are scoped to the LIVE surfaces: `main.jsx` carries a
        SECOND `planOptions()` grid inside the archived
        `LEGACY_WIZARD_ARCHIVED` block (dead code, never rendered), so a
        whole-file substring would pass a disclosure that only ever rendered
        there."""
        js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
        main = (REPO_ROOT / "website" / "apps" / "dashboard" / "src"
                / "main.jsx").read_text(encoding="utf-8")

        assert "overageLine: t.overage" in js, (
            "planOptions() must expose the disclosure only for a metered tier")
        assert "pricing.display?.overage_line" in js, (
            "the disclosure must be pricing.json's own display.overage_line")

        # Excise the archived wizard block (same anchor/marker pair
        # overview.test.js derives its A0 slice with) so the render assertions
        # below can only be satisfied by code that actually runs.
        archived_anchor = "LEGACY_WIZARD_ARCHIVED && welcomeOriented && ("
        archived_start = main.index(archived_anchor)
        archived_end = main.index("\n                )}\n", archived_start)
        live = main[:archived_start] + main[archived_end:]

        assert "{p.overageLine && (" in live, (
            "the LIVE Billing plan grid must render the disclosure when the "
            "flag is set — a line rendered only in the archived (dead) grid "
            "is not a disclosure")

        # The paid-new-org purchase dialog commits a metered subscription, so
        # it must show the selected plan's overage line too.
        dialog_start = live.index('id="create-org-title-purchase"')
        dialog_end = live.index("Continue to checkout", dialog_start)
        dialog = live[dialog_start:dialog_end]
        assert "newOrgSelectedPlan?.overageLine && (" in dialog, (
            "the paid-new-org purchase dialog must render the SELECTED "
            "plan's overage line — it commits checkout for a metered "
            "subscription")
        # A bare `overageLine` substring is not enough: the same silent no-op
        # this test exists to prevent is reachable by breaking the SELECTION,
        # which leaves the render textually intact while `newOrgSelectedPlan`
        # is undefined and nothing renders. Pin the predicate and the render
        # variable so a predicate regression reds instead of passing.
        assert ("team?.checkout_price_ids?.[p.tier] === createTeamPlan" in live), (
            "the dialog's selected plan must be resolved by PRICE ID against "
            "checkout_price_ids (the state holds a price id, not a tier key) "
            "— matching on p.tier directly would resolve nothing and render "
            "no disclosure")
        assert "newOrgSelectedPlan.overageLine" in dialog, (
            "the dialog must render the resolved plan's own overageLine")

        assert "per additional 10k" not in main, (
            "the overage price string belongs in pricing.json, not re-typed "
            "in the dashboard")

    def test_set_billing_references_toggle_buttons(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        fn = _function_refs(html, "setBilling")
        for el in ("btn-monthly", "btn-annual"):
            assert f"getElementById('{el}')" in fn

    def test_render_self_host_references_section(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        fn = _function_refs(html, "renderSelfHost")
        assert "getElementById('selfhost-section')" in fn

    def test_all_inventoried_element_ids_exist_in_page(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        for el in ("pricing-grid", "pricing-usage-line", "selfhost-section",
                   "btn-monthly", "btn-annual", "annual-badge"):
            assert f'id="{el}"' in html, f"missing element id: {el}"

    def test_init_wiring_honors_annual_default(self):
        """Init runs setBilling('annual') when display.annual_default is
        true — a flipped default silently prices everyone monthly."""
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        pricing, _ = _mirror_data()
        assert "if (PRICING.display.annual_default) setBilling('annual')" in html
        assert "renderSelfHost();" in html
        # Annual is the default (matches the -20% badge init)
        assert pricing["display"]["annual_default"] is True

    def test_annual_badge_shows_discount(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        pricing, _ = _mirror_data()
        # The badge markup is <span class="badge" id="annual-badge">-20%</span>
        assert f"-{pricing['display']['annual_discount_pct']}%" in html


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. Static pricing fallback — the section must not be EMPTY without JS
# ═══════════════════════════════════════════════════════════════════════════════


def _served(markup: str) -> str:
    """Markup a READER actually gets: HTML comments, `<script>` and `<style>`
    bodies removed — including an UNTERMINATED comment opener, which swallows
    the rest of the document in a browser.

    Without this the gate is satisfied by markup nobody receives — wrapping the
    static cards in `<!-- … -->` (the natural way to disable the fallback while
    debugging) would leave every assertion below green while the no-JS reader is
    back to an empty grid, which is the failure this section exists to catch.
    Same lesson as `tests/_html_links.py` (#3436).
    """
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    markup = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style>", " ", markup, flags=re.S | re.I)
    # ⛔ An UNTERMINATED `<script>`/`<style>` swallows the rest of the document —
    # that is what a browser does with it, so anything after the opener is not
    # markup a reader receives. The paired substitutions above cannot match a
    # body with no closer, so without these two lines such a body stays
    # "served" — and every `_served` row can be satisfied from inside it.
    # Measured (#13696 round 10): deleting the real footer CTA and parking its
    # tagged `href` in a trailing unterminated `<script>` left the whole static
    # suite green (168 passed). Both pages are balanced today, so this truncates
    # nothing now; it exists so the next unbalanced one cannot be a hiding place.
    markup = re.sub(r"<script\b.*", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*", " ", markup, flags=re.S | re.I)
    return re.sub(r"<!--.*", " ", markup, flags=re.S)


def _ends_inside_attribute_value(section: str) -> bool:
    """True when the slice stops inside a tag, i.e. inside an attribute value.

    A `</section>` carried in a quoted OR unquoted attribute value is not an end
    tag to a browser, but it ends this slice, so whatever follows it renders
    while no assertion below can see it.

    The verdict comes from the tokenizer, not from a delimiter scan: an
    incomplete tag is exactly what `HTMLParser` leaves in `rawdata` after
    `feed()`, and a value may itself contain `<` or `>` (both are parse errors the
    tokenizer folds into the value), which defeats any `rfind("<")` or regex.

    Trailing TEXT such as `<p>Ratio = 2` or `<div class="x">Ratio = 2` is a
    complete document fragment, so it is not reported here — the `stray`
    assertion names it accurately, where an attribute report would send a
    maintainer hunting for an attribute that does not exist.
    """
    probe = HTMLParser(convert_charrefs=True)
    probe.feed(section)
    return bool(probe.rawdata)


def _served_without_comments(markup: str) -> str:
    """`_served`, except a comment is DELETED rather than replaced by a space.

    A comment contributes neither text nor whitespace, so a space here
    synthesises a word boundary no reader sees: `<span>2M+</span><!--x-->
    <span>views</span>` renders `2M+views` but would pin as `2M+ views`. This is
    the variant `#progress`'s text path slices with; `_served` is unchanged for
    its existing callers, which take structure (and, in the pricing path, text)
    from markup where a comment never sits between two words.
    """
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    markup = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style>", " ", markup, flags=re.S | re.I)
    return re.sub(r"<!--.*", "", markup, flags=re.S)


def _pricing_section(html: str | None = None) -> str:
    """The `#pricing-section` region as a reader receives it.

    Sliced on the STRIPPED document (not on a sentinel comment), so a comment
    that disables part of the section removes it from this gate too. Stops at
    the first `</section>`, so the nested-section guard below keeps a claim in a
    nested section from being invisible here.

    `html` defaults to product.html — the page this gate was written for. It is
    parameterized because #13645's second product page renders the SAME pricing
    section, and `TestPricingSourceIsShared` holds the two to each other.
    """
    src = _served(html if html is not None else PRODUCT_HTML.read_text(encoding="utf-8"))
    marker = 'id="pricing-section"'
    assert marker in src, (
        "the page lost #pricing-section — this gate's subject moved")
    start = src.index(marker)
    section = src[start:src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #pricing-section — this slice stops at the "
        "first </section>, so anything after it would be invisible to every "
        "assertion here. Re-scope this gate.")
    return section


def _static_pricing_grid(html: str | None = None) -> str:
    """The static cards: between `#pricing-grid` and the usage line."""
    section = _pricing_section(html)
    start = section.index('id="pricing-grid"')
    end = section.index('id="pricing-usage-line"')
    assert end > start, "#pricing-usage-line no longer follows #pricing-grid"
    return section[start:end]


def _render_facts(fn: str) -> dict:
    """The render's OWN literals, read out of `renderPricing()`.

    The point is that the static markup is compared against the render — not
    against a second hand-typed copy of the render in this file. Anything the
    static cards share with the render (destination, price suffix, CTA class,
    exclusion marker, badge) is extracted here, so a funnel change on the
    render side fails here instead of silently desynchronizing the no-JS copy.
    """
    cta = re.search(
        r"""<a href="([^"]+)" class="btn\$\{name === '([a-z]+)' \? '([^']+)' : ''\}">\$\{cta\}</a>""",
        fn)
    assert cta, "renderPricing()'s CTA template changed shape — re-point this gate"
    suffix = re.search(r'<div class="price">\$\{fmtPrice\(t\.price\)\.big\}<span>([^<]*)</span></div>', fn)
    assert suffix, "renderPricing()'s price template changed shape — re-point this gate"
    excl = re.search(
        r'<div class="feature"><span class="([a-z]+)">([^<]*)</span> <span class="muted">\$\{f\}</span></div>',
        fn)
    assert excl, "renderPricing()'s exclusion template changed shape — re-point this gate"
    tag = re.search(r"'<span class=\"popular-tag\">([^<]*)</span>'", fn)
    assert tag, "renderPricing()'s 'Most popular' badge literal moved"
    cta_expr = re.search(r"const cta = name === '([a-z]+)' \? '([^']*)' : '([^']*)';", fn)
    assert cta_expr, "renderPricing()'s CTA copy changed shape"
    sep = re.search(
        r"\$\{PRICING\.display\.overage_line\}([^$]*)\$\{overageTiers\.join\(", fn)
    assert sep, "renderPricing()'s overage sentence changed shape — re-point this gate"
    return {
        "cta_href": cta.group(1),
        "free_key": cta_expr.group(1),
        "free_cta": cta_expr.group(2),
        "paid_cta": cta_expr.group(3),
        "cta_secondary_class": cta.group(3).strip(),   # the `btn <x>` suffix
        "cta_class_key": cta.group(2),
        "price_suffix": suffix.group(1),
        "excluded_class": excl.group(1),
        "excluded_glyph": excl.group(2),
        "popular_tag": _beat_text(tag.group(1)),
        "overage_separator": sep.group(1).strip(),
    }


def _static_cards(html: str | None = None) -> list[dict]:
    """One dict per static `.pricing-card`, in DOM order.

    Every field the JS render writes is captured, including the CTA, the
    "Most popular" badge, the price suffix and the INCLUDED/EXCLUDED marker —
    a gate that read only the prices would stay green while the revenue button
    drifted to `href="/signup"`, or while "Overage" was re-rendered with a green
    check on the one tier that does not include it (both measured).
    """
    grid = _static_pricing_grid(html)
    cards = []
    for chunk in grid.split('<div class="pricing-card')[1:]:
        features_block = re.search(r'<div class="features">(.*?)</div>\s*<a ', chunk, re.S)
        assert features_block, "a static pricing card has no features block"
        rows = re.findall(
            r'<div class="feature"><span class="(ck|x)">([^<]*)</span>\s*'
            r'<span( class="muted")?>(.*?)</span>',
            features_block.group(1))
        assert rows, "a static pricing card has no feature rows"
        tag = re.search(r'<span class="popular-tag">(.*?)</span>', chunk, re.S)
        price = re.search(r'<div class="price">(.*?)<span>([^<]*)</span>', chunk, re.S)
        assert price, "a static pricing card has no price block"
        cta = re.search(r'<a href="([^"]*)" class="(btn[^"]*)">(.*?)</a>', chunk, re.S)
        assert cta, "a static pricing card has no CTA anchor"
        cards.append({
            "popular": chunk.lstrip().startswith('popular'),
            "popular_tag": _beat_text(tag.group(1)) if tag else None,
            "label": _beat_text(re.search(r"<h3>(.*?)</h3>", chunk, re.S).group(1)),
            "big": _beat_text(price.group(1)),
            "suffix": price.group(2),
            "billing": _beat_text(re.search(r'<p class="billing">(.*?)</p>', chunk, re.S).group(1)),
            "included": [_beat_text(t) for m, _g, _mu, t in rows if m == "ck"],
            "excluded": [_beat_text(t) for m, _g, _mu, t in rows if m == "x"],
            # (marker class, glyph, dimmed?) — the render writes an exclusion as
            # `<span class="x">✕</span> <span class="muted">…</span>`, so a lost
            # `muted` is an exclusion rendered as body copy.
            "excluded_markers": [(m, g, mu.strip())
                                 for m, g, mu, _t in rows if m != "ck"],
            "cta_href": cta.group(1),
            "cta_class": cta.group(2),
            "cta_label": _beat_text(cta.group(3)),
        })
    return cards


def _static_usage_line(html: str | None = None) -> str:
    m = re.search(r'<p class="pricing-usage" id="pricing-usage-line">(.*?)</p>',
                  _pricing_section(html), re.S)
    assert m, "#pricing-usage-line lost its static content"
    return _beat_text(m.group(1))


def _static_selfhost_block(html: str | None = None) -> str:
    """The `#selfhost-section` element, closed by its OWN `</div>`.

    Sliced by depth rather than "to the end of the section": a link added later
    inside `#pricing-section` (a "Talk to sales" line, say) would otherwise fail
    this gate although the block it tests did not change.
    """
    section = _pricing_section(html)
    marker = section.index('id="selfhost-section"')
    # Start at the `<div` that OPENS the element, not at the id inside its
    # tag — otherwise the first balanced child (`.actions`) closes the slice.
    start = section.rindex("<div", 0, marker)
    depth = 0
    for m in re.finditer(r"<div\b|</div>", section[start:]):
        depth += 1 if m.group(0) != "</div>" else -1
        if depth == 0:
            return section[start:start + m.end()]
    raise AssertionError("#selfhost-section has no balanced closing </div>")


class TestStaticPricingFallback:
    """The pricing section is the revenue surface, and it used to be EMPTY
    without JavaScript.

    `#pricing-grid`, `#pricing-usage-line` and `#selfhost-section` were rendered
    entirely by the page's inline script, so a reader that does not execute JS —
    a crawler, or an AI evaluator reading the page — saw the Monthly/Annual
    toggle above a blank grid — before this change `#pricing-section` rendered
    `Pricing · <sub> · Monthly · Annual -20% · Use of the service subject to our
    Terms` with `document.querySelectorAll('.pricing-card').length === 0`.

    The static markup is a THIRD copy of the tier facts (the inline script's
    `PRICING` mirror is the second, product/pricing.json the first), so it is
    the copy most likely to rot. This class keeps it honest by deriving every
    expectation — prices, labels, features, CTA copy/href/class, the price
    suffix, the exclusion marker, the badge, the overage sentence, the
    self-host licence and links — from the mirror and from `renderPricing()`/
    `renderSelfHost()` themselves, never by re-typing the value here. Slices are
    read through `_served()`, so markup that no reader receives (comment or
    `<script>`-wrapped) cannot satisfy the gate.
    """

    def _expected(self) -> tuple[list[str], str, dict]:
        """(tier order, the mode the static cards must carry, PRICING mirror)."""
        pricing = _extract_pricing_object()
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        names = re.search(r"const names = \[(.*?)\]", fn, re.S)
        assert names, "renderPricing() must keep its `names` tier order"
        order = re.findall(r"'([a-z]+)'", names.group(1))
        assert order, "could not read renderPricing()'s tier order"
        mode = "annual" if pricing["display"]["annual_default"] else "monthly"
        return order, mode, pricing

    def _labels(self) -> dict:
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        labels_match = re.search(r"const labels = \{([^}]*)\}", fn)
        assert labels_match, "renderPricing() must define a `labels` display-name map"
        return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", labels_match.group(1)))

    def test_render_keys_the_free_cta_from_one_tier(self):
        """The render decides the free CTA's COPY and its CLASS with two separate
        `name === '<tier>'` conditionals. If they disagree, the JS grid paints one
        tier's button as secondary and the static cards the other, with nothing
        red — so pin them to the same key."""
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        facts = _render_facts(fn)
        assert facts["cta_class_key"] == facts["free_key"], (
            "renderPricing() keys the free CTA's class on "
            f"{facts['cta_class_key']!r} but its copy on {facts['free_key']!r}")

    def test_a_reader_can_see_the_grid(self):
        """A hidden grid satisfies every content assertion while the no-JS reader
        is back to an empty-looking section, so the container must stay
        renderable: live region present, no inline hiding."""
        grid = _static_pricing_grid()
        opening = grid[:grid.index(">") + 1]
        assert 'aria-live="polite"' in opening, (
            "#pricing-grid lost its aria-live region (setBilling re-renders it)")
        assert "style=" not in opening, (
            "#pricing-grid carries an inline style — a `display:none` there "
            "hides the static fallback from the reader this gate protects")

    def test_every_public_tier_renders_in_the_source(self):
        order, _, _ = self._expected()
        cards = _static_cards()
        assert len(cards) == len(order), (
            f"#pricing-grid carries {len(cards)} static cards for {len(order)} "
            f"tiers ({', '.join(order)}) — a missing card is a blank slot for a "
            f"no-JS reader")

    def test_static_cards_carry_the_mirror_facts(self):
        order, mode, pricing = self._expected()
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        labels = self._labels()
        facts = _render_facts(fn)
        disc = pricing["display"]["annual_discount_pct"]

        for name, card in zip(order, _static_cards(), strict=True):
            tier = pricing["tiers"][name]
            expected_label = labels.get(name, name.capitalize())
            big, total = _fmt_price(tier["price"], mode, disc)
            assert card["label"] == expected_label, (
                f"static card {name}: label {card['label']!r} != {expected_label!r}")
            assert card["big"] == big, (
                f"static card {name}: price {card['big']!r} != {big!r} ({mode})")
            assert card["suffix"] == facts["price_suffix"], (
                f"static card {name}: price suffix {card['suffix']!r} != the "
                f"render's {facts['price_suffix']!r}")
            assert card["billing"] == total, (
                f"static card {name}: billing line {card['billing']!r} != {total!r}")
            assert card["included"] == tier["features"], (
                f"static card {name}: features drifted from the PRICING mirror "
                f"{card['included']!r} != {tier['features']!r}")
            assert card["excluded"] == tier["excluded"], (
                f"static card {name}: exclusions drifted "
                f"{card['excluded']!r} != {tier['excluded']!r}")
            # An exclusion must LOOK like an exclusion: the render marks it with
            # its own class+glyph, so a green check on a non-included line is a
            # silent claim that the tier includes it.
            assert card["excluded_markers"] == [
                (facts["excluded_class"], facts["excluded_glyph"], 'class="muted"')
            ] * len(tier["excluded"]), (
                f"static card {name}: excluded rows are marked "
                f"{card['excluded_markers']!r}, but the render marks them "
                f"({facts['excluded_class']!r}, {facts['excluded_glyph']!r}, "
                f"muted)")
            assert card["popular"] is tier["popular"], (
                f"static card {name}: popular flag drifted")
            # The CTA is what actually converts.
            assert card["cta_label"] == (
                facts["free_cta"] if name == facts["free_key"] else facts["paid_cta"]), (
                f"static card {name}: CTA label {card['cta_label']!r} != the "
                f"render's copy")
            assert card["cta_href"] == facts["cta_href"], (
                f"static card {name}: CTA points at {card['cta_href']!r}, the "
                f"render at {facts['cta_href']!r}")
            expected_class = ("btn " + facts["cta_secondary_class"]
                              if name == facts["free_key"] else "btn")
            assert card["cta_class"] == expected_class, (
                f"static card {name}: CTA class {card['cta_class']!r} != "
                f"{expected_class!r} — the free CTA loses its secondary styling")
            # The badge is the most conspicuous element on the section; a card
            # marked `popular` without its visible tag is a silent demotion.
            assert card["popular_tag"] == (
                facts["popular_tag"] if tier["popular"] else None), (
                f"static card {name}: 'Most popular' badge is "
                f"{card['popular_tag']!r} but the tier's popular flag is "
                f"{tier['popular']} (the render writes {facts['popular_tag']!r})")

    def test_static_overage_line_matches_the_render(self):
        order, _, pricing = self._expected()
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        labels = self._labels()
        facts = _render_facts(fn)
        metered = [labels.get(n, n.capitalize()) for n in order
                   if pricing["tiers"][n]["overage"]]
        expected = (f"{pricing['display']['overage_line']} "
                    f"{facts['overage_separator']} {', '.join(metered)}") if metered \
            else pricing["display"]["overage_line"]
        assert _static_usage_line() == expected, (
            f"#pricing-usage-line's static text drifted from the render "
            f"({_static_usage_line()!r} != {expected!r})")

    def test_static_self_hosted_block_is_not_empty(self):
        """Same failure mode one section down: `#selfhost-section` was JS-only,
        so a no-JS reader lost the licence summary and the self-host links."""
        fn = _function_refs(PRODUCT_HTML.read_text(encoding="utf-8"), "renderSelfHost")
        block = _static_selfhost_block()
        pricing, _ = _mirror_data()
        assert _beat_text(pricing["display"]["license_self_hosted"]) in _beat_text(block), (
            "the static self-host block must carry pricing.json's licence line")
        heading = re.search(r"<h4>([^<]*)</h4>", fn)
        assert heading, "renderSelfHost()'s heading moved"
        static_heading = re.search(r"<h4>([^<]*)</h4>", block)
        assert static_heading, "the static self-host block lost its <h4> heading"
        # Compared as the block's OWN heading, not as a substring: the actions
        # row carries a `/self-hosted` link, so a substring check would pass on
        # the link text even after the heading itself was rewritten.
        assert _beat_text(static_heading.group(1)) == _beat_text(heading.group(1)), (
            f"static self-host heading {_beat_text(static_heading.group(1))!r} != "
            f"the render's {_beat_text(heading.group(1))!r}")
        # Every link the render writes, in the same order — a missing or
        # retargeted one is invisible to a reader that cannot run the script.
        def links(src: str) -> list[tuple[str, str]]:
            return [(_beat_text(h), _beat_text(t)) for h, t in
                    re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', src, re.S)]

        assert links(block) == links(fn), (
            "the static self-host links (href + label) no longer match "
            f"renderSelfHost()'s: {links(block)!r} != {links(fn)!r}")
        # The render's own wrappers, so the block is not merely present but
        # styled the same (an unstyled `<p>` in place of `.license` reads as
        # body copy, and a bare `<div>` in place of `.actions` unstacks the row).
        for wrapper in ("class=\"license\"", 'class="actions"'):
            assert wrapper in fn, f"renderSelfHost() lost {wrapper}"
            assert wrapper in block, f"the static self-host block lost {wrapper}"

    def test_static_toggle_highlights_the_same_period_the_cards_price(self):
        """The static cards carry `display.annual_default` figures, so the pill
        highlighted in the SOURCE must be that same period — otherwise a no-JS
        reader sees Annual prices under a lit "Monthly" pill."""
        # `_served()`, for the same reason every other slice uses it: a
        # commented-out pill is markup no reader receives.
        html = _served(PRODUCT_HTML.read_text(encoding="utf-8"))
        _, _, pricing = self._expected()
        want = "annual" if pricing["display"]["annual_default"] else "monthly"
        for period in ("monthly", "annual"):
            m = re.search(rf'<button id="btn-{period}"([^>]*)>', html)
            assert m, f"#btn-{period} markup moved"
            attrs = m.group(1)
            is_active = 'class="active"' in attrs
            assert is_active is (period == want), (
                f"#btn-{period} is {'active' if is_active else 'inactive'} in the "
                f"source but display.annual_default makes {want!r} the default "
                f"— a no-JS reader gets the toggle and the cards disagreeing")
            assert 'aria-pressed="' + ("true" if is_active else "false") + '"' in attrs, (
                f"#btn-{period}'s static aria-pressed disagrees with its own class")


# ── #1566: welcome.html's provisioning pipeline must stay dead ──

# #8066/#8027: the subject is the SERVED copy, NOT the machine-local relay.
# This gate used to read `skills/tortoise-decide/SKILL.md` through the tracked
# `skills` symlink, whose committed blob is an ABSOLUTE path in one developer's
# home (`/Users/danielospina/Documents/GitHub/agent-infra/skills`). The link
# dangles everywhere else, so the test `pytest.skip`ped: the gate was green
# exactly where it could not see the file (measured 2026-10-16 on the CI-style
# checkout — `SKIPPED [1] ... skills/ symlink not present`), and on a machine
# where it did resolve it graded whichever unrelated agent-infra checkout
# happened to be linked. The skip's own explanation was wrong on BOTH kinds of
# machine: `/Users/home/agent-infra/skills` (a live agent-infra, 95/98 of this
# repo's `operations/skills/*` targets present) does NOT carry `tortoise-decide`
# at all — it ships `how-to-use-tortoise`, `tortoise-file-finding` and
# `tortoise-verify-chain`. The product skills are tracked in THIS repo, under
# `website/apps/dashboard/public/skills/`, and those are the bytes the
# onboarding installer serves (`https://app.premiselabs.co/skills/<name>/SKILL.md`).
# Reading the tracked served copy is deterministic and is the pattern
# `tests/test_ingest_conformance.py` adopted for the same relay in #8499 and
# that `tests/test_served_skills.py` already uses. The dead relay itself is
# #8066/#8027 (owner-gated remedy — see #8027's "the choice is the owner's");
# the source↔served parity gap is #4358/#4643.
DECIDE_SKILL = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
                / "skills" / "tortoise-decide" / "SKILL.md")


def test_tortoise_decide_skill_ships_the_workflow():
    """#1643 (Task 3): the SERVED tortoise-decide skill exists in the tracked
    tree and AUTHORS the decision workflow (options → criteria → findings →
    edges → mitigations → EP ranking) — the onboarding skills primer links a
    REAL invokable skill.

    ⛔ A missing subject is a FAIL, not a skip (#8066): a gate whose subject is
    resolved through a machine-local path has no result on the CI path, and its
    green is an artifact of absence."""
    assert DECIDE_SKILL.exists(), (
        f"the tracked served skill is missing: {DECIDE_SKILL} — the onboarding "
        "primer links it, so this gate has no subject (#8066)")
    src = DECIDE_SKILL.read_text()
    for marker in ("tortoise-decide", "options", "criteria", "findings",
                   "IMPL", "NAND", "mitigation", "confidence"):
        assert marker in src, f"skill missing the {marker!r} step"
    # Tool-based (MCP): the skill must reference the graph-write tools, not
    # require a local FalkorDB.
    assert "create_point" in src or "tortoise_create_point" in src, \
        "the skill must be tool-based (MCP write tools)"


def test_welcome_provisioning_pipeline_is_dead_since_1566():
    """#1566 (review P2): welcome.html's provisioning pipeline must STAY dead
    — restoring it would recreate the double-provision surface #1082/#1566
    guard against.

    #3501 removed the session bridge itself, so the pipeline is now dead by
    CONSTRUCTION rather than by having had its symbols stripped out of a
    still-present bridge. The assertion is therefore inverted and widened: the
    bridge must no longer exist at all, and neither may the provisioning
    symbols it used to host.

    Pinning the stronger property matters here — the old form
    (``assert "runSessionBridge" in src``) would now FAIL on the correct
    implementation, and the tempting "fix" of deleting that line would leave
    the provisioning symbols themselves unpinned.
    """
    src = Path("website/apps/dashboard/public/welcome.html").read_text()
    # Comments in welcome.html name the removed markers to explain #3501, so
    # the absence checks must run against comment-stripped source.
    src_code = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src_code = re.sub(r"/\*.*?\*/", "", src_code, flags=re.S)
    src_code = re.sub(r"^[ \t]*//.*$", "", src_code, flags=re.M)
    for dead in ("provisionViaEdgeFunction", "waitForProvisioning",
                 "revealKeyOnce", "claimStatusGuard",
                 "runSessionBridge"):
        assert dead not in src_code, (
            f"{dead} must not exist — the provisioning surface is dead "
            "(#1566) and its host bridge was removed in #3501"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Unshipped-capability claims on the marketing page (#3432)
# ═══════════════════════════════════════════════════════════════════════════════


def _automate_beat_markup() -> str:
    """The `<section id="beat-automate">…</section>` slice, from the SECTION tag.

    The slice must start at `<section`, not at the `id=` attribute: starting at
    the attribute leaves `id="beat-automate" class="beat workflow">` outside any
    tag for `_beat_text`'s strip, and the pinned text then carries that fragment
    (caught in plan review as a P0). The nested-section assert below is the same
    guard `tests/test_website_docs_consistency.py` carries for `#beat-hero`: this
    slice stops at the FIRST `</section>`, so a claim inside a nested section
    would be invisible to every assertion in this class.
    """
    src = PRODUCT_HTML.read_text(encoding="utf-8")
    marker = 'id="beat-automate"'
    assert marker in src, (
        "the #beat-automate section moved or was renamed — this gate's subject is "
        "gone; re-point it at wherever the Automate claim now lives (#3432)")
    i = src.index(marker)
    start = src.rindex("<section", 0, i)
    section = src[start:src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #beat-automate — this slice stops at the "
        "first </section>, so a claim in the nested section would be invisible "
        "to every assertion in this class (#3432)")
    return section


def _beat_text(markup: str) -> str:
    """Rendered text of a markup slice: tags stripped, entities decoded, spaces collapsed."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", markup))).strip()


def _norm(parts: list[str]) -> str:
    """Rendered text from collected chunks: concatenated, whitespace collapsed.

    Concatenated, NOT joined with a separator: `<span>2M+</span><span>views</span>`
    renders `2M+views`, and a synthesised space would let that change pass the
    pins while a reader sees different text. Spacing therefore has to come from
    the source — `handle_data` keeps whitespace-only chunks for exactly that.
    """
    return re.sub(r"\s+", " ", "".join(parts)).strip()


#: Declarations that stop text reaching a reader, checked on an inline
#: `style=`. There is no stylesheet path on purpose (see the class docstring).
#: The zero values are anchored at a property boundary and accept every SIGNED
#: and decimal spelling of zero (`0`, `.0`, `00`, `+0`, `-0`, `0.0px`) — but not
#: a computed one (`calc(0)`), which no regular expression can see. Two pieces
#: each earn their place: the `(?![.\d])` lookahead is what keeps an honest
#: `opacity: 0.35` or `font-size:0.9em` from reading as a zero, while
#: `(?<![-\w])` keeps a DIFFERENT property whose name ends in `color`
#: (`background-color: transparent`) from reading as `color: transparent`.
_HIDING_DECL = re.compile(
    r"(?<![-\w])(?:"
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|opacity\s*:\s*[+-]?(?:0+(?:\.0*)?|\.0+)(?:[eE][+-]?\d+)?(?![.\d])"
    r"|color\s*:\s*transparent"
    r"|font-size\s*:\s*[+-]?(?:0+(?:\.0*)?|\.0+)(?:[eE][+-]?\d+)?(?![.\d])"
    r"|content-visibility\s*:\s*hidden"
    r")",
    re.I)


class _ProgressTextParser(HTMLParser):
    """Reads the served `#progress` markup STRUCTURALLY instead of by regex.

    A regex over markup cannot tell a tag from a `<` in copy, mis-splits a tag
    whose attribute value contains `>`, and never sees copy carried in `alt=` or
    `title=`. Walking the document gets those right by construction, so what
    this parser reports is exactly what a TEXT pin cannot cover:

    - `attribute_copy` — copy-bearing attributes with a non-empty value: they
      render, and a pin on text cannot see them. `srcdoc` is included because an
      `<iframe>`'s document renders inside the section.
    - `hidden` — text that does not reach every reader, or reaches none: an
      element marked `hidden`, one whose inline `style` hides it
      (`_HIDING_DECL`), or text inside `aria-hidden="true"`. The last one is an ACCESSIBILITY defect, not a
      visual one — a sighted reader still sees that text — and it is text-only
      so the page's own decorative `<svg aria-hidden="true">` still passes.
    - `stray` — text outside the title, the headings and the bullets: a claim
      added anywhere else in the section, including between a heading and its
      list, and in a list that precedes every heading.

    Scope, stated plainly: this is a TEXT gate. It pins the words the section
    renders and refuses text this walk cannot attribute to a heading or a
    bullet. It is NOT a defence against CSS: `clip`, `text-indent`, an
    off-screen `position`, a `transform`, or any rule in the page stylesheet can
    make text invisible without the walk noticing, and an attempted check for
    those was removed rather than kept — it was unsound both ways (it matched
    the page's own `html.gated section { display: none }` and missed indirection
    through a class). A hidden-claim audit is a different instrument.

    Markup that moves without changing a word (a `class`/`id`, a `<ul>` wrapped
    in a `<div>`, an `<hr>`) carries no text and therefore passes. Text is
    collected per element, and `convert_charrefs` decodes `&ldquo;` to the curly
    quotation mark the page renders, so the tuples compare against what a reader
    reads.
    """

    COPY_ATTRS = ("alt", "title", "placeholder", "aria-label", "value", "srcdoc")
    #: HTML void elements never close, so `handle_starttag` does not push them.
    #: Their synthetic end tag therefore has nothing to match and closes nothing
    #: (`handle_endtag` only pops on a match); the set is here so the self-closing
    #: override never treats one as ordinary HTML and tries to close it.
    VOID = frozenset((
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ))
    HIDING_STYLE = _HIDING_DECL

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (tag, aria_hidden) — the flag rides the stack so `aria-hidden` only
        # counts when it wraps TEXT.
        self.stack: list[tuple[str, bool]] = []
        self.cells: list[tuple[str, list[str]]] = []
        self.title: list[str] = []
        self.attribute_copy: list[str] = []
        self.hidden: list[str] = []
        self.stray: list[str] = []
        self._heading: list[str] = []
        #: Whether a heading OPENED this cell — separate from its text being
        #: non-empty, so `<h3></h3>` still owns the bullets that follow it.
        self._heading_seen = False
        self._bullets: list[str] = []
        self._li: list[list[str]] = []
        #: list nesting depth when each `_li` buffer opened — `</li>` is
        #: optional before the next sibling `<li>`, but a nested list inside an
        #: `<li>` must NOT be read as a sibling.
        self._li_depth: list[int] = []
        self._list_depth = 0

    def _flush_open_li(self) -> None:
        """Close any `<li>` still open (the browser auto-closes it), so its text
        reaches the pins instead of being carried into the next cell.

        Closing is inner-first, mirroring `handle_endtag`: a nested list inside
        an `<li>` merges into the bullet that contains it, or the inner items
        would surface as siblings in the wrong order.
        """
        while len(self._li) > 1:
            inner = _norm(self._li.pop())
            self._li_depth.pop()
            self._li[-1].append(inner)
        while self._li:
            self._bullets.append(_norm(self._li.pop()))
            self._li_depth.pop()

    def _close_cell(self) -> None:
        """Finish the cell whose bullets have all been seen (a cell is complete
        when the NEXT heading opens, or the document ends).

        Bullets collected before any heading exists have no cell to belong to,
        so they are reported as stray. Bullets collected AFTER the last heading
        belong to that (still open) cell and reach its tuple — either way they
        are pinned or they fail; neither path drops text.

        "A heading was seen" is a FLAG, not `self._heading` being non-empty: an
        `<h3></h3>` (or one holding only a comment or an icon) carries no text
        yet still opens a cell, and reading emptiness as "no heading" reported
        that cell's pinned bullets as stray.
        """
        self._flush_open_li()
        if self._heading_seen:
            self.cells.append((_norm(self._heading), list(self._bullets)))
        else:
            self.stray.extend(b for b in self._bullets if b)
        self._heading, self._bullets = [], []
        self._heading_seen = False

    def handle_starttag(self, tag: str, attrs) -> None:
        aria_hidden = False
        for name, value in attrs:
            low = name.lower()
            if low in self.COPY_ATTRS:
                if value:
                    self.attribute_copy.append(f"<{tag} {name}={value!r}>")
                continue
            if low == "hidden" or (low == "aria-hidden" and (value or "").lower() == "true"):
                if low == "hidden":
                    self.hidden.append(f"<{tag} {name}>")
                else:
                    aria_hidden = True
            elif low == "style" and value and self.HIDING_STYLE.search(value):
                self.hidden.append(f"<{tag} style={value!r}>")
        if tag not in self.VOID:
            self.stack.append((tag, aria_hidden))
        if tag in ("ul", "ol"):
            self._list_depth += 1
        elif tag == "h3":
            self._close_cell()
            self._heading_seen = True
        elif tag == "li":
            if self._li and self._li_depth[-1] == self._list_depth:
                # Implicit close: `</li>` is optional before the next sibling.
                # It merges into an enclosing bullet exactly as an explicit
                # `</li>` does, so ordering cannot depend on the omission.
                self._li_depth.pop()
                text = _norm(self._li.pop())
                if self._li:
                    self._li[-1].append(text)
                else:
                    self._bullets.append(text)
            self._li.append([])
            self._li_depth.append(self._list_depth)

    def handle_startendtag(self, tag: str, attrs) -> None:
        """A self-closing tag is closed only where a browser closes it.

        The default fires BOTH halves, which is wrong on an ordinary HTML element
        (`<li/>`, `<p/>`): the slash is a parse error there, so the element stays
        OPEN and closing it would merge or drop the text that follows. Only
        foreign content (SVG/MathML) honours the slash. Void elements are left to
        `handle_starttag`, which never pushes them.
        """
        self.handle_starttag(tag, attrs)
        foreign = tag in ("svg", "math") or any(
            t in ("svg", "math") for t, _ in self.stack)
        if foreign:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        # Only a MATCHING end tag closes anything. An end tag with nothing to
        # match — `</br>` on a void element, a stray or mistyped close — is
        # ignored by a browser, and popping for it would drop every open
        # ancestor: an enclosing `aria-hidden` would stop applying, and text
        # after `<h3>Early<br></br>demand</h3>` would be misrouted to stray.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in ("ul", "ol"):
            # A closing list closes the items it opened: `</li>` is optional
            # before `</ul>` too, and leaving a buffer open would nest the next
            # SIBLING item into the item before it.
            while self._li and self._li_depth[-1] >= self._list_depth:
                self._li_depth.pop()
                text = _norm(self._li.pop())
                if self._li:
                    self._li[-1].append(text)
                else:
                    self._bullets.append(text)
            self._list_depth = max(0, self._list_depth - 1)
        if tag == "li" and self._li:
            self._li_depth.pop()
            text = _norm(self._li.pop())
            if self._li:
                # a nested list inside an <li> merges into the outer bullet:
                # still pinned, and a mismatch still fails.
                self._li[-1].append(text)
            else:
                self._bullets.append(text)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if data.strip() and any(aria for _, aria in self.stack):
            self.hidden.append(f"aria-hidden text {data.strip()!r}")
        if self._li:
            self._li[-1].append(data)
        elif any(tag == "h3" for tag, _ in self.stack):
            self._heading.append(data)
        elif any(tag == "h2" for tag, _ in self.stack):
            self.title.append(data)
        elif data.strip():
            # Indentation between elements is not a claim; real text is.
            self.stray.append(data.strip())

    def close(self) -> None:
        super().close()
        self._close_cell()


def _automate_paragraph_markup() -> str:
    section = _automate_beat_markup()
    match = re.search(r"<p>.*?</p>", section, re.S)
    assert match, (
        "#beat-automate has no <p>…</p> — the beat was restructured and this "
        "gate's subject changed shape (#3432)")
    return match.group(0)


class TestAutomateBeatCopy:
    """#3432 / #8466: the `04 · Automate` beat's copy, and the facts behind it.

    #3432 pinned this paragraph while the beat advertised webhook delivery in
    the present tense and no outbound webhook existed anywhere in `tortoise/`
    (every hit was an INBOUND receiver — `/webhooks/stripe`, the connector
    servers). That is resolved: push delivery SHIPPED in PR #8104 (epic #2080
    W4 phase 2) — `tortoise/webhook_delivery.py` (subscription registry,
    dispatcher, retry/backoff, delivery receipts, HMAC signing) plus four
    tenant REST routes declared in `config/rest-surface-inventory.yml`, drained
    at the end of a tenant's dream cycle with `TORTOISE_WEBHOOK_DISPATCH` as
    the kill switch. The `planned, not shipped — see #3909` hedge then became
    the stale claim, so #8466 replaced the paragraph and retired the two
    assertions whose subject the shipped capability removed:

    * the `pricing.json` premise test — its own docstring named the failure mode
      that happened ("the premise reads a `pricing.json` flag, and such flags
      LAG"). `product/pricing.json` still reads `features.webhooks: "planned"`
      on pro/team while the delivery layer is on main, so that assertion was
      green while reality moved. Flipping the flag is deliberately NOT part of
      #8466: the file's own `_comment` records that these feature strings lag
      by design pending a pure-doc follow-up, and `per_graph_keys` is in the
      same state.
    * the tracker-link test — an earlier revision of this copy carried a
      roadmap claim that needed a link; the owner's final copy does not link a
      tracker either.

    ⚠️ THE COPY IS THE OWNER'S, VERBATIM, AND ONE OF ITS TWO CLAUSES IS ROADMAP.
    The paragraph reads "Define a confidence threshold or lifecycle events to fire
    webhooks." The LIFECYCLE half ships: `webhook_delivery._validated_types`
    filters the durable `:GraphEvent` types → the 12 `CLAIM_EVENT_TYPES` in
    `tortoise/shared_state/events.py`. The CONFIDENCE-THRESHOLD half does NOT:
    no subscription carries a threshold/condition field, and nothing fires on
    an EP belief crossing a threshold — that trigger is #3909 (OPEN,
    `deferred`, `not-launch-relevant`), and §5.3 of
    `docs/plans/2026-10-11-2081-webhook-delivery.md` says #3909 reuses this
    layer. The owner ruled on 2026-10-13 that the beat carries this copy
    anyway (see the OVERRIDES line on #8466). ⛔ DO NOT "fix" the paragraph by
    deleting the clause as an unshipped-availability claim: that edit was made
    in PR #8470 and reverted by the owner's instruction.

    What is left is a paraphrase guard, and it is deliberately weak: the pin is
    a hand-maintained SECOND COPY of a prose claim (the durable binding is
    #5063), and its scope is this beat's static markup in this one file — a
    claim added to another beat, to another page, or injected at runtime by the
    page's own GSAP block is green here. It is kept because this paragraph has
    already carried a wrong availability claim in each direction: unshipped-but-
    advertised before #3432, shipped-but-disclaimed after.
    """

    # The paragraph's rendered text — the owner's copy, verbatim. Amended by the
    # owner on 2026-10-18 (#8466 pin): "Define confidence threshold" → "Define a
    # confidence threshold" (a missing article), and the second sentence dropped
    # to "Automate decisions and agent actions." (the "investment decisions … and
    # more" tail overclaimed). Both edits are the owner's, made on the same
    # sentence the docstring above calls theirs — not a paraphrase.
    EXPECTED_PARAGRAPH_TEXT = (
        "Define a confidence threshold or lifecycle events to fire webhooks. "
        "Automate decisions and agent actions."
    )

    def test_automate_beat_paragraph_is_pinned(self):
        """The paragraph is pinned so a paraphrase cannot slip past.

        A token check ("does 'webhook' appear, and is 'planned' somewhere in the
        beat?") passed on `Use webhooks to trigger workflows… planned`, which is
        still an availability claim. Equality does not.
        """
        assert _beat_text(_automate_paragraph_markup()) == self.EXPECTED_PARAGRAPH_TEXT, (
            "the 04 · Automate paragraph changed. Before updating this pin, "
            "answer: is the paragraph still the owner's copy VERBATIM? It is an "
            "owner-authored sentence, not a derivable availability statement — "
            "the confidence-threshold clause is roadmap (#3909) and is here by "
            "the owner's 2026-10-13 ruling (OVERRIDES on #8466). Removing that "
            "clause as an unshipped-availability claim was done in PR #8470 and "
            "reverted; do not repeat it. LIFECYCLE-event push delivery does "
            "ship: tortoise/webhook_delivery.py + /v1/context/subscriptions."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. /investors Progress: cell order + both cells' pinned bullets (PR #9283)
# ═══════════════════════════════════════════════════════════════════════════════


def _investors_progress_section() -> str:
    """The `<section id="progress">…</section>` slice, from the SECTION tag, as
    a READER receives it — `_served_without_comments` strips comments (deleting
    them, not spacing them) before the slice is taken.

    Serving first is load-bearing, not tidiness: slicing the raw file and
    filtering afterwards cannot see a comment that opens BEFORE the section, so
    `<!-- <section id="progress"> … </section> -->` left every assertion in this
    class green while no reader receives the section at all — the exact failure
    `_served` exists to prevent. `_pricing_section` slices the stripped document
    for the same reason.

    Same construction (and the same nested-section guard) as
    `_automate_beat_markup`: the slice must start at `<section`, and this slice
    stops at the FIRST `</section>`, so a claim inside a nested section would be
    invisible to every assertion below.
    """
    src = _served(INVESTORS_HTML.read_text(encoding="utf-8"))
    raw = INVESTORS_HTML.read_text(encoding="utf-8")
    marker = 'id="progress"'
    # Strip comments BEFORE locating anything in the raw file: a commented
    # `</section>` would end the raw region early, and a comment that merely
    # NAMES the marker would make the raw and served slices describe different
    # sections.
    raw_wo_comments = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    assert marker in src, (
        "#progress is absent from website/investors.html as served — moved, "
        "renamed, or commented out. This gate's subject is gone; re-point it at "
        "wherever the Progress section lives (PR #9283)")
    assert src.count(marker) == 1 and raw_wo_comments.count(marker) == 1, (
        f"website/investors.html mentions {marker} "
        f"{src.count(marker)}x as served / {raw_wo_comments.count(marker)}x raw. "
        "This gate slices raw and served separately and they must describe the "
        "same section (PR #9283)")
    # `_served` deletes <style>/<script> bodies, so copy they inject (a
    # `content:` string appended to every heading, say) renders on the page and
    # is invisible to every assertion below. Only the RAW region can show them.
    raw_start = raw_wo_comments.rindex("<section", 0, raw_wo_comments.index(marker))
    raw_section = raw_wo_comments[raw_start:raw_wo_comments.index("</section>", raw_start)]
    assert not re.search(r"<(style|script)\b", raw_section, re.I), (
        "#progress carries a <style> or <script>. `_served` removes its body "
        "before this gate slices the section, so text it injects renders while "
        "no assertion can see it. Move the rule into the page stylesheet and "
        "delete it here (PR #9283).")
    text_src = _served_without_comments(INVESTORS_HTML.read_text(encoding="utf-8"))
    i = text_src.index(marker)
    start = text_src.rindex("<section", 0, i)
    section = text_src[start:text_src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #progress — this slice stops at the first "
        "</section>, so a claim in the nested section would be invisible to "
        "every assertion in this class (PR #9283)")
    # Testing for a `</section>` literal inside `section` cannot work — the
    # slice stops BEFORE it. The truncation itself is what shows up: the slice
    # ends inside an attribute value.
    assert not _ends_inside_attribute_value(section), (
        "#progress's slice ends inside an attribute value — a `</section>` "
        "carried in one truncated it early, so whatever follows renders "
        "unchecked (PR #9283)")
    return section


class _ProfileCellCounter(HTMLParser):
    """Counts the DIRECT `<div>` children of the section's ONE `.profile` block.

    "Direct" is structural: a `<div>` counts only when no other element is open
    inside `.profile`, so wrapping the cells in a `<span>` (which leaves the grid
    with one child) counts nothing, while wrapping them in a `<div>` counts one.

    Two children is a NECESSARY condition for the side-by-side read, not a
    sufficient one: the columns themselves come from the `.profile` declaration,
    which this counter does not read, so a `grid-template-columns: 1fr` edit
    stacks them and stays green. What it removes is the specific bypass that
    merged both cells into one child while keeping two `<h3>`s.

    `count` is summed across every `.profile` block, so it says nothing on its
    own about how many blocks there are: splitting the section into two
    `.profile` divs (one cell each) also yields 2 while the columns stack.
    `profiles` is what rules that out.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.count = 0
        self.profiles = 0
        self._inside = False
        #: Elements open INSIDE the `.profile` div. Empty means a `<div>`
        #: starting now is a direct child; a `<span>`/`<a>` wrapper puts its
        #: contents one level down, where they are not grid children.
        self._open: list[str] = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        """HTML ignores the self-closing slash, so the element stays OPEN.

        The default start-then-end pair would close a `<div/>` the browser keeps
        open, and every child after it would fall outside the profile.
        """
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs) -> None:
        if not self._inside:
            if (tag == "div"
                    and "profile" in (dict(attrs).get("class") or "").split()):
                self._inside = True
                self.profiles += 1
                self._open = []
            return
        if tag == "div" and not self._open:
            self.count += 1
        if tag not in _ProgressTextParser.VOID:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._inside:
            return
        if tag == "div" and not self._open:
            self._inside = False
            return
        # An unmatched end tag closes nothing (a browser ignores it); popping
        # for it would close the profile on a stray `</span>`.
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i] == tag:
                del self._open[i:]
                break


def _walk_progress_section(markup: str) -> _ProgressTextParser:
    """Run the walk over `markup` placed inside a minimal `#progress` shell.

    Synthetic markup, not the live page: it reaches parser branches the served
    section does not exercise today, so a branch cannot regress to silently
    allowing a claim while the pins stay green.
    """
    parser = _ProgressTextParser()
    parser.feed(
        '<section id="progress"><div class="wrap"><h2>Progress</h2>'
        f'<div class="profile">{markup}</div></div></section>')
    parser.close()
    return parser


def _progress_cells() -> list[tuple[str, list[str]]]:
    """`(h3 heading, rendered bullets)` per cell, in DOM order.

    The walk is `_ProgressTextParser`, over the SERVED section
    (`_investors_progress_section`), so a comment is not a claim a reader
    receives. The parser refuses — rather than silently ignoring — the three
    things a text pin cannot cover: copy in an attribute, a hidden element, and
    any text outside the title, the headings and the bullets. Markup that moves
    without changing a word does not fail.
    """
    parser = _ProgressTextParser()
    parser.feed(_investors_progress_section())
    parser.close()
    assert not parser.attribute_copy, (
        "#progress carries copy in an attribute: "
        f"{parser.attribute_copy!r}. It renders, but these pins compare TEXT and "
        "cannot see it — put the claim in a <li> and pin it, or drop the value "
        "(PR #9283).")
    assert not parser.hidden, (
        f"#progress hides an element: {parser.hidden!r}. A claim that reaches no "
        "reader while its pin stays green is the failure this gate exists for: "
        "for a `hidden`/inline-`style` hit delete the bullet and its tuple "
        "instead of hiding it, and for an `aria-hidden` hit remove the attribute "
        "— a sighted reader still sees that text, so deleting the copy would "
        "hide a real claim from everyone (PR #9283).")
    assert _norm(parser.title) == "Progress", (
        f"#progress's own title is {_norm(parser.title)!r}, not 'Progress' — a "
        "claim can hide there and no pinned tuple covers it (PR #9283)")
    assert not parser.stray, (
        "#progress carries text outside the pinned structure: "
        f"{parser.stray!r}. No EXPECTED_*_BULLETS tuple can see it — put the "
        "claim in a <li> and pin it, or remove it (PR #9283).")
    return parser.cells


class TestInvestorsProgressCopy:
    """PR #9283: the Progress section's cell order and both cells' pinned bullets.

    THREE failure modes.

    1. **The cell order silently reverts.** `.profile` is
       `display: grid; grid-template-columns: 1fr 1fr` with no `direction`
       override. Two rules reach `.profile`'s COLUMNS (typography does not
       affect order): `#gtm .profile > div:last-child` (scoped to `#gtm`) and
       the 620px breakpoint
       (`.outcomes, .profile { grid-template-columns: 1fr }`). Neither REORDERS
       a cell — one spans the last GTM cell, the other stacks the columns — so
       DOM order remains the rendered order at every width, and the section must
       OPEN on what exists (`Product & distribution`) rather than on the demand
       claim. Rendering collapses the difference: there is no diff marker, no
       test, and no CSS selector that notices a swap.

    2. **A traction figure moves without the owner.** These bullets are the
       claims this page makes to investors, so each one is pinned verbatim, in
       DOM order. A figure that changes is a claim the page cannot back — the
       provenance is recorded next to the tuples.

    3. **The quotation is paraphrased.** A reviewer P2 — "the quotation asserts
       a verbatim utterance nobody recorded" — was actioned by an agent in
       `fbd706a` as a rewrite of the owner's line into indirect speech (`asked
       when they could use it`). The owner reverted the rewrite in `cd864dc`
       and recorded the P2 as an ACCEPTED RISK: the copy is the owner's, and a
       reviewer finding does not authorise an agent to rewrite it. This pin
       makes the next such rewrite fail here instead of shipping
       (`tortoise#9283`).

    Scope, stated plainly: this is a hand-maintained SECOND COPY of prose over
    one static page's markup. Within `#progress` the extraction is fail-closed
    (`_progress_cells`): copy in an attribute, text hidden by one of the inline
    declarations in `_HIDING_DECL` or by a `hidden` attribute, an injected
    `<style>`/`<script>`, and any text outside the pinned `<h3>`/`<li>` text all
    fail rather than hiding.

    NOT covered, and deliberately: a claim injected at runtime; a claim on
    another page; and ANY hiding done from the page stylesheet, whole section or
    one bullet. A stylesheet check was written and then removed — it matched the
    page's own `html.gated section { display: none }` and missed indirection
    through a class, so it bought false reds in exchange for coverage it could
    not deliver. A hidden-claim audit is a different instrument.
    """

    # Rendered left/right order of the two cells: what exists, then the demand.
    EXPECTED_CELL_ORDER = ("Product & distribution", "Early demand")
    # Both cells' bullets, verbatim and in DOM order — entities decoded, so the
    # curly quotation marks are pinned too (ASCII quotes are NOT this copy).
    #
    # Both cells are pinned because these are the traction claims this page
    # makes to investors: a figure that changes without the owner saying so is a
    # claim the page cannot back. Provenance for the current figures: the
    # submitted a16z Speedrun application video (2026-11-01) — "more than two
    # million views", "over 40 calls", "more than 35 people now waiting". The
    # page renders that last one as `35+ waiting to try it`.
    EXPECTED_PRODUCT_BULLETS = (
        "Working alpha, beta in development",
        "2M+ views on founder-led content, generating inbound leads",
    )
    EXPECTED_DEMAND_BULLETS = (
        "40+ calls: \u201cwhen can I use it\u201d",
        "35+ waiting to try it",
        "1 team design partner providing data & feedback",
        "Enterprise discussions underway",
    )
    # What an earlier revision carried. It must not come back: "prospective"
    # disclaims the noun it modifies.
    RETIRED_LINE = "20+ prospective beta testers"

    def test_progress_opens_on_what_exists(self):
        """Left cell is `Product & distribution` — DOM order is render order."""
        counter = _ProfileCellCounter()
        counter.feed(_investors_progress_section())
        counter.close()
        assert counter.profiles == 1, (
            f"#progress must hold ONE `.profile` block, found {counter.profiles} "
            "— two blocks (one cell each) stack the columns instead of placing "
            "them side by side, which is the point of the section (PR #9283)")
        assert counter.count == 2, (
            f"#progress must hold exactly two `.profile` cells, found "
            f"{counter.count} — the side-by-side read (what exists | what is "
            "claimed) is the point of the section, and merging them into one "
            "child leaves the right column empty (PR #9283)")
        cells = _progress_cells()
        assert len(cells) == 2, (
            f"#progress must carry exactly two cells, found {len(cells)} "
            "headings (PR #9283)")
        headings = tuple(heading for heading, _ in cells)
        assert headings == self.EXPECTED_CELL_ORDER, (
            f"#progress cell order changed: {headings!r} != "
            f"{self.EXPECTED_CELL_ORDER!r}. The section must open on what "
            "EXISTS ('Product & distribution'); the demand claim belongs in the "
            "right cell (PR #9283). DOM order is the rendered order — the grid "
            "is `1fr 1fr` with no `direction` override and no positional rule "
            "reaching #progress — so swapping the two <div>s inverts the page.")

    def test_product_bullets_are_the_sanctioned_copy_verbatim(self):
        """The `Product & distribution` cell, character-for-character."""
        cells = dict(_progress_cells())
        assert "Product & distribution" in cells, (
            f"the 'Product & distribution' cell is gone (cells: {sorted(cells)}) "
            "— this gate's subject moved (PR #9283)")
        actual = tuple(cells["Product & distribution"])
        assert actual == self.EXPECTED_PRODUCT_BULLETS, (
            f"the Product & distribution bullets changed: {actual!r} != "
            f"{self.EXPECTED_PRODUCT_BULLETS!r}. These are traction claims made "
            "to investors — the submitted application video is the source for "
            "the `2M+ views` figure, so take it from there, never paraphrase it "
            "from a nearby draft.")

    def test_early_demand_bullets_are_the_sanctioned_copy_verbatim(self):
        """The whole `Early demand` cell, character-for-character."""
        cells = dict(_progress_cells())
        assert "Early demand" in cells, (
            f"the 'Early demand' cell is gone (cells: {sorted(cells)}) — this "
            "gate's subject moved (PR #9283)")
        actual = tuple(cells["Early demand"])
        assert actual == self.EXPECTED_DEMAND_BULLETS, (
            f"the Early-demand bullets are not the owner's copy verbatim: "
            f"{actual!r} != {self.EXPECTED_DEMAND_BULLETS!r}. The first bullet "
            "is the owner's quotation — ⛔ DO NOT 'fix' it by paraphrasing it "
            "to indirect speech: that edit was made in fbd706a (a reviewer P2 "
            "about the quotation asserting a verbatim utterance nobody "
            "recorded), reverted by the owner in cd864dc, and the P2 recorded "
            "as an ACCEPTED RISK. The copy is the owner's call. If the copy "
            "genuinely changed, get the owner's wording and update this pin "
            "with it.")

    def test_the_hiding_declarations_and_their_zero_spellings(self):
        hiding = (
            "display:none", "visibility:hidden", "color:transparent",
            "content-visibility:hidden",
            # A zero value is the same declaration however it is spelled, sign
            # included: `.0`, `00` and `+0` hide the text just as `0` does, so a
            # parser anchored only on the bare form leaves a live bypass. A
            # computed zero (`calc(0)`) is out of reach by construction.
            "opacity:0", "opacity:0.0", "opacity:.0", "opacity:00",
            "opacity:+0", "opacity:-0",
            "font-size:0", "font-size:0.0px", "font-size:00", "font-size:+0",
        )
        for css in hiding:
            assert _HIDING_DECL.search(f"margin:0;{css}"), css

    def test_honest_styles_are_not_mistaken_for_hiding(self):
        for css in (
            "opacity:0.35", "opacity:.9", "opacity:1", "opacity:+0.5",
            "font-size:0.9em", "font-size:+.5em",
            "background-color:transparent", "transition:opacity 0.2s",
        ):
            assert not _HIDING_DECL.search(css), css

    def test_an_unterminated_attribute_value_is_refused(self):
        # A quoted value is the common shape, but an unquoted one truncates the
        # slice just as effectively: the tokenizer appends the `<` to the value,
        # so the browser never sees an end tag while this slice does.
        for truncated in ('<span title="a', "<span title='a", "<span title=a",
                          "</ul><span title=a",
                          # a value may contain `>` or `<`
                          '<span title="a>b', '<span data-x="a < b ',
                          "<span data-x=a<b "):
            assert _ends_inside_attribute_value(truncated), truncated
        for whole in ('<span title="a</section>b">', "<li><span>x</span></li>",
                      '<div class="profile">', "<ul>",
                      # A trailing text node is not an attribute value: the
                      # `stray` assertion is what should report it, and a closed
                      # tag before the `=` proves the text is outside the tag.
                      "</div><p>Ratio = 2", "</div><p>Revenue=2M",
                      '<div class="x">Ratio = 2', '<p class="note">Total ='):
            assert not _ends_inside_attribute_value(whole), whole

    def test_an_empty_heading_still_owns_its_bullets(self):
        # A heading that renders no text (`<h3></h3>`, or one holding only a
        # comment) still opens a cell. Reading its emptiness as "no heading"
        # reported that cell's pinned bullets as stray text.
        assert _walk_progress_section(
            "<h3></h3><ul><li>a</li><li>b</li></ul>").cells == [("", ["a", "b"])]
        assert _walk_progress_section(
            "<h3><!-- x --></h3><ul><li>a</li></ul>").cells == [("", ["a"])]
        # Bullets with NO heading at all still have no cell to belong to.
        orphan = _walk_progress_section("<ul><li>a</li></ul>")
        assert orphan.cells == [] and orphan.stray == ["a"]

    def test_a_comment_between_words_does_not_synthesise_a_space(self):
        # The served text a reader gets has no space there, so the pin must not
        # either; `_served` would turn the comment into one.
        assert _served_without_comments(
            "<span>2M+</span><!--x--><span>views</span>"
        ) == "<span>2M+</span><span>views</span>"
        assert _served_without_comments("a<!--x-->b") == "ab"
        # The space a reader DOES get is preserved.
        assert _served_without_comments("a <!--x--> b") == "a  b"

    def test_a_comment_inside_the_section_does_not_synthesise_a_space(self) -> None:
        # No comment sits inside the real section, so this reads the slice from a
        # temporary page: with `_served` (comment -> space) the bullet would pin
        # as `2M+ views` while a reader gets `2M+views`.
        global INVESTORS_HTML
        page = Path(tempfile.mkdtemp()) / "investors.html"
        page.write_text(
            '<section id="progress"><div class="wrap"><h2>Progress</h2>'
            '<div class="profile"><div><h3>Product</h3><ul class="bullets">'
            '<li><span>2M+</span><!--x--><span>views</span></li></ul></div>'
            '<div><h3>Demand</h3><ul class="bullets"><li><span>a</span></li>'
            '</ul></div></div></div></section>',
            encoding="utf-8")
        original, INVESTORS_HTML = INVESTORS_HTML, page
        try:
            assert dict(_progress_cells())["Product"] == ["2M+views"]
        finally:
            INVESTORS_HTML = original

    def test_a_nested_list_never_reorders_a_bullet(self):
        # `</li>` is optional before the next sibling, so the same markup is
        # read the same way whether it is written or omitted. Closing inner
        # first is what keeps the order; closing outer first emitted the inner
        # items as siblings ahead of their own parent.
        for markup, expected in (
            ("<li>a<ul><li>b<li>c</ul></li>", ["abc"]),
            ("<li>a<ul><li>b</li><li>c</li></ul></li>", ["abc"]),
            # `</li>` is optional before `</ul>` too: the item that contains the
            # nested list must close there, or the next SIBLING nests into it.
            ("<li>a<ul><li>b</ul><li>c</li><li>d</li>", ["ab", "c", "d"]),
            ("<li>a<ul><li>b</li></ul></li><li>c</li><li>d</li>", ["ab", "c", "d"]),
        ):
            assert _walk_progress_section(f"<h3>H</h3><ul>{markup}</ul>").cells == [("H", expected)]
        assert _walk_progress_section("<h3>H</h3><ul><li>a</li><li>b</li></ul>").cells == [
            ("H", ["a", "b"])]
        # An `<li>` left open at the END of the document, or at a cell boundary,
        # is still rendered by the browser: `_flush_open_li` is what carries it
        # into the pins. Without these three the flush body could be deleted and
        # every remaining case would still pass.
        unclosed = _walk_progress_section("<h3>H</h3><ul><li>claim</ul>")
        assert unclosed.cells == [("H", ["claim"])]
        assert not unclosed._li and not unclosed._li_depth
        assert _walk_progress_section(
            "<h3>H</h3><ul><li>claim").cells == [("H", ["claim"])]
        assert _walk_progress_section(
            "<h3>A</h3><ul><li>ax<h3>B</h3><ul><li>bx").cells == [
                ("A", ["ax"]), ("B", ["bx"])]
        assert _walk_progress_section(
            "<h3>H</h3><ul><li>a<ul><li>b").cells == [("H", ["ab"])]

    def test_the_walks_survive_self_closing_tags(self):
        # The counter runs the start half alone for every self-closing tag, so a
        # `<br/>` or `<img/>` inside a cell can neither crash it nor unbalance
        # its depth; the `<div class="profile"/>` case below is the one that
        # would fail if it closed elements the browser keeps open.
        counter = _ProfileCellCounter()
        counter.feed('<div class="profile"><div><h3>a</h3><ul><li>x<br/></li>'
                     '</ul></div><div><h3>b</h3><ul><li><img src="y"/></li>'
                     '</ul></div></div>')
        assert counter.count == 2
        # "Direct" is structural: a non-div wrapper leaves the grid with ONE
        # child, so it must not read as two cells.
        for wrapper in ("span", "a href='#'", "p"):
            wrapped = _ProfileCellCounter()
            wrapped.feed('<div class="profile"><' + wrapper + '><div>a</div>'
                         '<div>b</div></' + wrapper.split()[0] + '></div>')
            assert wrapped.count == 0, wrapper
        # A div wrapper merges the cells into one grid child.
        merged = _ProfileCellCounter()
        merged.feed('<div class="profile"><div><div>a</div><div>b</div></div></div>')
        assert merged.count == 1
        # Two `.profile` blocks stack the columns even though `count` is 2.
        split = _ProfileCellCounter()
        split.feed('<div class="profile"><div>a</div></div>'
                   '<div class="profile"><div>b</div></div>')
        assert (split.profiles, split.count) == (2, 2)
        # HTML ignores the slash: the div stays open, so both children count.
        counter = _ProfileCellCounter()
        counter.feed('<div class="profile"/><div>a</div><div>b</div>')
        assert counter.count == 2
        # An end tag with nothing to match (`</br>` on a void element) is ignored
        # by a browser, so it must not close an ancestor: doing that reddened an
        # honest heading and switched off an enclosing `aria-hidden`.
        assert _walk_progress_section(
            "<h3>H<br></br>2</h3><ul><li>a</li></ul>").cells == [("H2", ["a"])]
        assert not _walk_progress_section(
            "<h3>H<br></br>2</h3><ul><li>a</li></ul>").stray
        assert len(_walk_progress_section(
            '<h3>H</h3><ul><li><div aria-hidden="true">a<br></br>b</div>'
            '</li></ul>').hidden) == 2
        # `<li/>` stays open in HTML, so the text after it is that bullet's.
        assert _walk_progress_section(
            "<h3>H</h3><ul><li/>a<li>b</li></ul>").cells == [("H", ["a", "b"])]

    def test_the_walk_refuses_what_a_text_pin_cannot_see(self):
        """The parser's branches, on synthetic markup rather than the live page.

        Every refusal below exists because a claim escaped the pins in an
        earlier revision of this gate, and the live page exercises none of them.
        Without these cases a branch could regress to "silently allow" and the
        suite would stay green — the failure mode the gate exists to prevent.
        """

        walk = _walk_progress_section

        # Refusals — each is a claim a reader could be shown.
        assert walk('<h3>H</h3><ul><li>a</li></ul><img alt="claim">').attribute_copy
        assert walk('<h3>H</h3><ul><li>a</li></ul><iframe srcdoc="claim">').attribute_copy
        assert walk('<h3>H</h3><ul><li hidden>a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li style="display:none">a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li style="color:transparent">a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li style="opacity:0">a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li><span aria-hidden="true">a</span></li></ul>').hidden
        assert walk('<h3>H</h3><p>claim</p>').stray == ["claim"]
        assert walk('<p>claim</p><h3>H</h3><ul><li>a</li></ul>').stray == ["claim"]
        assert walk('<h3>H</h3>lead-in<ul><li>a</li></ul>').stray == ["lead-in"]

        # Honest markup — none of these is a claim, so none may refuse.
        assert not walk('<h3>H</h3><ul><li>a</li></ul><svg aria-hidden="true"></svg>').hidden
        assert not walk('<h3>H</h3><ul><li>a</li></ul><img alt="">').attribute_copy
        assert not walk('<h3>H</h3><ul><li style="opacity:0.35">a</li></ul>').hidden
        assert not walk('<h3>H</h3><hr><ul><li>a</li></ul>').stray
        assert not walk('<div><h3 class="x">H</h3></div><ul><li>a</li></ul>').stray

        # Pinning still works through the shapes the page may legitimately take.
        assert walk('<h3>H</h3><ul><li>a</li></ul>').cells == [("H", ["a"])]
        # No space is synthesised: `out` and `in` touch in the markup, so they
        # touch in the pin — a change a reader sees cannot pass.
        assert walk('<h3>H</h3><ul><li>out<ul><li>in</li></ul></li></ul>').cells == [
            ("H", ["outin"])]
        assert walk('<h3>H</h3><ul><li><span>a</span><span>b</span></li></ul>').cells == [
            ("H", ["ab"])]
        assert walk('<h3>H</h3><ul><li><span>a</span> <span>b</span></li></ul>').cells == [
            ("H", ["a b"])]
        # Self-closing tags must not drain the stack: the synthetic end tag has
        # nothing to match on a void element and would drop every ancestor.
        assert walk('<h3>H</h3><ul><li><div aria-hidden="true">'
                    '<img src="x"/>secret</div>a</li></ul>').hidden
        assert walk('<h3>H<br/>x</h3><ul><li>a</li></ul>').cells == [("Hx", ["a"])]
        assert not walk('<h3>H</h3><ul><li>a</li></ul><svg aria-hidden="true"/>').hidden
        assert walk('<h3>H</h3><ul><li>claim</ul>').cells == [("H", ["claim"])]
        assert walk('<h3>H</h3><div><ul><li>a</li></ul></div>').cells == [("H", ["a"])]
        # `</li>` is optional before the next <li>: the browser closes it, so
        # these are two bullets and neither may leak into the next cell.
        assert walk('<h3>H</h3><ul><li>a<li>b</li></ul>').cells == [("H", ["a", "b"])]
        assert walk('<h3>A</h3><ul><li>ax</ul><h3>B</h3><ul><li>bx</li></ul>').cells == [
            ("A", ["ax"]), ("B", ["bx"])]

    def test_the_retired_prospective_beta_line_is_gone(self):
        """`20+ prospective beta testers` must not return anywhere in the section."""
        section_text = _beat_text(_investors_progress_section())
        assert self.RETIRED_LINE not in section_text, (
            f"{self.RETIRED_LINE!r} is back in #progress. 'prospective' "
            "disclaims the noun it modifies; it was replaced by the owner's own "
            "figures (PR #9283)")


#: Directories under the website scan roots that hold BUILD OUTPUT or vendored code,
#: never a declaration or a page of ours. Shared by both mono scans so they cannot
#: drift: `dist/` is the dashboard's and blog-admin's bundle, `node_modules/` the
#: installed tree, and `.wrangler/` (`.gitignore:7`) is what `wrangler dev` emits —
#: a bundled copy of `website/functions/**`, so a stale bundle carries the PRE-FIX
#: blog sheet verbatim. Scanning it made both mono guards RED over a correct tree on
#: any machine that had run the site's own dev server, and told the developer to add
#: `font-family` to generated code (#10254 review). The repo's other scans already
#: skip it for the same reason (`tests/test_cross_subdomain_cookie_sync.py:237`,
#: `tests/test_no_legacy_token_path.py:44`).
BUILD_OUTPUT_DIRS = ("node_modules", "dist", ".wrangler")


class TestMonospaceStackRendersAsAMonoFace:
    """Every mono font declaration must resolve to a real mono face on macOS Chrome (#6870).

    The stack this repo used everywhere was
    `'SF Mono', 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace`. None of
    those named faces is installed on a stock macOS Chrome, so the declaration fell
    through to the bare `monospace` keyword — and Chrome's macOS default for that
    keyword is **Courier**.

    ⚠️ **`ui-monospace` does NOT fix this on Chrome.** It is a Safari 13.1+ generic;
    Chrome never shipped it (crbug.com/40194142) and, measured with CDP
    `CSS.getPlatformFontsForNode` on this repo's Chromium: `ui-monospace` alone →
    Times (the declaration is dropped), `ui-monospace, Arial` → Arial, `'SF Mono'` →
    Times, `SFMono-Regular` → Times, `Menlo` → **Menlo**, old stack → **Courier**.
    So the thing that makes Chrome render a mono face is **`Menlo`**; `ui-monospace`
    is carried first only so Safari resolves the platform UI mono. Both are required
    below, and `Menlo` is required *because* it is the one face Chrome resolves —
    "tidy up the redundant fallbacks" would resurrect the bug, which is why the
    guard asks for it by name instead of trusting the generic.

    ⚠️ **Scope:** it checks DECLARATIONS, not usage. A page that replaces
    `font-family: var(--mono)` with a serif renders a serif and this stays green —
    that is a layout decision, not the Courier defect, and it stays out of scope.

    Pinned rather than remembered because the stack is hand-copied into ~28 places
    across the marketing pages, the dashboard, the blog renderer, the blog-admin app,
    the cookie-consent banner and the served MCP consent page (`tortoise/oauth.py`):
    re-copying the old one from a neighbouring file is a one-keystroke regression and
    it is SILENT — the page still renders, just in the wrong face. Six such copies
    were missed on the first pass, and a 28th (the consent page every MCP client
    approves a grant on) was found by review.

    The scan keys on **the declaration value**, not on the `SF Mono` name: a stack
    listing none of the old names (`'Cascadia Code', monospace`) renders in Courier
    too, and an earlier version that keyed on the name PASSED exactly that mutation.
    It strips CSS/JS comments (so prose saying "not monospace" is not a stack) and
    blanks `@font-face` blocks, whose `font-family` is the name being *defined* rather
    than a fallback list — without that, `font-family:'monospace'` in an inline style
    was skipped as if it were a webfont name, and two real regressions (a JSX
    `fontFamily:'monospace'` and a canvas `ctx.font='… monospace'`) went green.
    """

    SUFFIXES = (".html", ".css", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".py")
    # `--mono:`, `font-family:`, camelCase `fontFamily:` and JS `ctx.font=` (both
    # appear in the React surfaces), the `font:` shorthand (which smuggles a mono
    # family past a font-family-only scan), and tailwind's `mono: [...]`.
    DECL = re.compile(
        r"(?:--mono|font-family|fontFamily|font|mono)\s*[:=]\s*"
        # a JS template literal puts `${…}` in the middle of the value, so allow it
        r"((?:\$\{[^}]*\}|[^;{}\n]|\n[ \t])*)",
        re.IGNORECASE)
    #: `/* */`, `<!-- -->` and `//`-to-end-of-line (but not the `//` in a URL)
    COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->|(?<!:)//[^\n]*", re.DOTALL)
    #: `@font-face { font-family: <name> }` declares a NAME, not a fallback stack.
    FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.IGNORECASE)

    #: directories and served python modules that emit CSS for a browser.
    #: `render.py` carries no mono declaration today; it is listed because it IS
    #: an HTML emitter, so a declaration added there is scanned rather than found
    #: by review (the `oauth.py` history) — and
    #: `test_no_other_python_module_emits_a_mono_declaration` is the ratchet that
    #: keeps this list honest in both directions.
    ROOTS = ("website", "apps")
    MODULES = ("tortoise/oauth.py", "tortoise/render.py")
    #: build output and vendored trees that no declaration of ours lives in
    SKIP_DIRS = BUILD_OUTPUT_DIRS
    #: everything under here that emits a mono declaration must be in `MODULES`
    PY_ROOT = "tortoise"

    def _sources(self):
        """Every file this guard reads — FAIL CLOSED on a declared subject that is gone.

        A `continue`/`is_file()` skip here is a fail-open: renaming or moving
        `apps/` or `tortoise/oauth.py` left the scan GREEN over the 8 canvas/JSX
        declarations and the MCP consent page it exists to protect (`#6870`
        review P2). The floor in `test_no_mono_declaration_omits_a_face_...`
        does not cover it either — losing `apps/` leaves 28 declarations, still
        above the 20 floor. So a declared subject that is not there is a
        FAILURE that names the subject to update, never a silent narrowing.
        """
        repo = Path(__file__).resolve().parent.parent
        for root_name in self.ROOTS:
            root = repo / root_name
            assert root.is_dir(), (
                f"{root_name}/ is gone — update ROOTS in {type(self).__name__}. "
                "The scan silently narrowed instead, so every declaration under "
                "it is now unguarded (#6870 review P2).")
            # os.walk + a pruned `dirnames`, NOT `sorted(root.rglob("*"))`: rglob
            # materialises the WHOLE tree — node_modules and dist included —
            # before the filters run, turning a ~0.4 s test into a multi-second
            # walk on any machine that has built the dashboard or blog-admin.
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in self.SKIP_DIRS)
                for name in sorted(filenames):
                    path = Path(dirpath) / name
                    if path.suffix in self.SUFFIXES:
                        yield path
        for mod in self.MODULES:
            path = repo / mod
            assert path.is_file(), (
                f"{mod} is gone — update MODULES in {type(self).__name__}. Its "
                "emitted HTML would otherwise leave the scan GREEN over a served "
                "surface (#6870 review P2).")
            yield path

    @classmethod
    def _blank(cls, m):
        """Replace a comment/`@font-face` block with spaces, KEEPING newlines.

        Offsets and line numbers therefore stay the ORIGINAL file's: an earlier
        version collapsed comments to one space, so every failure message below
        a multi-line `/* … */` comment pointed at the wrong line.
        """
        return re.sub(r"[^\n]", " ", m.group(0))

    @classmethod
    def _strip(cls, text):
        """Blank comments and `@font-face` blocks, preserving length + newlines."""
        return cls.FONT_FACE.sub(cls._blank, cls.COMMENT.sub(cls._blank, text))

    @classmethod
    def _mono_values(cls, text):
        """Yield `(offset, value)` for every mono declaration value in `text`."""
        for m in cls.DECL.finditer(text):
            value = m.group(1)
            if "monospace" not in value.lower() or "var(--mono" in value:
                continue
            yield m.start(), value

    def _declarations(self):
        """Yield `(path, line, value)` for every mono declaration the guard reads."""
        for path in self._sources():
            text = self._strip(path.read_text(errors="replace"))
            for offset, value in self._mono_values(text):
                yield path, text.count("\n", 0, offset) + 1, value

    def _mono_declarations(self):
        repo = Path(__file__).resolve().parent.parent
        offenders, checked = [], 0
        for path, line, value in self._declarations():
            checked += 1
            low = value.lower()
            why = []
            if "menlo" not in low:
                why.append("no `Menlo` (the only face macOS Chromium resolves)")
            if "ui-monospace" not in low:
                why.append("no `ui-monospace` (Safari)")
            elif "menlo" in low and low.index("ui-monospace") > low.index("menlo"):
                why.append("`ui-monospace` must come before the named faces")
            if why:
                offenders.append(f"{path.relative_to(repo)}:{line}: {value.strip()[:100]} -> {'; '.join(why)}")
        return checked, offenders

    def test_no_mono_declaration_omits_a_face_chrome_actually_resolves(self):
        checked, offenders = self._mono_declarations()
        assert checked >= 20, (
            f"only {checked} mono font declaration(s) found under {'/'.join(self.ROOTS)} "
            f"and {', '.join(self.MODULES)}. If the stack was consolidated into one "
            "place, delete this test rather than letting it pass on nothing — a scan "
            "that finds nothing proves nothing.")
        assert not offenders, (
            "these mono declarations cannot render as a mono face on macOS Chromium "
            "(issue #6870), so they fall back to Courier:\n  " + "\n  ".join(offenders) +
            "\nUse: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, 'Cascadia Code', "
            "'Fira Code', 'JetBrains Mono', monospace")

    def test_every_file_it_scans_selects_this_guard(self):
        """The REVERSE direction: a file the guard READS must RUN it when changed.

        Registration in `config/ci-surfaces.yml` only makes this file
        CLASSIFIED; selection is what makes it RUN. Before the `onboarding`
        `SOURCE_PATTERNS` entries for `apps/graph-viz/src/` and
        `tortoise/oauth.py`, a diff touching ONLY one of those files selected no
        surface at all (`apps/` is in `NON_PYTHON_PREFIXES` and matched no
        pattern → `surfaces=[]`, tier-1 smoke) or selected `api`+`core` with the
        guard absent — so a re-copy of the old stack into a graph-viz canvas
        label or into the served MCP consent page merged GREEN.

        Derived from what the scan actually reads, so it cannot rot: a new
        declaration in an unwired path fails here instead of shipping unguarded
        (the `#6870` review P1 class, and `#1349/#3332/#3616` before it).
        """
        from tools.ci_selection import load_manifest, select

        repo = Path(__file__).resolve().parent.parent
        manifest = load_manifest()
        guard = Path(__file__).name
        missing = []
        for path in sorted({p for p, _, _ in self._declarations()}):
            rel = path.relative_to(repo).as_posix()
            selection = select([rel], "pull_request", manifest)
            if guard not in selection["test_files"]:
                missing.append(f"{rel} (selected surfaces: {selection['surfaces'] or 'none'})")
        assert not missing, (
            "these files carry a mono declaration this guard checks, but editing "
            "one selects no leg that runs this guard — so the Courier regression "
            "(#6870) merges green on exactly the diff that causes it. Add the "
            "path (or its directory) to SOURCE_PATTERNS['onboarding'] in "
            "tools/ci_selection.py, naming CONCRETE paths — select() matches with "
            "startswith and has no glob support:\n  " + "\n  ".join(missing))

    def test_no_other_python_module_emits_a_mono_declaration(self):
        """A NEW HTML emitter must be declared in `MODULES`, not discovered late.

        `tortoise/oauth.py` — the MCP consent page every client approves a grant
        on — was found by REVIEW, not by the scan, and its declaration lives in
        a module no `website/`-rooted pass could see (`#6870` review P2). This is
        the reverse ratchet for that population: a second emitter (or a move of
        the consent page's CSS into a new module) fails here, where adding it to
        `MODULES` is a one-line registration instead of a silent blind spot.
        """
        repo = Path(__file__).resolve().parent.parent
        undeclared = []
        for path in sorted((repo / self.PY_ROOT).rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            if rel in self.MODULES:
                continue
            raw = path.read_bytes()
            # Cheap byte pre-filter: 244 modules / ~16 MB, and no `.py` file can
            # carry a declaration without this substring (the value filter below
            # requires it), so the decode+regex pass only runs on candidates.
            if b"monospace" not in raw:
                continue
            text = self._strip(raw.decode("utf-8", "replace"))
            if next(self._mono_values(text), None) is not None:
                undeclared.append(rel)
        assert not undeclared, (
            "these modules emit a mono declaration but are NOT in "
            f"{type(self).__name__}.MODULES, so the scan never reads them and a "
            "re-copied stack there ships unguarded (#6870):\n  "
            + "\n  ".join(undeclared))

# ═══════════════════════════════════════════════════════════════════════════
# Product-root marker contract (PR #9652 review, P1)
# ═══════════════════════════════════════════════════════════════════════════
# The tortoise host serves `product.html` at `/` (the
# `website/functions/_middleware.ts` rewrite). TWO surfaces pin a string to
# prove that rewrite happened, and neither can catch the other:
#
#   1. `tests/e2e/test_legal_pages.py` — `PRODUCT_ROOT_MARKER`, read by the
#      local-emulation canary and by the served-root assertions inside
#      `_assert_serves_product` (raw body: an attribute does not survive
#      `_clean()`'s tag stripping). No count is stated on purpose — every add
#      or retirement would re-stale the number (#3950); the shape is pinned by
#      `test_every_marker_read_goes_through_the_raw_body_helper` below.
#   2. `.github/workflows/deploy-pages.yml` — the post-deploy DNS preflight
#      greps the served root and only then exports `TORTISE_HOST_CHECK=1`.
#
# That preflight's miss branch is a `::warning::`, so a stale marker does not
# red the job — it turns the tortoise-host half of the suite OFF, green. The
# pre-merge `legal-e2e` job (`.github/workflows/ci.yml`) DOES run
# `tests/e2e/test_legal_pages.py`, against a local `wrangler pages dev` with
# `TORTISE_HOST_CHECK=1` — but it reads the e2e constant only, never the
# workflow's grep, so it cannot see the preflight half drift; and until this PR
# fixed the constant's ordering, its local-emulation canary was dead and the
# tortoise-host tests skipped there too. Post-deploy, the preflight's marker is
# read by no pre-merge leg at all. In BOTH legs a drifted marker SKIPS the
# tortoise-host half green, so the change that breaks the marker is the one
# whose verification quietly stops running (PR #8597 named this; PR #9652 hit it
# again by replacing the hero `<h1>`, which WAS the marker) — which is why the
# pin belongs here, graded at PR time on the committed files.
#
# This file is selected whenever `website/product.html` changes (the
# `onboarding` surface in config/ci-surfaces.yml), AND also whenever
# `tests/e2e/test_legal_pages.py` changes (a `SOURCE_PATTERNS["onboarding"]`
# row in tools/ci_selection.py) — the file that holds the constant this gate
# parses had to be reachable, or the marker could be re-cut there with the gate
# asleep. Both paths are pinned by
# tests/test_ci_selection.py::test_product_root_marker_guard_runs_on_a_legal_pages_only_diff.
LEGAL_PAGES_E2E = REPO_ROOT / "tests" / "e2e" / "test_legal_pages.py"
DEPLOY_PAGES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml"
INDEX_HTML = REPO_ROOT / "website" / "index.html"


def _root_marker(name: str) -> str:
    """The literal of a module-level `NAME = "..."` in the e2e module.

    Parsed with `ast` rather than imported: that module needs playwright and a
    live host, and it calls `_tortoise_emulated_locally()` at import time. `ast`
    reads the literal exactly (quote style and escapes included) without
    executing anything.
    """
    src = LEGAL_PAGES_E2E.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(target, "id", None) == name
                   for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, str) and value, (
            f"{name} must be a non-empty string literal")
        return value
    raise AssertionError(
        "tests/e2e/test_legal_pages.py no longer defines a module-level "
        f"{name} assignment — this gate's subject moved")


def _product_root_marker() -> str:
    """The marker `tests/e2e/test_legal_pages.py` asserts on the served root."""
    return _root_marker("PRODUCT_ROOT_MARKER")


def _company_root_marker() -> str:
    """The marker `tests/e2e/test_legal_pages.py` asserts on the company root.

    Added by PR #9813: the company root was pinned by its hero PROSE
    ("the primary function of memory is not recall, but learning"), so the
    first hero rewrite deleted the pin — the exact defect PR #8597 and PR #9652
    each hit on the product root before the marker there became structural.
    """
    return _root_marker("COMPANY_ROOT_MARKER")


def _deploy_preflight_marker() -> str:
    """The literal the `verify-legal` DNS preflight greps the served root for."""
    src = DEPLOY_PAGES_WORKFLOW.read_text(encoding="utf-8")
    line = next(
        (ln for ln in src.splitlines()
         if "ROOT_MARKER" in ln and "grep" in ln and "-qF" in ln),
        None)
    assert line is not None, (
        ".github/workflows/deploy-pages.yml has no `grep -qF` on $ROOT_MARKER — "
        "the tortoise-host preflight this gate pairs with moved or was removed")
    match = re.search(r"grep\s+-qF\s+(['\"])(.+?)\1", line)
    assert match, f"cannot extract the preflight's root marker literal from: {line.strip()}"
    return match.group(2)


def _root_element(html: str, where: str = "website/product.html") -> str:
    """The page's `<html ...>` open tag."""
    match = re.search(r"<html\b[^>]*>", html, re.I)
    assert match, f"{where} has no <html> open tag"
    return match.group(0)


RAW_BODY_HELPER = "_assert_serves_product"
RAW_BODY_HELPER_COMPANY = "_assert_serves_company"
EMULATION_CANARY = "_tortoise_emulated_locally"

#: Every served-root marker the e2e module defines, and the helpers allowed to
#: read it. Both markers are structural ATTRIBUTES on the page's `<html>`, so
#: every read must be on the RAW body — `_clean()` strips tags, attributes
#: included, and an assertion written against cleaned text can never match.
SERVED_ROOT_MARKERS = ("PRODUCT_ROOT_MARKER", "COMPANY_ROOT_MARKER")
RAW_BODY_HELPERS = (RAW_BODY_HELPER, RAW_BODY_HELPER_COMPANY, EMULATION_CANARY)


def _marker_read_sites(src: str) -> list[tuple[str, int, str]]:
    """Every READ of a served-root marker, as `(enclosing function, line, name)`.

    `ast` rather than a regex: the constant's name also appears in the error
    message below itself and in comments, which a text scan cannot tell from a
    read.
    """

    class _Reads(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []
            self.reads: list[tuple[str, int, str]] = []

        def _visit_function(self, node: ast.AST) -> None:
            self.stack.append(getattr(node, "name", "<lambda>"))
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _visit_function  # type: ignore[assignment]
        visit_AsyncFunctionDef = _visit_function  # type: ignore[assignment]

        def visit_Name(self, node: ast.Name) -> None:
            if node.id in SERVED_ROOT_MARKERS and isinstance(node.ctx, ast.Load):
                self.reads.append(
                    (self.stack[-1] if self.stack else "<module>", node.lineno, node.id))

    visitor = _Reads()
    visitor.visit(ast.parse(src))
    return visitor.reads


def _asserted_expressions(src: str, function: str) -> list[str]:
    """The unparsed test expression of every `assert` inside `function`."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return [
                ast.unparse(child.test)
                for child in ast.walk(node)
                if isinstance(child, ast.Assert)
            ]
    return []


def _called_names(src: str, function: str) -> list[str]:
    """Every function CALLED inside `function`, by simple name.

    `ast`, not a text scan: the whole point is to tell what a test actually
    invokes from what its comments, docstring or messages mention.
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            names: list[str] = []
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    names.append(func.attr)
            return names
    return []


def _asserted_string_literals(src: str) -> list[tuple[int, str]]:
    """Every string LITERAL that could carry a served-root prose pin.

    Deliberately BROAD: narrowing this is how the first version of this check
    missed the shapes below. It reads every string constant in the module
    except two — DOCSTRINGS and assert MESSAGES — because both are
    documentation, and both legitimately quote the sentence they explain.
    Everything else counts:

    * an `assert` test (`assert "<sentence>" in r2.text()`);
    * a module-level constant, which is this module's own convention for a
      pin (`COMPANY_HERO_PIN = "<sentence>"` … `assert COMPANY_HERO_PIN in …`);
    * a comparison inside a helper (`if "<sentence>" not in body: raise …`);
    * comments — invisible to `ast`, so a note that QUOTES the hero (however it
      is wrapped) can never red the caller.
    """
    tree = ast.parse(src)
    documentation: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and node.msg is not None:
            documentation.update(id(child) for child in ast.walk(node.msg))
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                documentation.update(id(child) for child in ast.walk(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def _shares_a_run_of(a: str, b: str, length: int) -> str | None:
    """A shared contiguous `length`-character run between `a` and `b`, if any.

    Contiguity is what a source-text scan gets WRONG when the text wraps: two
    lines joined are never adjacent in the file. Comparing normalised LITERALS
    makes the check independent of how the source happens to be wrapped, in
    either direction (the literal can be longer or shorter than the subject).

    A run must span a space to count: a `length`-character match inside a
    single word ("ction") is not a phrase anyone pins copy by.
    """
    for short, long in ((a, b), (b, a)):
        if len(short) < length:
            continue
        for start in range(len(short) - length + 1):
            window = short[start:start + length]
            if " " in window and window in long:
                return window
    return None


class TestProductRootMarkerContract:
    """The served-root marker is ONE structural string, agreed by both pins.

    Scope, stated plainly: this asserts the marker exists in the COMMITTED page
    and matches what the workflow greps. It cannot prove what production serves
    (that is `tests/e2e/test_legal_pages.py`, post-deploy); its job is to make a
    copy edit that orphans either pin red at PR time instead of silently
    disarming the tortoise-host suite after deploy.
    """

    def test_the_marker_identifies_the_committed_product_page(self):
        """The pinned marker must exist in `website/product.html`."""
        marker = _product_root_marker()
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        assert marker in html, (
            f"PRODUCT_ROOT_MARKER {marker!r} does not occur in "
            "website/product.html. The tortoise host serves that file at `/`, "
            "so both consumers of the marker — tests/e2e/test_legal_pages.py "
            "and the deploy-pages.yml DNS preflight — can no longer prove the "
            "middleware rewrite. The preflight's miss branch is a ::warning::, "
            "so the tortoise-host tests would SKIP (green) instead of failing. "
            "If you edited the page's identity, move the marker in BOTH places.")

    def test_the_marker_does_not_also_match_the_company_root(self):
        """The marker's job is to discriminate product.html from index.html."""
        marker = _product_root_marker()
        index = INDEX_HTML.read_text(encoding="utf-8")
        assert marker not in index, (
            f"PRODUCT_ROOT_MARKER {marker!r} also occurs in website/index.html, "
            "which the company root (`premiselabs.co/`) serves. It no longer "
            "distinguishes the two roots, so `test_middleware_root_rewrites` "
            "and the deploy preflight would pass on the wrong page.")

    def test_the_deploy_preflight_greps_the_same_marker(self):
        """One marker, one source: the workflow must grep the test's string."""
        assert _deploy_preflight_marker() == _product_root_marker(), (
            "the deploy-pages.yml DNS preflight and PRODUCT_ROOT_MARKER in "
            "tests/e2e/test_legal_pages.py disagree. Moving one without the "
            "other either reds the post-deploy suite on a correct page or "
            "skips it green: the preflight only exports TORTISE_HOST_CHECK=1 "
            "when its grep matches, and the miss branch is a ::warning::.")

    def test_the_marker_is_an_attribute_on_the_root_element(self):
        """Structural, not prose: a copy edit must not be able to move it."""
        marker = _product_root_marker()
        root = _root_element(PRODUCT_HTML.read_text(encoding="utf-8"))
        assert marker in root, (
            f"PRODUCT_ROOT_MARKER ({marker!r}) is not part of the page's <html> "
            f"element ({root!r}). A marker that is a sentence of marketing copy "
            "(the `#beat-hero` h1 was one until PR #9652) is re-broken by every "
            "hero rewrite — PR #8597 and PR #9652 both had to move it. Keep the "
            "pin on page identity; keep the hero owner-editable.")

    def test_the_marker_is_defined_before_the_emulation_canary_runs(self):
        """Ordering pin: the canary reads the marker, so it must be bound first.

        `TORTISE_EMULATED = _tortoise_emulated_locally()` is a module-level call
        whose body reads `PRODUCT_ROOT_MARKER`. Bound after it, the read raises
        NameError, which the canary's own `except Exception: return False`
        swallows — so the canary reads False unconditionally and the whole
        tortoise-host half skips on the local (`127.0.0.1`) CI leg even when
        emulation works.
        """
        lines = LEGAL_PAGES_E2E.read_text(encoding="utf-8").splitlines()
        defined = next(
            (i for i, ln in enumerate(lines) if ln.startswith("PRODUCT_ROOT_MARKER = ")),
            None)
        canary = next(
            (i for i, ln in enumerate(lines)
             if ln.startswith("TORTISE_EMULATED = _tortoise_emulated_locally()")),
            None)
        assert defined is not None, "PRODUCT_ROOT_MARKER assignment not found"
        assert canary is not None, "the TORTISE_EMULATED canary call was moved or renamed"
        assert defined < canary, (
            f"PRODUCT_ROOT_MARKER is bound on line {defined + 1}, AFTER the "
            f"`_tortoise_emulated_locally()` call on line {canary + 1} that reads "
            "it. The NameError is swallowed by the canary's bare except, so "
            "TORTISE_EMULATED is False on every local run and the tortoise-host "
            "tests skip regardless of the marker. Define the marker first.")

    def test_every_marker_read_goes_through_the_raw_body_helper(self):
        """One shape for every served-root read: the RAW body, via one helper.

        The marker is a structural ATTRIBUTE, and `_clean()` strips tags — so
        an assertion written the old way (``PRODUCT_ROOT_MARKER in
        _clean(body)``, which is what PR #9652 had to rewrite at two sites) can
        never pass, and a copy of it added later would silently red the
        post-deploy suite on a correct page. This pins the SHAPE rather than a
        count, so adding or retiring a served-root assertion does not re-stale
        it (#3950); only reverting to a `_clean()`d or ad-hoc read does.
        """
        src = LEGAL_PAGES_E2E.read_text(encoding="utf-8")
        reads = _marker_read_sites(src)
        assert reads, (
            "no read of PRODUCT_ROOT_MARKER/COMPANY_ROOT_MARKER found in "
            "tests/e2e/test_legal_pages.py — this gate's subject moved")
        offenders = [
            (fn, line, name) for fn, line, name in reads
            if fn not in RAW_BODY_HELPERS
        ]
        assert not offenders, (
            "a served-root marker is read outside "
            f"{'/'.join(h + '()' for h in RAW_BODY_HELPERS)} at "
            + ", ".join(f"line {line} ({name} in {fn}())"
                        for fn, line, name in offenders)
            + ". These markers are structural attributes on <html>, and "
            "`_clean()` strips tags, so an assertion that cleans the body first "
            "can never match — it reds the served-root suite on a correct page "
            "and needs a rewrite the next time it lands (PR #9652 rewrote two). "
            "Read the raw document through `_assert_serves_product(body, "
            "where)` / `_assert_serves_company(body, where)`.")

        asserted = _asserted_expressions(src, RAW_BODY_HELPER)
        assert asserted, (
            f"`{RAW_BODY_HELPER}` in tests/e2e/test_legal_pages.py contains no "
            "assert — the served-root contract has no subject left")
        assert "PRODUCT_ROOT_MARKER in body" in asserted, (
            f"`{RAW_BODY_HELPER}` no longer asserts `PRODUCT_ROOT_MARKER in "
            f"body` (found: {asserted!r}). It must read the RAW document — "
            "`_clean(body)` strips the attribute off the <html> element, so the "
            "assertion could never match.")

        company_asserted = _asserted_expressions(src, RAW_BODY_HELPER_COMPANY)
        assert company_asserted, (
            f"`{RAW_BODY_HELPER_COMPANY}` in tests/e2e/test_legal_pages.py "
            "contains no assert — the company-root half of the middleware "
            "regression (#6) has no subject left")
        assert "COMPANY_ROOT_MARKER in body" in company_asserted, (
            f"`{RAW_BODY_HELPER_COMPANY}` no longer asserts "
            f"`COMPANY_ROOT_MARKER in body` (found: {company_asserted!r}). It "
            "must read the RAW document — `_clean(body)` strips the attribute "
            "off the <html> element, so the assertion could never match. This "
            "is precisely how the company root came to be pinned by hero prose "
            "(PR #9813).")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Company-root marker contract (PR #9813 review, P1)
# ═══════════════════════════════════════════════════════════════════════════
# The company host (`premiselabs.co/`) serves `website/index.html` and the
# middleware regression test proves it by asserting a marker on the served
# document. That marker used to be the hero SENTENCE
# ("the primary function of memory is not recall, but learning"), so the first
# company-hero rewrite deleted it and the served-root assertion went red on a
# correct page — the same defect PR #8597 and PR #9652 each hit on the product
# root before it moved to `data-page="product"` (see
# TestProductRootMarkerContract above). This class is the PR-time half of the
# remedy: the marker must be page IDENTITY, it must live on the `<html>`
# element of the committed page, and it must not double as the product marker.

HEADING_TAG_RE = re.compile(r"<h([1-6])\b[^>]*>", re.I)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
STYLE_OR_SCRIPT_RE = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)
MEDIA_PRELUDE_RE = re.compile(r"@media\s*\(\s*max-height:\s*(\d+)\s*px\s*\)\s*\{")


#: `@media (max-width: Npx) { ... }` — the narrow-viewport blocks. Height has
#: `_max_height_media_blocks`; width had no reader, which is how #13696 item 3
#: (a flat `.outcomes { max-width }` override inside this block) survived a
#: full review cycle and had to be caught by eye (#13696).
MAX_WIDTH_MEDIA_RE = re.compile(r"@media\s*\(\s*max-width:\s*(\d+)\s*px\s*\)\s*\{")


def _css_declarations(css: str, selector: str) -> str | None:
    """The body of the LAST **base** `selector { ... }` rule, or None.

    Reads through `_css_rules_with_scope`, so it is COMMENT-BLANKED and it sees
    a rule that shares its prelude with other selectors. This was the last
    structural reader still scanning the RAW sheet: `/* max-width: min(660px,
    74ch); */` inside the shared-measure rule left
    `test_the_outcomes_grid_is_never_wider_than_the_prose_above_it` GREEN with
    the prose's measure gone — the same false PASS as #14020's review P2 on the
    two rows beside this one, so the ROOT is fixed here and `_css_rules` (whose
    only remaining consumer this was) is deleted rather than left as a trap.

    "Base" is deliberate: a selector legitimately appears twice (base rule +
    narrow-viewport override), and `[-1]` over ALL rules answers "what does the
    media block say" when the caller asked "what is the declaration" — the
    `@media (max-width: 760px) .outcomes` override sits after the shared rule
    and would win a `[-1]` over the whole sheet (#13696).
    """
    rules = _css_rules_with_scope(css, selector)
    if not rules:
        return None
    base = [body for body, inside_media in rules if not inside_media]
    return (base or [body for body, _inside in rules])[-1]


def _css_blanked(css: str) -> str:
    """`css` with every `/* … */` comment replaced by spaces, SAME length.

    Same length on purpose: an index found in the blanked text is a valid index
    into the real sheet, so a reader can brace-count structure here and still
    describe the sheet. Comments matter to every structural reader below — a
    comment that QUOTES a rule is matched as a rule by any `@media…{` or `--x:`
    scan. Measured: a note quoting `@media (max-width: 760px)` swallowed the
    hero's base rule into a phantom media span, and the page still quotes
    `--prose-measure: min(660px, 74ch)` in prose (:445-446), which a `--x:` scan
    reads as a declaration (#14020 review P1/P2.4).

    ⛔ The `@media` example is HISTORICAL: that note was deleted in
    `d6b4776350a`, so no comment on the page quotes a media query today — the
    rationale outlives the example, and `_max_width_media_blocks` /
    `_max_height_media_blocks` both cite it. Do not read this docstring as a
    claim about the sheet's current comments.
    ⛔ HTML COMMENTS ARE STRIPPED TOO. These readers are handed WHOLE PAGES, and
    a `/* … */`-only blanker leaves `<!-- … -->` intact: a CSS rule parked in a
    markup comment (genuinely dead — the browser never parses it) satisfied both
    `PE_ON_RULE_RE` and the `@media (max-height: …)` scan, keeping
    `test_the_beat_can_receive_the_scroll_gesture_while_visible` and
    `test_each_tall_beat_declares_the_css_escape_hatch` GREEN with no live rule
    on the page (measured, #14020 review). This is the "each stripper ALONE is
    half a stripper" lesson `_live_markup` records, applied here; composing the
    two in ONE blanker is what keeps every reader below honest instead of
    fixing each call site.
    """
    css = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), css, flags=re.S)
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), css, flags=re.S)


def _media_block_spans(css: str) -> list[tuple[int, int]]:
    """Every `@media (…) { … }` block as a `(start, end)` character span.

    `_max_width_media_blocks` answers "what does each narrow block say"; this
    answers "is THIS rule inside one", which is the question the hero rows need
    (#14020 review P2.4): a viewport override is a legitimate re-centring, so
    only the last BASE rule may be read as the desktop alignment.
    """
    blanked = _css_blanked(css)
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"@media[^{]*\{", blanked):
        inner = _brace_block(blanked, m.end() - 1)
        spans.append((m.start(), m.end() + len(inner) + 1))
    return spans


def _css_rules_with_scope(css: str, selector: str) -> list[tuple[str, bool]]:
    """Every rule whose selector LIST contains `selector`, `(body, inside_media)`.

    Reads the comma-separated PRELUDE, not the rule's exact text: a rule that
    shares its prelude with other selectors (`#beat-hero .hero-sub,
    #beat-hero .hero-links { text-align: center }`) is a different string from
    the exact selector, so an exact-text reader cannot see it — measured at
    #14020 review P2.5: appending that rule at top level left the hero rows
    green while the desktop subtitle was centred (the 71px-off-axis defect of
    #13696 item 1). `_css_rule_preludes` reads the same way for `.outcomes`.

    Bodies come from the COMMENT-BLANKED sheet (see `_css_blanked`), so a
    `text-align`/`max-width` named in a comment cannot satisfy an assertion.

    A reader that ignores `@media` scope cannot say whether a body sits inside
    a media block, and that is exactly the difference between the desktop rule
    and a narrow-viewport override: a `[-1]` over the whole sheet is the LAST
    rule — on `#beat-hero .doc-copy` that is `@media (max-width: 760px)`'s centred
    override, so a later BASE `text-align: center` appended after it would leave
    a `[-1]`-based assertion green while the desktop column is centred (#14020
    review P2.4, measured).
    """
    #: `selector` may itself be a comma-separated LIST, and then a rule matches
    #: only when its prelude covers ALL of them — the shared-measure caller asks
    #: for `#beat-intro p, #beat-compute > p, .outcomes` as one request, and a
    #: rule listing only two of the three is a DIFFERENT (broken) invariant, not
    #: a match. For a single selector this is the same test as membership.
    wanted = {part.strip() for part in selector.split(",")}
    blanked = _css_blanked(css)
    media = _media_block_spans(css)
    out: list[tuple[str, bool]] = []
    for m in re.finditer(r"([^{}]+)\{", blanked):
        prelude = m.group(1).strip()
        if not prelude or prelude.startswith("@"):
            continue
        if not wanted.issubset({p.strip() for p in prelude.split(",")}):
            continue
        out.append((_brace_block(blanked, m.end() - 1),
                    any(start <= m.start() < end for start, end in media)))
    return out


def _effective_decl(body: str, prop: str) -> str | None:
    """The LAST `prop: …` declaration in a rule body — the one that WINS.

    ⛔ "`prop: value` occurs somewhere in this block" is not "the rule says
    `value`": inside a block the last declaration wins, so appending
    `text-align: center` below a `text-align: left` leaves a substring search
    green with the rule centred — measured at #14020 review cycle 3, P1: the
    hero subtitle row passed with `text-align: center` appended to the very
    block it reads, which is the ordinary way an editor changes a rule.
    Read the winning value, not the presence of a string.
    """
    # Split on `;` rather than regexing `(?:\A|;)\s*prop…`: a regex that
    # CONSUMES the separator cannot match two declarations of the same property
    # back to back, which is exactly the append shape this guards — the first
    # draft of this helper reported the FIRST `text-align` and both mutants
    # stayed green (#14020 review cycle 3, measured).
    last = None
    for decl in _css_blanked(body).split(";"):
        name, sep, value = decl.partition(":")
        if sep and name.strip().lower() == prop.lower():
            last = value.strip()
    return last


def _css_last_declaring_body(css: str, selector: str, prop: str) -> str | None:
    """The LAST rule APPLYING AT DESKTOP that names `selector` and DECLARES `prop`.

    This is the body a caller should hand to `_effective_decl`, and the
    difference from `_css_rules_with_scope(...)[-1]` is not cosmetic: `[-1]` is
    the last rule whose PRELUDE is that selector text, whether or not it says
    anything about `prop`, so ANY later `.pricing-card { … }` rule that does not
    repeat `display` hides the rule that does.

    Measured (#14406 review round 13) on the tree at `f091cc8dedb2`, with the
    class's own rows and the page restored byte-identically after every mutant:

        .pricing-card { align-self: stretch }        RED  "`.pricing-card` is not a flex container"
        .pricing-card { border-color: #fff }         RED  "`.pricing-card` is not a flex container"
        .pricing-card .btn { border-radius: 2rem }   RED  "the card's CTA does not take the leftover height"

    Every one of those is a page that renders all four CTAs on the shared
    baseline — the card IS a flex container and its CTA DOES take the slack —
    and the first is the very edit review round 12 declared GREEN, which it
    measured written INSIDE the existing block. The same edit written as its own
    rule still red, with a message naming a property the page satisfies: a
    maintainer's correct edit cannot clear it, and the guard's own docstring
    says why that matters ("a guard that reds the page it ships is a guard a
    maintainer deletes").

    Resolution stays LAST-WINS, so the override the rows exist for is still
    caught — `_effective_decl` reports the winning declaration inside the body,
    and this helper picks the last body that declares the property at all. An
    appended `.pricing-card { display: block }` is therefore still the owner and
    still reds (measured: RED before and after this reader).

    ⛔ `at_desktop=True` (the default) reads through `_css_desktop_rules`, so a
    rule inside a `min-width` media block is the owner when it is the last one
    that declares `prop` (#14406 review round 15). Skipping media made the
    compensation's TWO OPERANDS come from DIFFERENT TREES: the graded winner is
    read by `_css_block_end_cascade`, which DOES reach `min-width` media, while
    `@media (min-width: 1024px) { .pricing-card { padding-bottom: 2rem } }` was
    invisible to the `expected` side — so the popular CTA was measured 4px low at
    1440x900 with all 205 tests in the module GREEN. A `max-` block that cannot
    apply at desktop size is still skipped, so the mobile hatches stay invisible.
    """
    # ⛔ "NON-MEDIA" IS THE WRONG AXIS; the CONDITION is (#14406 review round 16).
    # This reader used to keep only rules at TOP LEVEL, so a `@media` block that
    # applies at the very viewport every measurement here is taken at (1440x900)
    # was invisible — the mirror of round 6's finding, which fixed the same
    # prefix confusion in `_css_desktop_rules` and never reached this reader.
    # Measured on the tree at `2d1958c1b9bb` — each mutant APPENDED inside the
    # page's `<style>` block (after every base rule, the only position where a
    # media rule actually wins the 1440x900 cascade), the CTA class's 9 rows
    # run, then the page restored byte-identically (`sha256 d74e7c9dd20d`):
    #
    #   @media (min-width: 1200px) { .pricing-card.popular { border: 3px solid #f00 } }
    #   @media (min-width: 1200px) { .pricing-card { padding: 2rem } }
    #        -> 9 passed / 9 passed — GREEN over a page whose inset is derived
    #           from a base rule the browser no longer applies (fail-OPEN).
    #
    # A third shape, `@media (min-width: 1200px) { .pricing-card.popular {
    # padding-bottom: 1.75rem } }`, was ALREADY RED before this fix — the
    # popular row resolves the whole `padding` family through
    # `_css_block_end_cascade`, which has been media-aware since round 5. The
    # two reads that were not are the ones below.
    # already answers exactly this question — it keeps a media rule whose
    # THRESHOLD can apply at desktop (and fails closed on a `not`, an
    # uncomparable unit, or a query list) — so the reader resolves through it
    # instead of re-deciding what `inside_media` means. The page's own hatches
    # (`max-width: 760px` / `360px`, `max-height: 820px`) stay excluded, so a
    # correct page is not red for stacking its cards on a phone.
    wanted = {part.strip() for part in selector.split(",")}
    declaring = [
        body for prelude, body, _condition in _css_desktop_rules(css)
        if wanted.issubset({p.strip() for p in prelude.split(",")})
        and _effective_decl(body, prop) is not None
    ]
    return declaring[-1] if declaring else None


def _css_all_rules(css: str) -> list[tuple[str, str, bool]]:
    """Every style rule as `(prelude, body, inside_media)`, comment-blanked.

    The reader of last resort for a question no single selector can answer —
    "does ANY rule reach this element?" (#14020 review cycle 3, P1: the lead
    row read only rules whose prelude NAMES `#beat-intro .lead`, so
    `#beat-intro p:first-of-type { font-size: 2rem }` moved the lead's `ch`
    with every row green). `@media` preludes are returned as their own entries
    only when they carry declarations; callers that care pass `inside_media`.
    """
    blanked = _css_blanked(css)
    media = _media_block_spans(css)
    out: list[tuple[str, str, bool]] = []
    for m in re.finditer(r"([^{}]+)\{", blanked):
        prelude = m.group(1).strip()
        if not prelude or prelude.startswith("@"):
            continue
        out.append((prelude, _brace_block(blanked, m.end() - 1),
                    any(start <= m.start() < end for start, end in media)))
    return out


def _media_blocks(css: str) -> list[tuple[int, int, str]]:
    """Every `@media (…) { … }` block as `(start, end, condition)`.

    `_media_block_spans` answers "is this rule inside a media block"; this also
    returns the CONDITION, because the condition decides whether the block can
    affect a desktop viewport at all — and that is the difference between a
    legitimate narrow-viewport hatch and an override that ships (#14406 review
    round 4, finding G3).
    """
    blanked = _css_blanked(css)
    out: list[tuple[int, int, str]] = []
    for m in re.finditer(r"@media([^{]*)\{", blanked):
        inner = _brace_block(blanked, m.end() - 1)
        out.append((m.start(), m.end() + len(inner) + 1, m.group(1).strip()))
    return out


#: The desktop floor `_css_desktop_rules` compares a `max-` threshold against.
#: `max-width` / `max-height` are INCLUSIVE (`width <= N`), so a threshold at or
#: above these DOES apply at a desktop viewport — MDN, "Using media queries":
#: `@media (max-width: 1440px)` matches a 1440-wide viewport and is equivalent to
#: `@media (width <= 1440px)`. The pricing measurements are taken at 1440x900 and
#: the page stacks the cards below ~907px, so 1100 is above the page's own narrow
#: hatch (nothing it treats as "narrow" is pulled in) and low enough that every
#: block able to affect a 1440x900 render is seen. Both are deliberately
#: conservative: a threshold between the floor and 1440 is INCLUDED even though
#: it cannot affect the 1440 measurement.
DESKTOP_MIN_WIDTH = 1100
DESKTOP_MIN_HEIGHT = 900

#: `max-<axis>: <value>` inside a media CONDITION — the value may carry any unit.
MAX_SIDE_MEDIA_RE = re.compile(r"max-(width|height)\s*:\s*([^)\s]+)")

#: A top-level logical OR — the `or` KEYWORD of a media condition (Media Queries
#: 4 §3: `<media-condition> or <media-condition>`). The look-ahead refuses
#: `orange`/`order`, and the caller's look-behind refuses `color`.
OR_KEYWORD_RE = re.compile(r"or(?![\w-])", re.IGNORECASE)

#: A NEGATED media query or condition (`not all and (max-width: 760px)`).
NOT_KEYWORD_RE = re.compile(r"(?<![\w-])not(?![\w-])", re.IGNORECASE)


def _media_query_components(condition: str) -> list[str]:
    """The alternative queries of a media query LIST **or** an `or` condition.

    Both spellings are the same logical OR, and both are split so that
    `_media_applies_at_desktop` decides by the BEST alternative instead of by a
    single threshold drawn from the whole string:

    * a query LIST — *"a media query list is true if any component query is true
      and false only when all component queries are false"* (W3C Media Queries
      Level 4 §3; MDN, "Using media queries": *"comma-separated media queries
      act as a logical OR"*), fixed in #14406 review round 7;
    * an `or` CONDITION — `<media-condition> or <media-condition>` is the same
      OR one level in. `and` binds tighter than `or`, so the top-level split is
      on `or`, exactly as it is on a comma. Measured while closing the round-7
      class: `@media (max-width: 760px) or (min-width: 900px)` applies at
      1440x900 through its SECOND operand, and the round-7 splitter (commas
      only) returned it whole, read `max-width: 760px` out of it, and called it
      a narrow hatch — `_css_reaching_decls` did NOT see
      `.pricing-card .btn { margin-top: 0 }` inside it, so every row of
      `TestProductTeamsPricingCardCtaBaseline` stayed GREEN over the 3-baseline
      split this class exists to catch.

    A comma inside `var(--narrow, 760px)` / `calc(…)` is NOT a separator, so the
    split tracks parenthesis depth; an `or` inside the parens is left alone for
    the same reason.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    index = 0
    while index < len(condition):
        char = condition[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and char == ",":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        if (depth == 0 and OR_KEYWORD_RE.match(condition, index)
                and not (index and re.match(r"[\w-]", condition[index - 1]))):
            parts.append("".join(current))
            current = []
            index += 2
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _media_applies_at_desktop(condition: str) -> bool:
    """Could a media CONDITION apply at a desktop viewport — by its THRESHOLD?

    The predicate is the comparison a browser makes, not the spelling of the
    condition. Measured (#14406 review round 6, finding 4): the first draft
    skipped on the `max-` SUBSTRING, so
    `@media (max-width: 1440px) { .pricing-card .btn { margin-top: 0 } }` —
    which matches the very viewport every measurement in this PR is taken at —
    was invisible to every row of `TestProductTeamsPricingCardCtaBaseline`: all
    four stayed GREEN while the rendered CTAs split to three baselines 70.2px
    apart. `max-width: 1200px` is the same shape one breakpoint down, and such
    a block is a plausible "align the cards on laptops" edit.

    `max-width: 760px` / `max-height: 820px` (the page's own viewport hatches)
    are still EXCLUDED — they cannot apply at 1440x900, and the cards stack
    there by design.

    Fail-closed on anything this reader cannot compare: a `max-` value in a unit
    that is not plain `px` (`50em`, `calc(...)`, `var(...)`) is treated as
    REACHING a desktop viewport, because guessing "narrow" is the direction that
    hides an override.

    ⛔ TWO NARROW SPELLINGS THIS READER DOES NOT MODEL, measured at this head
    rather than assumed, and both deliberately left on the fail-closed side
    (they are FALSE REDS, the direction a maintainer can SEE and a whole PR's
    rounds have accepted; the other direction hides an override and ships):

    * the Media Queries 4 RANGE syntax — `@media (width <= 760px) { … }` does
      not carry `max-width`, so nothing is `constrained` and the block is read
      as reaching desktop. Measured: `.pricing-card .btn { margin-top: 0 }`
      inside it reds 4 rows of `TestProductTeamsPricingCardCtaBaseline`, on a
      page a browser renders correctly. The `max-width` spelling of the SAME
      hatch stays excluded, so this is a spelling gap and not a threshold one.
    * a bare MEDIA TYPE — `@media print { … }` carries no threshold by design
      and is likewise read as reaching, although print cannot affect a
      1440x900 SCREEN measurement. `print` appears in the query-LIST rows
      above for the same reason (an unmodelled component fails closed), so
      excluding it here would reverse that recorded choice; this note exists so
      the next reader finds the measurement instead of re-deriving it.

    Both shapes are recorded as evidence, not fixed: modelling them is a
    behaviour change that belongs in its own round with its own mutants.

    The CONDITION may be a query LIST or an `or` condition — both are a logical
    OR — so the answer is `any()` over the alternatives, never a verdict from
    one threshold picked out of the whole string. Measured (#14406 review round
    7): `@media (max-width: 760px), (min-width: 900px) { .pricing-card .btn {
    margin-top: 0 } }` applies at 1440x900 through its SECOND query, and the
    single-threshold form read it as a narrow hatch — all four rows of
    `TestProductTeamsPricingCardCtaBaseline` stayed GREEN while the rendered
    CTAs split back to three baselines 70.2px apart.
    """
    return any(_media_query_applies_at_desktop(component)
               for component in _media_query_components(condition))


def _media_query_applies_at_desktop(component: str) -> bool:
    """Could ONE alternative query (one comma- and `or`-free condition) apply at desktop."""
    # `not` inverts the whole query, so the `max-` threshold no longer says what
    # this reader thinks it says: `not all and (max-width: 760px)` is TRUE at
    # 1440x900 (the inner query is false there, and `not` flips it), while the
    # un-negated reading calls it a narrow hatch and hides the rule. Measured
    # while closing the round-7 class: the mutant
    # `@media not all and (max-width: 760px) { .pricing-card .btn {
    # margin-top: 0 } }` was invisible to `_css_reaching_decls`, so all four
    # rows of `TestProductTeamsPricingCardCtaBaseline` stayed GREEN over a
    # rendered 3-baseline split. Negation is not modelled, so this fails CLOSED
    # (treats the query as reaching a desktop viewport) — the same direction
    # every other unmodellable input takes here.
    #
    # Round 8 reached the same conclusion independently by a different mutant
    # (`not print and (max-width: 760px)`, caught by
    # `test_a_negated_media_query_cannot_be_read_as_a_narrow_hatch`), so the
    # two lanes agree; the keyword regex is shared rather than duplicated.
    if NOT_KEYWORD_RE.search(component):
        return True
    constrained = False
    for axis, raw in MAX_SIDE_MEDIA_RE.findall(component):
        floor = DESKTOP_MIN_WIDTH if axis == "width" else DESKTOP_MIN_HEIGHT
        if not re.fullmatch(r"\d+(?:\.\d+)?px", raw):
            return True
        constrained = True
        if float(raw[:-2]) >= floor:
            return True
    return not constrained


def _css_desktop_rules(css: str) -> list[tuple[str, str, str | None]]:
    """Every style rule that can apply at a DESKTOP viewport, `(prelude, body, media)`.

    `media` is the `@media` condition the rule sits in, or `None` at top level.

    A rule inside a `max-width`/`max-height` block whose threshold is BELOW the
    desktop floor (`DESKTOP_MIN_WIDTH` / `DESKTOP_MIN_HEIGHT`) is EXCLUDED — a
    viewport hatch, and the pricing cards legitimately stack one per row below
    ~907px, so a narrow override is the page working as designed. Every OTHER
    media block is INCLUDED, because `min-width` / `min-height` / preference
    blocks apply AT desktop size — and so does a `max-width` at or above the
    floor, which is why the test is the THRESHOLD and not the `max-` prefix
    (#14406 review round 6, finding 4: `@media (max-width: 1440px) { … }`
    applies at the measured 1440x900 viewport and the prefix test skipped it).

    Measured (#14406 review round 4, finding G3):
    `@media (min-width: 1024px) { .pricing-card .btn { margin-top: 0 } }` left
    the entire CTA class GREEN while the rendered buttons split to three
    baselines, 70.2px apart — the defect signature of #13645 item 6. A reader
    that skips every media rule cannot see an override hiding in a `min-width`
    one, so `inside_media` is the wrong axis: the CONDITION is.
    """
    blanked = _css_blanked(css)
    media = _media_blocks(css)
    out: list[tuple[str, str, str | None]] = []
    for m in re.finditer(r"([^{}]+)\{", blanked):
        prelude = m.group(1).strip()
        if not prelude or prelude.startswith("@"):
            continue
        if "<" in prelude:
            # The FIRST rule of the sheet carries the whole document `<head>`
            # as its prelude — this scan starts at index 0 — and downstream
            # readers had to defend against that junk prefix themselves. The
            # defence one of them chose was `if "\n" in part: continue`, and
            # that skip also threw away legitimate MULTI-LINE selectors, so a
            # wrapped `#pricing-section\n  .pricing-card.popular { … }`
            # (1,2,0, a browser applies it) was invisible to the cascade with
            # the guard GREEN (#14406 review round 9, measured). Cut the head at
            # the `<style>` tag HERE, once, so the prelude is the selector list
            # it actually is and no reader has to skip newlines.
            prelude = prelude.rsplit(">", 1)[-1].strip()
        condition = next((c for start, end, c in media
                          if start <= m.start() < end), None)
        if condition is not None and not _media_applies_at_desktop(condition):
            continue
        out.append((prelude, _brace_block(blanked, m.end() - 1), condition))
    return out


def _css_rightmost_compound(part: str) -> str:
    """The RIGHTMOST compound of one comma part of a selector list.

    Combinators are ` `, `>`, `+` and `~` — but only at the TOP level of the
    selector. Splitting on a flat `[\\s>+~]+` is wrong for the very selectors
    this reader has to model: `~` is also the attribute operator in
    `[class~="pricing-card"]`, and `+`/`~` can sit inside a functional
    pseudo-class or a quoted value. The scan below tracks bracket, parenthesis
    and quote depth, so `[class~="pricing-card"]` stays ONE compound and
    `.grid ~ [class^='pricing']` still splits into two.

    Measured (#14406 review round 8): with the flat split, the compound of
    `[class~="pricing-card"]` came out as `="pricing-card"]`, so the class
    attribute form was invisible to every reach reader.
    """
    depth = 0
    quote = ""
    current: list[str] = []
    compounds: list[str] = []
    for char in part:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and (char.isspace() or char in ">+~"):
            if current:
                compounds.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        compounds.append("".join(current))
    return compounds[-1] if compounds else ""


def _css_rightmost_selects(rightmost: str, compound: str) -> bool:
    """Does this RIGHTMOST compound select elements matching `compound`?

    `compound` is written the way the callers write it: a class (`.pricing-card`,
    `.btn`). The literal class text is matched, and so are the CLASS-ATTRIBUTE
    selectors that select the same elements — for `class`, `[class~="x"]` IS
    the `.x` shorthand (`~=` is the whitespace-separated-word match, and a class
    is one word of the `class` attribute).

    Measured (#14406 review round 8): `[class~="pricing-card"] { display: block }`
    appended to the sheet left every row of
    `TestProductTeamsPricingCardCtaBaseline` GREEN, because the readers matched
    the literal `.pricing-card` text and this selector names its own class
    without the dot. That is the same false PASS the attribute form of the
    selector exists to produce, so the reader models it rather than trusting
    one spelling.

    The prefix/suffix/substring forms are matched on the operator's own
    semantics rather than by guessing, and an attribute VALUE this reader cannot
    read (a bare identifier, `var(…)`, an unquoted word) fails CLOSED — it counts
    as selecting. Over-reaching reds a correct page, which a maintainer can see;
    the other direction hides an override, which is the defect class every round
    of this review has found.

    ⛔ `=` IS ONE OF THOSE OPERATORS, and round 8 left it out. The operator was
    read inside the REGEX (`[~|^$*]?=`), so a selector that spelled the operator
    the plain way never matched the pattern at all and fell through to `False` —
    not to the fail-closed arm, which only covered a value it could not parse.
    Measured (#14406 review round 9): `[class="btn"] { margin-top: 0 }` selects
    the same CTAs as `.btn` (three of the four cards carry `class="btn"`
    exactly), restores the three-baseline / 70.2px split, and left EVERY row of
    `TestProductTeamsPricingCardCtaBaseline` GREEN; `[class="pricing-card"]
    { display: block }` was invisible to the flex row for the same reason.

    ⛔ AND ATTRIBUTE NAMES ARE CASE-INSENSITIVE in HTML, so `[CLASS="btn"]`
    selects exactly what `[class="btn"]` does — a lowercase-only match is the
    same blind spot wearing a different case (#14406 review round 9).
    """
    if compound in rightmost:
        return True
    if not compound.startswith("."):
        return False
    name = compound[1:]
    if not name:
        return False
    # The attribute NAME is matched case-insensitively and the operator is read
    # OUT of the value, so an operator (or a `[class]` presence test) this
    # reader does not model fails CLOSED instead of falling through to `False`.
    for m in re.finditer(r"\[\s*class\b([^\]]*)\]", rightmost, re.IGNORECASE):
        raw = m.group(1).strip()
        operator = re.match(r"([~|^$*]?=)\s*(.*)$", raw, re.S)
        if not operator:
            return True
        op, raw_value = operator.group(1), operator.group(2).strip()
        quoted = re.fullmatch(r"[\"'](.*)[\"']", raw_value, re.S)
        if not quoted:
            # A value this reader cannot read (a variable, an unquoted word)
            # may match the class, so it counts — fail closed.
            return True
        value = quoted.group(1)
        if op == "=" and name in value.split():
            # `=` compares the WHOLE attribute value, and a class list is
            # space-separated — so `[class="pricing-card popular"]` is exactly
            # the popular card's attribute and must count. Comparing the two
            # strings for equality instead would miss every multi-class card
            # (and `[class="pricing-card popular"] { … !important }` would take
            # the cascade with the guard GREEN), while `[class="btn-secondary"]`
            # still does NOT reach `.btn` because the class is one word of that
            # value, not the whole of it (#14406 review round 9).
            return True
        if op == "~=" and value == name:
            return True
        if op == "|=" and (value == name or name.startswith(value + "-")):
            return True
        if op == "^=" and name.startswith(value):
            return True
        if op == "$=" and name.endswith(value):
            return True
        if op == "*=" and value in name:
            return True
    return False


class _FirstClassElement(HTMLParser):
    """The FIRST start tag in a document whose class list contains one class.

    The SUBJECT's identity — `("a", {"href": "/auth", "class": "btn"})` — is
    the half of the selector-matching question no CSS reader can answer from a
    selector alone: `_css_compound_matches_element` needs to know which ELEMENT
    the subject is before it can say whether a compound that names no class
    selects it (#14406 review round 10).
    """

    def __init__(self, cls: str) -> None:
        super().__init__(convert_charrefs=False)
        self._cls = cls
        self.element: tuple[str, dict[str, str]] | None = None

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.element is not None:
            return
        # Attribute NAMES are case-insensitive in HTML, so the map is keyed
        # lowercase; `class` is looked up the same way it is spelled by the
        # parser, which already lowercases names.
        found = {name.lower(): (value or "") for name, value in attrs}
        if self._cls in found.get("class", "").split():
            self.element = (tag.lower(), found)

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)


def _subject_element(markup: str, compound: str,
                     ) -> tuple[str, dict[str, str]] | None:
    """The page element a class-carrying COMPOUND selects — or None.

    ⛔ THE COMPOUND MAY CARRY SEVERAL CLASSES, and passing the raw suffix made
    the element branch DEAD for exactly the operand that needs it (#14406 review
    round 21). `_element_carrying_class(css, compound[1:])` for
    `.pricing-card.popular` asked for a class literally named
    `pricing-card.popular`, found none, and returned None — so
    `_border_end_width_cascade` saw element-reached rules for the BASE card but
    not for the POPULAR one. Measured on the tracked page: appending
    `.pricing-grid .pricing-card { border-bottom-width: 1px }` (or the
    `#pricing-section` form) moved the browser's extra from 0 to -1px while the
    reader still computed 1.0, and the relation row stayed GREEN over a 1px
    split — the #13645 item 6 signature. The MOST SPECIFIC class (the last one)
    is what identifies the subject.
    """
    if not compound.startswith("."):
        return None
    classes = [name for name in compound.lstrip(".").split(".") if name]
    if not classes:
        return None
    return _element_carrying_class(markup, classes[-1])


#: One pseudo-class or pseudo-element token, WITH its parenthesised argument
#: when it has one (`:nth-child(2)`, `::before`, `:not(.popular)`).
_CSS_PSEUDO_TOKEN = re.compile(r"::?[\w-]+(?:\([^)]*\))?")

#: The NEGATION form, whose argument says what the rule does NOT select.
_CSS_NOT_PSEUDO = re.compile(r":not\(([^)]*)\)", re.IGNORECASE)

#: ONE class-only compound, e.g. `.popular` or `.popular.featured` — the only
#: `:not()` argument this reader can decide from class sets alone. A tag, id,
#: attribute or nested pseudo leaves it UNDECIDED, which counts as matching.
_CSS_NOT_CLASS_ONLY = re.compile(r"(?:\.[\w-]+)+$")

#: The pseudo-classes whose ARGUMENT is a selector list, so the classes inside it
#: are part of what the rule names (`:is(.pricing-card.popular)` selects the
#: popular card). Every other pseudo narrows or decorates without naming one.
_CSS_IS_PSEUDO = re.compile(r":(?:is|where)\(([^)]*)\)", re.IGNORECASE)


def _css_class_fragment(selector: str) -> str:
    """`selector` with its pseudo-classes reduced to what they say about CLASSES.

    `:not(…)` is the CALLER's to disprove first — its argument says what the rule
    does NOT select, so it must be resolved before this runs, never folded in.
    """
    unwrapped = _CSS_IS_PSEUDO.sub(r" \1 ", selector)
    return _CSS_PSEUDO_TOKEN.sub(" ", unwrapped)


#: One class token, so a TAG-qualified compound (`div.pricing-card`) yields the
#: class it names rather than a bare `div` in the class position.
_CSS_CLASS_TOKEN = re.compile(r"\.([\w-]+)")


def _css_classes(selector: str) -> set[str]:
    """The class names a selector fragment NAMES (`.a.b` → `{a, b}`)."""
    return set(_CSS_CLASS_TOKEN.findall(selector))


def _compound_selects_every_subject(rightmost: str, compound: str) -> bool:
    """Does this RIGHTMOST rule select EVERY element of `compound`?

    ⛔ `_css_rightmost_selects` compares the class TEXT, so `.pricing-card`
    does not match the compound `.pricing-card.popular` — and
    `_css_compound_matches_element` refuses class-named compounds by design (it
    owns the ELEMENT question). Between the two, a rule that reaches the popular
    card through its BASE class was invisible to the popular operand of a
    relation whose base operand saw it (#14406 review round 21).

    `rightmost`'s classes being a SUBSET of `compound`'s is the exact
    criterion: `.pricing-card` selects every `.pricing-card.popular` element,
    while `.pricing-card.popular` does not select a plain `.pricing-card`. Both
    are class-only compounds, so no tag, id or attribute question arises, and
    the caller keeps the specificity ordering — a base rule cannot out-rank the
    popular card's own rule, so a correct page is unaffected (verified: the
    clean page still reads base 1.0 / popular 2.0 / extra 1.0).

    ⛔ AND A PSEUDO-CLASS IS NOT A LICENCE TO GIVE UP (#14406 review round 23,
    measured). Bailing on any `:` threw away the whole class-only question, and
    the two other reach readers both miss the same selectors —
    `_css_rightmost_selects` because the COMPOUND text is not a substring of
    them, `_css_compound_matches_element` because it refuses class-named
    compounds. So `.popular:nth-child(2) { border-bottom-width: 3px }` selected
    the popular card in a browser (same (0,2,0) as its own border rule, later in
    source) and was invisible to BOTH operands: the reader derived extra_px 1,
    the browser applied 2, and every row of
    `TestProductTeamsPricingCardCtaBaseline` stayed GREEN over a page whose
    popular CTA sits 1px off the shared baseline — the #13645 item 6
    signature, the same fail-open the class-only bug produced.

    A pseudo-class this reader cannot evaluate is treated the way
    `_css_compound_matches_element` treats one: it MAY match, so the rule counts
    (fail CLOSED — over-reaching reds a correct page a maintainer can see,
    while the other direction hides an override). One form IS disprovable:
    `:not(.x)` where `x` is a class the subject carries cannot select it, and
    counting it would false-RED a correct page.

    ⛔ AND `:not()` TAKES A SELECTOR LIST WHOSE ARGUMENT IS A CONJUNCTION
    (#14406 review round 25, measured). `:not(.a.b)` excludes only an element
    carrying BOTH `a` and `b`; intersecting the argument's class SET with the
    subject's read it as "excludes if EITHER", so
    `.pricing-card:not(.popular.featured) { border-bottom-width: 1px }` was
    treated as unreachable for the popular card, the reader derived extra_px 1.0
    against a browser 0.0, and all eleven rows of
    `TestProductTeamsPricingCardCtaBaseline` stayed GREEN over a 1px split.
    Disproof now requires ONE ALTERNATIVE to be provably matched — a class-only
    compound whose classes the subject carries — because that is the only thing
    classes alone can decide; `:not(div)` and `:not(.a, .b)` stay undecided and
    therefore count (fail closed).
    """
    if not compound.startswith("."):
        return False
    if "[" in rightmost or "#" in rightmost:
        return False
    subject = _css_classes(compound)
    for exclusion in _CSS_NOT_PSEUDO.finditer(rightmost):
        for alternative in exclusion.group(1).split(","):
            candidate = alternative.strip()
            if _CSS_NOT_CLASS_ONLY.fullmatch(candidate) and (
                    _css_classes(candidate) <= subject):
                return False
    rule = _css_classes(_css_class_fragment(
        _CSS_NOT_PSEUDO.sub(" ", rightmost)))
    return bool(rule) and rule <= subject


def _compound_requires_a_foreign_class(rightmost: str, compound: str) -> bool:
    """Does `rightmost` REQUIRE a class that `compound`'s element lacks?

    The precise form of the `skip_popular` test, which was the string
    `".popular" in part` (#14406 review round 23, measured). That text test is
    wrong in BOTH directions on the property the compensation exists because
    of, and it put the two operands of one relation in different worlds again:

    * FALSE OPEN — `.pricing-card:not(.popular) { border-bottom-width: 0 }`
      sets the three SIBLING cards' border and says nothing about the popular
      card's, so a browser applies extra 2px; the part names `.popular`, so it
      was dropped from the base operand, the reader kept the shipped 1px and
      every row stayed GREEN over a 2px split.
    * FALSE OFF (`section.popular .pricing-card { padding-bottom: … }`) is the
      mirror: an ANCESTOR naming `.popular` skipped the whole part, although
      the rule reaches every card in the grid — so a real edit stayed invisible.

    Classes the rule NAMES are what select the element; `:not(…)` names what it
    excludes, so those are removed before the comparison. A part with no class
    at all (`div.pricing-card`, `#pricing-grid`) is not foreign and stays.
    """
    if not compound.startswith("."):
        return False
    if "[" in rightmost or "#" in rightmost:
        return False
    required = _css_classes(_css_class_fragment(
        _CSS_NOT_PSEUDO.sub(" ", rightmost)))
    return bool(required) and not required <= _css_classes(compound)


def _element_carrying_class(markup: str, cls: str,
                            ) -> tuple[str, dict[str, str]] | None:
    """The first element carrying `cls`, as `(tag, attributes)` — or None.

    Parsed with `HTMLParser` rather than a regex: the attribute set is the
    thing being read (a bare `<a href="/auth">` rule selects the pricing CTA by
    its OWN tag and destination), and a regex over `class="…"` would have to
    re-implement quoting, ordering and case to get the same answer.
    """
    parser = _FirstClassElement(cls)
    parser.feed(markup)
    parser.close()
    return parser.element


#: The elements that never take a closing tag, so the depth stack must not wait
#: for one. Only the card's own markup is parsed here (h3/div/p/a/span), but a
#: `<br>` or an `<img>` added to a card must not desync the walk into thinking
#: every later element is one level deeper.
_VOID_TAGS = frozenset(["area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track",
                       "wbr"])


class _CardChildren(HTMLParser):
    """The DIRECT children of every `.pricing-card` element, in document order.

    The `order` property only reorders SIBLINGS, so "the CTA is the last item
    `margin-top: auto` aligns" is a statement about the card's own children:
    `order: -1` on the CTA, or `order: 1` on `.features`, both move the button
    relative to its siblings. A class-only reader sees the first (the CTA's
    class is the subject of the row) and is blind to the second, which is why
    the subject set comes from the MARKUP's containment rather than from a
    hand-written list of class names (#14406 review round 12).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._inside_card: list[bool] = []
        self.children: list[tuple[str, dict[str, str]]] = []

    def _enter(self, tag: str, attrs: list[tuple[str, str | None]],
               self_closing: bool) -> None:
        found = {name.lower(): (value or "") for name, value in attrs}
        if self._inside_card and self._inside_card[-1]:
            self.children.append((tag.lower(), found))
        if not self_closing:
            self._inside_card.append(
                "pricing-card" in found.get("class", "").split())

    def handle_starttag(self, tag, attrs):
        self._enter(tag, attrs, tag.lower() in _VOID_TAGS)

    def handle_startendtag(self, tag, attrs):
        self._enter(tag, attrs, True)

    def handle_endtag(self, tag):
        if self._inside_card:
            self._inside_card.pop()


def _card_direct_children(markup: str) -> list[tuple[str, dict[str, str]]]:
    """Every direct child of every `.pricing-card` in `markup`, as elements.

    `(tag, attributes)` — the same identity `_css_compound_matches_element`
    takes — so a rule that reaches a child by its TAG (`div { order: 1 }`) is
    read exactly like one that reaches it by a class. The static grid and
    `renderPricing()`'s template build the same children (the row that pins the
    CTA-last invariant cross-checks both paths), so one parse of the served
    markup covers the selector space a rule can use.
    """
    parser = _CardChildren()
    parser.feed(markup)
    parser.close()
    return parser.children


def _css_compound_matches_element(compound: str,
                                  element: tuple[str, dict[str, str]]) -> bool:
    """Can this RIGHTMOST compound select `element` (the subject's identity)?

    This is the OTHER half of `_css_rightmost_selects`: that function answers
    "does this compound name the subject's CLASS" — in every spelling of the
    `class` attribute, `[class~="x"]` / `[class="x"]` included — and this one
    answers "does it select the subject's ELEMENT", for the compounds that name
    no class at all. Measured (#14406 review round 10, before it existed):
    `#pricing-grid a { margin-top: 0 }` and `.pricing-grid a[href] {
    margin-top: 0 }` both take the pricing CTA's slack — (1,0,1) and (0,2,1)
    against the pinned `.pricing-card .btn`'s (0,2,0), so they win at ANY source
    order — and left EVERY row of `TestProductTeamsPricingCardCtaBaseline`
    GREEN while the rendered CTAs split back to three baselines, 70.2px apart
    (#13645 item 6).

    ⛔ IT IS NOT A LICENCE TO MATCH EVERYTHING, and the two ways it could
    false-RED a CORRECT page are both closed here rather than by the caller:

    * `p { margin: … }` rule on the page (measured: it has them) must not count
      as reaching the CTA — the TAG has to match, so `p` does not select an
      `<a>`.
    * `*` and `a[href="/auth"]` DO select it, but they cannot win the cascade
      against a stronger pin — that is `_css_element_reaches`'s specificity
      floor, not this function's business.

    Unreadable input fails CLOSED, the same direction the class reader takes: an
    attribute value this reader cannot model counts as matching.
    """
    tag, attrs = element
    if re.search(r":root\b", compound):
        # `:root` is the document element, whatever the subject is.
        return tag.lower() == "html"
    for match in re.finditer(
            r"\[\s*([\w-]+)\s*(?:([~|^$*]?=)\s*(\"[^\"]*\"|'[^']*'|[^\]]*?))?"
            r"\s*([iIsS])?\s*\]", compound):
        name = match.group(1).lower()
        operator, raw, insensitive = match.groups()[1:]
        if name not in attrs:
            return False
        if operator is None:
            continue                     # `[href]`: a presence test
        quoted = re.fullmatch(r"[\"'](.*)[\"']", (raw or "").strip(), re.S)
        if not quoted:
            return True                  # fail closed: may match
        value, actual = quoted.group(1), attrs[name]
        if insensitive:
            value, actual = value.lower(), actual.lower()
        if (operator == "=" and actual == value) or (
                operator == "~=" and value in actual.split()) or (
                operator == "|=" and (actual == value
                                       or actual.startswith(value + "-"))) or (
                operator == "^=" and actual.startswith(value)) or (
                operator == "$=" and actual.endswith(value)) or (
                operator == "*=" and value in actual):
            continue
        return False
    core = re.sub(r"\[[^\]]*\]", " ", compound)
    for match in re.finditer(r"#([\w-]+)", core):
        if attrs.get("id") != match.group(1):
            return False
    # ⛔ The id has been VALIDATED by the loop above, so it must not survive into
    # the TAG comparison below — `#pricing-grid` left in `core` made `remainder`
    # read `#pricing-grid` and the final check compare it to `div`, so an id-only
    # rightmost compound could never match the element it names. Measured
    # (#14406 review round 12): `#pricing-grid { align-items: center }` left all
    # nine rows of `TestProductTeamsPricingCardCtaBaseline` GREEN over three
    # separate baselines while the class spelling reds, and the element carries
    # both spellings (`<div class="pricing-grid" id="pricing-grid"`), so the id
    # form is at least as likely an edit.
    core = re.sub(r"#[\w-]+", " ", core)
    if re.search(r"\.[\w-]+", core):
        # A class-named compound is `_css_rightmost_selects`'s question, and it
        # is already asked. Returning False here keeps ONE owner per question.
        return False
    # ⛔ THE ID IS CONSUMED ABOVE, and the duplicate strip that stood here is
    # gone: one owner per fix. Round 10's id test `continue`d and left the `#id`
    # text in `core`, so the remainder comparison asked whether the TAG
    # literally reads `#pricing-grid` and answered False — an id-ONLY rightmost
    # compound matched the subject's id and was discarded one line later. Round
    # 10's own mutants all put the id in an ANCESTOR position (`#pricing-grid
    # a`, `#pricing-grid div`), where the rightmost compound is the bare tag and
    # the bug cannot show (measured, #14406 review round 12).
    remainder = re.sub(r"::?[\w-]+(?:\([^)]*\))?", " ", core).strip()
    if remainder and remainder != "*":
        return remainder.lower() == tag.lower()
    # Nothing but pseudo-classes (`:hover`, `:first-child`, `:root` above): a
    # state/pseudo-class test this reader cannot evaluate, so it may match.
    return True


#: Which rule OWNS each subject's pinned declarations, as a SELECTOR — the
#: specificity floor a bare-element rule has to reach before it can override
#: them. `.pricing-card`'s `display`/`flex-direction` are pinned by the base
#: rule itself; the CTA's `margin-top: auto` is pinned by
#: `.pricing-card .btn`. A rule that cannot reach the pin's specificity cannot
#: win (the cascade's 2nd criterion), so including it would only false-RED a
#: correct page — which is why this floor exists on the AGREEMENT reader and
#: not on the cascade one, which sorts instead (#14406 review round 10).
#: ⛔ A SUBJECT WITH NO ENTRY HERE HAS NO ELEMENT REACH AT ALL, and that is a
#: silent loss of the round-10 fix rather than a tolerant default:
#: `#pricing-grid { align-items: center }` (1,0,0) is applied by a browser at
#: ANY source order and left every row of
#: `TestProductTeamsPricingCardCtaBaseline` GREEN, because `.pricing-grid` was
#: missing from this map — so `_css_reaching_decls` computed `floor = None` and
#: skipped the bare-element branch entirely (#14406 review round 12, measured).
#: Every subject a row pins must be listed; the `None`-floor path stays for the
#: subjects that are only ASKED about (`.features`, `.price`, …), where the
#: caller wants the fact, not a verdict.
PINNED_OWNER_SELECTOR = {".pricing-card": ".pricing-card",
                         ".btn": ".pricing-card .btn",
                         # ⛔ AND THE GRID ITSELF (#14406 review round 12). The
                         # height precondition is pinned on `.pricing-grid`'s own
                         # declarations, so a rule that cannot reach its (0,1,0)
                         # specificity cannot win — without an entry here the
                         # element branch of `_css_reaching_decls` is dead for
                         # this subject, which is how `#pricing-grid {
                         # align-items: center }` shipped as a fail-open.
                         ".pricing-grid": ".pricing-grid"}


def _css_element_reaches(part: str, rightmost: str,
                         element: tuple[str, dict[str, str]],
                         floor: tuple[int, int, int], value: str) -> bool:
    """Does a BARE-ELEMENT compound select the subject AND out-rank the pin?

    Both halves are load-bearing and were measured (#14406 review round 10):

    * WITHOUT the element test, the guard is blind to `#pricing-grid a` — a
      rule that moves the CTA with every row GREEN.
    * WITHOUT the floor, it false-REDs a correct page on the page's own
      `*, *::before, *::after { margin: 0; padding: 0 }` (a universal selector
      matches every subject but out-ranks nothing) and on `.selfhost a { … }`
      ((0,1,1), below the CTA's pin) — and a guard that reds the page it ships
      is a guard a maintainer deletes.

    `!important` beats the floor: an important declaration out-ranks ANY
    non-important one regardless of specificity, so it is admitted whatever the
    selector's weight.
    """
    if re.search(r"\.[\w-]+", rightmost):
        return False                     # a class-named compound: other branch
    if not _css_compound_matches_element(rightmost, element):
        return False
    if re.search(r"!\s*important\s*$", value):
        return True
    specificity = _css_specificity(part)
    # An unmodellable selector (`:is()` etc.) counts as MAXIMALLY specific, the
    # same fail-closed direction `_css_specificity`'s callers already take.
    return specificity is None or specificity >= floor


def _css_reaching_decls(css: str, prop: str, compound: str,
                        tokens: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Every rule that declares `prop` and can select `compound` at desktop size.

    `_css_rules_with_scope` matches a prelude only when it NAMES the exact
    selector text and never consults specificity, so a HIGHER-SPECIFICITY rule
    under a different prelude can override a pinned declaration while every
    exact-selector row stays green. Measured (#14406 review round 3): appending
    `.pricing-grid .pricing-card .btn { margin-top: 0 }` — (0,3,0) against the
    pinned `.pricing-card .btn`'s (0,2,0), so it wins at ANY source order —
    left all three rows of `TestProductTeamsPricingCardCtaBaseline` GREEN while
    the rendered CTAs split again.

    This is the AGREEMENT half of that question (whatever reaches the subject
    must say the same thing) and it is what the class's rows use for the
    declarations no other row covers: the card's `display`/`flex-direction`,
    the popular card's `padding-bottom`. Measured on the tree before it:
    `.pricing-grid .pricing-card { display: block }` and
    `.pricing-grid .pricing-card.popular { padding-bottom: 1.75rem }` both left
    every row in the class GREEN while the rendered baselines split.

    Reach, and its LIMITS (stated, because a guard that overclaims its reach is
    how the next lane trusts a shield that is not there — #14406 review round 4,
    finding D):

    * ANCESTOR selectors are reached — the test is on the RIGHTMOST compound of
      each comma part, so `.pricing-grid .btn` and `.pricing-card > .btn` both
      count as selecting a `.btn` (`compound` is a SUBSTRING of that compound,
      so `.btn-secondary` counting too is deliberate fail-closed slack).
    * `min-width` / `min-height` / preference media blocks are reached, and so
      is a `max-width` / `max-height` whose THRESHOLD is at or above the desktop
      floor; only a `max-` block that cannot apply at desktop size is skipped
      (see `_css_desktop_rules` and `_media_applies_at_desktop`).
    * It is NOT a cascade resolver: it does not prove which declaration wins.
      Callers therefore assert that every candidate AGREES with the pinned
      value — a disagreement is either a live override or dead weight the next
      editor cannot tell apart.
    * It reads NAMED properties only. A caller that pins `margin-top` must also
      pin the logical alias it delegates to (`margin-block-start`) and any
      shorthand that resets it (`margin`, `margin-block`) — see
      `SLACK_PROPS` in `TestProductTeamsPricingCardCtaBaseline`.
    * ⛔ IT ALSO REACHES A SELECTOR THAT NAMES NO CLASS BUT SELECTS THE
      SUBJECT'S ELEMENT, when that selector can out-rank the pinned rule. Round
      9 declared this a residual — `a` / `#pricing-grid a` / `a[href="/auth"]`
      select the page's own CTA tag and destination, and all three left every
      row of `TestProductTeamsPricingCardCtaBaseline` GREEN. Round 10 settled it
      with the subject's ELEMENT in the reader's contract
      (`_element_carrying_class` → `_css_compound_matches_element`) rather than
      by widening the class test: the tag has to match, so the page's own
      `p { margin: … }` rules cannot count, and `_css_element_reaches` adds the
      specificity floor that keeps `*` and `.selfhost a { … }` from reding a
      correct page. The floor is the cascade's own rule — a rule that cannot
      reach the pin's specificity cannot win — not a tolerance.

      What is still NOT reached: a compound whose subject it cannot decide from
      identity alone but that names a class for a DIFFERENT element (`a.foo`
      matching `.btn`'s anchor through a class the subject does not carry), and
      `@layer` / `@supports` / inheritance, which no reader here models.
    """
    out: list[tuple[str, str]] = []
    element = _subject_element(css, compound)
    owner = PINNED_OWNER_SELECTOR.get(compound)
    floor = _css_specificity(owner) if owner else None
    for prelude, body, _condition in _css_desktop_rules(css):
        value = _effective_decl(body, prop)
        if value is None:
            continue
        for part in (p.strip() for p in prelude.split(",")):
            rightmost = _css_rightmost_compound(part)
            if _css_rightmost_selects(rightmost, compound) and all(
                    t in rightmost for t in tokens):
                out.append((part, value))
                break
            if (element is not None and floor is not None
                    and _css_element_reaches(part, rightmost, element, floor,
                                             value)):
                out.append((part, value))
                break
    return out


#: Every property that can SET `padding-bottom` on one element. `padding` and
#: `padding-block` are SHORTHANDS and their omitted sides are set to `initial`,
#: so they reset it — the same family `SLACK_PROPS` pins for `margin`, and the
#: half `padding-bottom`/`padding-block-end` alone cannot see (#14406 review
#: round 5, finding 1: `padding: 1.75rem` and `padding-block: 1.75rem` both
#: restored the owner-reported defect with every row in the class GREEN).
PADDING_END_PROPS = ("padding-bottom", "padding-block-end", "padding",
                     "padding-block")

#: The same family for the CTA's block-END margin. None of these should declare
#: `auto` on the CTA: a second `auto` does not add to `margin-top: auto`, it
#: SPLITS the card's free space with it, centring the button in each card's own
#: leftover (#14406 review round 12).
BLOCK_END_MARGIN_PROPS = ("margin-bottom", "margin-block-end", "margin",
                          "margin-block")

#: Every property through which the CTA's OWN box can take the card's leftover
#: space — growth, or a block size DERIVED FROM THE CARD — instead of leaving it
#: to the pinned `margin-top: auto`. `SLACK_PROPS` covers the other mechanism
#: (a declaration that STARVES the auto margin); this is the one where the auto
#: margin is starved by the CTA's own box growing to meet it (#14406 review
#: round 17, measured — see `_cta_takes_the_slack_by_sizing`).
GROW_PROPS = ("flex", "flex-grow")
CONTAINER_RELATIVE_BLOCK_SIZE_PROPS = ("flex-basis", "height", "block-size",
                                       "min-height", "min-block-size")

#: A percentage anywhere in a block-size value — the only spelling that makes an
#: element's size depend on its CONTAINER. Captured with its number so that the
#: inert `0%` (a zero basis/floor is overridden by the flex item's own
#: `min-height: auto`, measured) is not read as container-relative.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


def _is_nonzero_percentage(value: str) -> bool:
    """Is any percentage in `value` non-zero (`calc(100% - 2rem)` included)?"""
    return any(float(match.group(1)) != 0.0
               for match in _PERCENT_RE.finditer(value))


def _sizing_takes_the_slack(prop: str, value: str) -> bool:
    """Would `prop: value` on the CTA consume the space `margin-top: auto` needs?

    The property decides what the value MEANS, so this is a per-family read and
    not a substring test:

    * `flex-grow` — any non-zero value grows the item into the card's leftover.
    * `flex` — the shorthand's FIRST number is `flex-grow` (CSS Flexible Box
      Layout §7.1.1), so `1` / `1 1 auto` / `1 1 0%` all grow, while `none`
      (`0 0 auto`) and `initial` (`0 1 auto`) do not. `auto` is `1 1 auto` — it
      grows — and is caught by the keyword not being a number. An unreadable
      shorthand fails CLOSED (treated as growing), the direction every reader in
      this file takes for a value it cannot decide.
    * `flex-basis` and the block-size family — a NON-ZERO PERCENTAGE is the only
      spelling whose value is derived from the card; `3rem`, `50vh`, `0%` and
      `max-height` are not (the last two are measured inert on this page).
    """
    bare = _without_important(value).strip().lower()
    if prop == "flex-grow":
        try:
            return float(bare) > 0
        except ValueError:
            return True
    if prop == "flex":
        if bare in ("none", "initial"):
            return False
        tokens = bare.split()
        grow = None
        for token in tokens:
            try:
                grow = float(token)
            except ValueError:
                continue
            break
        if grow is None or grow > 0:
            return True
        return any(_is_nonzero_percentage(token) for token in tokens[1:])
    # `flex-basis` / `height` / `block-size` / `min-height` / `min-block-size`.
    return _is_nonzero_percentage(bare)


def _cta_takes_the_slack_by_sizing(css: str) -> list[tuple[str, str, str]]:
    """Rules that make the CTA's OWN box grow into the slack, as `(prelude, prop, value)`.

    ⛔ THE PINNED `margin-top: auto` CAN BE STARVED WITHOUT TOUCHING A MARGIN.
    Every row in `TestProductTeamsPricingCardCtaBaseline` pinned the SLACK
    MECHANISM (`SLACK_PROPS`, `BLOCK_END_MARGIN_PROPS`) or the card's own box
    (`display` / `flex-direction` / `height` on `.pricing-card`) — and a flex
    item that GROWS, or whose block size is a percentage of its card, takes the
    free space itself, so the auto margin has nothing left to absorb and the
    button stops being pinned to the card's bottom edge. Each card's content is
    a different height (that IS #13645 item 6), so the four CTA tops re-split.

    Measured at `bc6525269cba` at 1440x900 (headless Chromium; the page restored
    byte-identically, `sha256 d74e7c9dd20d`, after every mutant) — CTA tops, and
    the verdict of all NINE rows of the class:

        .pricing-card .btn { flex: 1 }             6345.58 6345.58 6367.05 6415.78  GREEN
        .pricing-card .btn { flex: auto }          (same)                            GREEN
        .pricing-card .btn { flex-grow: 1 }        (same)                            GREEN
        .pricing-card .btn { flex-grow: 0.5 }      6380.69 6380.69 6391.42 6415.78  GREEN
        .pricing-card .btn { flex-basis: 100% }    (the 3-baseline split)            GREEN
        .pricing-card .btn { flex: 0 0 100% }      (same)                            GREEN
        .pricing-card .btn { flex: 1 1 0% }        (same)                            GREEN
        .pricing-card .btn { height: 100% }        (same)                            GREEN
        .pricing-card .btn { block-size: 100% }    (same)                            GREEN
        .pricing-card .btn { min-height: 100% }    (same)                            GREEN
        .pricing-card .btn { min-block-size: 100% } (same)                           GREEN

    — the shipped page reads 6415.78 four times, and `margin-top: 0` (already
    RED) reads the same three-baseline split, so the signature is the one this
    class exists for and not a shape of its own. The controls that must stay
    GREEN are measured too, and are NOT refused: `flex: none` / `flex: initial`
    / `flex: 0 1 auto` / `flex-grow: 0` / `flex-basis: auto` / `flex-basis: 0%`
    / `flex: 0 1 0%` / `height: 3rem` / `height: 50vh` / `inline-size: 100%` /
    `max-height: 100%` — every one of them leaves the four tops equal.

    Reach is `_css_reaching_decls`'s (rightmost-compound matching, ancestors and
    media blocks included, with the specificity floor that keeps the page's own
    `a { … }` rules out), so a rule that reaches the CTA through a DIFFERENT
    selector is seen as well as one that names `.pricing-card .btn`.
    """
    offenders: list[tuple[str, str, str]] = []
    for prop in (*GROW_PROPS, *CONTAINER_RELATIVE_BLOCK_SIZE_PROPS):
        for prelude, value in _css_reaching_decls(css, prop, ".btn"):
            if _sizing_takes_the_slack(prop, value):
                offenders.append((prelude, prop, value))
    return offenders


def _css_tokens(value: str) -> list[str]:
    """A declaration value split on whitespace, but keeping `calc(…)` whole.

    A plain `value.split()` shreds `calc(1.75rem - 1px)` into three tokens, so a
    shorthand author who wrote `padding: calc(1.75rem - 1px)` would be read as
    setting a `calc(1.75rem` block-end side and would RED a correct page. Depth
    tracking removes that false red for the cost of six lines.
    """
    tokens: list[str] = []
    depth, current = 0, []
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


#: A trailing `!important` on a declaration value. It is a PRIORITY marker, not
#: part of the value, so every reader that compares or splits a value must strip
#: it first. Three measured consequences of NOT stripping it (#14406 review
#: round 12): `margin-bottom: auto !important` is invisible to an equality test
#: against `auto` (a fail-open); `margin-top: auto !important` — the pinned
#: intent, only stronger — reads as a COMPETING declaration to the slack row;
#: and `display: flex !important` reads as not `flex` (both false REDs).
_CSS_IMPORTANT = re.compile(r"!\s*important\s*$")


def _without_important(value: str) -> str:
    """`value` with a trailing `!important` removed."""
    return _CSS_IMPORTANT.sub("", value).strip()


def _css_block_end(prop: str, value: str) -> str:
    """The value `prop` gives the BLOCK-END (bottom) side of the box.

    Both box-side families are ordered lists, so the token that lands on
    block-end is positional: for `padding-block`/`margin-block` one value sets
    both sides and two are `(start, end)`; for `padding`/`margin`, one value sets
    all four sides, two are `(block, inline)`, three are `(block-start, inline,
    block-end)` and four are `(block-start, inline-end, block-end, inline-start)`
    — so block-end is the THIRD token for 3 and 4 values and the FIRST otherwise.
    `SLACK_PROPS` uses the same rule for `margin`/`margin-block`'s block-START
    side (first token).

    The MARGIN family is read by the same rule (#14406 review round 11):
    `margin-bottom: auto` (or `margin-block-end: auto`, or `margin: auto`)
    SHARES the card's free space with the pinned `margin-top: auto`, so the CTA
    is centred in each card's leftover instead of pushed to its bottom — and
    each card has a different leftover, so the four baselines split with every
    other row in the class GREEN (measured).
    """
    if prop not in ("padding", "padding-block", "margin", "margin-block"):
        return value
    tokens = _css_tokens(_without_important(value))
    if not tokens:
        return value
    if prop in ("padding-block", "margin-block"):
        return tokens[-1] if len(tokens) > 1 else tokens[0]
    return tokens[2] if len(tokens) >= 3 else tokens[0]


#: A CSS length this file can convert to px: a number and `px` or `rem`.
_CSS_LENGTH = re.compile(r"([0-9]*\.?[0-9]+)(px|rem)$")

#: A BARE zero — `0`, `0.0`, `.0` — the one unitless number CSS accepts as a
#: length. One definition, because the border-width reader and `_css_length_px`
#: must agree on it (#14406 review round 20).
_BARE_ZERO_LENGTH = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


def _css_root_px(css: str) -> float | None:
    """The page's `rem` base in px — 16 unless `html`/`:root` overrides it.

    Returning `None` on an override is deliberately fail-closed: this reader
    assumes the CSS default root size, and a page that moved it would make every
    `rem` below silently wrong (#14406 review round 15).
    """
    for prelude, body, _inside_media in _css_all_rules(css):
        parts = [p.strip().lower() for p in prelude.split(",")]
        if ("html" in parts or ":root" in parts) and (
                _effective_decl(body, "font-size") is not None):
            return None
    return 16.0


def _css_length_px(value: str, root: float | None) -> float | None:
    """`value` as a px number, or `None` when it is not a length this reads.

    A BARE `0` is a valid length in CSS and is read as zero (#14406 review round
    15: the page's `* { margin: 0 }` reset was otherwise unparseable and red the
    two block-end rows on a correct page). Any other unitless number is NOT a
    length, so it stays `None` and the caller fails.

    `None` is the caller's signal to FAIL rather than skip — a geometry claim
    derived from a length the reader cannot convert is not a claim (#14406
    review round 15).
    """
    text = _without_important(value).strip().lower()
    if _BARE_ZERO_LENGTH.fullmatch(text) and float(text) == 0.0:
        return 0.0
    match = _CSS_LENGTH.match(text)
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    if unit == "px":
        return number
    return None if root is None else number * root


def _css_block_start(prop: str, value: str) -> str:
    """The value `prop` gives the BLOCK-START (top) side of the box.

    The mirror of `_css_block_end` for the START side, and the ONE reader both
    slack rows use — the positive pin (`margin-top: auto`) and the negative one
    (no `auto` on the block-END side) — so the two cannot drift apart. The
    START side is always the FIRST token: `margin`/`margin-block` with one value
    set both sides and with two are `(start, end)` (MDN, "margin"). Any other
    property already names its side, so its value is returned unchanged.
    """
    if prop not in ("margin", "margin-block"):
        return value
    tokens = _css_tokens(_without_important(value))
    return tokens[0] if tokens else value


def _css_align_keyword(prop: str, value: str) -> str:
    """The `align-items`/`align-self` keyword `prop` sets on the subject.

    `place-items` / `place-self` are the shorthands for `align-items` +
    `justify-items` / `align-self` + `justify-self`, and ONE value sets both —
    so `place-items: center` re-centres the grid's boxes exactly as
    `align-items: center` does, and a reader that only queried the longhand
    would be blind to it. The ALIGN half comes FIRST; a `safe`/`unsafe` position
    keyword prefixes the half it belongs to rather than replacing it (MDN,
    "place-items"), so it is skipped rather than returned.
    """
    tokens = [t.lower() for t in _css_tokens(value)]
    if not tokens:
        return value.strip().lower()
    head = tokens[0]
    if head in ("safe", "unsafe") and len(tokens) > 1:
        return tokens[1]
    return head


#: Every property that can SET `flex-direction` on one element. `flex-flow` is
#: the shorthand for `flex-direction` + `flex-wrap`, and its two components are
#: ORDER-INSENSITIVE (`<'flex-direction'> || <'flex-wrap'>` — W3C CSS Flexible
#: Box Layout 1 §5.1; MDN, "flex-flow": *"the components can appear in either
#: order"*), so the direction is whichever token is a direction keyword — and an
#: OMITTED direction takes its INITIAL value `row`, which is itself an override
#: of the pinned `column` (MDN, "flex-flow": *"defaults are row and nowrap"*).
#: Measured (#14406 review round 8): both `flex-flow: row` and the
#: direction-free `flex-flow: nowrap` appended to `.pricing-card` left every row
#: of `TestProductTeamsPricingCardCtaBaseline` GREEN while a browser applied the
#: row layout and the CTA left the shared baseline — the same shorthand hole
#: `SLACK_PROPS` closes for `margin` and `PADDING_END_PROPS` for `padding`.
FLEX_DIRECTION_PROPS = ("flex-direction", "flex-flow")
FLEX_DIRECTION_VALUES = frozenset(
    ("row", "row-reverse", "column", "column-reverse"))


def _css_flex_direction(prop: str, value: str) -> str:
    """The `flex-direction` value `prop` gives the box — `row` when omitted.

    For `flex-direction` itself this is the value unchanged. For `flex-flow` it
    is the direction keyword if the shorthand carries one, in either position,
    and otherwise the property's INITIAL value `row` — because the shorthand
    RESETS the component it omits, which is the half a token-presence check
    cannot see (`flex-flow: nowrap` reads as "no direction here" and is in fact
    "direction: row").

    ⛔ `!important` is stripped first, for EVERY prop: it is a priority marker,
    not part of the value, and leaving it on made `flex-direction: column
    !important` — the pin's own intent, only stronger — read as a DISAGREEMENT
    with the pinned `column`, and made `display: flex !important` read as not
    `flex` (#14406 review round 12, both measured RED on a correct page).
    """
    value = _without_important(value)
    if prop not in FLEX_DIRECTION_PROPS:
        return value
    if prop == "flex-direction":
        return value.strip().lower()
    for token in _css_tokens(value):
        if token.strip().lower() in FLEX_DIRECTION_VALUES:
            return token.strip().lower()
    return "row"


def _css_specificity(selector: str) -> tuple[int, int, int] | None:
    """`(ids, classes, types)` for ONE selector part, or `None` if unmodellable.

    Deliberately small. It answers one question — does a rule that disagrees
    with a pinned value out-rank or TIE the pinned rule? — and a functional
    pseudo-class (`:is()`, `:where()`, `:not()`, `:has()`) changes specificity in
    ways this counter does not model, so it returns `None` and the one caller
    treats that as MAXIMALLY specific (fail closed) rather than guessing.
    """
    if re.search(r":(?:is|where|not|has|matches)\(", selector):
        return None
    attrs = re.findall(r"\[[^\]]*\]", selector)
    bare = re.sub(r"\[[^\]]*\]", " ", selector)
    pseudo = re.findall(r"::?[\w-]+", bare)
    # The COLUMNS are the standard's, not "pseudo vs element" (MDN, Specificity:
    # the CLASS column is classes + attribute selectors + PSEUDO-CLASSES, the
    # TYPE column is type selectors + PSEUDO-ELEMENTS). The first draft counted
    # every pseudo and then subtracted BOTH the pseudo-elements and every type
    # selector from the class count, so each type selector cancelled a class:
    # `div.pricing-card` read (0,0,1) instead of (0,1,1), `p::before` read
    # (0,-1,2), and a type-qualified override of the popular card's compensation
    # ranked BELOW the pinned rule — `section .pricing-card.popular { … }`
    # (0,2,1) stayed GREEN while a browser applied it (#14406 review round 6,
    # finding 1, measured with the helper itself).
    pseudo_classes = len([p for p in pseudo if not p.startswith("::")])
    pseudo_elements = len(pseudo) - pseudo_classes
    rest = re.sub(r"::?[\w-]+|\.[\w-]+|#[\w-]+", " ", bare)
    types = len(re.findall(r"(?<![\w-])[a-zA-Z][\w-]*", rest))
    return (len(re.findall(r"#[\w-]+", bare)),
            len(re.findall(r"\.[\w-]+", bare)) + len(attrs) + pseudo_classes,
            types + pseudo_elements)


def _css_block_end_cascade(css: str, compound: str,
                           props: tuple[str, ...] = PADDING_END_PROPS,
                           ) -> list[tuple[str, str, str]]:
    """Every declaration that sets `compound`'s block-END inset, weakest → strongest.

    The LAST entry is the one a browser applies, so a caller can assert the
    WINNER's value. That is the difference from `_css_reaching_decls`'s
    agreement invariant, and for this property it is the difference between a
    guard and a false pass: the popular card's own `.pricing-card.popular`
    `padding-bottom: calc(1.75rem - 1px)` (0,2,0) and the base `.pricing-card`
    `padding: 1.75rem` (0,1,0) DISAGREE ON PURPOSE, so "every candidate must
    agree" reds a correct page; and `.pricing-grid .pricing-card { padding-bottom:
    1.75rem }` (0,2,0, appended after) TIES the winner and takes it, which a
    `('.popular',)`-filtered sweep cannot see at all (#14406 review round 5,
    finding 1 — two cascade-valid mutants, both 1px off, both GREEN).

    Order is the author-origin cascade: `!important` first, then specificity,
    then source order (rule index, then declaration index inside the block, so a
    later `padding-bottom` beats an earlier `padding` in the SAME block).

    Reach — and it is NOT full CSS:

    * The subject is the RIGHTMOST compound of each comma part, matched by
      SUBSTRING (`compound in rightmost`), the same slack `_css_reaching_decls`
      documents: ancestors are reached, `.pricing-cardx` matches too.
    * `*` and `*::…` count as reaching — a universal selector out-ranks nothing
      on specificity but `* { padding: 0 !important }` bypasses the cascade
      entirely, and leaving it out is a fail-open hole of exactly the class this
      row guards.
    * A property the caller does not list is invisible; a selector whose
      specificity cannot be modelled (`:is()` etc.) is ranked MAXIMALLY
      specific, so an unmodelled override fails closed (red).
    * It does not model `@layer`, `@supports`, inheritance or `revert` — none of
      which this page uses, and each of which would need its own reader.
    * It reaches a selector that names NO class but selects the subject's
      ELEMENT (`#pricing-grid a`, `#pricing-grid div`, `a[href="/auth"]`,
      `*`), through the same `_css_compound_matches_element` the agreement
      reader uses (#14406 review round 10). ⛔ NO specificity floor here, and
      that asymmetry with `_css_element_reaches` is deliberate: this reader
      ORDERS the cascade, so a weak bare-element rule is ranked BELOW the pin
      and cannot become the winner — it needs no exclusion to be harmless,
      while the agreement reader has no ordering and must exclude it or red a
      correct page.
    """
    ranked: list[tuple[tuple, str, str, str]] = []
    element = _subject_element(css, compound)
    for rule_index, (prelude, body, _condition) in enumerate(_css_desktop_rules(css)):
        for part in (p.strip() for p in prelude.split(",")):
            # `_css_desktop_rules` cuts the document `<head>` off the FIRST
            # rule's prelude, so a part can only carry markup if that cut had
            # nothing to cut at; a NEWLINE is NOT a reason to skip — selectors
            # wrap, and skipping them hid a wrapped
            # `#pricing-section` / `.pricing-card.popular { padding-bottom:
            # 1.75rem }` from the cascade (#14406 review round 9, measured: the
            # winner stayed `calc(1.75rem - 1px)` while a browser applied the
            # wrapped rule).
            if "<" in part:
                continue
            rightmost = _css_rightmost_compound(part)
            if not _css_rightmost_selects(rightmost, compound) and not (
                    rightmost.startswith("*")) and not (
                    element is not None
                    and _css_compound_matches_element(rightmost, element)):
                continue
            specificity = _css_specificity(part) or (9, 9, 9)
            for decl_index, decl in enumerate(_css_blanked(body).split(";")):
                name, sep, value = decl.partition(":")
                prop = name.strip().lower()
                if not sep or prop not in props:
                    continue
                important = bool(re.search(r"!\s*important\s*$", value))
                clean = re.sub(r"!\s*important\s*$", "", value).strip()
                ranked.append(((important, specificity, rule_index, decl_index),
                               part, prop, _css_block_end(prop, clean)))
    ranked.sort(key=lambda entry: entry[0])
    return [(part, prop, value) for _rank, part, prop, value in ranked]


def _css_rule_preludes(css: str, selector: str) -> list[tuple[str, str]]:
    """Every rule whose selector LIST contains `selector`, as `(prelude, body)`.

    An exact-selector-TEXT reader cannot see a rule that
    shares its prelude with other selectors — which is the shape the shared
    prose measure uses (`#beat-intro p, #beat-compute > p, .outcomes { … }`) and
    therefore the shape any `.outcomes` override can hide in: `.outcomes
    { max-width: 660px }` as its own rule is a DIFFERENT string from the shared
    prelude, and appending it leaves a prelude-exact reader green (#14020
    review P2.3). Split on commas and read the list. Comments are blanked first
    (`_css_blanked`), so a commented-out rule is not a declaration.
    """
    blanked = _css_blanked(css)
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"([^{}]+)\{", blanked):
        prelude = m.group(1).strip()
        if not prelude or prelude.startswith("@"):
            continue
        parts = {p.strip() for p in prelude.split(",")}
        if selector in parts:
            out.append((prelude, _brace_block(blanked, m.end() - 1)))
    return out


def _max_width_media_blocks(html: str) -> list[tuple[int, str]]:
    """Every `@media (max-width: Npx) { ... }` block as `(N, body)`.

    Reads the COMMENT-BLANKED sheet: a raw scan matches a media query QUOTED in
    a CSS comment and emits a phantom block, which can false-RED an assertion
    about what that block declares (measured at #14020 review cycle 4: a comment
    quoting `@media (max-width: 360px) { .outcomes { max-width: 660px } }`
    produced one, and the page's own notes are the kind of prose that quotes
    these queries — the note that did so here was deleted in `d6b4776350a`, so
    do not go looking for it in the page today).

    ⛔ The failure mode of a RAW reader here is FAIL-CLOSED (a phantom block
    makes a row red, not green), which is why this one was only ever noise. Do
    not read that as "raw readers are safe in this file": the sibling
    `_max_height_media_blocks` had the same raw scan and its failure mode is the
    opposite — a COMMENTED-OUT `@media (max-height: …)` block is invisible to a
    raw reader, so the escape-hatch row could be defeated by a comment. Both
    read the blanked sheet now (#14020 review cycle 4, measured).
    """
    blanked = _css_blanked(html)
    return [(int(m.group(1)), _brace_block(blanked, m.end() - 1))
            for m in MAX_WIDTH_MEDIA_RE.finditer(blanked)]


def _heading_levels(html: str) -> list[int]:
    """Heading levels in document order, ignoring comments and style/script."""
    stripped = HTML_COMMENT_RE.sub("", html)
    stripped = STYLE_OR_SCRIPT_RE.sub("", stripped)
    return [int(m.group(1)) for m in HEADING_TAG_RE.finditer(stripped)]


def _brace_block(text: str, open_index: int) -> str:
    """The `{...}` block that opens at `open_index`, brace-counted to its end."""
    depth, i = 0, open_index
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
        i += 1
    return text[open_index + 1:]


def _max_height_media_blocks(html: str) -> list[tuple[int, str]]:
    """Every `@media (max-height: Npx) { ... }` block as `(N, body)`.

    Brace-counted, NOT `.*?}` up to the first close: one declaration block per
    selector inside the media query is semantically identical to a shared one,
    and a regex that stops at the first `}` would read that refactor as "no
    rule names this selector" — a false red on a correct page.

    ⛔ Reads the COMMENT-BLANKED sheet, and here that is LOAD-BEARING rather than
    cosmetic: the escape-hatch row asks "does this tall beat declare the
    hatch?", and a RAW reader cannot see a commented-out `@media (max-height:
    820px)` block — so `/* … */` around the real block, or a comment QUOTING
    one, let the row stay green while every beat had lost its hatch. That is a
    false PASS, not noise (#14020 review cycle 4: measured GREEN with the real
    block commented out, before this change). Blanking makes the commented
    block invisible and the row red, which is the direction a guard must fail.
    """
    blanked = _css_blanked(html)
    blocks: list[tuple[int, str]] = []
    for match in MEDIA_PRELUDE_RE.finditer(blanked):
        blocks.append((int(match.group(1)),
                       _brace_block(blanked, match.end() - 1)))
    return blocks


#: An inline `style` ATTRIBUTE in any spelling the HTML parser accepts.
#: `style="…"` is only ONE of them: an unquoted attribute value, a
#: single-quoted value and a value with whitespace around the `=` are all valid
#: HTML and all out-rank every rule in the sheet, which is what makes one
#: spelling a fail-open rather than a formatting difference. `(?<![\w-])` keeps
#: a `data-style=…` / `x-style=…` attribute out, so the reader cannot false-RED
#: a correct page that carries a similarly-named attribute.
INLINE_STYLE_ATTR_RE = re.compile(
    r"(?<![\w-])style\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE)


def _inline_style_attributes(markup: str) -> list[str]:
    """Every inline `style` attribute in `markup`, in ANY quoting spelling.

    An inline declaration out-ranks every selector in the sheet, so
    `<div class="pricing-card" style="display:block">` splits the four CTAs
    back to three baselines while no CSS reader can see it (#14406 review
    round 8). The row that closes that hole read the attribute as the LITERAL
    `style="` … `"`, which is one spelling out of four: `style='display:block'`,
    `style = "display:block"` and the unquoted `style=display:block` are all
    valid HTML, all apply, and all left every row of
    `TestProductTeamsPricingCardCtaBaseline` GREEN (#14406 review round 9,
    measured against the pre-fix reader). The reader takes the attribute, not a
    formatting convention, so the row cannot be defeated by a quote character.

    It returns the matched spine (attribute name through value) rather than a
    boolean, so a failure message can quote the offending attribute verbatim.
    """
    return INLINE_STYLE_ATTR_RE.findall(markup)


#: The e2e test that proves the company host's served root.
COMPANY_ROOT_CALL_SITE = "test_middleware_root_rewrites"

#: How many contiguous characters a string literal must share with the company
#: hero sentence before it counts as a PROSE PIN, plus a word break (a run
#: inside one word is not copy). This is a NOISE TRADE-OFF, not the shortest
#: possible pin: `"not recall"` (10 characters) would work as a prose pin, and
#: so would `"recall, but"` (11) — they are tolerated because shorter runs need
#: no hero copy at all (the longest run ANY current assertion in the e2e module
#: shares with the hero is 5 characters: `"ction"`, `" not "`), and a predicate
#: that fires on those is a predicate everyone deletes.
HERO_PROSE_RUN = 15


#: The synthetic subject the non-vacuity fixtures are built against. It is NOT
#: the live hero on purpose: a fixture quoting the copy under review reds the
#: moment the owner rewrites the hero — the exact coupling this PR removes at
#: deploy time — and its failure reads as "the predicate is broken", which is
#: how a guard like this gets weakened.
FIXTURE_SENTENCE = "alpha beta gamma delta epsilon zeta eta theta"


def _company_hero_sentence() -> str:
    """`website/index.html`'s `<h1>` as one normalised (lowercase) sentence.

    Entities are unescaped so the comparison is against the sentence as it
    READS (`&`), not as it is spelled in markup (`&amp;`), and the space that
    tag-stripping leaves before sentence-final punctuation is removed —
    otherwise the compared string is not the sentence anyone quotes.
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.S | re.I)
    assert h1, "website/index.html has no <h1> — the hero moved"
    text = unescape(re.sub(r"<[^>]+>", " ", h1.group(1))).replace("\u00a0", " ")
    sentence = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(text.split())).lower()
    assert sentence, "the company hero <h1> is empty"
    return sentence


class TestCompanyRootMarkerContract:
    """The served company-root marker is ONE structural string (PR #9813).

    Scope, stated plainly: this asserts the marker in the COMMITTED page and
    the SHAPE of the e2e read. It cannot prove what production serves (that is
    `tests/e2e/test_legal_pages.py`, post-deploy). Its job is to make a hero
    rewrite that orphans the pin visibly fail at PR time — the pin it replaced
    (hero prose) was invisible to every pre-merge leg that runs on a website
    diff, because the whole e2e module self-skips without `RUN_LEGAL_E2E=1`.
    """

    def test_the_marker_identifies_the_committed_company_page(self):
        """The pinned marker must exist in `website/index.html`."""
        marker = _company_root_marker()
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert marker in html, (
            f"COMPANY_ROOT_MARKER {marker!r} does not occur in "
            "website/index.html. The company host serves that file at `/`, so "
            "`test_middleware_root_rewrites` can no longer prove the company "
            "root is index.html — and, because the whole e2e module skips "
            "without RUN_LEGAL_E2E=1, no pre-merge website leg would see it. "
            "If you edited the page's identity, move the marker in BOTH places.")

    def test_the_marker_does_not_also_match_the_product_root(self):
        """The company marker must not double as the product root marker."""
        marker = _company_root_marker()
        product = PRODUCT_HTML.read_text(encoding="utf-8")
        assert marker not in product, (
            f"COMPANY_ROOT_MARKER {marker!r} also occurs in "
            "website/product.html, which the tortoise host serves at `/`. The "
            "two served roots then become indistinguishable, so the middleware "
            "rewrite assertions would pass on the wrong page.")

    def test_the_marker_is_an_attribute_on_the_root_element(self):
        """Structural, not prose: a copy edit must not be able to move it."""
        marker = _company_root_marker()
        root = _root_element(INDEX_HTML.read_text(encoding="utf-8"),
                             "website/index.html")
        assert marker in root, (
            f"COMPANY_ROOT_MARKER ({marker!r}) is not part of the company "
            f"page's <html> element ({root!r}). A marker that is a sentence of "
            "marketing copy is re-broken by every hero rewrite — PR #9813's "
            "hero rewrite is exactly what deleted the prose pin this replaced. "
            "Keep the pin on page identity; keep the hero owner-editable.")

    def test_the_served_root_is_proven_structurally_not_by_prose(self):
        """The served-root test proves the root with the marker, never by text.

        This pins the SHAPE of the call site — the one thing that stops the
        historical defect returning: `test_middleware_root_rewrites` must call
        `_assert_serves_company` (which reads the RAW body and asserts
        `COMPANY_ROOT_MARKER`) and must make NO `_clean()` call at all. A prose
        pin cannot be added without text to compare, so "no `_clean()` here"
        is the mechanical statement of "the root is not proven by reading the
        page's words".
        """
        src = LEGAL_PAGES_E2E.read_text(encoding="utf-8")
        calls = _called_names(src, COMPANY_ROOT_CALL_SITE)
        assert calls, (
            f"tests/e2e/test_legal_pages.py no longer defines "
            f"`{COMPANY_ROOT_CALL_SITE}` — this gate's subject moved, so the "
            "company root has no served-document proof at all")
        assert RAW_BODY_HELPER_COMPANY in calls, (
            f"`{COMPANY_ROOT_CALL_SITE}` does not call "
            f"`{RAW_BODY_HELPER_COMPANY}()` (calls: {sorted(set(calls))!r}). "
            "The company root must be proven by `COMPANY_ROOT_MARKER` on the "
            "raw document — a prose assertion is what PR #9813 had to rewrite.")
        assert "_clean" not in calls, (
            f"`{COMPANY_ROOT_CALL_SITE}` calls `_clean()` (calls: "
            f"{sorted(set(calls))!r}). `_clean()` strips tags and therefore "
            "the `data-page` attribute off `<html>`, so any assertion built on "
            "it either can never match or is a prose pin on the page's words "
            "— the PR #8597/#9652/#9813 defect. Read the raw body through "
            f"`{RAW_BODY_HELPER_COMPANY}(body, where)`.")

    def test_no_literal_pins_the_company_root_by_hero_copy(self):
        """No string literal in the e2e module may carry the company hero copy.

        Read from the `ast` string LITERALS, not from the source text, so the
        check is independent of how the file is wrapped and of what its
        comments/docstrings quote — the previous 40-character prefix scan
        reddened a CORRECT page when a comment two files away was reflowed, and
        stayed green for a pin of the hero's tail.
        """
        sentence = _company_hero_sentence()
        offenders = []
        for line, literal in _asserted_string_literals(
                LEGAL_PAGES_E2E.read_text(encoding="utf-8")):
            shared = _shares_a_run_of(
                " ".join(literal.split()).lower(), sentence, HERO_PROSE_RUN)
            if shared:
                offenders.append((line, literal, shared))
        assert not offenders, (
            "tests/e2e/test_legal_pages.py carries the company HERO COPY in a "
            "string literal at "
            + ", ".join(f"line {line} ({literal[:60]!r})"
                        for line, literal, _ in offenders)
            + f". It shares {HERO_PROSE_RUN}+ characters with the hero "
            f"({offenders[0][2]!r}), so the next hero rewrite reds the "
            "served-root suite on a CORRECT page — the PR #9813 defect. Pin "
            "`COMPANY_ROOT_MARKER` on the `<html>` element instead, and read "
            "it through `_assert_serves_company()`.")

    def test_the_prose_pin_checks_reject_the_old_shapes(self):
        """Both checks above must FIRE on the shapes they exist to stop.

        Without this, the two tests could pass vacuously (a predicate that
        matches nothing is green on every tree). Each fixture is the defect in
        miniature, checked through the SAME helpers the tests use, against a
        SYNTHETIC subject — never the live hero, or the fixture would red the
        day the owner rewrites the copy this PR sets out to keep editable.
        """
        def fires(source: str) -> bool:
            return any(
                _shares_a_run_of(" ".join(literal.split()).lower(),
                                 FIXTURE_SENTENCE, HERO_PROSE_RUN)
                for _, literal in _asserted_string_literals(source))

        prose_pins = (
            # the shape PR #8597/#9652/#9813 shipped: the root pinned by a
            # sentence of the hero, asserted on cleaned text
            'def test_middleware_root_rewrites():\n'
            f'    assert "{FIXTURE_SENTENCE}" in _clean(r2.text())\n',
            # the same pin WRAPPED across source lines — this is what reddened
            # a correct tree under the old source-text scan
            'def test_middleware_root_rewrites():\n'
            '    assert (\n'
            '        "alpha beta gamma"\n'
            '        " delta epsilon zeta"\n'
            '    ) in r2.text()\n',
            # a FRAGMENT of the hero, the class the old 40-character prefix
            # could never see
            'def test_middleware_root_rewrites():\n'
            '    assert "delta epsilon zeta" in r2.text()\n',
            # …and the file's OWN pin convention: a module-level constant
            'HERO_PIN = "alpha beta gamma delta epsilon"\n'
            'def test_middleware_root_rewrites():\n'
            '    assert HERO_PIN in r2.text()\n',
            # …and a prose check written as a helper guard, not an assert
            'def _assert_serves_company(body, where):\n'
            '    if "alpha beta gamma delta epsilon" not in body:\n'
            '        raise AssertionError("company root prose drifted")\n',
        )
        for fixture in prose_pins:
            assert fires(fixture), f"the prose-pin check does NOT fire on: {fixture!r}"

        clean_read = (
            'def test_middleware_root_rewrites():\n'
            '    body = _clean(r2.text())\n'
            '    assert COMPANY_ROOT_MARKER in body\n')
        calls = _called_names(clean_read, COMPANY_ROOT_CALL_SITE)
        assert "_clean" in calls, (
            "the call-site check does NOT fire when the served-root test reads "
            f"`_clean()` (calls: {calls!r})")
        assert RAW_BODY_HELPER_COMPANY not in calls, (
            "the call-site check does not notice a missing "
            f"`{RAW_BODY_HELPER_COMPANY}()` call (calls: {calls!r})")

        # …and neither check may fire on a CORRECT site, however it is wrapped
        # or documented: comments, docstrings and assert messages that quote the
        # sentence are documentation, not pins.
        documented = (
            '"""Quotes the hero: "' + FIXTURE_SENTENCE + '"."""\n'
            '# a comment quoting the hero, wrapped mid-sentence:\n'
            '#   alpha beta gamma\n'
            '#   delta epsilon zeta eta theta\n'
            'def test_middleware_root_rewrites():\n'
            '    """Helper docstring: ' + FIXTURE_SENTENCE + '."""\n'
            '    assert COMPANY_ROOT_MARKER in r2.text(), (\n'
            '        "missing marker — pin data-page, not "\n'
            '        "' + FIXTURE_SENTENCE + '")\n')
        assert not fires(documented), (
            "the literal scan reads comments, docstrings or assert messages — "
            "a note quoting the hero would red a correct tree: "
            f"{_asserted_string_literals(documented)!r}")
        documented_calls = _called_names(documented, COMPANY_ROOT_CALL_SITE)
        assert RAW_BODY_HELPER_COMPANY not in documented_calls
        assert "_clean" not in documented_calls


# ═══════════════════════════════════════════════════════════════════════════
# 9. Document outline: no skipped heading level (PR #9813 review, P2)
# ═══════════════════════════════════════════════════════════════════════════
# Replacing the company page's `<h2>` with a `<p>` left the outline stepping
# h1 -> h3, which is an AT-navigation / WCAG H42 regression: screen-reader
# heading navigation and Lighthouse's `heading-order` audit both read the
# committed markup, so the pin belongs here, on the files.
class TestPageHeadingOutline:
    def test_the_company_front_page_does_not_skip_a_heading_level(self):
        levels = _heading_levels(INDEX_HTML.read_text(encoding="utf-8"))
        assert levels, "website/index.html has no headings"
        assert levels[0] == 1, (
            f"website/index.html's first heading is h{levels[0]}, not h1 "
            f"(levels: {levels})")
        for prev, cur in pairwise(levels):
            assert cur <= prev + 1, (
                f"website/index.html skips a heading level: h{prev} -> h{cur} "
                f"(levels: {levels}). Do not replace a section heading with a "
                "paragraph — a level skip is an accessibility regression, and "
                "the fix costs one tag (`<h2 class=\"tortoise-pitch\">`).")

    def test_the_company_front_page_has_a_level_two_heading(self):
        """The product section must keep a heading, not just body prose."""
        levels = _heading_levels(INDEX_HTML.read_text(encoding="utf-8"))
        assert 2 in levels, (
            f"website/index.html has no h2 at all (levels: {levels}). The "
            "Tortoise section on the company front page is the product's only "
            "heading there; demoting it to a paragraph leaves the page's main "
            "section unnamed for AT users.")

    def test_the_product_front_page_does_not_skip_a_heading_level(self):
        levels = _heading_levels(PRODUCT_HTML.read_text(encoding="utf-8"))
        assert levels and levels[0] == 1, (
            f"website/product.html's first heading is not h1 (levels: {levels})")
        for prev, cur in pairwise(levels):
            assert cur <= prev + 1, (
                f"website/product.html skips a heading level: h{prev} -> h{cur} "
                f"(levels: {levels})")

    def test_the_niche_product_page_does_not_skip_a_heading_level(self):
        """#13645: same outline contract, one page over.

        Measured outline: `[1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4]` — one hero h1;
        SIX h2 (the compute beat's "Organisational Intelligence" — #13645 item 2,
        added 2026-11-23, which is why the intro is the only heading-less beat
        left — then the three steps, onboarding and pricing); the four
        tier-card h3s under `#pricing-section`; and one h4 on the self-hosted
        card. No level skips.

        ⛔ A STEP rendered as an h3 — an easy slip when a beat is pasted in — is
        an accessibility regression here exactly as it is on the product page,
        because h3 is only legitimate inside the pricing block (the tier cards).
        Scope any such reading to ABOVE `#pricing-section`; the h3s below it are
        correct and must not be "fixed" down to h2.
        """
        levels = _heading_levels(PRODUCT_TEAMS_HTML.read_text(encoding="utf-8"))
        assert levels and levels[0] == 1, (
            "website/tortoise-for-product-teams.html's first heading is not h1 "
            f"(levels: {levels})")
        for prev, cur in pairwise(levels):
            assert cur <= prev + 1, (
                "website/tortoise-for-product-teams.html skips a heading level: "
                f"h{prev} -> h{cur} (levels: {levels})")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Short-viewport beats must scroll, not clip (PR #9813 review, P2)
# ═══════════════════════════════════════════════════════════════════════════
# `.beat` is `position: fixed; height: 100%; justify-content: center` with no
# overflow rule, so content taller than the viewport is cut at BOTH edges and
# is unreachable (document scroll is consumed by ScrollTrigger). Both fronts
# carry beats whose text is now taller than a landscape phone's viewport:
# `#beat-tortoise` (the closing pitch, PR #9813) and `#beat-cta` (the waitlist
# form + its legal line) on index.html, and `#beat-hero` (the subtitle PR #9813
# enlarged) on product.html. Each needs the existing `@media (max-height: ...)`
# escape hatch — AND a way to RECEIVE the gesture: `.beat` is
# `pointer-events: none`, so without the visibility-gated `.beat.pe-on` rule a
# gesture over the pitch text passes through to the document and drives the
# ScrollTrigger scrub instead of the beat's overflow box. Declaring
# `overflow-y: auto` alone is decoration (PR #9813 review, P2).
SHORT_VIEWPORT_BEATS = (
    (INDEX_HTML, "#beat-cta", "the company waitlist form + its legal line"),
    (INDEX_HTML, "#beat-tortoise", "the company closing pitch + its CTA"),
    (PRODUCT_HTML, "#beat-hero", "the product hero + its CTA"),
    # #13645: the niche page's hero is a SPLIT — copy beside an owned 9/16 video
    # (398 px tall on its own), so it is taller than any text-only hero here.
    (PRODUCT_TEAMS_HTML, "#beat-hero", "the split hero: copy + the 9/16 video"),
    # …and its compute beat carries a paragraph PLUS the six-row ↑/↓ grid, which
    # is the other beat that overruns a landscape phone.
    (PRODUCT_TEAMS_HTML, "#beat-compute", "the compute claim + the ↑/↓ outcomes grid"),
)

#: A landscape phone is ~390-430 px tall, so a hatch that only fires below this
#: leaves the viewport it exists for with no escape hatch at all.
MIN_SHORT_VIEWPORT_PX = 480

#: A rule that lets a VISIBLE beat take pointer events (the animation script
#: toggles the class from the scrubbed opacity, so invisible beats stay
#: click-through — #744).
PE_ON_RULE_RE = re.compile(
    r"\.beat\.pe-on\b[^{}]*\{[^{}]*pointer-events\s*:\s*auto", re.S)
PE_ON_TOGGLE_RE = re.compile(r"classList\.toggle\(\s*['\"]pe-on['\"]")
PE_ON_SEED_RE = re.compile(r"classList\.add\(\s*['\"]pe-on['\"]")
BEAT_LIST_RE = re.compile(r"const\s+beats\s*=\s*\[(.*?)\]", re.S)


def _call_objects(text: str, call_start: int) -> list[str]:
    """Every top-level `{...}` object inside the call whose `(` follows `call_start`."""
    i = text.find("(", call_start)
    if i < 0:
        return []
    depth, objects = 0, []
    while i < len(text):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return objects
        elif char == "{" and depth == 1:
            block = _brace_block(text, i)
            objects.append(block)
            i += len(block) + 2   # '{' + block + '}' — resume after the close
            continue
        i += 1
    return objects


def _beat_tween_var_objects(html: str) -> list[list[str]]:
    """The vars objects of every `gsap.to(el, …)` / `gsap.fromTo(el, …)` call."""
    return [_call_objects(html, match.start())
            for match in re.finditer(r"gsap\.(?:to|fromTo)\(\s*el\s*,", html)]


class TestShortViewportBeatsScrollInsteadOfClipping:
    """The escape hatch is DECLARED (here) and REACHABLE (next test).

    Scope, stated plainly: this reads the committed markup and script. It does
    not open a browser, so it cannot observe clipping pixel-for-pixel — what it
    can do is pin the conditions without which the hatch provably cannot work:
    the `@media (max-height: ...)` rule with both declarations at a breakpoint
    that fires on a landscape phone, the visibility-gated pointer-events rule,
    and the per-tween wiring that keeps that class in step with the opacity. A
    change that drops any of them stops being a silent regression.
    """

    def test_each_tall_beat_declares_the_css_escape_hatch(self):
        for path, selector, what in SHORT_VIEWPORT_BEATS:
            blocks = _max_height_media_blocks(path.read_text(encoding="utf-8"))
            assert blocks, (
                f"{path.name} declares no `@media (max-height: ...)` rule. "
                f"{what} is a fixed full-height beat with centred content, so "
                "a short viewport clips it at both edges.")
            covering = [
                (height, body) for height, body in blocks
                if selector in body
            ]
            assert covering, (
                f"{path.name}: no `@media (max-height: ...)` rule names "
                f"{selector} (blocks found: "
                f"{[(h, b[:60]) for h, b in blocks]!r}). {what} is a fixed "
                "full-height beat whose content is taller than a landscape "
                "phone's viewport, and centred overflow is clipped at both "
                f"ends — the CTA becomes unreachable. Extend the "
                f"short-viewport rule to {selector}.")
            escaping = [
                height for height, body in covering
                if "overflow-y: auto" in body
                and "justify-content: flex-start" in body
            ]
            assert escaping, (
                f"{path.name}: the `@media (max-height: ...)` rule naming "
                f"{selector} does not both switch to `justify-content: "
                "flex-start` and allow `overflow-y: auto` "
                f"(found: {[b for _, b in covering]!r}). Centred overflow is "
                "cut at BOTH edges, so the first line goes above the viewport "
                "and the CTA below it.")
            assert max(escaping) >= MIN_SHORT_VIEWPORT_PX, (
                f"{path.name}: the escape hatch for {selector} only fires at "
                f"`max-height: {max(escaping)}px`, which a landscape phone "
                f"(~390-430 px tall) never reaches — {what} stays clipped on "
                f"the very viewport this rule exists for. Keep the breakpoint "
                f"at {MIN_SHORT_VIEWPORT_PX}px or wider.")

    def test_the_beat_can_receive_the_scroll_gesture_while_visible(self):
        """`overflow-y: auto` on a `pointer-events: none` box does nothing.

        A gesture over the pitch text would pass THROUGH the beat to the
        document, where ScrollTrigger consumes it — so the clipped content
        never moves and the beat's own overflow box is decoration. The
        mechanism is visibility-gated hit-testing: the beat must be in the
        script's beat list, the script must seed and then toggle `.pe-on` from
        the scrubbed opacity, EVERY opacity tween on it must keep that class in
        step, and the beat's own `pointer-events: none` default (if it has one)
        must come with a `.pe-on` companion that out-ranks it.
        """
        for path, selector, what in SHORT_VIEWPORT_BEATS:
            html = path.read_text(encoding="utf-8")
            beat_id = selector.lstrip("#")
            assert PE_ON_RULE_RE.search(_css_blanked(html)), (
                f"{path.name} declares no `.beat.pe-on {{ pointer-events: auto "
                "}` rule. `.beat` is `pointer-events: none`, so the overflow "
                f"hatch on {selector} can never receive a wheel/touch gesture "
                f"— {what} stays clipped where it is cut, and the gesture "
                "scrolls the page instead. Gate hit-testing on visibility "
                "(the `.beat.pe-on` pattern) rather than dropping the overlay "
                "rule, which would let invisible beats swallow clicks (#744).")
            assert PE_ON_TOGGLE_RE.search(html), (
                f"{path.name} never toggles `.pe-on`: the class must follow the "
                "scrubbed opacity (`classList.toggle('pe-on', opacity > "
                "0.05)`), or the beat takes pointer events at the wrong time "
                "— always (swallowing clicks, #744) or never (the overflow "
                "hatch stays inert).")
            assert PE_ON_SEED_RE.search(html), (
                f"{path.name} never seeds `.pe-on` for the beat that is visible "
                "at load (`i === 0`). No fade tween runs before the first "
                "scroll, so the first beat keeps `pointer-events: none` — its "
                "own overflow box stays unreachable until it has already "
                "started to fade.")
            beat_list = BEAT_LIST_RE.search(html)
            assert beat_list and beat_id in beat_list.group(1), (
                f"{path.name}: `{beat_id}` is not in the script's beat list "
                f"({beat_list.group(0) if beat_list else 'not found'!r}), so no "
                "fade tween ever updates its `.pe-on` state and the overflow "
                "hatch is never reachable.")

            # The mechanism has to hold EVERY frame the beat is on screen, not
            # just exist somewhere: each scrubbed beat tween must carry the sync
            # callback. Deleting one leaves that beat permanently click-through
            # (fade-in) or permanently hit-testable while invisible (fade-out,
            # the #744 direction).
            synced, unsynced = 0, []
            for objects in _beat_tween_var_objects(html):
                for config in (obj for obj in objects if "scrollTrigger" in obj):
                    synced += 1
                    if "onUpdate" not in config:
                        unsynced.append(config[:80])
            assert synced, (
                f"{path.name}: no scrolled `gsap` beat tween (a `gsap.to(el, …)` "
                "/ `gsap.fromTo(el, …)` with a `scrollTrigger`) was found — the "
                "beat sequence this guard reads moved.")
            assert not unsynced, (
                f"{path.name}: {len(unsynced)} of {synced} beat tweens do not "
                f"call the `.pe-on` sync ({unsynced!r}). Without it the beat's "
                "hit-testing drifts out of step with its visibility — a "
                "faded-out beat keeps swallowing clicks (#744) or a visible "
                "one never gains the gesture and its overflow hatch is dead.")

            # A `pointer-events: none` DEFAULT on the beat itself is fine — it
            # is what an invisible overlay needs — but an id selector out-ranks
            # `.beat.pe-on`, so it must come with a `.pe-on` companion.
            if re.search(r"#" + re.escape(beat_id)
                         + r"\s*\{[^{}]*pointer-events\s*:\s*none", html):
                assert re.search(r"#" + re.escape(beat_id)
                                 + r"\.pe-on\s*\{[^{}]*pointer-events\s*:\s*auto",
                                 html), (
                    f"{path.name} declares `pointer-events: none` on "
                    f"`{selector}` and no `{selector}.pe-on {{ pointer-events: "
                    "auto }}` companion. The id selector out-ranks "
                    "`.beat.pe-on`, so the visibility-gated rule is defeated "
                    "and the beat can never receive the scroll gesture.")


class TestARenderedCodeElementGetsAMonoFace:
    """A page that renders `<code>`/`<pre>` must give the ELEMENT a mono family (#6870).

    `TestMonospaceStackRendersAsAMonoFace` proves every *declaration* is right, and
    that is not enough: a `<code>` or `<pre>` element with NO matching rule inherits
    nothing — the user-agent's `font-family: monospace` beats inheritance — so it
    renders in Courier even when `--mono` is perfect everywhere in the file.

    That is exactly how the blog renderer and the 404 page survived the first pass:
    measured with CDP `CSS.getPlatformFontsForNode`, the blog's `<code>`/`<pre>` and
    `website/404.html`'s `<code>` were **Courier** while every sibling page was
    **Menlo**, because those two files rendered the element and never styled it.

    Scope is the self-contained page: a file under `ROOTS` carrying its own `<style>`
    block, i.e. the marketing pages, the 404, and the blog renderer's inline sheet. A
    React component styled by a separate stylesheet is out of scope — this test cannot
    see across files, and pretending otherwise would pin nothing. Two more emitters are
    self-contained with their own sheet and are deliberately OUTSIDE `ROOTS` because
    they are not pages this tree serves for reading: `tortoise/oauth.py:2145` (the MCP
    consent page) and `tortoise/render.py:94` (the renderer's own document), both of
    which the sibling declaration guard scans. Neither renders `<code>`/`<pre>` today,
    so no live element is missed — and
    `test_the_out_of_root_emitters_render_no_code` pins that precondition, so growing
    one there reds with the repair named instead of slipping past this scan.

    WHAT THIS PINS: every self-contained page that renders `<code>`/`<pre>` carries a
    rule naming that element whose family resolves to a mono face. That is the shape
    the defect had — the element existed and nothing styled it.

    WHAT IT DOES NOT PIN, stated because a guard is trusted for what it claims: CSS is
    not modelled, it is pattern-matched. It does not evaluate `@media`/`@supports`
    conditions or a `<style media=…>` attribute, does not model `<noscript>`, does not
    parse CSS strings (so a `/*` inside `content:"…"` is treated as a comment), and
    reads `font-family` plus the `font:` shorthand rather than resolving the cascade.
    A page could therefore satisfy it and still render Courier under some condition.
    Each of those is latent — no page in this tree carries such a shape — and closing
    them properly means parsing CSS, not adding a fifth regex.

    One thing it DOES model, because it is the defect's own shape: the family list is
    **ordered**. The browser takes the first family it has installed (MDN
    `font-family`; the generics are always installed), so a mono face named *after* a
    family Chrome certainly reaches is not the face the element gets —
    `code{font-family:'SF Mono', monospace, Menlo}` renders **Courier**, which is
    #6870 with `Menlo` one keystroke too late. The list is walked in priority order and
    refused when a Courier-attracting token (`monospace`, `courier`, `serif`,
    `sans-serif`) is reached before any `Menlo`; `ui-monospace` is not one of them,
    because Chrome drops it and the shipped stacks therefore carry it first. What is
    still not modelled: whether a *named* face is installed (a guard cannot know), and
    per-character fallback — so a named serif chosen in plain sight stays GREEN, which
    is the layout-decision boundary the sibling class records.

    Four ways an earlier version of this guard went GREEN over a page that renders a
    non-mono face, each closed here:

    1. **Inert text.** It read the file as text, so a `/* … */` comment inside the
       sheet, a `<style>` inside an HTML comment, a `<style>` inside a `<script>`
       string, and a `<style>` inside `<template>` each counted as styling. The
       document is blanked for comments, script bodies and template bodies before any
       sheet is read.
    2. **`var(--mono)` with nothing to resolve to.** The token was accepted without
       asking whether the page defines `--mono` at all, or whether the value it
       defines is a mono face (`--mono: Georgia, serif` passed). The declaration is
       now resolved through the page's own definitions, two hops, and must land on a
       mono face — and a `var()` **fallback** is read only when the token is missing,
       so `--mono: Georgia, serif` with `font-family: var(--mono, Menlo)` is REFUSED
       instead of being credited through fallback text CSS never substitutes
       (`var(--mono, …Menlo…)` ships 4x in
       `website/apps/dashboard/src/index.css:583,753,919,996`). A reference that
       resolves to nothing invalidates the **whole** declaration at computed-value
       time, and that signal is propagated out of the resolver's recursion instead of
       being dropped as empty text, so `--mono: var(--nope)` plus
       `font-family: Menlo, var(--mono)` is refused rather than credited through the
       trailing face the browser never applies. The accepted face is `Menlo` alone,
       because the sibling class measured `'SF Mono'` and `SFMono-Regular` as Times on
       this repo's Chromium (`TestMonospaceStackRendersAsAMonoFace`, above) — crediting
       them re-blessed the Courier defect on the one route the sibling guard cannot see.
    3. **A rule that styles the element only in context.** `.art-body pre code` — and
       `pre.anything code` — says nothing about an inline `<code>` in a paragraph, so
       that pairing is skipped; everything else is the cascade.
    4. **A custom property NAME read case-blind.** The resolver folded custom property
       names, so `:root{--Mono:Menlo} code{font-family:var(--MONO)}` resolved to
       `Menlo` and went GREEN. css-variables-1 §2 is explicit that custom property
       names are **not** ASCII case-insensitive and are equal only when identical —
       `--foo` and `--FOO` are distinct properties — so CSS computes the
       guaranteed-invalid value, invalidates `font-family` at computed-value time, and
       the element renders the UA `monospace`: Courier, the #6870 face. Names now match
       exactly; only the family *value* is folded, which css-fonts makes
       case-insensitive (#10254 review, round 13).

    Three ways it wrongly REFUSED a correct page, all closed: a rule wrapped in
    `@media`, a `font-family: var(--code-font)` indirection, and the element with a
    class or attribute (`code.inline{…}`). The rule match is innermost-only, the
    indirection resolves, a `font:` shorthand naming a mono face is accepted, and the
    last compound may carry a class/id/attribute/pseudo.

    One residual does fail GREEN, and it is named here rather than implied: a custom
    property is collected from **every** rule that declares it, regardless of the
    selector, and resolved for every element — but custom properties inherit
    (css-variables-1, the `--*` property table: `Inherited: yes`), so a token declared
    on a scoped rule does not reach a `<code>` outside that scope.
    `.art-body{--mono:Menlo} code{font-family:var(--mono)}` is therefore credited here,
    while a browser computes the guaranteed-invalid value for a `code` outside
    `.art-body`, invalidates the whole declaration, and renders the inherited family.
    Latent, not live — eight of the nine subjects define `--mono` in `:root`
    (`404.html:17`, `faq.html:24`, … `_lib.ts:1098`) and the ninth,
    `website/apps/dashboard/public/404.html`, resolves no `var()` at all: its `code{}`
    rule names `ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace` directly
    (`:41-42`), so there is no token for this limit to mis-resolve. Eight, not nine, at
    this head (#10254 review, round 11). And keying `defined` by declaring selector
    would mean modelling which ancestors an element has, i.e. the CSS parse this guard
    deliberately does not do. Pinned by
    `test_a_scoped_token_is_credited_and_that_is_a_documented_limit`, so closing it is a
    deliberate edit and never a silent change (#10254 review, round 9).

    Known residual, deliberately not modelled: a page rendering `<code>` **only**
    inside `<pre>` is refused, because the `pre code` pairing is skipped
    unconditionally. It fails RED, never green, and no page in this tree is shaped
    that way. A `var()` nested inside a fallback (`var(--a, var(--b, Menlo))`) is *not*
    a residual — the reference is matched paren by paren and substituted recursively,
    which is what css-variables-1 §"replace a var()" step 3 does (the fallback is
    itself substituted), so the family that is credited is the one the browser would
    compute. Truncating at the first `)` instead credited `--brand: Georgia, serif` +
    `font-family: var(--mono, var(--brand, Menlo))` as mono (#10254 review, round 6).
    """

    #: the trees this guard scans. `apps/` is NOT an empty declaration: it contributes
    #: 2 of the `MIN_DECIDABLE` subjects today — `apps/graph-viz/index.html` (a page
    #: that carries its own `<style>` and renders no `<code>`/`<pre>` YET) and
    #: `apps/graph-viz/src/components/ontology/HierarchyChart.tsx` (own `<style>`, not a
    #: page) — while the only `apps/` file that DOES render the element,
    #: `apps/graph-viz/src/components/ontology/OntologyView.tsx`, carries no own sheet,
    #: so it is not decidable here. MEASURED 2026-10-29 (#10254 review, round 12): the
    #: earlier wording ("`apps/` yields no self-contained page today") was stale — the
    #: top-level `apps/` root already carries sheets, and dropping it takes the ratchet
    #: to 19 < `MIN_DECIDABLE`. So removing it must be a deliberate edit that moves the
    #: floor too, never a silent narrowing (see `_sources`); each root's contribution is
    #: pinned by `test_every_page_it_can_decide_on_selects_this_guard`.
    ROOTS = ("website", "apps")
    #: measured 2026-10-28 at this tree: 9 self-contained pages carry `<code>`/`<pre>`
    #: (faq, privacy, 404, self-hosted, security, docs, license,
    #: apps/dashboard/public/404.html, and the blog renderer `_lib.ts`). The floor sits
    #: ON the measurement on purpose: a gap between the floor and the count is what lets
    #: the scan narrow without a red, so removing a page must move this number.
    MIN_PAGES = 9
    #: measured 2026-10-28 at this tree: 21 files under `ROOTS` carry their own `<style>`
    #: block — the set `test_every_page_it_can_decide_on_selects_this_guard` ratchets. A
    #: SEPARATE constant because it is a separate population: reusing `MIN_PAGES` as that
    #: test's floor left a 12-page gap, which is the narrowing this file forbids. Equal to
    #: the measurement on purpose, for the same reason.
    MIN_DECIDABLE = 21

    SUFFIXES = (".html", ".css", ".js", ".ts", ".jsx", ".tsx")
    RENDERS_CODE = re.compile(r"<(code|pre)\b", re.IGNORECASE)
    #: only the page's own sheet is author CSS for this guard. Inert text is stripped
    #: first: `<script>` strings, `<template>` bodies (not rendered), HTML comments.
    STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
    INERT = re.compile(r"<script\b[^>]*>.*?</script>|<template\b[^>]*>.*?</template>",
                       re.DOTALL | re.IGNORECASE)
    COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.DOTALL)
    #: INNERMOST rules only: a body containing `{` means the match is an at-rule
    #: prelude wrapping another rule, and the inner one is the rule that styles it
    RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
    #: `font-family` and the `font:` shorthand — the shorthand smuggles a family past
    #: a font-family-only scan, the lesson the sibling class already learned
    FAMILY = re.compile(r"(?:font-family|font)\s*:\s*([^;}]*)", re.IGNORECASE)
    VAR_DEF = re.compile(r"(--[\w-]+)\s*:\s*([^;}]*)")
    #: the tokens a family VALUE asks for — the population the scoped-token limit below
    #: applies to. Deliberately not `--mono`-only: the guard credits `var(--anything)`,
    #: so `--code-font` declared on a scoped rule is the same hole under another name.
    VAR_REF = re.compile(r"var\(\s*(--[\w-]+)", re.IGNORECASE)
    #: the selector's LAST compound must be the element itself — `.code{}` is the big
    #: "404" glyph, and `.art-body pre{font-family:inherit}` inherits rather than sets.
    #: The element may carry a class, id, attribute or pseudo — `code.inline{…}` styles
    #: `<code>`, so only a following TAG character disqualifies the compound
    ELEMENT_LAST = re.compile(r"(?:code|pre|kbd|samp)(?![a-zA-Z0-9_-])", re.IGNORECASE)
    TAG = re.compile(r"([a-zA-Z][\w-]*)")
    #: a real face, NOT the `monospace` keyword: on macOS Chrome that keyword IS
    #: Courier, which is the defect. The sibling guard requires Menlo by name for the
    #: same reason, and accepting the keyword let `var(--mono, monospace)` pass with
    #: `--mono` undefined or set to a serif.
    #: `Menlo` is the ONLY name here, and that is measured rather than assumed: the
    #: sibling class (`TestMonospaceStackRendersAsAMonoFace`, above) records CDP
    #: `CSS.getPlatformFontsForNode` on this repo's Chromium that `'SF Mono'` → Times
    #: and `SFMono-Regular` → Times while
    #: `Menlo` → Menlo, and #6870's availability probe records `'SF Mono'` as not
    #: exposed to web content. Crediting those two names made this guard GREEN over
    #: `font-family: var(--mono, 'SF Mono', monospace)` — Courier-class, the exact
    #: defect #6870 is about — on the `var()`-fallback route the sibling guard skips
    #: (it ignores any value containing `var(--mono`), so nothing else caught it
    #: (#10254 review, round 8).
    MONO = ("menlo",)
    #: The mirror of `MONO`: a family that makes Chrome reach a NON-mono face before
    #: any mono face named after it can apply. `font-family` is an ORDERED list and
    #: the browser uses "the first font in the list that is installed" (MDN
    #: `font-family`; the generics are always installed), so `Menlo` appended AFTER
    #: `monospace` is not the face the element gets — `monospace` IS Courier on macOS
    #: Chrome, the #6870 face. Matching `menlo` anywhere in the resolved value, with
    #: position ignored, made this guard GREEN over
    #: `code{font-family:'SF Mono', monospace, Menlo}` — the defect one keystroke too
    #: late (#10254 review).
    #: `ui-monospace` is deliberately NOT an attractor: it is a hyphenated generic
    #: Chrome drops (the sibling's measured table above), which is exactly why the
    #: shipped stacks carry it first. The boundaries are word-ish, not a substring test,
    #: so `ui-monospace` cannot match `monospace` and `sans-serif` cannot be read as the
    #: bare `serif` generic. A NAMED face such as `Georgia` is not here either: naming a
    #: serif face in plain sight is a layout decision, which is the boundary the sibling
    #: class records — only the generics/Courier are the Courier defect.
    COURIER_ATTRACTING = re.compile(
        r"(?<![\w-])(?:sans-serif|monospace|courier|serif)(?![\w-])")

    @staticmethod
    def _families(value):
        """The family tokens of `value`, in the order the browser tries them.

        `font-family` is a comma-separated list, and a `font:` shorthand prefixes the
        same list with size/weight/line-height — so `font:12px Menlo` is one token that
        must still be read as Menlo. Split on top-level commas only, so a comma inside a
        quoted family name (`'Foo, Bar'`) does not split it.
        """
        tokens, buf, quote = [], [], None
        for ch in value:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf.append(ch)
            elif ch == ",":
                tokens.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        tokens.append("".join(buf))
        return [t.strip() for t in tokens if t.strip()]

    def _is_mono(self, value, defined, depth=0):
        """Does `value` give the element a mono face, once the page's tokens are substituted?"""
        # substitute BEFORE reading any face: a `var()` reference names no family of
        # its own, so a keyword found in the un-substituted text is not a decision
        # about what the element computes to. Reading the raw string first is how
        # `var(--mono, Menlo)` was credited while `--mono: Georgia, serif` — the face
        # Chrome actually used — went unread (PR #10254 review, round 3).
        substituted = self._substitute(value, defined, depth)
        if substituted is None:
            # invalid at computed-value time (css-variables-1 §2.2: a guaranteed-invalid
            # value in a property value makes the property invalid at computed-value
            # time). The declaration does not apply, the element inherits, and the UA
            # `monospace` — Courier — wins. Never a face, whichever names it carried.
            return False
        # the FAMILY VALUE is folded here and only here: font family names are ASCII
        # case-insensitive (css-fonts-4), unlike the custom property NAMES the resolver
        # matches, which keep their casing.
        v = substituted.strip().lower()
        if not v or v in ("inherit", "unset", "initial", "revert"):
            # inheriting is the failing case for an element with no author rule: the
            # UA `monospace` sheet would win. A parent that IS mono is out of scope.
            return False
        # Walk the list in PRIORITY order, not `any(name in value)`: the browser takes
        # the FIRST family it has installed, so a mono face named after a family the
        # browser certainly reaches is not the face the element gets. The first token
        # that names a mono face is a pass; a Courier-attracting token reached first is
        # a refusal; anything else (an unavailable named face, `ui-monospace`, a serif
        # face chosen in plain sight) is passed over, exactly as the browser passes it.
        for family in self._families(v):
            if any(m in family for m in self.MONO):
                return True
            if self.COURIER_ATTRACTING.search(family):
                return False
        return False

    @staticmethod
    def _var_reference(text, start):
        """`(name, fallback, end)` for the `var(` at `text[start]`, matched paren by paren.

        `end` is the index of its closing `)`. The fallback is split at the first
        *top-level* comma, so `var(--mono, var(--brand, Menlo))` yields the fallback
        `var(--brand, Menlo)` — whole, not truncated at the inner `)`.

        There is deliberately no `var(...)` regex here: parens cannot be counted by one,
        and truncating at the first `)` is how an earlier version credited a serif named
        only inside an unresolved fallback (#10254 review, round 6).

        `None` for an unbalanced reference: that is a parse error in CSS, so the whole
        declaration is dropped and no family in it applies — the caller refuses it
        rather than reading the residue as text.

        The returned NAME keeps the author's casing. Custom property names are the one
        identifier family CSS does NOT fold: css-variables-1 §2, "Unlike other CSS
        properties, custom property names are not ASCII case-insensitive. Instead,
        custom property names are only equal to each other if they are identical to
        each other" — `--foo` and `--FOO` are two distinct properties. Lowercasing the
        name here made this guard disagree with the browser in the fail-GREEN
        direction: `:root{--Mono:Menlo} code{font-family:var(--MONO)}` is the
        guaranteed-invalid value with no fallback, so `font-family` is invalid at
        computed-value time and the element renders the UA `monospace` — Courier, i.e.
        #6870 itself — while the collapsed key resolved it to `Menlo` (#10254 review,
        round 13). Only the FAMILY VALUE is case-insensitive (css-fonts), and `_is_mono`
        is where that fold belongs.
        """
        depth, i = 0, start + 3
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        else:
            return None
        inner = text[start + 4:i]
        depth, comma = 0, None
        for j, ch in enumerate(inner):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                comma = j
                break
        if comma is None:
            return inner.strip(), None, i
        return inner[:comma].strip(), inner[comma + 1:], i

    def _substitute(self, value, defined, depth=0):
        """`value` with its `var()` references replaced the way CSS replaces them.

        A **defined** custom property is substituted and its fallback is *dead text*:
        css-variables-1 §"Using Cascading Variables" makes the second argument the
        substitution value only "when the value of the referenced custom property is
        the guaranteed-invalid value", i.e. missing or invalid. Crediting a fallback
        regardless is a false green and not a hypothetical one: the live idiom
        `font-family: var(--mono, ui-monospace, …Menlo…)` ships 4x in
        `website/apps/dashboard/src/index.css` (583, 753, 919, 996), so any page that
        also defined `--mono` as a serif would pass this guard while macOS Chrome
        rendered the serif.

        Reference resolution is recursive and paren-balanced, because the fallback is
        itself substituted (css-variables-1 "replace a var()" step 3): `var(--a,
        var(--b, Menlo))` computes to whatever `--b` is, so the family this guard may
        credit is the one the browser would actually use — reading the fallback as
        flat text credited a serif sitting inside it (#10254 review, round 6).

        A missing token falls back. A reference that resolves to nothing — missing with
        no fallback, defined as empty, or a chain past the depth cap — makes the whole
        declaration invalid at computed-value time (CSS does not fall through to the
        next family in the list), so the element inherits and the guard goes RED. That
        is the safe direction: over-detection, never a false green.

        Returns `None` for that invalid case rather than `""`, so the flag survives the
        recursion. Returning empty *text* lost it one hop down — `--mono: var(--nope)`
        with `font-family: Menlo, var(--mono)` came back as `Menlo,`, which reads as a
        mono face although CSS invalidates the whole declaration and the element renders
        Courier (#10254 review, round 8).
        """
        out, i, invalid = [], 0, False
        while i < len(value):
            if value[i:i + 4].lower() != "var(":
                out.append(value[i])
                i += 1
                continue
            ref = self._var_reference(value, i)
            if ref is None:
                # unbalanced `var(`: a parse error, so CSS drops the WHOLE declaration
                # and no family written in it applies. Refuse rather than credit the
                # residue's text (`Menlo, var(--mono` would otherwise read as mono).
                invalid = True
                break
            name, fallback, end = ref
            resolved = defined.get(name)
            chosen = resolved if resolved is not None else fallback
            if chosen is None or not chosen.strip() or depth >= 2:
                invalid = True
                i = end + 1
                continue
            substituted = self._substitute(chosen, defined, depth + 1)
            # `None` is the recursion's invalid signal — propagate it, do not append it
            # as text, which is how the flag was lost one hop down. A leftover `var(`
            # is the same refusal (an unresolved reference is not a face).
            if substituted is None or "var(" in substituted.lower():
                invalid = True
            else:
                out.append(substituted)
            i = end + 1
        return None if invalid else "".join(out)

    def _elements_styled_mono(self, css):
        """The element names (`code`, `pre`) that some rule gives a mono family."""
        # keyed by the author's exact name: custom property names are case-SENSITIVE
        # (css-variables-1 §2 — `--foo` and `--FOO` are distinct), so folding case
        # here resolves a reference the browser invalidates (#10254 review, round 13).
        # `value` is left raw too; family-value folding is `_is_mono`'s job.
        defined = {name.strip(): value for name, value in self.VAR_DEF.findall(css)}
        # the LAST matching rule wins the cascade, so a later
        # `code{font-family:inherit}` must un-style it, not be ignored
        last_family = {}
        for selectors, body in self.RULE.findall(css):
            families = self.FAMILY.findall(body)
            if not families:
                continue
            family = families[-1]
            for sel in selectors.split(","):
                parts = re.split(r"[\s>+~]+", sel.strip())
                last = parts[-1]
                match = self.ELEMENT_LAST.match(last)
                if not match:
                    continue
                # the compound may carry a class/id/attr/pseudo — the ELEMENT is the tag
                element = match.group(0).lower()
                # `pre code` (and `pre.anything code`) styles code *inside* pre; it is
                # not the generic style for an inline <code>, so it must not decide it
                if element == "code" and len(parts) > 1:
                    tag = self.TAG.match(parts[-2])
                    if tag and tag.group(1).lower() == "pre":
                        continue
                last_family[element] = family
        return {e for e, fam in last_family.items() if self._is_mono(fam, defined)}

    def test_no_self_contained_page_renders_code_or_pre_unstyled(self):
        repo = Path(__file__).resolve().parent.parent
        offenders, checked = [], 0
        for path in self._sources(repo):
            text = path.read_text(errors="replace")
            if not self.RENDERS_CODE.search(text):
                continue
            # blank the whole DOCUMENT first: a `<style>` inside a `<script>` string, a
            # `<template>` body, or an HTML comment is inert, and blanking only the
            # extracted sheets missed the last two
            css = "\n".join(self.STYLE.findall(
                self.COMMENT.sub(" ", self.INERT.sub(" ", text))))
            if not css.strip():
                continue
            checked += 1
            # per element, not "some rule somewhere": the blog styled `pre` and left
            # inline `code` to the UA sheet, which passed a set-level check
            rendered = {e.lower() for e in self.RENDERS_CODE.findall(text)}
            missing = sorted(rendered - self._elements_styled_mono(css))
            if missing:
                offenders.append(f"{path.relative_to(repo)} ({', '.join('<' + m + '>' for m in missing)})")
        # the floor is the MEASURED count (9 self-contained pages today), not a guess: it
        # has no slack, so deleting or moving a page is a deliberate act that moves this
        # number with it, instead of narrowing the scan silently (PR #10254 review).
        assert checked >= self.MIN_PAGES, (
            f"only {checked} self-contained page(s) found under "
            f"{'/'.join(self.ROOTS)} — the scan is not looking at the right tree")
        assert not offenders, (
            "these pages render these elements but no rule in their own <style> gives the ELEMENT "
            "a mono family, so the user-agent's `monospace` applies and macOS Chrome renders Courier "
            "(issue #6870):\n  " + "\n  ".join(offenders) +
            "\nAdd e.g. `code{font-family:var(--mono)}` AND make sure that page defines `--mono` as a "
            "mono face (an undefined var() invalidates the declaration and the element inherits).")

    @staticmethod
    def _sources(repo):
        # os.walk with a pruned `dirnames`, NOT `rglob`: rglob materialises the whole
        # tree, `node_modules` included, before any filter can run (see the sibling
        # class's note — it is the same walk, and the same cost).
        # `.wrangler` is in this set for the reason the shared constant records: it is
        # a gitignored COPY of `website/functions/**`, so a stale bundle contains the
        # pre-fix blog sheet and both tests in this class failed on generated code
        # (#10254 review P3).
        skip = BUILD_OUTPUT_DIRS
        for root_name in TestARenderedCodeElementGetsAMonoFace.ROOTS:
            root = repo / root_name
            # fail closed: a missing root would narrow the scan silently
            assert root.is_dir(), (
                f"{root_name}/ is gone — update ROOTS in "
                f"{TestARenderedCodeElementGetsAMonoFace.__name__}")
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip]
                for name in filenames:
                    if Path(name).suffix not in TestARenderedCodeElementGetsAMonoFace.SUFFIXES:
                        continue
                    yield Path(dirpath) / name

    def test_every_page_it_can_decide_on_selects_this_guard(self):
        """The REVERSE direction: a page this guard can DECIDE on must RUN it when changed.

        Registration in `config/ci-surfaces.yml` only makes this file CLASSIFIED;
        selection is what makes it RUN. Declaring `apps/` in `ROOTS` and stopping
        there is exactly the silent-drop class `#1349/#3332/#3616` name: the guard
        grows a subject whose diff selects no leg that runs it, so the regression it
        exists for merges GREEN and main goes red only on the next trunk run.

        MEASURED at the head this landed (#10254 review P2), over the files this scan
        actually reads (`_sources`, node_modules/dist pruned): 21 of the files under
        `ROOTS` carry their own `<style>` block — the set this guard can decide on —
        and 20 of those already selected this surface. `apps/graph-viz/index.html` was
        the only one that did not (`select([…]) -> surfaces=[], full=False`, guard ABSENT;
        `apps/` is in `NON_PYTHON_PREFIXES` and matched no `SOURCE_PATTERNS` row, so
        the diff filtered to `changed == []` and took the docs-only tier-1 early
        return). That page renders no `<code>`/`<pre>` today, which is why it had to
        be derived rather than noticed.

        The subject set is the own-`<style>` set, NOT the `<code>`-rendering set: a
        page becomes decidable the moment it renders the element, so ratcheting only
        the pages that render it today would leave the next one free to arrive
        unguarded. Derived from what the scan actually reads, so it cannot rot.
        """
        from tools.ci_selection import load_manifest, select

        repo = Path(__file__).resolve().parent.parent
        manifest = load_manifest()
        guard = Path(__file__).name
        missing = []
        decidable = 0
        # per-root census: a root that contributes ZERO is the shape the `ROOTS`
        # comment used to (wrongly) describe for `apps/`, and it is the shape that
        # makes a declared root decorative rather than a subject set (#10254 review,
        # round 12).
        by_root = {root: 0 for root in self.ROOTS}
        for path in sorted(self._sources(repo)):
            text = path.read_text(errors="replace")
            # same blanking order the guard uses: inert text is not a sheet
            if not self.STYLE.search(self.COMMENT.sub(" ", self.INERT.sub(" ", text))):
                continue
            decidable += 1
            rel = path.relative_to(repo).as_posix()
            by_root[Path(rel).parts[0]] += 1
            selection = select([rel], "pull_request", manifest)
            if guard not in selection["test_files"]:
                missing.append(f"{rel} (selected surfaces: {selection['surfaces'] or 'none'})")
        assert decidable >= self.MIN_DECIDABLE, (
            f"only {decidable} page(s) carry their own <style> under "
            f"{'/'.join(self.ROOTS)} — the scan is not looking at the right tree")
        # Every DECLARED root must actually contribute subjects. `apps/` carries 2 of
        # the 21 today (`apps/graph-viz/index.html`, `HierarchyChart.tsx`), which is the
        # fact the `ROOTS` comment states; if that ever drops to 0 the root is
        # decorative and the comment is stale again — move the floor and the comment
        # together instead of letting this assert be the only record (#10254 round 12).
        assert all(count for count in by_root.values()), (
            "a declared root carries no own-<style> subject: "
            f"{by_root!r} over ROOTS={'/'.join(self.ROOTS)} — dropping or emptying a "
            "root narrows the scan; if the narrowing is intended, move MIN_DECIDABLE "
            "and the ROOTS comment in the same edit")
        assert not missing, (
            "these pages carry their own <style> block, so this guard would DECIDE on "
            "them if they rendered `<code>`/`<pre>` — but editing one selects no leg "
            "that runs this guard, so the Courier regression (#6870) would merge green "
            "on exactly the diff that causes it. Add the path to "
            "SOURCE_PATTERNS['onboarding'] in tools/ci_selection.py, naming a CONCRETE "
            "path — select() matches with startswith and has no glob support:\n  "
            + "\n  ".join(missing))

    def test_a_var_fallback_is_dead_text_when_the_token_is_defined(self):
        """The resolver's own cases, pinned so the guard cannot silently un-fix (#10254 review).

        Probes, not pages: each `css` string is a whole sheet handed to the resolver, and the
        expectation is what CSS would compute for a `<code>` under it. The third and fourth rows
        are the false greens the review measured — a mono face named ONLY in a fallback that the
        browser never substitutes, because `--mono` is defined to a serif.
        """
        cases = [
            # (why, css, is `code` given a mono face?)
            ("defined serif, mono fallback — fallback is dead text (css-variables §3)",
             ":root{--mono:Georgia,serif} code{font-family:var(--mono, Menlo)}", False),
            ("same, quoted face)",
             ":root{--mono:Georgia,serif} code{font-family:var(--mono, 'SF Mono')}", False),
            ("same, the live dashboard idiom",
             ":root{--mono:Georgia,serif} "
             "code{font-family:var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace)}", False),
            ("defined mono, fallback unused",
             ":root{--mono:Menlo,monospace} code{font-family:var(--mono, Georgia)}", True),
            # Custom property NAMES are case-sensitive (css-variables-1 §2: "custom
            # property names are not ASCII case-insensitive … only equal to each other
            # if they are identical to each other"), so a reference differing in case is
            # the guaranteed-invalid value, not the token. These three were GREEN while
            # the resolver folded the name — and CSS computes exactly the #6870 face
            # (no fallback → invalid at computed-value time → inherit → UA `monospace`,
            # which is Courier on macOS Chrome). Pinned in BOTH directions, and only the
            # family VALUE is folded (`menlo` matches `Menlo`).
            ("a case-differing reference is the guaranteed-invalid value, NOT the token",
             ":root{--Mono:Menlo} code{font-family:var(--MONO)}", False),
            ("…the other way round: `--mono` declared, `--MONO` asked for",
             ":root{--mono:Menlo} code{font-family:var(--MONO)}", False),
            ("…and it is not rescued by an unrelated `--mono` elsewhere",
             ":root{--MONO:Menlo;--mono:Georgia,serif} code{font-family:var(--Mono)}", False),
            ("a case-differing reference falls back like any other missing token",
             ":root{--Mono:Menlo} code{font-family:var(--MONO, Menlo)}", True),
            ("the IDENTICAL name resolves — case is preserved, never folded away",
             ":root{--Mono:Menlo} code{font-family:var(--Mono)}", True),
            ("missing token — the fallback IS substituted",
             "code{font-family:var(--mono, Menlo)}", True),
            ("defined serif, no fallback to rescue it",
             ":root{--mono:Georgia,serif} code{font-family:var(--mono)}", False),
            ("token defined as an unresolvable var() → RED, never green",
             ":root{--mono:var(--nope)} code{font-family:var(--mono, Menlo)}", False),
            ("a later `font:` shorthand resets the family",
             ":root{--mono:Menlo} code{font-family:var(--mono)} code{font:12px Georgia}", False),
            ("`inherit` is the failing case, not a pass",
             "code{font-family:inherit}", False),
            ("bare `monospace` IS Courier on macOS Chrome — not a mono face here",
             "code{font-family:var(--mono, monospace)}", False),
            # `'SF Mono'` and `SFMono-Regular` are measured as Times on macOS Chrome in
            # this very file (`TestMonospaceStackRendersAsAMonoFace`, CDP on this repo's
            # Chromium) and #6870
            # records `'SF Mono'` as not exposed to web content. This route — a mono
            # name in a `var()` fallback — is the one the sibling declaration guard
            # cannot see, because it skips values containing `var(--mono`.
            ("`'SF Mono'` in a fallback is Times, not mono — #6870's own measurement",
             "code{font-family:var(--mono, 'SF Mono', 'Cascadia Code', monospace)}", False),
            ("`SFMono-Regular` on its own is Times too",
             "code{font-family:SFMono-Regular}", False),
            # A family list is ORDERED: the browser takes the first family it has
            # installed, so a mono face appended AFTER a family Chrome always reaches
            # is not the face the element gets. `monospace` IS Courier here, so these
            # are #6870 itself — and they were GREEN while `menlo` was matched
            # anywhere in the value (#10254 review).
            ("a mono face appended after the generic IS the #6870 face again",
             "code{font-family:monospace, Menlo}", False),
            ("the generic via the live dashboard's `var()` fallback shape",
             "code{font-family:var(--mono, ui-monospace, monospace, Menlo)}", False),
            ("`'SF Mono'`, the generic, then `Menlo` — the defect one keystroke late",
             "code{font-family:'SF Mono', monospace, Menlo}", False),
            ("a serif generic reached before the mono face",
             "code{font-family:serif, Menlo}", False),
            # …and the other direction, so the ordered rule cannot false-red what
            # ships: `ui-monospace` is a generic Chrome DROPS
            # (`TestMonospaceStackRendersAsAMonoFace`), so the
            # real stack — which carries it first — still resolves to `Menlo`.
            ("the shipped stack: `ui-monospace` first is not a Courier attractor",
             "code{font-family:ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace}",
             True),
            ("`Menlo` before the generic still wins — order, not presence",
             "code{font-family:Menlo, monospace}", True),
            ("a `font:` shorthand naming the mono face is still accepted",
             "code{font:italic bold 12px/1.4 Menlo}", True),
        ]
        for why, css, expect in cases:
            got = "code" in self._elements_styled_mono(css)
            assert got == expect, (
                f"{'false green' if got else 'false red'}: {why}\n  css: {css}\n"
                f"  guard says mono={got}, CSS computes mono={expect}")

    def test_a_scoped_token_is_credited_and_that_is_a_documented_limit(self):
        """The one fail-GREEN shape this guard documents instead of fixing (#10254 r9).

        `VAR_DEF` collects `--x: …` from every rule whatever selector declares it, and
        `_elements_styled_mono` resolves any `var(--x)` against that flat map. A custom
        property INHERITS (css-variables-1, the `--*` property table: `Inherited: yes`),
        so a token declared on `.art-body` does not reach a `<code>` outside it: the
        reference there is the guaranteed-invalid value, the declaration is invalid at
        computed-value time, and the element renders its PARENT's family — not the mono
        face this guard credits.

        This is the opposite direction from the two residuals the class docstring
        states, which both fail RED. It stays a documented LIMIT because keying
        `defined` by declaring selector means modelling which ancestors an element has;
        and it is latent today — eight of the nine subjects define `--mono` in `:root`,
        and the ninth (`website/apps/dashboard/public/404.html`) names the family list
        directly in its `code{}` rule (`:41-42`) and resolves no `var()` at all, so this
        limit has no token there to mis-resolve. Eight, not nine, at this head (#10254
        review, round 11). The assertion is written to RED the moment the limit is
        closed, so closing it is a deliberate edit and this probe is deleted with the
        docstring bullet, rather than the shape being re-derived as a finding for the
        fourth time.
        """
        probes = [
            # (why, css) — each is GREEN here; the CSS computes no mono face for `code`
            ("token declared on a scoped ancestor rule",
             ".art-body{--mono:Menlo} code{font-family:var(--mono)}"),
            ("token declared inside a conditional group",
             "@media print{:root{--mono:Menlo}} code{font-family:var(--mono)}"),
        ]
        for why, css in probes:
            assert "code" in self._elements_styled_mono(css), (
                f"the documented declaring-scope limit CHANGED ({why}): the guard now "
                f"REFUSES a token whose declaring selector it cannot prove reaches the "
                f"element. That is the fail-safe direction, so update the "
                f"`WHAT IT DOES NOT PIN` bullet and delete this probe.\n  css: {css}")

    #: selectors that reach EVERY element, so a token declared on one is not scoped:
    #: `:root` is the html element (all elements inherit from it), `html` is itself, and
    #: `*` matches everything. Only the third has real cascade caveats, and none of them
    #: can leave an element without the token, which is what this ratchet asks.
    DOCUMENT_WIDE_SELECTORS = frozenset({":root", "html", "*"})

    @classmethod
    def _scoped_family_tokens(cls, css):
        """Tokens a family value asks for that are declared only on a SCOPED rule.

        The documented fail-GREEN limit is `VAR_DEF` collecting a custom property from
        any rule whatever its selector. It is a limit and not a bug because a token on
        `:root` reaches every element (custom properties inherit, css-variables-1; MDN
        `--*`: "custom properties are scoped to the element(s) they are declared on",
        `Inherited: yes`), so the flat map and a browser agree on every page in this
        tree. This helper is the mechanical form of that sentence: it returns
        `[(token, declaring_selectors)]` for every token a `font-family`/`font` value
        references whose declaring rules are NOT document-wide. Non-empty = the limit is
        LIVE on that sheet, i.e. the class docstring's "latent, not live" no longer
        holds and the flat map can credit a face the element never computes.
        """
        declared = {}
        for selectors, body in cls.RULE.findall(css):
            for name, _value in cls.VAR_DEF.findall(body):
                declared.setdefault(name.strip(), set()).update(
                    s.strip() for s in selectors.split(",") if s.strip())
        referenced = set()
        for _selectors, body in cls.RULE.findall(css):
            for family in cls.FAMILY.findall(body):
                referenced.update(t.strip() for t in cls.VAR_REF.findall(family))
        return [
            (token, sorted(declared[token])) for token in sorted(referenced)
            # a token declared nowhere is a different defect (the guard refuses it, fail
            # closed), and it is not this one: there is no scope to be wrong about.
            if token in declared and not declared[token] <= cls.DOCUMENT_WIDE_SELECTORS
        ]

    def test_the_scoped_token_limit_is_latent_on_every_page_it_decides_on(self):
        """Every `var()` family on a page it DECIDES on resolves a document-wide token.

        The class docstring justifies leaving the declaring-scope limit unmodelled with a
        MEASURED claim — "latent, not live" — and that claim was wrong in the tree twice
        while it was hand-written prose: round 10 and round 11 both re-measured "all nine
        subjects define `--mono` in `:root`" as EIGHT of nine
        (`website/apps/dashboard/public/404.html` names the family list directly and
        resolves no `var()`, so the limit cannot be tripped there). A count in a comment
        is not a guard; this is. It REDs the moment a page hands an element a family
        through a token declared on a scoped selector, which is the day the limit stops
        being latent — and the repair is then either moving the token to `:root` or
        stating the limit as LIVE.
        """
        repo = Path(__file__).resolve().parent.parent
        offenders, checked = [], 0
        for path in self._sources(repo):
            text = path.read_text(errors="replace")
            if not self.RENDERS_CODE.search(text):
                continue
            css = "\n".join(self.STYLE.findall(
                self.COMMENT.sub(" ", self.INERT.sub(" ", text))))
            if not css.strip():
                continue
            checked += 1
            for token, scopes in self._scoped_family_tokens(css):
                offenders.append(
                    f"{path.relative_to(repo)}: {token} declared on "
                    f"{', '.join(scopes)} — a family value asks for it")
        assert checked >= self.MIN_PAGES, (
            f"only {checked} self-contained page(s) found under "
            f"{'/'.join(self.ROOTS)} — the scan is not looking at the right tree")
        assert not offenders, (
            "the documented declaring-scope limit is no longer LATENT — these pages "
            "feed a `code`/`pre` family through a token declared only on a scoped rule, "
            "so the guard credits a face the browser never computes for the element "
            "(custom properties inherit: a token on `.art-body` does not reach a "
            "`<code>` outside it, so the reference is the guaranteed-invalid value and "
            "the whole declaration is invalid at computed-value time):\n  "
            + "\n  ".join(offenders)
            + "\nMove the token to `:root` (or another document-wide selector), or "
              "state the limit as LIVE in the class docstring and extend "
              "`test_a_scoped_token_is_credited_and_that_is_a_documented_limit` — "
              "closing it silently is the one thing this file forbids.")

    def test_the_scoped_token_predicate_separates_the_three_shapes(self):
        """Non-vacuity for the ratchet above: it must RED the scoped shape and only it.

        Without this, `_scoped_family_tokens` returning `[]` on the real tree is
        indistinguishable from a predicate that returns `[]` always — the failure mode
        the sibling class records for a guard whose subject went missing.
        """
        assert self._scoped_family_tokens(
            ":root{--mono:Menlo} code{font-family:var(--mono)}") == []
        assert self._scoped_family_tokens(
            "code{font-family:Menlo}") == []          # the ninth subject's shape
        scoped = self._scoped_family_tokens(
            ".art-body{--mono:Menlo} code{font-family:var(--mono)}")
        assert scoped == [("--mono", [".art-body"])], (
            "the ratchet above no longer recognises the documented limit's own shape — "
            f"its clean verdict on the real tree means nothing. Got {scoped!r}")
        # and the `var()`-under-another-name route, which the flat map covers equally
        assert self._scoped_family_tokens(
            "main{--code-font:Menlo} code{font-family:var(--code-font)}"
        ) == [("--code-font", ["main"])]

    def test_a_nested_var_is_resolved_the_way_the_browser_resolves_it(self):
        """A `var()` inside a fallback, and an unresolvable reference in a list (#10254 r6).

        The fallback of a `var()` is *itself substituted* by css-variables-1 §"replace a
        var()" step 3, so `var(--mono, var(--brand, Menlo))` computes to whatever
        `--brand` is. Reading the fallback as flat text — the reference regex stopped at
        the first `)`, so it never saw `Menlo` as living inside another `var()` — credited
        a **serif** as a mono face: the round-6 review's false green, reproduced against
        this class's own logic before it was fixed.

        The last two rows are the other direction of the same mistake: a reference that
        resolves to nothing invalidates the **whole** declaration at computed-value time,
        and CSS does not then fall through to the next family in the list — so a trailing
        `Menlo` written after an unresolvable `var()` is not a face the element gets.
        """
        cases = [
            # (why, css, is `code` given a mono face?)
            ("a serif inside the fallback's own var() — the round-6 false green",
             ":root{--brand:Georgia,serif} "
             "code{font-family:var(--mono, var(--brand, Menlo))}", False),
            ("the same shape, inner token missing → the browser uses its fallback",
             "code{font-family:var(--mono, var(--brand, Menlo))}", True),
            ("the same shape, `--mono` defined → the outer fallback is dead text",
             ":root{--mono:Menlo} code{font-family:var(--mono, var(--brand, Menlo))}", True),
            ("the same shape, `--mono` defined as a serif",
             ":root{--mono:Georgia,serif} "
             "code{font-family:var(--mono, var(--brand, Menlo))}", False),
            ("an unresolvable `var()` invalidates the whole list, not just its slot",
             "code{font-family:var(--mono), Menlo}", False),
            ("a token whose own value carries an unresolvable var() is guaranteed-invalid",
             ":root{--mono:Menlo,var(--nope)} code{font-family:var(--mono)}", False),
            # the invalid signal has to survive the recursion: returning empty *text*
            # appended `Menlo,` for the next family, so a declaration CSS invalidates
            # whole read as a mono face (round 8's false green)
            ("an invalid token voids the declaration around it — the trailing face is dead",
             ":root{--mono:var(--nope)} code{font-family:Menlo, var(--mono)}", False),
            ("the same, leading face first",
             ":root{--mono:var(--nope)} code{font-family:var(--mono), Menlo}", False),
            ("an unbalanced var( is a parse error, so no family in the value applies",
             "code{font-family:Menlo, var(--mono}", False),
        ]
        for why, css, expect in cases:
            got = "code" in self._elements_styled_mono(css)
            assert got == expect, (
                f"{'false green' if got else 'false red'}: {why}\n  css: {css}\n"
                f"  guard says mono={got}, CSS computes mono={expect}")

    #: the self-contained emitters this guard's `ROOTS` does NOT reach, each carrying the
    #: `<style>` block that makes it a page the sibling declaration guard scans.
    OUT_OF_ROOT_EMITTERS = ("tortoise/oauth.py", "tortoise/render.py")

    def test_the_out_of_root_emitters_render_no_code(self):
        """The scope limit is stated, so its precondition is pinned instead of assumed.

        `Scope` above names two self-contained emitters that carry their own sheet but
        sit outside `ROOTS` (`tortoise/`, not `website/` + `apps/`), and that sentence is
        only true while neither renders a `<code>`/`<pre>`: an element there is one this
        scan cannot see, so the Courier defect (#6870) would have no guard on that
        surface at all. A narrow, deliberate exclusion is exactly the kind of claim that
        rots silently, so the precondition is measured here rather than trusted.

        Non-vacuity: the same predicate is applied first to the pages INSIDE `ROOTS`, and
        must find at least `MIN_PAGES` of them — a predicate that had stopped seeing
        elements would otherwise pass this test by finding nothing anywhere.

        The exclusion itself is pinned by the two properties that actually make these
        emitters invisible to this scan, each with the mutation that reds it. Asserted
        over `inside` instead — the shape this test shipped with — it could never fire:
        `inside` is derived from `_sources`, i.e. from `ROOTS`, so no member of it can be
        a path outside `ROOTS`, and an assertion no edit can falsify pins nothing
        (#10254 review P3). Being outside `ROOTS` is one half; the other is that `.py` is
        not in `SUFFIXES`, so the walk would not read them even under a widened root.
        """
        repo = Path(__file__).resolve().parent.parent

        def _visible(rel):
            text = (repo / rel).read_text(errors="replace")
            return self.COMMENT.sub(" ", self.INERT.sub(" ", text))

        scanned = {p.relative_to(repo).as_posix() for p in self._sources(repo)}
        inside = [rel for rel in sorted(scanned) if self.RENDERS_CODE.search(_visible(rel))]
        assert len(inside) >= self.MIN_PAGES, (
            "this predicate finds no element even on the pages this guard scans "
            f"({inside}) — it is not measuring what it claims")

        under_root = [rel for rel in self.OUT_OF_ROOT_EMITTERS
                      if rel.split("/")[0] in self.ROOTS]
        assert not under_root, (
            f"{under_root} now sit under a `ROOTS` entry ({self.ROOTS}) — the scope note "
            "in this class says they are outside it, so either narrow `ROOTS` back or "
            "adopt the emitter the way the message below describes")
        readable = [rel for rel in self.OUT_OF_ROOT_EMITTERS
                    if Path(rel).suffix in self.SUFFIXES]
        assert not readable, (
            f"{readable} carry a suffix this scan reads ({self.SUFFIXES}) — the suffix "
            "set is half of why these emitters are invisible here, so adopting `.py` "
            "makes the scope note's justification false even while `ROOTS` is unchanged")
        reachable = [rel for rel in self.OUT_OF_ROOT_EMITTERS if rel in scanned]
        assert not reachable, (
            f"{reachable} are yielded by `_sources` — this scan reads a page the scope "
            "note claims it cannot, so the exclusion is no longer deliberate")

        grew = []
        for rel in self.OUT_OF_ROOT_EMITTERS:
            text = _visible(rel)
            assert self.STYLE.search(text), (
                f"{rel} no longer carries its own `<style>` block — the scope note in "
                "this class names it as a self-contained page, so either the note or "
                "this list is now stale")
            if self.RENDERS_CODE.search(text):
                grew.append(rel)
        assert not grew, (
            "these self-contained emitters sit OUTSIDE this guard's ROOTS and have grown "
            "a `<code>`/`<pre>` element — that element has no mono rule this scan can "
            "see, which is the #6870 defect on a surface with no guard. Either give the "
            "element a mono family inside the emitter, or declare the file in `ROOTS` "
            "and extend the ratchet, so the exclusion stays deliberate:\n  "
            + "\n  ".join(grew))


class TestSiteIconAndSocialPreviewContract:
    """Every page must carry a real icon and a real preview image (PR #11092 era).

    THE DEFECT THIS PINS. Both live origins answered `/favicon.ico` with **404**,
    and the two pages that matter most (`index.html` for premiselabs.co,
    `product.html` for tortoise.premiselabs.co — the host that serves it at `/`)
    declared **no icon at all and no `og:image`**: a browser showed a blank tab,
    and a link pasted into Slack/X/WhatsApp rendered as a bare title with no
    card. Five other pages did declare an icon — `/logo.png`, a 400x244 WORDMARK
    — which a tab renders as a squashed bar, so "there is a `rel=icon`" was
    never the property that mattered.

    So the contract is stated in the three parts a human actually sees, and each
    part is checked against the file on disk rather than against the tag:

    1. **The icon resolves.** `/favicon.ico`, `/favicon.png` and
       `/apple-touch-icon.png` exist in the served tree, the PNG is SQUARE and
       at least 180px (the apple-touch size), and the ICO is a real icon
       container holding an embedded PNG — not a renamed image.
    2. **The preview resolves and is the size it claims.** `og:image` maps to a
       file in the tree, and `og:image:width`/`height` match the JPEG's actual
       SOF header. A card whose declared size disagrees with the bytes is
       cropped by the scraper that trusts the tag.
    3. **No page re-uses the wordmark as an icon.** `/logo.png` is a wordmark;
       it may appear in page content, never in a `rel=icon`.

    The dimensions are read with `struct`/`re` over the SVG-free formats we
    ship (PNG IHDR, JPEG SOF, ICO directory) — stdlib only, no imaging library,
    so the guard runs in the fast lane.

    ⛔ WHAT THIS CAN SEE ABOUT CONTENT, AND WHAT IT STILL CANNOT. Every icon is
    DECODED (`_png_palette`) — `favicon.png`, `apple-touch-icon.png`, and the PNG
    payloads inside `favicon.ico` (the container Windows and Edge actually draw) — and
    must hold more than one colour with no colour above 95% of the canvas, so a flat
    rectangle in place of a mark reds. Two colours is legitimate art and passes; the
    measured placeholder this PR deletes reds at 99.2% one colour. The card is
    a JPEG, which this file cannot decode without dragging a wheel into the fast
    lane, so it is bounded by ENCODED SIZE (>= 40 KB) — a proxy, with its
    measurements written beside it, and it does reject a solid 1200x630 JPEG (22,016
    bytes measured).
    What remains unseen is whether the card DEPICTS the right thing: a 60 KB
    photograph of something unrelated passes. The blog's own head is emitted by
    `website/functions/blog/_lib.ts` and is guarded by `tests/test_blog_seo_meta.mjs`,
    which executes the real renderer.
    """

    WEB = REPO_ROOT / "website"
    ICON_ICO = "favicon.ico"
    ICON_PNG = "favicon.png"
    ICON_APPLE = "apple-touch-icon.png"
    # Declared once: every page's preview image is 1200x630 (the 1.91:1 the
    # scrapers crop to), and the two bespoke pages below ship their own.
    OG_SIZE = (1200, 630)
    # The two hosts this site is served from. Declared once because every page's
    # preview URL is checked against it — see the og:image assertions.
    SITE_ORIGINS = frozenset({"premiselabs.co", "tortoise.premiselabs.co"})
    # The base an icon href is resolved against before it is compared with a file's
    # path. Since epic #11762 there is ONE marketing host: `tortoise.premiselabs.co`
    # is a permanent 301 to it, so a page is never served FROM that host and no
    # document-relative href can mean it. This must be a member of `SITE_ORIGINS`
    # (every shipped icon href is host-relative, so membership is what passes).
    SITE_ROOT = "https://premiselabs.co/"
    # The retired product host. It is KEPT as a permanent 301 — the hostname is
    # never deleted — so `SITE_ORIGINS` above deliberately still holds it: the
    # asset tree (`/assets/*.jpg`, `/skills/*.skill`, the icons) really is served
    # from both, and narrowing the set would red honest asset URLs. What may NOT
    # name it is a PAGE: a canonical, an `og:url`, an `og:image` or a link into a
    # page that moved. That is asserted positively, per page, by
    # `TestProductSetIsServedUnderTheOneHost`, rather than inferred from a
    # membership set that also has to describe assets.
    RETIRED_PRODUCT_HOST = "tortoise.premiselabs.co"
    # The encoded-size floor a link-preview card must clear. MEASURED, not guessed:
    # a FLAT 1200x630 JPEG at maximum quality is 22,016 B (`sips -s formatOptions
    # best`), so the first cut's 20 KB floor passed the very image it was written to
    # reject. The floor is enforced on EVERY page's declared card, and the four cards
    # at this head are 94,066 B (og-tortoise.jpg), 49,555 B (investors-og.jpg),
    # 50,393 B (design-partners-intro-og.jpg) and 61,103 B (product-teams-og.jpg) —
    # i.e. 2.35x, 1.24x, 1.26x and 1.53x the floor, not 2.35x each. Re-encoding the
    # SAME pixels is what eats the margin: with
    # `ffmpeg -q:v 4` the two smaller cards measure 37,024 B and 39,818 B, both under
    # the floor. That is the one false red this bound can produce, which is why
    # `_card_floor_violation`'s message tells the reader to LOOK at the card first.
    # `test_the_content_guards_reject_a_blank_image_and_keep_their_measured_floors`
    # holds this floor ABOVE the flat measurement, so lowering it to 20 KB again is red.
    CARD_MIN_BYTES = 40_000
    # What a flat 1200x630 JPEG measures — the image CARD_MIN_BYTES exists to reject.
    # Recorded as a number so the relationship is ASSERTED (`FLAT_CARD_BYTES <
    # CARD_MIN_BYTES`) rather than left implicit in a comment.
    FLAT_CARD_BYTES = 22_016
    # A decoded icon must hold at least this many distinct colours — TWO, because a
    # hard-edged two-colour mark is legitimate art (pixel art, a `crispEdges` SVG
    # export, anything without antialiasing) and a guard that reds honest artwork is a
    # guard the next person weakens instead of fixes. This was 8, and the comment
    # beside the check claimed "a two-colour logo would still pass at 8" — it could
    # not, 2 < 8, so the sentence asserted the opposite of what the code did. What
    # rejects the degenerate case is the PAIR: one flat colour trips this floor AND
    # the dominant-colour floor below. Neither alone is enough to describe a mark.
    ICON_MIN_COLOURS = 2
    # How much of the canvas the mark must span. The artwork's own padding put it at
    # 68% (#11318 shipped that straight downscale) and in a tab strip it read as a
    # small glyph on a slab; `tools/build_site_icons.py` crops it to 84%. 80% is the
    # floor, not the target: it is the point at which the mark stops reading as small
    # next to a neighbour whose glyph fills its tile, and it sits above 68% so a
    # re-export of the unpadded artwork reds instead of quietly undoing this.
    MARK_WIDTH_FLOOR = 0.80
    # The tile's corner pixels. 0 = the tab background shows through the rounded corner;
    # apple-touch-icon is the exception and must stay opaque (iOS masks it itself).
    ROUNDED_CORNER_ALPHA = 0
    # Transparent corners alone do not make a rounded tile — four punched pixels would
    # pass. This is how far along each edge the first fully-opaque pixel must sit, as a
    # fraction of the side: an arc of radius r reaches an edge at exactly r, so with the
    # builder's 21% this measures 21%, while a nibbled square measures under 1%.
    CORNER_INSET_FLOOR = 0.10
    # ...and no single colour may cover more than this fraction of its canvas.
    ICON_MAX_DOMINANT = 0.95

    # --- format readers -------------------------------------------------

    @staticmethod
    def _png_size(data: bytes) -> tuple[int, int]:
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        return struct.unpack(">II", data[16:24])

    @staticmethod
    def _png_pixels(data: bytes) -> tuple[int, int, list[list[tuple[int, ...]]]]:
        """Decode an 8-bit PNG to (width, height, rows of pixel tuples) — stdlib only.

        ⛔ WHY A DECODER LIVES IN A TEST. `/blog/og-image.png` was a 4.6 KB
        placeholder: two colours, 99.2% of the canvas flat background, a bare accent
        rule, no mark and no text. Every dimension assertion in this file passed on
        it, because a 1200x630 rectangle IS 1200x630. A card (or an icon) that renders
        as an empty block is the failure these guards exist to prevent, and only the
        pixels can tell it apart from art. No imaging library is in the dev env, and
        adding one for a guard would make the fast lane depend on a wheel — so this
        decodes 8-bit PNGs (the only depth this repo writes) directly.

        ⛔ ONE DECODER, TWO CONSUMERS. `_png_palette` counts colours and the rounded-tile
        guard reads GEOMETRY and ALPHA out of the same rows. A second decoder for the
        second question is how the two drift, and this file already carries the cost of
        one reader being right in one place and wrong in another (the source-order meta
        readers). Alpha stays in the tuple, so a transparent pixel is its own colour: a
        decoder that silently dropped the alpha channel reds the colour floor rather
        than passing a blank tile.
        """
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        pos = 8
        width = height = 0
        depth = colour = 0
        idat = b""
        palette: list[tuple[int, ...]] = []
        while pos < len(data):
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            kind = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            pos += 12 + length
            if kind == b"IHDR":
                width, height, depth, colour = struct.unpack(">IIBB", body[:10])
                assert body[12] == 0, "interlaced PNG — not the shape this repo writes"
            elif kind == b"PLTE":
                palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
            elif kind == b"IDAT":
                idat += body
            elif kind == b"IEND":
                break
        assert depth == 8, f"bit depth {depth} — this decoder reads 8-bit PNGs"
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]

        def paeth(a: int, b: int, c: int) -> int:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                return a
            return b if pb <= pc else c

        stride = width * channels
        # ⛔ BOUNDED, because an unbounded `zlib.decompress` inflates whatever a
        # checked-in IDAT expands to (zlib reaches ~1000:1), so a crafted icon could
        # allocate without limit in CI. The IHDR already states the exact size — one
        # filter byte per row plus `width * channels` — so the bound is known before a
        # byte is inflated, and a stream that overruns it is a malformed PNG rather
        # than something to keep decoding.
        expected = height * (stride + 1)
        stream = zlib.decompressobj()
        raw = stream.decompress(idat, expected + 1)
        assert len(raw) == expected and not stream.unconsumed_tail, (
            f"PNG image data does not expand to the {expected} B its IHDR geometry "
            f"({width}x{height}, {channels} channel(s)) declares — refusing to decode it")
        prev = bytearray(stride)
        rows: list[list[tuple[int, ...]]] = []
        off = 0
        for _ in range(height):
            ftype = raw[off]
            off += 1
            line = bytearray(raw[off:off + stride])
            off += stride
            if ftype:
                for i in range(stride):
                    a = line[i - channels] if i >= channels else 0
                    b = prev[i]
                    c = prev[i - channels] if i >= channels else 0
                    if ftype == 1:
                        line[i] = (line[i] + a) & 0xFF
                    elif ftype == 2:
                        line[i] = (line[i] + b) & 0xFF
                    elif ftype == 3:
                        line[i] = (line[i] + (a + b) // 2) & 0xFF
                    elif ftype == 4:
                        line[i] = (line[i] + paeth(a, b, c)) & 0xFF
                    else:
                        raise AssertionError(f"bad PNG filter {ftype}")
            prev = line
            row: list[tuple[int, ...]] = []
            for x in range(0, stride, channels):
                px = tuple(line[x:x + channels])
                row.append(palette[px[0]] if colour == 3 else px)
            rows.append(row)
        return width, height, rows

    @classmethod
    def _png_palette(cls, data: bytes) -> dict[tuple[int, ...], int]:
        """How many pixels of each colour a PNG really holds."""
        _w, _h, rows = cls._png_pixels(data)
        counts: dict[tuple[int, ...], int] = {}
        for row in rows:
            for px in row:
                counts[px] = counts.get(px, 0) + 1
        return counts

    @staticmethod
    def _flat_png(size: int = 256,
                  rgba: tuple[int, int, int, int] = (0x06, 0x0B, 0x14, 0xFF)) -> bytes:
        """A one-colour 8-bit RGBA PNG — the degenerate icon, built here.

        ⛔ WHY A SYNTHETIC IMAGE IS IN THIS FILE. The pixel floor below is worth
        something only if it REJECTS, and the file it was written for
        (`/blog/og-image.png`, the deleted two-colour placeholder) is gone. This is
        that shape rebuilt in memory, so the guard's FAILURE path runs on every CI
        run instead of surviving as a spot check recorded in a PR body.
        """
        ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
        raw = (b"\x00" + bytes(rgba) * size) * size      # filter byte + one pixel/column

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (struct.pack(">I", len(body)) + kind + body
                    + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    @staticmethod
    def _two_colour_png(size: int = 256, band: int = 20) -> bytes:
        """A two-colour, hard-edged mark — the artwork the colour floor must NOT red.

        ⛔ WHY THIS FIXTURE EXISTS. The floor beside it started at 8 distinct colours,
        and its own comment claimed "a two-colour logo would still pass at 8" — it
        could not, 2 < 8, so a `crispEdges`/pixel-art mark (no antialiasing, exactly
        the art a designer might choose) reded with "that is a flat rectangle, not a
        mark". Lowering the floor to 2 fixed that, but nothing MEASURED the fix: the
        only icons in the tree decode to ~200+ colours, so pushing the floor back to
        8 left every assertion green. With this fixture it reds.

        20 rows of accent over 256 is 7.8% of the canvas, i.e. dominant 92.2% — under
        `ICON_MAX_DOMINANT` on purpose, so the fixture isolates the COLOUR-COUNT floor
        from the dominant one.
        """
        ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
        accent, background = (0x06, 0xB6, 0xD4, 0xFF), (0x06, 0x0B, 0x14, 0xFF)
        raw = b"".join(
            b"\x00" + (bytes(accent) if y < band else bytes(background)) * size
            for y in range(size))

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (struct.pack(">I", len(body)) + kind + body
                    + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    @classmethod
    def _icon_mark_violations(cls, name: str, data: bytes, *,
                              in_tree: bool = True) -> list[str]:
        """Every reason a decoded icon is not a mark; empty means it carries art.

        Split out of the guard so the SAME predicate can be run against a blank
        fixture (see `test_the_content_guards_reject_a_blank_image_and_keep_their_
        measured_floors`). A check whose failure path is never executed cannot be
        told apart from a no-op.

        `name` is a BARE name of a file in the served tree: the `website/` prefix is
        added here, so a caller that passes one produces `website/website/…` in the
        only message that matters. A SYNTHETIC fixture is not in the tree, so it
        passes `in_tree=False` — otherwise its message names a path that does not
        exist, and the reader is sent looking for a file that was never there.
        """
        counts = cls._png_palette(data)
        pixels = sum(counts.values())
        where = f"website/{name}" if in_tree else name
        problems: list[str] = []
        if len(counts) < cls.ICON_MIN_COLOURS:
            problems.append(
                f"{where} holds {len(counts)} distinct colour(s) over "
                f"{pixels} pixels — that is a flat rectangle, not a mark; every size "
                "assertion would pass on it")
        dominant = max(counts.values()) / pixels
        if dominant >= cls.ICON_MAX_DOMINANT:
            problems.append(
                f"{where} is {dominant:.1%} a single colour — a mark needs to "
                "carry something the background does not")
        return problems

    @classmethod
    def _og_asset_path(cls, image: str) -> Path:
        """The file a declared `og:image` URL resolves to in the served tree.

        The QUERY is stripped before the URL becomes a path: `…/og-tortoise.jpg?v=2`
        is a legitimate cache-bust that changes no file, and a guard that keeps it
        reports "not in the served tree" about a page that is correct. The ORIGIN is
        deliberately dropped rather than matched here — the same tree is served from
        two hosts, and that is a separate assertion (`SITE_ORIGINS`).
        """
        return cls.WEB / urlsplit(image).path.lstrip("/")

    @classmethod
    def _og_card_bytes(cls, image: str) -> tuple[Path | None, bytes]:
        """The file a declared card URL resolves to, and its bytes; `(None, b"")` if absent.

        The default `load` for `_card_violations`: existence and the bytes come from the
        served tree, through `_og_asset_path` (so a `?v=2` cache-bust still resolves).
        """
        path = cls._og_asset_path(image)
        return (path, path.read_bytes()) if path.is_file() else (None, b"")

    @staticmethod
    def _filtered_png() -> tuple[bytes, dict[tuple[int, ...], int], list[int]]:
        """A 5-row RGB PNG whose rows use filter types 0–4, and the histogram it encodes.

        ⛔ WHY THIS FIXTURE EXISTS. Both fixtures around it emit filter type 0
        (`b"\x00" + row`), so Sub/Up/Average/Paeth reconstruction — the branch every
        REAL icon in this tree takes (measured: favicon.png rows use filters 1,2,3,4) —
        was executed by no test. The failure direction is why that matters: a wrong
        predictor invents extra colours AND lowers the dominant share, i.e. it satisfies
        both content floors, so a broken decoder would read noise as art while every
        assertion stayed green.

        The rows are a gradient, not a flat fill, because a flat row makes Sub, Up,
        Average and Paeth degenerate to the same output and the fixture would prove
        nothing about any of them.

        Returns `(png, expected histogram, filter types the stream uses)` — the filter
        list is returned so a future edit cannot quietly collapse it back to type 0 and
        leave this test passing on the untested path again.
        """
        width, height = 8, 5
        pixels = [
            [((x * 37 + y * 13) & 0xFF, (x * 53 + y * 29) & 0xFF, (x * 71 + y * 17) & 0xFF)
             for x in range(width)]
            for y in range(height)
        ]

        def paeth(a: int, b: int, c: int) -> int:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                return a
            return b if pb <= pc else c

        body = bytearray()
        prev = [0] * (width * 3)
        filters: list[int] = []
        for y, row in enumerate(pixels):
            flat = [c for px in row for c in px]
            ftype = y % 5
            filters.append(ftype)
            encoded = bytearray()
            for i, value in enumerate(flat):
                a = flat[i - 3] if i >= 3 else 0
                b = prev[i]
                c = prev[i - 3] if i >= 3 else 0
                predictor = (0, a, b, (a + b) // 2, paeth(a, b, c))[ftype]
                encoded.append((value - predictor) & 0xFF)
            body += bytes([ftype]) + bytes(encoded)
            prev = flat

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

        expected: dict[tuple[int, ...], int] = {}
        for row in pixels:
            for px in row:
                expected[px] = expected.get(px, 0) + 1
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(bytes(body))) + chunk(b"IEND", b""),
                expected, filters)

    @staticmethod
    def _jpeg_stream_length(data: bytes) -> int:
        """The bytes up to the EOI that ENDS THE SCAN — not the last `FFD9` in the file.

        ⛔ A PADDED FILE IS NOT A CARD, and file length cannot tell: a flat 1200x630
        JPEG is 4,723 B, and the SAME image padded with ~36 KB of trailing zeros cleared
        a 40 KB floor with every other assertion — dimensions, type, magic bytes — still
        green.

        ⛔ AND `rfind(b"\\xff\\xd9")` CANNOT TELL EITHER. Byte-stuffing makes FFD9
        unambiguous only INSIDE an entropy-coded scan (`FF` is always followed by `00` or
        a restart marker there); bytes appended AFTER the image are inside no stream, so
        padding closed with a two-byte trailer measured as if it had been painted on —
        measured: 4,723 B + zeros + `FF D9` = 41,500 B and the floor cleared. So the walk
        follows the segment chain from SOI to SOS and then the scan itself, and stops at
        the FIRST EOI it meets. A real card's stream ends there; anything after it is
        padding and does not count.

        Residual, accepted and named: an in-band metadata block (an EXIF thumbnail, a
        large ICC packet) sits BEFORE the EOI and still counts toward the floor. That is
        the residual the floor has always had — it bounds encoded bulk, never whether the
        card DEPICTS the right thing — and closing it would mean decoding the JPEG, which
        this fast-lane guard deliberately does not do.
        """
        n = len(data)
        i = 2
        while i + 4 <= n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker == 0xD9:  # EOI with no scan (header-only or empty image)
                return i + 2
            if marker == 0xDA:  # SOS: entropy-coded data follows
                j = i + 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
                while j + 1 < n:
                    if data[j] == 0xFF:
                        nxt = data[j + 1]
                        if nxt == 0xD9:
                            return j + 2
                        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:  # stuffing / restart
                            j += 2
                            continue
                    j += 1
                return n
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        return n

    @staticmethod
    def _png_stream_length(data: bytes) -> int:
        """The bytes up to the end of the IEND chunk — a PNG's image IS its chunks.

        The JPEG walk above must never be applied to a PNG: `FF D9` inside IDAT is
        ordinary compressed data, so a card would be measured by a random interior byte
        pair. Both directions were measured on real bytes: a flat PNG padded past 40 KB
        measured 41,000 under a JPEG-only measure (a blank card clearing the floor), and
        `website/logo.png` — 35,670 B, whose IDAT does contain `FF D9` — measured 29,390
        under the superseded `rfind` measure, i.e. 6,280 B short of a legitimate card.
        The padded-PNG fixture in `test_the_content_guards_reject_a_blank_image_and_keep_
        their_measured_floors` is what catches a dispatch regression; the `logo.png` pin
        beside it asserts the PNG path still measures a real PNG at its full length.
        """
        n = len(data)
        pos = 8
        while pos + 8 <= n:
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            kind = data[pos + 4:pos + 8]
            if kind == b"IEND":
                return min(pos + 12 + length, n)
            if length > n:  # malformed length: do not walk off the end
                return n
            pos += 12 + length
        return n

    @classmethod
    def _encoded_length(cls, data: bytes) -> int:
        """How many bytes of this card are actually IMAGE, dispatched on its magic.

        A format with no reader here is measured as its file length — the only honest
        statement the guard can make without parsing it, and the floor is a bulk check,
        not a format check (`_card_size` owns the declared-type dispatch).
        """
        if data[:2] == b"\xff\xd8":
            return cls._jpeg_stream_length(data)
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return cls._png_stream_length(data)
        return len(data)

    @classmethod
    def _card_floor_violation(cls, page: str, path: Path, data: bytes) -> str | None:
        """Why an encoded card is too small to BE a card — None when it clears."""
        encoded = cls._encoded_length(data)
        if encoded >= cls.CARD_MIN_BYTES:
            return None
        return (f"{page}'s card {path.name} holds only {encoded} bytes of encoded image "
                f"({len(data)} B on disk), under the "
                f"{cls.CARD_MIN_BYTES // 1000} KB floor — a flat 1200x630 card of one "
                f"colour measures {cls.FLAT_CARD_BYTES // 1000} KB, so a card this "
                "small is usually near-uniform and a link preview of it is an empty "
                "block. If this card is REAL, open it: a legitimate re-encode can land "
                "here (the smallest real card is 49,555 B; re-saving it with "
                "`ffmpeg -q:v 4` measures 37,024 B). Then lower CARD_MIN_BYTES in "
                "tests/test_website_static.py and record what you measured.")

    @classmethod
    def _card_size(cls, declared_type: str, raw: bytes) -> tuple[int, int]:
        """The card's real pixel size, read with the reader the page's TYPE names.

        Split out of the guard so BOTH dispatch branches run against real bytes (see
        `test_the_card_type_dispatch_reads_the_format_the_page_names`): every page in
        this tree declares `og:image:type: image/jpeg`, and the only PNG card the repo
        ever had is deleted by this PR, so without that fixture the `image/png` branch
        is executed by nothing and a JPEG-only dispatch reads green.

        The declared type is the page's own statement about its card, so it is taken at
        its word and the BYTES are held to it — a page that legitimately ships a PNG
        card is read by the PNG reader instead of being reported as "not a JPEG", and
        a page that lies about the type still reds.
        """
        readers = {"image/jpeg": cls._jpeg_size, "image/png": cls._png_size}
        assert declared_type in readers, (
            f"og:image:type {declared_type!r} has no reader here — this guard reads "
            f"{sorted(readers)}; add a reader rather than dropping the assertion")
        return readers[declared_type](raw)

    @classmethod
    def _card_violations(cls, page_name: str, head: str, load=None) -> list[str]:
        """Every way this page's link-preview card fails the contract; empty means it passes.

        ⛔ SPLIT OUT SO THE PAGE-LEVEL APPLICATION IS DRIVEN BY A TEST, not only by this
        tree's own pages — the same reason `_icon_trio_violations` is. MEASURED on the
        code before this split, all 13 tests in this class GREEN under each of three
        mutants: the encoded-size floor application deleted, `_card_size` reverted to
        `_jpeg_size` (the JPEG-only dispatch this PR replaced), and the image resolved
        with the raw `image.split("//", 1)[-1]` form (the `?v=2` false red this PR
        fixed). The three behaviours this PR exists to add on the card path were pinned
        only at the helpers they call, never at the loop that calls them — so the loop's
        half could be `assert True` and nothing noticed.

        `load` is the test seam: it maps an `og:image` URL to `(path | None, bytes)`. The
        default reads the served tree (which is what the page loop uses), so the
        query-stripping path is exercised by the real pages rather than bypassed.

        Every check is collected rather than asserted, so a card that is BOTH the wrong
        format and too small reports both — a guard that stops at the first defect is how
        the second one stays hidden.
        """
        load = load or cls._og_card_bytes
        image = cls._og(head, "image")
        if not image:
            return [f"{page_name} declares no og:image — a shared link renders no card"]
        path, raw = load(image)
        if path is None:
            return [f"{page_name} points og:image at {image}, which is not in the served "
                    f"tree (looked for {cls._og_asset_path(image)}) — the scraper fetches "
                    "a 404 and shows no card"]

        violations: list[str] = []
        declared_type = cls._og(head, "image:type")
        actual: tuple[int, int] | None = None
        if not declared_type:
            violations.append(
                f"{page_name} declares no og:image:type — a scraper that has to sniff the "
                "format is a scraper that can guess wrong")
        else:
            try:
                actual = cls._card_size(declared_type, raw)
            except AssertionError as exc:
                violations.append(
                    f"{page_name} declares og:image:type {declared_type!r} but "
                    f"{path.name}'s bytes are not one: {exc}")

        declared = (cls._og(head, "image:width"), cls._og(head, "image:height"))
        if not all(value and value.strip().isdigit() for value in declared):
            violations.append(
                f"{page_name} declares og:image:width/height {declared[0]!r}x{declared[1]!r}, "
                "which is not a pixel count — a scraper then reserves a box nobody promised")
        elif actual is not None and (int(declared[0]), int(declared[1])) != actual:
            violations.append(
                f"{page_name} declares {int(declared[0])}x{int(declared[1])} but "
                f"{path.name} is {actual[0]}x{actual[1]}")

        floor = cls._card_floor_violation(page_name, path, raw)
        if floor:
            violations.append(floor)
        return violations

    @staticmethod
    def _jpeg_size(data: bytes) -> tuple[int, int]:
        assert data[:2] == b"\xff\xd8", "not a JPEG"
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF0/SOF1/SOF2 carry the frame size
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + length
        raise AssertionError("no SOF marker in JPEG")

    @classmethod
    def _ico_entries(cls, data: bytes) -> list[dict]:
        """One dict per icon: the DIRECTORY's claim and what its PAYLOAD really is.

        The directory and the payload are two independent statements about the
        same image, and only the payload is what a renderer draws. A file whose
        second entry declares 48x48 while pointing at the 32x32 payload renders
        blank in Edge and fine in Chrome — so both are read here and compared.
        """
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        assert (reserved, kind) == (0, 1), "not an icon container"
        assert count >= 1, "an .ico with no entries"
        out = []
        for n in range(count):
            base = 6 + 16 * n
            w, h, _cols, _res, planes, bpp, length, offset = struct.unpack(
                "<BBBBHHII", data[base:base + 16])
            assert offset + length <= len(data), (
                f"favicon.ico entry {n} runs past the end of the file "
                f"(offset {offset} + length {length} > {len(data)} bytes)")
            assert length > 0, f"favicon.ico entry {n} declares an empty payload"
            payload = data[offset:offset + length]
            is_png = payload[:8] == b"\x89PNG\r\n\x1a\n"
            out.append({
                "declared": (w or 256, h or 256),
                "planes": planes,
                "bpp": bpp,
                "is_png": is_png,
                "actual": cls._png_size(payload) if is_png else None,
                # Kept so the CONTENT check can decode what the directory merely
                # describes: every assertion above reads the container's claims and
                # the payload's geometry, and a solid-colour payload satisfies both.
                "payload": payload,
            })
        return out

    # --- helpers --------------------------------------------------------

    # The population every assertion from here down iterates. Pinned, because a
    # MOVE narrows it silently: `glob("*.html")` is non-recursive, so filing six
    # pages into a subdirectory left these guards covering 8 of the 14 pages then
    # served and green (epic #11762 rejected that shape for exactly this reason).
    # Adding or removing a page is a deliberate act; a page that leaves coverage by
    # moving is not.
    PAGES_AT_11762 = (
        "404.html", "contact.html", "design-partners-intro.html", "docs.html",
        "dpa.html", "faq.html", "index.html", "investors.html", "license.html",
        "privacy.html", "product.html", "security.html", "self-hosted.html",
        # #13645: the niche product page. ADDED, not moved — a top-level served
        # document at the company root, so every head/card guard below covers it.
        "tortoise-for-product-teams.html",
        "tos.html",
    )

    def _pages(self):
        pages = sorted(self.WEB.glob("*.html"))
        assert pages, "no pages found — the guard would pass vacuously"
        return pages

    def test_the_guard_population_is_pinned(self):
        """The population the icon/card guards iterate must not shrink by accident."""
        assert tuple(p.name for p in self._pages()) == self.PAGES_AT_11762, (
            "the marketing-page population changed. That is legitimate only when a "
            "page is deliberately added or retired — a page that MOVED out of "
            "`website/*.html` silently leaves every guard below and stays green")

    @classmethod
    def _head(cls, html: str) -> str:
        """The head's MARKUP: comments and inline `<style>`/`<script>` bodies removed.

        ⛔ This is not tidying — it is the fix for a fail-open the first cut had. The
        value readers below are regexes over this text, and an HTML comment is
        invisible to a browser: wrapping `og:image` in `<!-- -->` shipped a page with
        NO preview image at all while all eight assertions passed, because the regex
        happily matched the commented-out tag. A guard that reads what a browser
        ignores reports green on a page a scraper sees as empty. Stripping first makes
        the value readers and `test_the_head_is_well_formed_markup` answer about the
        SAME text, which is the text a scraper parses.

        Comments are also where this repo QUOTES tags (the noindex rationale on
        `design-partners-intro.html` quotes a `<meta name="robots">`), and
        `404.html` carries its whole stylesheet in the head — counting either as
        markup is the opposite error.
        """
        start = html.index("<head")
        return cls._head_markup(html[start:html.index("</head>", start)])

    # The readers below go through the PARSER, not a regex, and that is not
    # fastidiousness: `<meta content="…" property="og:image">` is identical to a
    # browser, to every scraper and to this file's own structural test, but a
    # source-order regex reports "declares no og:image — a shared link renders no
    # card" about a page that is correct. A guard that cries wolf on a legitimate
    # edit teaches the next person to weaken it, which is how a guard dies.
    @classmethod
    def _meta_content(cls, head: str, **attrs: str) -> str | None:
        metas, _ = cls._head_metas(head)
        for meta in metas:
            if all(meta.get(k) == v for k, v in attrs.items()):
                return meta.get("content")
        return None

    @classmethod
    def _og(cls, head: str, prop: str) -> str | None:
        return cls._meta_content(head, property=f"og:{prop}")

    @classmethod
    def _twitter(cls, head: str, name: str) -> str | None:
        return cls._meta_content(head, name=f"twitter:{name}")

    @classmethod
    def _links(cls, head: str) -> list[dict[str, str]]:
        """Every `<link>` element in the head, as its attribute dict."""

        class Collector(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.links: list[dict[str, str]] = []
                self.in_head = False

            def handle_starttag(self, tag: str, attrs) -> None:
                if tag == "head":
                    self.in_head = True
                elif tag == "link" and self.in_head:
                    self.links.append({k: (v or "") for k, v in attrs})

            def handle_endtag(self, tag: str) -> None:
                if tag == "head":
                    self.in_head = False

        collector = Collector()
        collector.feed(head)
        collector.close()
        return collector.links

    # --- 1. the icon resolves ------------------------------------------

    def test_every_page_declares_the_icon_trio(self):
        """Two `rel=icon` rows (ICO + PNG) plus the apple-touch icon, every page.

        The `rel` is asserted, not just the `href`: `<link rel="preload"
        href="/favicon.ico">` mentions the file and gives the tab no icon at all, and
        an href-only check cannot tell the two apart.

        The `sizes` attribute is held to the BYTES rather than to a literal, because a
        declared size is a promise a renderer reserves space for: a 512px re-export of
        a 256px file is square, is >= 180, passes every size assertion — and makes all
        15 pages lie about it. So the tag is checked against the IHDR (PNG) and against
        the directory (ICO), which is what the tab will actually draw.
        """
        png_w, png_h = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple_w, _apple_h = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {
            i["declared"][0]
            for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())
        }

        png = (png_w, png_h)
        apple = (apple_w, _apple_h)
        for page in self._pages():
            violations = self._icon_trio_violations(
                page.name, page.read_text(), png, ico_sizes, apple)
            assert not violations, "\n".join(violations)

    def _icon_trio_violations(self, page_name: str, head: str,
                              png: tuple[int, int], ico_sizes: set[int],
                              apple: tuple[int, int], *,
                              origins: frozenset[str] | None = None) -> list[str]:
        """Every way this page's `<head>` fails the icon contract; empty means it passes.

        ⛔ SPLIT OUT SO THE PAGE-LEVEL APPLICATION IS DRIVEN BY A TEST, not only by this
        tree's own pages. The commit that added these assertions proved the off-host and
        wrong-`sizes` paths with a hand-run probe against `website/404.html` and never with
        a suite assertion — so replacing either one with `assert True` left 87 tests green,
        and a real page regression would have shipped green. That is precisely the class of
        defect this guard exists to end, one level ABOVE the helpers it calls.
        """
        links = self._links(self._head(head))
        # `rel` is split CASE-INSENSITIVELY: HTML lowercases attribute NAMES, never values,
        # so `rel="ICON"` is the same relation — comparing the raw value reded a correct
        # page with a message that blamed it for having no icon at all.
        violations = [
            f"{page_name} does not declare {href} with the rel that makes it an icon — a "
            "page with no icon shows a blank tab and, on a share, no avatar; iOS draws a "
            "screenshot instead of the mark"
            for href in self._missing_icon_links(links, origins=origins)]
        # ⛔ EVERY link is audited, and the apple-touch branch is dispatched BY NAME:
        # `"apple-touch-icon".split()` is `["apple-touch-icon"]`, so a `"icon" in rel`
        # filter reaches neither of its branches and the apple link's `sizes` went
        # unchecked while this guard reported the page correct.
        violations += [
            v for v in (self._declared_sizes_violation(
                page_name, link, png, ico_sizes, apple, origins=origins)
                for link in links) if v]
        return violations

    @classmethod
    def _missing_icon_links(cls, links: list[dict], *,
                            origins: frozenset[str] | None = None) -> list[str]:
        """The icon files this `<link>` set fails to declare with the rel that makes it one.

        Split out of the trio guard so the SAME predicate can be run against a synthetic
        link set (`test_the_declared_sizes_guard_refuses_a_wrong_apple_slot`): every page
        in this tree spells `rel` in lower case, so the case-INSENSITIVE read is
        unobservable on the real population — a regression to `.split()` alone leaves
        every page-level assertion green while `rel="ICON"` (valid HTML) reds a correct
        page. A check whose failure path never runs cannot be told apart from a no-op.
        """
        declared = {(cls._icon_ref(ln.get("href", ""), origins=origins), token)
                    for ln in links
                    for token in ln.get("rel", "").lower().split()}
        missing: list[str] = []
        for href in (f"/{cls.ICON_ICO}", f"/{cls.ICON_PNG}"):
            if (href, "icon") not in declared:
                missing.append(href)
        if (f"/{cls.ICON_APPLE}", "apple-touch-icon") not in declared:
            missing.append(f"/{cls.ICON_APPLE}")
        return missing


    @classmethod
    def _icon_ref(cls, href: str, *,
                  origins: frozenset[str] | None = None,
                  base: str | None = None) -> str:
        """The PATH an icon href names when it is THIS site's; "" when it is not.

        The query is not part of what is served: `?v=2` on a favicon href is standard
        cache-busting, changes no file and is honoured by every browser, so comparing
        raw hrefs reports "declares no rel=icon" about a page that is correct — the
        same false red `_og_asset_path` avoids for `og:image`.

        ⛔ AND THE ORIGIN IS CHECKED, NOT DROPPED. `urlsplit("//evil.example/
        favicon.ico").path` is `/favicon.ico` — the netloc is discarded — so a
        protocol-relative href "declared" the icon by path while the browser fetched
        it from ANOTHER HOST, and the guard reported green on a tab that has no icon
        (or someone else's). Only an href that resolves to one of `SITE_ORIGINS`
        yields a path; anything else yields "" and matches no icon assertion.

        `origins` is the SURFACE's own origin set. It exists because the guard now grades
        the app tree too, and `app.premiselabs.co` is a real origin of ours that
        `SITE_ORIGINS` deliberately does not carry (that set is what the marketing
        `og:image` guard holds a card to — widening IT would loosen a different check).
        Default `None` keeps every existing caller on `SITE_ORIGINS`.

        ⛔ `base` IS THE PAGE'S OWN URL, NOT THE SITE ROOT (review round 3). A href is
        resolved by the BROWSER against the page that carries it, so a page in a
        subdirectory declaring `href="favicon.ico?v=…"` requests `<dir>/favicon.ico`. Every
        href was resolved against `SITE_ROOT`, so such a page read GREEN for a file that does
        not exist — measured on a new `apps/dashboard/public/settings/index.html` mutant,
        reachable only because the app surface now `rglob`s for nested pages.
        """
        url = urlsplit(urljoin(base or cls.SITE_ROOT, href))
        if url.hostname not in (origins if origins is not None else cls.SITE_ORIGINS):
            return ""
        return url.path

    @staticmethod
    def _parse_declared_sizes(token: str) -> set[int] | None:
        """The square, single-slot sizes a `sizes="32x32 48x48"` token promises.

        `None` means "this token is not that list" — the CALLER turns it into a
        violation, quoting the token. ⛔ IT MUST NOT RAISE. Its only caller is
        `_declared_sizes_violation`, which is a VIOLATION COLLECTOR: `_icon_trio_
        violations` documents itself as returning *every* way a page fails the contract,
        and a raise here instead escapes the collector with ONE raw AssertionError,
        losing every other violation on the page. Measured on the code that raised: a
        spec-valid head whose `rel="icon"` links omit the OPTIONAL `sizes=` raised, and
        a head that ALSO omitted two of the three icon links reported only the sizes
        assertion — the two missing-link violations were never returned.
        """
        pairs = re.findall(r"(\d+)x(\d+)", token)
        if not pairs or not all(a == b for a, b in pairs):
            return None
        return {int(a) for a, _ in pairs}

    @classmethod
    def _declared_sizes_violation(cls, page_name: str, link: dict,
                                  png: tuple[int, int], ico_sizes: set[int],
                                  apple: tuple[int, int], *,
                                  origins: frozenset[str] | None = None) -> str | None:
        """Why a `<link>`'s declared `sizes=` disagrees with the file's bytes; None = agrees.

        ⛔ SPLIT OUT OF THE TRIO GUARD for the reason `_icon_mark_violations` is: the
        apple-touch branch fires only when a page declares `sizes=` on that link, and
        NO page does — measured at this head, 24 surfaced documents carry 24 apple links
        and 0 of them declare one. Inline it was therefore unreachable, and a check whose
        failure path never runs cannot be told apart from a no-op.
        `test_the_declared_sizes_guard_refuses_a_wrong_apple_slot` now feeds it the
        link the branch is written for, in both directions.

        `sizes` is OPTIONAL on the apple link — the href alone is what iOS needs — so
        absent is not a defect. When it IS declared it is a promise like the others
        (MDN's own example is `rel="apple-touch-icon" sizes="114x114"`), and this
        guard knows the file's real geometry, so a wrong one must not pass silently.

        ⛔ ON THE `rel="icon"` LINKS IT IS REQUIRED, AND THAT IS A HOUSE RULE, not the
        spec. The spec makes `sizes` a hint that is optional everywhere; this site pins
        both tab-icon slots so a renderer reserves the right box instead of guessing, so
        a page that omits one is reported. Stated here because the alternative is that
        the requirement looks like an accident of the parser. Every omission and every
        token this guard cannot read is RETURNED as a violation — never raised — so one
        defect can never hide the rest.
        """
        rels = link.get("rel", "").lower().split()
        path = cls._icon_ref(link.get("href", ""), origins=origins)
        declared = link.get("sizes")
        if "icon" in rels and path == f"/{cls.ICON_PNG}":
            return cls._sizes_violation(page_name, declared, {png[0]}, cls.ICON_PNG,
                                       f"IHDR is {png[0]}x{png[1]}", "box")
        if "icon" in rels and path == f"/{cls.ICON_ICO}":
            return cls._sizes_violation(page_name, declared, ico_sizes, cls.ICON_ICO,
                                       f"directory holds {sorted(ico_sizes)}", "container")
        if ("apple-touch-icon" in rels and path == f"/{cls.ICON_APPLE}"
                and declared):
            return cls._sizes_violation(page_name, declared, {apple[0]}, cls.ICON_APPLE,
                                       f"IHDR is {apple[0]}x{apple[1]}", "box")
        return None

    @classmethod
    def _sizes_violation(cls, page_name: str, declared: str | None, expected: set[int],
                         target: str, truth: str, unit: str) -> str | None:
        """Why a `<link>`'s declared `sizes=` disagrees with what the bytes hold.

        Returns a violation string — never raises — because every branch below is a way
        a page can fail a contract, not a programming error. `truth` states what the file
        actually holds (`"IHDR is 256x256"` / `"directory holds [32, 48]"`) and `unit`
        names the thing that ends up wrong (`box` for a PNG, `container` for the ICO).
        """
        if not declared:
            return (f"{page_name} declares no sizes= for {target} — this site pins that "
                    f"slot ({truth}) so a renderer reserves the right {unit} instead of "
                    "guessing. `sizes` is OPTIONAL in the spec, so this is a house rule "
                    "(see `_declared_sizes_violation`); declare it, or change the rule "
                    "deliberately")
        parsed = cls._parse_declared_sizes(declared)
        if parsed is None:
            return (f"{page_name} declares sizes={declared!r} for {target}, which is not "
                    "a square '<w>x<h>' slot list — a tab icon is square, and a token "
                    "this guard cannot read is a promise it cannot check")
        if parsed == expected:
            return None
        return (f"{page_name} declares sizes={declared!r} for {target}, whose {truth} — "
                f"the tag and the {unit} must agree, or the tab reserves the wrong {unit}")

    def test_the_declared_sizes_guard_refuses_a_wrong_apple_slot(self):
        """NON-VACUITY: the branches no page in this tree drives must still be able to RED.

        ⛔ WHY. The apple-touch `sizes` branch fires only when a page declares `sizes=` on
        that link, and none does (24 apple links, 0 declaring one, measured at this
        head), so inline it was dead code. The trio's `icon`/rel membership is the same
        class of hole from the other side: every page here spells `rel` in lower case,
        so the case-insensitive read is unobservable on the real population.

        Each edge is pinned in BOTH directions — the wrong slot reds, the right one does
        not — so none of these assertions is either dead or a constant.
        """
        png = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {i["declared"][0]
                     for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())}
        apple_link = {"rel": "apple-touch-icon", "href": f"/{self.ICON_APPLE}"}

        wrong = dict(apple_link, sizes="114x114")
        assert apple[0] != 114, (
            "the fixture's wrong size (114) is the file's real width, so it isolates "
            "nothing")
        assert self._declared_sizes_violation("page.html", wrong, png, ico_sizes, apple), (
            f"declaring sizes=114x114 for a {apple[0]}x{apple[1]} apple-touch icon was not "
            "reded — the branch that exists to catch it is dead")

        right = dict(apple_link, sizes=f"{apple[0]}x{apple[1]}")
        assert self._declared_sizes_violation(
            "page.html", right, png, ico_sizes, apple) is None, (
            "the correct apple-touch size was reded, so the branch is not a check")

        assert self._declared_sizes_violation(
            "page.html", apple_link, png, ico_sizes, apple) is None, (
            "omitting the OPTIONAL sizes= on the apple link was reded — the href alone "
            "is what iOS needs")

        # The ICO branch: the directory holds [32, 48], so a single-slot declaration
        # must red. Killing this branch outright left the suite green, which is the
        # only reason it needs an edge of its own here.
        ico_link = {"rel": "icon", "href": f"/{self.ICON_ICO}",
                    "sizes": " ".join(f"{n}x{n}" for n in sorted(ico_sizes))}
        assert self._declared_sizes_violation(
            "page.html", ico_link, png, ico_sizes, apple) is None, (
            "the ICO's own directory sizes were reded")
        ico_wrong = dict(ico_link, sizes=f"{sorted(ico_sizes)[0]}x{sorted(ico_sizes)[0]}")
        assert self._declared_sizes_violation(
            "page.html", ico_wrong, png, ico_sizes, apple), (
            f"declaring sizes={ico_wrong['sizes']!r} for an ICO whose directory holds "
            f"{sorted(ico_sizes)} was not reded — the ICO branch is dead")

        upper = {"rel": "ICON", "href": f"/{self.ICON_PNG}", "sizes": "1x1"}
        assert self._declared_sizes_violation(
            "page.html", upper, png, ico_sizes, apple), (
            'rel="ICON" with a wrong sizes= was not reded — a case-sensitive read '
            "skips the link entirely, which is how a correct page reds and a wrong "
            "one passes)")

        off_host = {"rel": "icon", "href": "//evil.example/favicon.ico",
                    "sizes": f"{png[0]}x{png[1]}"}
        assert self._icon_ref(off_host["href"]) == "", (
            "a protocol-relative href was resolved to this site's path — the netloc was "
            "dropped, so an off-host icon reads as declared")

        # ...and the trio's own membership predicate, driven with a synthetic link set:
        # the real pages all spell `rel` in lower case, so this is the only place the
        # case-insensitive read is observable.
        complete = [{"rel": "icon", "href": f"/{self.ICON_ICO}"},
                    {"rel": "icon", "href": f"/{self.ICON_PNG}"},
                    dict(apple_link)]
        assert self._missing_icon_links(complete) == [], (
            "a complete, correctly-spelled icon trio was reported as missing links")
        assert self._missing_icon_links(
            [dict(ln, rel=ln["rel"].upper()) for ln in complete]) == [], (
            "an upper-case rel was reported as missing — HTML lowercases attribute "
            "NAMES, never values, so rel=\"ICON\" is the same relation")
        assert self._missing_icon_links(complete[:2]) == [f"/{self.ICON_APPLE}"], (
            "dropping the apple-touch link was not reported as a missing icon")
        assert self._missing_icon_links([complete[0], complete[2]]) == [f"/{self.ICON_PNG}"], (
            "dropping the PNG rel=icon was not reported as a missing icon")
        # ⛔ THE FILE KEY OF THE ICO AND APPLE BRANCHES. On this tree the PNG conjunct
        # shadows them (every page's PNG link is graded first and the ICO/apple hrefs are
        # the only other ones), so dropping either lets a SECOND, correctly-declared
        # variant (`/favicon-32.png`, `/apple-touch-icon-120x120.png`) be graded against
        # the default file and red — a false red nothing in the suite noticed.
        assert self._declared_sizes_violation(
            "page.html", {"rel": "icon", "href": "/favicon-32.png", "sizes": "32x32"},
            png, ico_sizes, apple) is None, (
            "a second, correctly-declared PNG icon was graded against the ICO")
        assert self._declared_sizes_violation(
            "page.html", {"rel": "apple-touch-icon",
                          "href": "/apple-touch-icon-120x120.png", "sizes": "120x120"},
            png, ico_sizes, apple) is None, (
            "a correctly-declared apple-touch variant was graded against apple-touch-icon.png")
        # ...and the PAGE-LEVEL application itself, driven by a synthetic head. Without
        # this, either assertion in the loop could be `assert True` and the suite stayed
        # green — measured by the round-6 reviewer against a genuinely broken 404.html.
        broken = ("<head>"
                  '<link rel="icon" href="/favicon.ico" sizes="32x32">'
                  f'<link rel="icon" href="/favicon.png" sizes="{png[0]}x{png[1]}">'
                  "</head>")  # no apple-touch link, and the wrong ICO slot
        trio = self._icon_trio_violations("synthetic.html", broken, png, ico_sizes, apple)
        assert any(self.ICON_APPLE in v for v in trio), (
            f"a head with no apple-touch link produced no page-level violation: {trio}")
        assert any(self.ICON_ICO in v and "sizes" in v for v in trio), (
            f"a head declaring the wrong ICO slot produced no page-level violation: {trio}")
        good = ("<head>"
                f'<link rel="icon" href="/favicon.ico" '
                f'sizes="{" ".join(f"{n}x{n}" for n in sorted(ico_sizes))}">'
                f'<link rel="icon" href="/favicon.png" sizes="{png[0]}x{png[1]}">'
                f'<link rel="apple-touch-icon" href="/{self.ICON_APPLE}" '
                f'sizes="{apple[0]}x{apple[1]}">'
                "</head>")
        assert self._icon_trio_violations(
            "synthetic.html", good, png, ico_sizes, apple) == [], (
            "a complete, correctly-declared head was reported as violating the contract")
        # ⛔ THE REL TOKEN, NOT JUST THE HREF. `<link rel="preload" href="/favicon.ico">`
        # mentions the file and gives the tab no icon at all — an href-only reduction of
        # this predicate reports all three as declared, which is the exact earlier cut's
        # defect, and it is silent unless this case drives it.
        assert self._missing_icon_links(
            [dict(ln, rel="preload") for ln in complete]
        ) == [f"/{self.ICON_ICO}", f"/{self.ICON_PNG}", f"/{self.ICON_APPLE}"], (
            "an icon file mentioned by a non-icon relation satisfied the trio — the rel "
            "token, not just the href, is what makes the link an icon")
        # ...and a NON-SQUARE `sizes=` token is rejected, not merely parsed: `32x32 48x16`
        # parses to {32, 48}, which EQUALS the ICO's directory, so without this the same
        # declaration is ACCEPTED. Rejection is `None` (not a raise — see
        # `_parse_declared_sizes`: the parser feeds a collector, which must return a
        # violation rather than escape the whole page over one unreadable token).
        assert self._parse_declared_sizes("32x32 48x16") is None, (
            'a non-square sizes= token was accepted — "48x16" is not a slot a tab can '
            "draw, and it parses to the same set as the ICO's directory")
        # ...and the parser still PARSES what it should, so `None` is not the only answer
        # it can give (a guard whose every input returns None passes the row above
        # vacuously).
        assert self._parse_declared_sizes("32x32 48x48") == {32, 48}, (
            "a square multi-slot sizes= token did not parse — the ICO's own declaration "
            "then rededs as unreadable")
        assert self._parse_declared_sizes("any") is None, (
            'sizes="any" parsed as a pixel slot — it is SVG-only syntax that names no '
            "size at all, so it cannot be checked against a raster icon's IHDR")
        assert self._missing_icon_links(
            [dict(ln, href="//evil.example/favicon.ico")
             if ln["href"] == f"/{self.ICON_ICO}" else ln for ln in complete]
        ) == [f"/{self.ICON_ICO}"], (
            "an off-host href was accepted as this site's ICO — the origin was dropped")

    def test_the_trio_collector_returns_every_violation_instead_of_raising(self):
        """A violation COLLECTOR must return them all; one defect may not hide another.

        ⛔ WHY THIS EXISTS. `_icon_trio_violations` is documented as returning *every* way
        a page's `<head>` fails the icon contract, and `sizes` is OPTIONAL on
        `rel="icon"` in the spec — so a minimal, universally-supported icon declaration
        is honest markup. Measured on the code that ASSERTED instead of returning: that
        head raised `AssertionError: the tag declares no sizes= — a renderer then
        guesses` straight out of the collector, and a head that also omitted two of the
        three icon links reported only the sizes assertion — the missing-link violations
        were computed and then thrown away with the exception. On the `rel="icon"`
        links the requirement is a HOUSE RULE (see `_declared_sizes_violation`) and it is
        reported as a violation; the apple link's stays OPTIONAL.
        """
        png = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {i["declared"][0]
                     for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())}

        minimal = ("<head>"
                   f'<link rel="icon" href="/{self.ICON_ICO}">'
                   f'<link rel="icon" href="/{self.ICON_PNG}">'
                   f'<link rel="apple-touch-icon" href="/{self.ICON_APPLE}">'
                   "</head>")
        trio = self._icon_trio_violations("synthetic.html", minimal, png, ico_sizes, apple)
        assert len(trio) == 2, (
            "a spec-valid head whose two rel=icon links omit the OPTIONAL sizes= must "
            f"report exactly the two house-rule violations; got {trio}")
        assert any(self.ICON_ICO in v and "sizes" in v for v in trio), (
            f"omitting sizes= on the ICO link was not reported: {trio}")
        assert any(self.ICON_PNG in v and "sizes" in v for v in trio), (
            f"omitting sizes= on the PNG link was not reported: {trio}")
        assert not any(self.ICON_APPLE in v for v in trio), (
            "omitting the OPTIONAL sizes= on the apple link was reported as a violation — "
            "the href alone is what iOS needs, and that exemption is deliberate")

        # An unreadable token is a VIOLATION with the token quoted, not a raise: `any`
        # is SVG-only syntax naming no size, and a non-square list cannot be a tab slot.
        cases = {
            "any": "SVG-only token",
            f"{png[0]}x{apple[0]}": "non-square slot list",
            "big": "not a <w>x<h> list at all",
        }
        for token, why in cases.items():
            link = {"rel": "icon", "href": f"/{self.ICON_PNG}", "sizes": token}
            violation = self._declared_sizes_violation(
                "page.html", link, png, ico_sizes, apple)
            assert violation and token in violation, (
                f"sizes={token!r} ({why}) was not returned as a violation quoting the "
                f"token; got {violation!r}")

        # ...and ONE DEFECT MAY NOT HIDE ANOTHER. The head below omits two icon links
        # AND the sizes= on the one link it does declare; both must come back.
        partial = ("<head>"
                   f'<link rel="icon" href="/{self.ICON_ICO}">'
                   "</head>")
        both = self._icon_trio_violations("synthetic.html", partial, png, ico_sizes, apple)
        assert any("does not declare" in v for v in both), (
            f"the missing icon links were not reported: {both}")
        assert any("sizes" in v for v in both), (
            f"the omitted sizes= was not reported alongside the missing links: {both}")

    def test_the_icon_files_exist_and_are_usable(self):
        """Every icon a page points at exists, and has the shape the tag implies."""
        for name in (self.ICON_ICO, self.ICON_PNG, self.ICON_APPLE):
            assert (self.WEB / name).is_file(), f"website/{name} is missing — every page links it"

        png_w, png_h = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        assert png_w == png_h, f"favicon.png is {png_w}x{png_h} — a tab icon must be square"
        assert png_w >= 180, (
            f"favicon.png is {png_w}px; the apple-touch slot needs >=180 and a "
            "smaller source is upscaled blurry by the OS")

        apple_w, apple_h = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        assert (apple_w, apple_h) == (180, 180), (
            f"apple-touch-icon.png is {apple_w}x{apple_h}; iOS reads 180x180")

        icons = self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())
        assert len(icons) >= 2, f"favicon.ico holds {len(icons)} image(s) — ship 32 and 48"
        assert all(i["is_png"] for i in icons), (
            "favicon.ico carries a raw DIB image — this repo's builder writes PNG "
            "payloads, and a DIB rewrite means the container was replaced by hand")
        assert {i["declared"][0] for i in icons} >= {32, 48}, (
            f"favicon.ico sizes are {sorted(i['declared'][0] for i in icons)}; Windows uses 32 and 48")
        for i in icons:
            assert i["declared"] == i["actual"], (
                f"favicon.ico declares {i['declared']} but the payload is {i['actual']} — "
                "the directory's size is what a renderer reserves, so a mismatch draws "
                "a blank or clipped icon in some browsers and looks fine in others")
            assert (i["planes"], i["bpp"]) == (1, 32), (
                f"favicon.ico entry declares planes={i['planes']} bpp={i['bpp']}; "
                "a PNG payload is 1 plane / 32bpp and anything else is a hand-edited directory")

        # ...and every one of them has something DRAWN on it. A 256x256 flat
        # rectangle is a valid PNG at a valid size and shows a blank tab: the
        # measured placeholder this PR deletes from the blog was exactly that shape.
        # ⛔ THE ICO'S PAYLOADS ARE INCLUDED. `favicon.ico` is the file Windows and Edge
        # draw, and every assertion above reads the container's CLAIMS plus the
        # payload's geometry — a two-entry ICO declaring 32 and 48 over solid
        # `#060b14` payloads satisfies the entry count, the `{32,48}` set, the
        # `declared == actual` pairing AND `(1 plane, 32 bpp)`, i.e. all of them, while
        # rendering a blank tab. Measured on a synthesized file.
        # ⛔ WHAT THIS DOES NOT ASSERT. The pair below answers "is anything drawn on
        # it", not "is this the right art": one flat colour reds (dominant == 1.0) and
        # the deleted placeholder reds at 99.2%, but a 60 KB photograph of something
        # unrelated passes both. The distinct-colour floor is **2**, not 8: a
        # hard-edged two-colour mark (pixel art, a `crispEdges` SVG export, any mark
        # without antialiasing) is legitimate art and must not red — and the earlier
        # floor of 8 did red it while this very comment claimed a two-colour logo
        # would pass at 8. It could not, and a guard that cries wolf at honest
        # artwork is a guard the next person weakens instead of fixes.
        drawn: list[tuple[str, bytes]] = [
            (n, (self.WEB / n).read_bytes())
            for n in (self.ICON_PNG, self.ICON_APPLE)
        ]
        drawn += [
            (f"{self.ICON_ICO} entry {n} ({i['declared'][0]}x{i['declared'][1]})",
             i["payload"])
            for n, i in enumerate(icons) if i["is_png"]
        ]
        for name, data in drawn:
            problems = self._icon_mark_violations(name, data)
            assert not problems, "\n".join(problems)

    def test_the_icon_is_a_rounded_tile_and_the_mark_fills_it(self):
        """The tab icon is a ROUNDED tile whose MARK fills it — the shape, not the size.

        Both claims are the owner's report in #11486, and both were invisible to every
        assertion above: the shipped icon was a 256x256 opaque SQUARE whose triangle
        spanned 68% of the canvas, so it passed the size check, the decode, the pixel
        floors, the ICO directory checks and the wordmark ban, and still looked like a
        hard slab with a small glyph next to neighbours whose art fills their tile.

        Measured on the bytes, not on the markup: a page that declares a 256x256 icon
        says nothing about what is drawn inside it.

        `apple-touch-icon.png` is asserted the OTHER way round on purpose — iOS applies
        its own rounded mask to that file, so a pre-rounded image would be rounded twice
        and show dark corners on a light home screen.
        """
        def alpha_of(px: tuple[int, ...]) -> int:
            """A pixel's alpha; a PNG with no alpha channel is opaque by definition.

            ⛔ MEASURED, because the first version REQUIRED four channels here and the
            apple-touch assertion went through the same helper — so an apple-touch that
            was opaque and square (exactly what its message demands) but saved as colour
            type 2 redded with "an icon with no alpha channel cannot have a rounded
            corner", which is the opposite of that file's contract. The old asset set was
            colour type 2, so an honest re-export tripped it.
            """
            return px[3] if len(px) == 4 else 255

        def check_tile(label: str, data: bytes) -> None:
            w, h, rows = self._png_pixels(data)
            # The TAB icons are the ones that must be rounded, and a 3-channel PNG cannot
            # be: there is no alpha to carry the corner. (apple-touch is checked apart.)
            assert all(len(px) == 4 for row in rows for px in row), (
                f"{label} has no alpha channel — a tab icon must be a rounded tile, and a "
                "PNG without transparency can only be a square. Rebuild it with "
                "tools/build_site_icons.py")
            corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
            for x, y in corners:
                alpha = alpha_of(rows[y][x])
                assert alpha == self.ROUNDED_CORNER_ALPHA, (
                    f"{label} is opaque at its ({x},{y}) corner (alpha {alpha}) — a "
                    "hard square edge, which is the shape #11486 reports as a slab. "
                    "Rebuild it with tools/build_site_icons.py, which masks the corners")

            # ...and the tile is ROUNDED, not merely nibbled. "Corner is transparent" is
            # satisfied by four punched pixels on a square slab (measured green), so the
            # arc is measured as an inset along each edge, in fractions of the side: a
            # radius-r arc reaches the edge at exactly r.
            floor = int(self.CORNER_INSET_FLOOR * w)
            edges = {
                "top": [(x, 0) for x in range(w)],
                "bottom": [(x, h - 1) for x in range(w)],
                "left": [(0, y) for y in range(h)],
                "right": [(w - 1, y) for y in range(h)],
            }
            for edge, line in edges.items():
                for start, step in ((0, 1), (len(line) - 1, -1)):
                    inset = 0
                    i = start
                    while 0 <= i < len(line) and alpha_of(rows[line[i][1]][line[i][0]]) < 255:
                        inset += 1
                        i += step
                    assert inset >= floor, (
                        f"{label} is fully opaque {inset}px in from the {edge} corner — "
                        f"under the {floor}px ({self.CORNER_INSET_FLOOR:.0%} of {w}) a "
                        "rounded tile needs, so the corner is nibbled rather than rounded "
                        "and the tab still reads as a square slab (#11486)")

            # The ground is whatever opaque colour covers the most pixels; the mark is
            # everything far from it. Both are measured, so no colour is hardcoded.
            opaque = [px for row in rows for px in row if px[3] == 255]
            assert opaque, f"{label} has no fully opaque pixel at all"
            counts: dict[tuple[int, ...], int] = {}
            for px in opaque:
                counts[px[:3]] = counts.get(px[:3], 0) + 1
            ground = max(counts, key=lambda c: counts[c])
            xs = [x for y in range(h) for x in range(w)
                  if rows[y][x][3] == 255
                  and sum(abs(rows[y][x][i] - ground[i]) for i in range(3)) > 60]
            assert xs, f"{label} shows nothing that is not its ground colour"
            span = (max(xs) - min(xs) + 1) / w
            assert span >= self.MARK_WIDTH_FLOOR, (
                f"{label}'s mark spans {span:.0%} of the canvas width ({(1 - span) / 2:.0%} "
                f"padding each side) — under the {self.MARK_WIDTH_FLOOR:.0%} floor, so in a "
                "tab strip it reads as a small glyph on a slab (#11486). The artwork "
                "carries its own padding; tools/build_site_icons.py crops it out rather "
                "than shrinking the mark to fit it")

        check_tile(self.ICON_PNG, (self.WEB / self.ICON_PNG).read_bytes())
        for i, entry in enumerate(self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())):
            if not entry["is_png"]:
                continue
            w, h = entry["declared"]
            check_tile(f"{self.ICON_ICO} entry {i} ({w}x{h})", entry["payload"])

        # ...and apple-touch is the exception, asserted rather than skipped.
        apple = (self.WEB / self.ICON_APPLE).read_bytes()
        w, h, rows = self._png_pixels(apple)
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert alpha_of(rows[y][x]) == 255, (
                f"{self.ICON_APPLE} is transparent at ({x},{y}) — iOS masks this file "
                "itself, so pre-rounded corners render as dark notches on a light home "
                "screen. It is the one icon that must stay a full-bleed square")

    def test_the_wordmark_is_never_used_as_an_icon(self):
        """`/logo.png` is a 400x244 wordmark — a tab renders it as a squashed bar.

        Reads the same parsed `<link>` elements the trio assertion reads, and splits
        `rel` the same way: a regex over the raw markup is case-sensitive,
        double-quote-only and order-sensitive, so `rel="shortcut icon"` — which the
        trio assertion deliberately accepts — walked straight past it.

        The href is resolved against a root URL before it is compared, because every
        page here is served at a root-level path: on `/faq`, `href="logo.png"` IS
        `/logo.png`, and a substring test on the attribute sees neither. Matching on
        `"icon" in token` rather than on two exact tokens is what covers
        `apple-touch-icon-precomposed`, which iOS still honours.
        """
        for page in self._pages():
            head = self._head(page.read_text())
            for ln in self._links(head):
                rel = ln.get("rel", "").lower().split()
                if not any("icon" in token for token in rel):
                    continue
                violation = self._icon_target_violation(page.name, ln)
                assert violation is None, violation

    @classmethod
    def _icon_target_violation(cls, page_name: str, link: dict) -> str | None:
        """Why an ICON link's TARGET is not a usable tab icon; None means it is.

        Split out of the guard above for the reason `_declared_sizes_violation` is: every
        page in this tree links three valid icons, so inlined the whole check was executed
        only by CORRECT input. Measured: restoring the existence check to AFTER the `.svg`
        exemption (the pre-fix order) kept every test green while `href="/assets/nope.svg"`
        silently passed — the same "proved with a hand-run probe, pinned by nothing"
        defect this class exists to end. `test_the_icon_target_guard_reads_existence_and_
        shape` drives each branch with a synthetic link set.

        ⛔ THE BLACKLIST IS ONE FILENAME WIDE. The property it is about is "a tab icon
        must be square" — a browser squashes a wide image into the square slot — and a
        filename test cannot see `assets/premiselabs-logo.png`, which is 160x98 and is the
        same wordmark by another name (measured: it passed while `logo.png` was refused).
        So the TARGET IS READ.
        ⛔ AND THE BYTES ARE READ FOR EVERY FORMAT, NOT JUST `.png`: gating on one
        extension is the same one-rule-wide hole one level down — measured,
        `rel="icon" href="/assets/og-tortoise.jpg"` (1200x630) passed every icon
        assertion, and so did a href naming a file that does not exist.
        ⛔ AND EXISTENCE COMES FIRST, BEFORE ANY FORMAT EXEMPTION. The `.svg` exemption is
        about having no intrinsic pixel box to squash — it is NOT a licence to skip the
        file check, and an early `continue` that did so let a page link a nonexistent
        vector icon while this guard reported green (measured: the only format that did).
        """
        # ⛔ THE ORIGIN IS CHECKED, NOT DROPPED — via `_icon_ref`, the one helper that
        # already does it. `urlsplit("//evil.example/favicon.png").path` is
        # `/favicon.png`, so a netloc-discarding read grades an off-host icon as this
        # site's own file and reports green on a tab that draws someone else's image.
        # Measured before this fix: `_icon_target_violation("page.html", {"rel": "icon",
        # "href": "//evil.example/favicon.png"})` returned None.
        path_part = cls._icon_ref(link.get("href", ""))
        if not path_part:
            # `_icon_ref` also returns "" for an href that names NO host at all — an
            # inline `data:` icon is legitimate HTML, so "another host" would be a
            # false statement about it. The branch says what is true of every case.
            return (f"{page_name} links an icon that is not a file this site serves "
                    f"(rel={link.get('rel')!r} href={link.get('href')!r}) — this guard "
                    "cannot verify it")
        assert "/logo.png" not in path_part, (
            f"{page_name} uses the wordmark as an icon: rel={link.get('rel')!r} "
            f"href={link.get('href')!r}")
        target = cls.WEB / path_part.lstrip("/")
        if not target.is_file():
            return (f"{page_name} links an icon this site does not serve ({path_part}) — "
                    "the tab draws nothing")
        if path_part.endswith(".svg"):
            # A vector icon has no intrinsic pixel box to squash — the tab sizes it from
            # the viewBox — so it is EXEMPT BY NAME from the shape check, above.
            return None
        raw = target.read_bytes()
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = cls._png_size(raw)
        elif raw[:2] == b"\xff\xd8":
            w, h = cls._jpeg_size(raw)
        elif raw[:4] == b"\x00\x00\x01\x00":
            # The ICO's directory entries are the slots a renderer reserves, so each of
            # them must be square too.
            for entry in cls._ico_entries(raw):
                if entry["declared"][0] != entry["declared"][1]:
                    return (f"{page_name} uses {path_part} as a tab icon and its "
                            f"{entry['declared'][0]}x{entry['declared'][1]} directory "
                            "entry is not square — that icon is drawn squashed")
            return None
        else:
            raise AssertionError(
                f"{page_name} uses {path_part} as a tab icon; this guard reads PNG, JPEG "
                "and ICO, and exempts only .svg — add a reader rather than leaving the "
                "tab unchecked")
        if w != h:
            return (f"{page_name} uses a {w}x{h} non-square image as a tab icon "
                    f"({path_part}) — the tab squashes it into a square")
        return None

    def test_the_icon_target_guard_reads_existence_and_shape(self):
        """NON-VACUITY for `_icon_target_violation`, driven by a synthetic link set.

        Every page links three valid icons, so on the real population this predicate is
        only ever run against input that passes — a regression in any branch is
        unobservable. MEASURED before this test existed: moving the existence check back
        below the `.svg` exemption left all 13 icon/card tests green while
        `href="/assets/nope.svg"` was accepted. Each edge is pinned in BOTH directions.
        """
        for link in ({"rel": "icon", "href": "/favicon.png"},
                     {"rel": "icon", "href": "/favicon.png?v=2"},
                     {"rel": "icon", "href": "/apple-touch-icon.png"},
                     {"rel": "icon", "href": "/favicon.ico"}):
            assert self._icon_target_violation("page.html", link) is None, (
                f"a valid icon link was reded: {link}")

        for missing in ("/assets/nope.png", "/assets/nope.svg"):
            violation = self._icon_target_violation(
                "page.html", {"rel": "icon", "href": missing}) or ""
            assert "does not serve" in violation, (
                f"an icon link to a missing file ({missing}) was accepted — the format "
                "exemption must come AFTER the existence check, measured: an early "
                f"`.svg` continue re-opened exactly this. Got: {violation!r}")

        wide = self._icon_target_violation(
            "page.html", {"rel": "icon", "href": "/assets/og-tortoise.jpg"}) or ""
        assert "non-square" in wide, (
            "a 1200x630 JPEG used as a tab icon was accepted — the shape check is "
            f"`.png`-only again. Got: {wide!r}")

        wordmark = self._icon_target_violation(
            "page.html", {"rel": "icon", "href": "/assets/premiselabs-logo.png"}) or ""
        assert "non-square" in wordmark, (
            "a 160x98 wordmark was accepted as a tab icon — the filename blacklist is the "
            f"only rule again. Got: {wordmark!r}")

        # ⛔ THE ORIGIN EDGE, AND IT MUST BE PINNED HERE OR THE FIX IS UNWATCHED. This
        # helper is graded on hrefs; every other fixture in this test is ON-SITE, so a
        # revert to `urlsplit(urljoin(...)).path` (which discards the netloc) leaves
        # every other assertion in this test behaving identically and the suite GREEN.
        # MEASURED with that revert restored: both off-host forms below return None.
        # `data:` is included because `_icon_ref` rejects it too, for a different
        # reason (it names no host at all), so the branch must not claim "another host".
        for off_host in ("//evil.example/favicon.png",
                         "https://evil.example/favicon.png",
                         "data:image/png;base64,AAAA"):
            refused = self._icon_target_violation(
                "page.html", {"rel": "icon", "href": off_host}) or ""
            assert "not a file this site serves" in refused, (
                f"{off_host!r} was accepted as a tab icon — the origin is being "
                f"discarded again (`urlsplit(...).path` drops the netloc). "
                f"Got: {refused!r}")

        try:
            self._icon_target_violation("page.html", {"rel": "icon", "href": "/logo.png"})
        except AssertionError:
            pass
        else:
            raise AssertionError("the wordmark was accepted as a tab icon")

    # --- 2. the preview resolves and is the size it claims ---------------

    @staticmethod
    def _head_markup(head: str) -> str:
        """The head with its two non-markup regions removed: comments and CSS/JS bodies.

        Both legitimately CONTAIN angle brackets — this repo's own noindex rationale
        quotes `<meta name="robots" content="noindex">` inside an HTML comment, and
        404.html inlines its whole stylesheet — so counting raw `<meta` occurrences
        in the document would flag prose as a broken tag.

        `<noscript>` and `<template>` are stripped for the same reason `<script>` is:
        their contents are not live head metadata. A scraper does not read an
        `og:image` that only exists inside a `<noscript>` fallback, so a guard that
        did would report green on a page with no preview — measured, before this
        list was complete.
        """
        text = re.sub(r"<!--.*?-->", "", head, flags=re.S)
        stripped = re.sub(r"<(style|script|noscript|template)\b.*?</\1>", "", text,
                          flags=re.S | re.I)
        # An UNCLOSED wrapper defeats the strip above, and CPython's parser treats
        # `noscript`/`template` as ordinary elements, so the metas inside are still
        # counted and still found — while a browser puts them in the inert fragment
        # and renders no card. Measured: an unclosed `<template>` before the metas read
        # green. `style`/`script` are CDATA to that parser and already fail closed, so
        # they are listed here only to keep one rule rather than two.
        leftover = re.findall(r"<(style|script|noscript|template)\b", stripped, flags=re.I)
        assert not leftover, (
            f"an unclosed <{leftover[0]}> in the head — the strip cannot remove a "
            "wrapper with no end tag, so the tags after it read as live markup here "
            "while a browser treats them as inert content (and a scraper sees no card)")
        return stripped

    @staticmethod
    def _head_metas(head: str) -> tuple[list[dict[str, str]], list[str]]:
        """Parse the head: one dict per `<meta>` element, plus any text that leaked.

        Used instead of the regexes above wherever STRUCTURE is the question. A
        regex answers "does this substring appear", which a malformed tag also
        satisfies; only a parser tells you the substring became an element.
        """

        # Elements whose TEXT is legitimate head content: a `title`, an inlined
        # `style` (404.html carries all of its CSS in the head), a `script`.
        # Their data is not the remnant of an unclosed tag, so it is not stray.
        TEXT_ELEMENTS = frozenset({"title", "style", "script", "noscript"})

        class Collector(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.metas: list[dict[str, str]] = []
                self.stray: list[str] = []
                self.in_head = False
                self.text_depth = 0

            def handle_starttag(self, tag: str, attrs) -> None:
                if tag == "head":
                    self.in_head = True
                elif not self.in_head:
                    return
                elif tag == "meta":
                    self.metas.append({k: (v or "") for k, v in attrs})
                elif tag in TEXT_ELEMENTS:
                    self.text_depth += 1

            def handle_endtag(self, tag: str) -> None:
                if tag == "head":
                    self.in_head = False
                elif tag in TEXT_ELEMENTS and self.text_depth:
                    self.text_depth -= 1

            def handle_data(self, data: str) -> None:
                if self.in_head and not self.text_depth and data.strip():
                    self.stray.append(data.strip())

        collector = Collector()
        collector.feed(head)
        collector.close()
        return collector.metas, collector.stray

    def test_the_head_is_well_formed_markup(self):
        """A meta tag that lost its `>` swallows the next one — and the regexes still pass.

        MEASURED ON THIS VERY CHANGE, which is why this test exists. Two pages were
        edited to add `twitter:image:alt`; the edit dropped the closing `>` of the
        `og:image:alt` line, so the file read

            <meta property="og:image:alt" content="…"
            <meta name="twitter:image:alt" content="…">>

        Every value assertion in `test_every_page_declares_a_social_preview` passed on
        both pages (`_og`/`_twitter` match `name="twitter:…"` anywhere in the head,
        including inside text another tag swallowed). A browser instead parsed ONE
        element, found no `twitter:image:alt` at all, and leaked a bare `>` into the
        head as visible text. A guard whose verdict is unchanged by a broken tag is
        not measuring the tag.

        So the structure is asserted from the parsed tree: every `<meta` the source
        spells must arrive as its OWN element (a swallowed one loses the count), and
        no non-whitespace text may appear in the head at all.
        """
        for page in self._pages():
            text = page.read_text()
            assert "<head" in text and "</head>" in text, f"{page.name} has no head"
            markup = self._head(text)

            metas, stray = self._head_metas(markup)
            assert not stray, (
                f"{page.name} emits text into <head>: {stray[:3]} — almost always the "
                "remnant of a tag that was not closed, which a browser renders as a "
                "stray character and a scraper ignores")
            assert markup.count("<meta") == len(metas), (
                f"{page.name} spells {markup.count('<meta')} '<meta' occurrences in the head "
                f"but only {len(metas)} parse as elements — a tag lost its '>' and "
                "swallowed the next one")

            # A swallowed tag, a stray quote or a doubled `"` all show up as an
            # attribute that has no business being there — a bare `"` parses as an
            # attribute whose KEY is a quote character. An all-or-nothing key check
            # is what catches the shape; no value assertion can, because the value
            # the regex reads is the one before the damage.
            allowed = {"property", "name", "content", "charset", "http-equiv"}
            for meta in metas:
                assert meta, f"{page.name} has a <meta> element with no attributes"
                odd = set(meta) - allowed
                assert not odd, (
                    f"{page.name} parsed a <meta> with attribute key(s) {sorted(odd)!r} "
                    f"({meta}) — a stray or doubled quote leaves a quote character "
                    "where an attribute name should be, and a swallowed tag leaves tag "
                    "text there")
                assert "<" not in " ".join(meta.values()) and ">" not in " ".join(meta.values()), (
                    f"{page.name} parsed a <meta> whose attributes contain tag text "
                    f"{meta} — the following tag was swallowed by an unclosed one")

    def test_every_page_declares_a_social_preview(self):
        """`og:image` + its size + alt, and the Twitter mirror, on every page."""
        for page in self._pages():
            head = self._head(page.read_text())
            image = self._og(head, "image")
            assert image, f"{page.name} declares no og:image — a shared link renders no card"
            assert image.startswith("https://"), (
                f"{page.name} og:image is {image!r}; a link-preview scraper does NOT "
                "resolve a relative URL, so a same-origin path silently renders no card "
                "at all while the page looks configured (the failure mode the bespoke "
                "preview pages already document)")
            assert image.split("/")[2] in self.SITE_ORIGINS, (
                f"{page.name} og:image points at {image.split('/')[2]}, not one of "
                f"{sorted(self.SITE_ORIGINS)} — the card must be served by us or the "
                "scraper fetches it from a host we do not control")
            assert self._og(head, "image:width") and self._og(head, "image:height"), (
                f"{page.name} declares og:image without its dimensions; scrapers that "
                "trust the tag then request an assumed size")
            alt = (self._og(head, "image:alt") or "").strip()
            assert alt, (
                f"{page.name} declares no usable og:image:alt — the card is unreadable to "
                "a screen reader and to any client that suppresses remote images (a "
                "whitespace-only value is not alt text)")
            assert (self._twitter(head, "image:alt") or "").strip() == alt, (
                f"{page.name} twitter:image:alt ({self._twitter(head, 'image:alt')!r}) "
                f"disagrees with og:image:alt ({alt!r}) — one platform then describes a "
                "different picture than the other")
            assert self._twitter(head, "card") == "summary_large_image", (
                f"{page.name} twitter:card is {self._twitter(head, 'card')!r}; without "
                "summary_large_image the 1200x630 image is cropped to a square thumbnail")
            assert self._twitter(head, "image") == image, (
                f"{page.name} twitter:image ({self._twitter(head, 'image')}) disagrees "
                f"with og:image ({image}) — one of the two platforms gets the wrong picture")

    def test_the_declared_preview_size_matches_the_file(self):
        """The tag is a promise about bytes; check it against the bytes.

        The per-page checks live in `_card_violations` so the PAGE-LEVEL application can
        be driven by a test (`test_the_page_card_predicate_applies_every_check_it_
        declares`); this loop applies them to every shipped page, which is what makes the
        tree's own cards guarded rather than only their helpers.
        """
        for page in self._pages():
            head = self._head(page.read_text())
            violations = self._card_violations(page.name, head)
            assert not violations, "\n".join(violations)

    def test_the_page_card_predicate_applies_every_check_it_declares(self):
        """NON-VACUITY for `_card_violations`: each branch REDS through the predicate.

        ⛔ WHY. MEASURED on the code before this predicate existed, all 13 tests in this
        class GREEN under each of three mutants — the floor application deleted,
        `_card_size` reverted to `_jpeg_size`, and the image resolved with the raw
        `image.split("//", 1)[-1]` form. The helpers were pinned and nothing pinned the
        loop that called them, so the loop's half could be `assert True`. That is the
        identical defect the icon half of this class was already fixed for.
        """
        good = self._head((self.WEB / "index.html").read_text())
        image = self._og(good, "image")
        assert image, "the fixture page declares no og:image"
        assert self._card_violations("index.html", good) == [], (
            "the shipped index.html was reported as violating its own card contract: "
            f"{self._card_violations('index.html', good)}")
        card_path, card_bytes = self._og_card_bytes(image)
        assert card_path is not None and len(card_bytes) > self.CARD_MIN_BYTES, (
            "the fixture card does not resolve in the served tree")

        def violations(head: str, load) -> list[str]:
            return self._card_violations("synthetic.html", head, load=load)

        # 1. THE ENCODED-SIZE FLOOR, applied by the PREDICATE and not merely by the
        #    helper: a flat 1200x630 JPEG whose pixels are nothing.
        flat = b"\xff\xd8" + b"\x00" * (self.FLAT_CARD_BYTES - 2)
        assert self._card_floor_violation("synthetic.html", card_path, flat), (
            "the fixture is not the flat card this case needs — FLAT_CARD_BYTES no "
            "longer trips the floor")
        flat_v = violations(good, lambda _url: (card_path, flat))
        assert any("floor" in v and "flat" in v for v in flat_v), (
            "a flat card fed through the page predicate produced no floor violation, so "
            f"the loop is not applying it; got {flat_v}")

        # 2. THE FORMAT DISPATCH reads the type the PAGE declares, and holds the bytes to
        #    it. Reverting `_card_size` to a JPEG-only reader reds the first case.
        png = self._two_colour_png(180)
        png_typed = good.replace('content="image/jpeg"', 'content="image/png"')
        assert png_typed != good, "the fixture head does not declare og:image:type"
        png_ok = violations(png_typed, lambda _url: (card_path, png))
        assert not any("og:image:type" in v for v in png_ok), (
            "a page that honestly declares a PNG card was reported as failing the "
            f"format check — the predicate is not dispatching on the declared type: {png_ok}")
        jpeg_typed_over_png = violations(good, lambda _url: (card_path, png))
        assert any("og:image:type" in v for v in jpeg_typed_over_png), (
            "a card whose bytes contradict the declared og:image:type passed the "
            f"predicate; got {jpeg_typed_over_png}")

        # 3. THE QUERY-STRIPPING PATH, with the REAL loader: a `?v=2` cache-bust on a
        #    correct tag resolves to the served file. Restoring the raw split form reds
        #    this — the helper test alone cannot, because it calls `_og_asset_path`
        #    directly rather than through the predicate.
        busted = good.replace(image, image + "?v=2")
        assert busted != good, "the fixture head does not carry the card URL"
        bust_v = self._card_violations("synthetic.html", busted)
        assert not any("not in the served tree" in v for v in bust_v), (
            "a legitimate ?v=2 cache-bust turned the card into a 404 in the predicate — "
            f"the query is not being stripped on this path: {bust_v}")

        # 4. AN ABSENT CARD and a page with no og:image at all, both reported.
        missing = good.replace(image, image.rsplit("/", 1)[0] + "/nope.jpg")
        assert any("not in the served tree" in v
                   for v in self._card_violations("synthetic.html", missing)), (
            "a card URL that resolves to nothing was accepted")
        headless = self._head("<head></head>")
        assert any("no og:image" in v
                   for v in self._card_violations("synthetic.html", headless)), (
            "a head with no og:image produced no violation")

        # 5. A USER-ENTERED size that is not a pixel count is a violation, not a crash:
        #    `int()` on a non-numeric attribute raised out of the loop before this split.
        junk = good.replace('content="1200"', 'content="wide"')
        assert junk != good, "the fixture head does not declare og:image:width"
        junk_v = self._card_violations("synthetic.html", junk)
        assert any("not a pixel count" in v for v in junk_v), (
            f"og:image:width='wide' did not produce a dimension violation: {junk_v}")

        # 6. A DECLARED size that disagrees with the bytes, so the dimension comparison
        #    itself is pinned (deleting it otherwise leaves every case above green).
        wrong = good.replace('content="1201"', 'content="1202"')
        if wrong == good:
            wrong = good.replace('content="1200"', 'content="1201"')
        wrong_v = self._card_violations("synthetic.html", wrong)
        assert any("1201x630" in v or "1x630" in v for v in wrong_v), (
            f"a declared width one pixel off the file was not reported: {wrong_v}")

    def test_the_card_type_dispatch_reads_the_format_the_page_names(self):
        """NON-VACUITY for `_card_size`: BOTH reader branches, on real bytes.

        Every page in this tree declares `og:image:type: image/jpeg`, and the only PNG
        card the repo ever had is deleted by this PR — so the `image/png` branch of the
        dispatch was executed by NOTHING. A branch that returned the other format's
        numbers, or a `readers` dict reduced to JPEG alone, read green.

        Each branch is run here against bytes it must accept AND against bytes of the
        other format, so the dispatch cannot be weakened by deleting a reader; and an
        unknown type must be REFUSED rather than sniffed, because a sniffing guard
        accepts a lie about the type it was written to catch.
        """
        card = (self.WEB / "assets" / "og-tortoise.jpg").read_bytes()
        assert self._card_size("image/jpeg", card) == (1200, 630), (
            "the shipped card, declared as what it is, does not read its own SOF frame")

        png = self._two_colour_png(180)
        assert self._card_size("image/png", png) == (180, 180), (
            "a PNG card must be read by the PNG reader and report its own IHDR")

        for declared, raw, expected in (("image/jpeg", png, "not a JPEG"),
                                        ("image/png", card, "not a PNG")):
            try:
                size = self._card_size(declared, raw)
            except AssertionError as exc:
                assert expected in str(exc), (
                    f"declaring {declared!r} over the OTHER format must red with "
                    f"{expected!r}, got: {exc}")
            else:
                raise AssertionError(
                    f"declaring {declared!r} over bytes of the other format returned "
                    f"{size} — a lying og:image:type passed the guard")

        try:
            self._card_size("image/webp", card)
        except AssertionError as exc:
            assert "no reader" in str(exc), exc
        else:
            raise AssertionError("an unknown og:image:type was accepted, not refused")

    def test_the_content_guards_reject_a_blank_image_and_keep_their_measured_floors(self):
        """NON-VACUITY: each content guard must FAIL on the image it was written for.

        Every floor in this class was argued from measurements taken outside CI, and
        a guard's failure path is exactly what a PR body forgets. Asserted here: the
        decoder reads real pixels, a blank image really reds BOTH floors, and the
        card floor really sits above the flat JPEG that defeated its first value.

        ⛔ The two card numbers are a PAIR, on purpose. #11318's first card floor was
        20 KB while a flat 1200x630 JPEG at maximum quality measures 22,016 B — so
        the check passed the exact image it existed to reject. Raising the floor
        without re-measuring the flat case repeats the mistake; this pin makes that
        red instead of green.
        """
        blank = self._flat_png()
        counts = self._png_palette(blank)
        assert sum(counts.values()) == 256 * 256, (
            "the decoder did not count every pixel of a 256x256 PNG, so a dominant-colour "
            "ratio computed from it is meaningless")
        assert len(counts) == 1, f"a flat PNG decoded to {len(counts)} colours, not 1"

        problems = self._icon_mark_violations(self.ICON_PNG, blank)
        assert len(problems) == 2, (
            "a blank icon must trip BOTH content floors (distinct colours and dominant "
            f"colour); it tripped {len(problems)}: {problems}")

        # A SYNTHETIC fixture is not in the served tree, so its message must not name a
        # `website/…` path — that sends the reader looking for a file that never existed.
        synthetic = self._icon_mark_violations("a synthetic fixture", blank, in_tree=False)
        assert synthetic and not any(p.startswith("website/") for p in synthetic), (
            "a synthetic fixture's message claims a `website/…` path: "
            f"{synthetic}")

        # ...and the OTHER direction, which nothing measured: a legitimate TWO-COLOUR
        # mark must not red. Without this fixture `ICON_MIN_COLOURS` can go back to 8 —
        # the review-1 defect, red on a `crispEdges`/pixel-art mark — and every test in
        # this class stays green, because the only icons in the tree decode to 200+
        # colours. A floor is only pinned when both of its edges are.
        mark = self._two_colour_png()
        mark_counts = self._png_palette(mark)
        assert len(mark_counts) == 2, (
            f"the two-colour fixture decoded to {len(mark_counts)} colours, so it isolates "
            "nothing")
        assert max(mark_counts.values()) / sum(mark_counts.values()) < self.ICON_MAX_DOMINANT, (
            "the fixture's accent covers too little of the canvas to isolate the "
            "colour-count floor from the dominant-colour one")
        assert self._icon_mark_violations(
            "the two-colour fixture", mark, in_tree=False) == [], (
            "a hard-edged two-colour mark reded "
            f"({self._icon_mark_violations('the two-colour fixture', mark, in_tree=False)}) "
            f"— ICON_MIN_COLOURS "
            f"is {self.ICON_MIN_COLOURS}, and a mark without antialiasing is art the "
            "next person might choose, not a blank rectangle")

        assert self.FLAT_CARD_BYTES < self.CARD_MIN_BYTES, (
            f"the card floor ({self.CARD_MIN_BYTES} B) is not above the flat 1200x630 "
            f"JPEG it exists to reject ({self.FLAT_CARD_BYTES} B), so a flat card passes")
        flat_card = b"\xff\xd8" + b"\x00" * (self.FLAT_CARD_BYTES - 2)
        # ⛔ AND PADDING IS NOT PAINT. Measuring FILE length let a flat image clear the
        # floor by appending zeros after the EOI — measured: +36 KB of trailing zeros
        # took a 4,723 B flat card past 40,000 B green. The floor reads the ENCODED
        # image, so the same bytes padded are still rejected.
        padded = flat_card[:2] + b"\x00" * (self.FLAT_CARD_BYTES - 4) + b"\xff\xd9"
        padded += b"\x00" * (self.CARD_MIN_BYTES + 1_000 - len(padded))
        assert len(padded) > self.CARD_MIN_BYTES, "the padding fixture is not past the floor"
        assert self._card_floor_violation("page.html", Path("card.jpg"), padded), (
            "a flat card PADDED past the floor cleared it — the floor is measuring the "
            "file, not the encoded image, and trailing bytes are not paint")

        # ...and the DECODER itself, on the path every real icon takes. Both fixtures
        # above emit filter type 0 only, so Sub/Up/Average/Paeth reconstruction was
        # executed by no test — and a wrong predictor there invents MORE colours and a
        # LOWER dominant share, i.e. it satisfies both floors while the guard reads
        # noise as art. The failure direction is a false PASS, so nothing else in this
        # file would notice; this fixture is patterned row by row so each filter has a
        # different, non-degenerate predictor.
        filtered, expected, filters = self._filtered_png()
        assert filters == [0, 1, 2, 3, 4], (
            f"the fixture no longer exercises every PNG filter type (got {filters}) — "
            "then the reconstruction branch is untested again")
        assert self._png_palette(filtered) == expected, (
            "the decoder did not reproduce the pixels a filter-type-1..4 PNG encodes — "
            "every real icon in the tree uses those filters")
        assert self._card_floor_violation("page.html", Path("card.jpg"), flat_card), (
            "a card the size of a measured flat JPEG cleared the floor")
        # ...and the card that ships is what clears it, so the floor is not just
        # "always red" and not "any file at all".
        shipped = (self.WEB / "assets" / "og-tortoise.jpg").read_bytes()
        assert self._card_floor_violation("page.html", Path("og-tortoise.jpg"),
                                          shipped) is None, (
            f"the shipped card ({len(shipped)} B) no longer clears the floor — look at "
            "it, then re-measure before touching CARD_MIN_BYTES")

        # ⛔ AND A TRAILING `FF D9` IN THE PADDING RE-OPENED THE HOLE. `rfind` finds the
        # last EOI in the FILE, and the two bytes that close padding are indistinguishable
        # from an image's end once you are outside any scan — measured: the same 4,723 B
        # flat card + zeros + `FF D9` = 41,500 B cleared the floor green. The walk now
        # stops at the EOI that ends the SCAN, so the trailer is not paint.
        trailer = (flat_card[:2] + b"\x00" * (self.FLAT_CARD_BYTES - 4) + b"\xff\xd9"
                   + b"\x00" * (self.CARD_MIN_BYTES + 1_000 - self.FLAT_CARD_BYTES)
                   + b"\xff\xd9")
        assert len(trailer) > self.CARD_MIN_BYTES, "the trailer fixture is not past the floor"
        assert self._card_floor_violation("page.html", Path("card.jpg"), trailer), (
            "a flat card padded and CLOSED WITH FF D9 cleared the floor — the measure is "
            "taking the last EOI in the file instead of the one that ends the image")

        # ⛔ AND THE MEASURE IS FORMAT-AWARE. A PNG's image ends at its IEND chunk; `FF D9`
        # inside IDAT is ordinary compressed data, so a measure that hunts `FF D9` both
        # FALSE-PASSES a padded PNG card and FALSE-FAILS a real one. `website/logo.png`
        # is the real witness: 35,670 B, and its IDAT happens to contain `FF D9`.
        # ⛔ MEASURED AT THIS HEAD, because the number is not folklore: the 6,280-short
        # (29,390 B) reading belongs to the SUPERSEDED `rfind`-for-the-last-`FF D9`
        # measure. The current segment/scan walk reads `logo.png` at its full 35,670 B
        # even when handed the JPEG branch, so it does NOT stop at an interior `FF D9`.
        # What the pin below therefore witnesses is narrower than the bug story: that
        # whichever branch ran ended at the file's own end. HEAD-MEASURED, it can NOT
        # tell the PNG branch from the JPEG one on `logo.png` — deleting the PNG branch
        # leaves it green, because the current JPEG walk also reads this PNG in full.
        # The padded-PNG fixture above is what catches a dispatch regression; this
        # assertion only catches a walk that ends anywhere but the file's own end.
        padded_png = self._flat_png(64)
        padded_png += b"\x00" * (self.CARD_MIN_BYTES + 1_000 - len(padded_png))
        assert self._card_floor_violation("page.html", Path("card.png"), padded_png), (
            "a flat PNG padded past the floor cleared it — the floor measured the file, "
            "not the PNG's chunks")
        in_tree_png = (self.WEB / "logo.png").read_bytes()
        assert b"\xff\xd9" in in_tree_png, (
            "website/logo.png no longer contains an FF D9 byte pair, so it no longer "
            "isolates the JPEG-delimiter bug it was chosen for — pick another PNG (or "
            "state that this witness is gone)")
        assert self._encoded_length(in_tree_png) == len(in_tree_png), (
            f"website/logo.png ({len(in_tree_png)} B) measures "
            f"{self._encoded_length(in_tree_png)} B — the PNG chunk walk did not end at "
            "the file's own end (a mis-parsed chunk length, or trailing bytes after "
            "IEND)")
        assert self._encoded_length(shipped) == len(shipped), (
            f"the shipped JPEG ({len(shipped)} B) measures "
            f"{self._encoded_length(shipped)} B — its stream must end at its own EOF")

        # The VALUE readers accept honest markup. A reordered attribute and a
        # cache-busting query are identical to a browser and to every scraper; the
        # source-order regexes they replaced called both broken (#11318 review 2).
        tag = ('<head><meta content="https://tortoise.premiselabs.co/assets/'
               'og-tortoise.jpg?v=2" property="og:image">')
        image = self._og(tag, "image")
        assert image is not None and image.endswith("og-tortoise.jpg?v=2"), (
            f"a reordered og:image tag read as {image!r} — that is the false red the "
            "parser replaced the regex for")
        assert self._og_asset_path(image).is_file(), (
            "a legitimate `?v=2` cache-bust on a correct og:image resolves to no file")

    def test_the_shared_preview_is_the_agreed_size(self):
        """1200x630 is the size the scrapers crop to; the shared image must be it."""
        shared = self.WEB / "assets" / "og-tortoise.jpg"
        assert shared.is_file(), "the shared preview image is missing"
        assert self._jpeg_size(shared.read_bytes()) == self.OG_SIZE, (
            f"assets/og-tortoise.jpg is not {self.OG_SIZE[0]}x{self.OG_SIZE[1]}")

    def test_the_two_pasted_urls_carry_the_preview_from_the_one_host(self):
        """The two URLs that get pasted into a chat: `premiselabs.co` and its `product root`.

        ⛔ THIS USED TO BE A TWO-ORIGIN TEST, and the property it guarded was "the
        card comes from the host the page is on" — `index.html` from
        premiselabs.co, `product.html` from tortoise.premiselabs.co. Epic #11762
        removed the second host (it is now a permanent 301), so that pair can no
        longer be the assertion: the surviving property is simply "both pasted
        URLs resolve to one origin, and both cards name it". Asserting the old
        pair would have pinned the very defect the epic removes.
        """
        origin = "https://premiselabs.co/"
        for name in ("index.html", "product.html"):
            head = self._head((self.WEB / name).read_text())
            image = self._og(head, "image")
            assert image and image.startswith(origin), (
                f"{name} is served from {origin} but points its preview at {image} — "
                "the card must come from the host that serves the page")

    # --- the icon, on EVERY surface ------------------------------------

    DASHBOARD_ROOT = WEB / "apps" / "dashboard" / "public"
    ICON_FILES = ("favicon.ico", "favicon.png", "apple-touch-icon.png")

    # ⛔ THE TWO RELATIONS THE TRIO USES, BY NAME — never a substring of the value.
    # `mask-icon` and `apple-touch-icon-precomposed` are DISTINCT relation tokens
    # (WebKit's own parser and Google's favicon guidance both separate them from `icon`),
    # so `"icon" in token` simultaneously refused a correct page that adds a legitimate
    # `mask-icon` and could not tell `rel="icon"` from `rel="apple-touch-icon"`.
    ICON_RELS = ("icon", "apple-touch-icon")

    # The app SURFACE is its own Pages deployment on its own origin, so its pages may
    # name `https://app.premiselabs.co/…` absolutely. `SITE_ORIGINS` stays the marketing
    # pair on purpose: it is what the `og:image` guard holds a card to, and widening THAT
    # would loosen a different check.
    ICON_ORIGINS = frozenset({*SITE_ORIGINS, "app.premiselabs.co"})

    @classmethod
    def _icon_hrefs(cls, html: str) -> list[str]:
        """The `href` of every tab-icon / touch-icon link in a page's head, in order.

        ⛔ THE RELATION IS NAMED, NOT SUBSTRING-MATCHED. `mask-icon` and
        `apple-touch-icon-precomposed` are DISTINCT relation tokens, so the previous
        `any("icon" in token for token in rel)` counted a legitimate `mask-icon` as a
        fourth tab icon (a FALSE RED on a correct page) and matched every one of the
        three kinds identically (the fail-open the rel assertion below closes).
        """
        out = []
        for link in cls._links(cls._head(html)):
            rel = link.get("rel", "").lower().split()
            if any(token in cls.ICON_RELS for token in rel):
                out.append(link.get("href", ""))
        return out

    @staticmethod
    def _href_version(href: str) -> str | None:
        query = href.split("?", 1)[1] if "?" in href else ""
        for part in query.split("&"):
            if part.startswith("v="):
                return part[2:]
        return None

    @staticmethod
    def _head_element_count(rendered: str) -> int:
        r"""How many `<head>` ELEMENTS a source renders — `<header>` is not one.

        ⛔ SPLIT OUT SO THE COUNT IS DRIVEN BY AN ASSERTION, not only by the surface test.
        The first cut was `rendered.count("<head")`, which counts a `<header>` element as a
        second `<head>`: `<header>` starts with the substring `<head`, and every one of the
        three self-rendered sources could legitimately gain one. It refused an honest page
        with a diagnosis ("a SECOND document … grade each one") describing a document that
        does not exist — a false red, and the class this file's `_icon_trio_violations`
        docstring says must not ship.

        ⛔ A WORD BOUNDARY ALONE IS NOT ENOUGH, IN EITHER DIRECTION (review rounds 7-8).
        `<head\b` is CASE-SENSITIVE and HTML tag names are not: `<HEAD>` counted 0, so a
        genuine second document spelled in uppercase passed `heads == 1` and the guard
        sliced the first while reporting green for both — the fail-open this count exists to
        prevent. And the boundary still matched `<head` inside a JS STRING: these sources
        build HTML with `.replace()` / concatenation, so `html.replace("<head", "<head>")`
        counted 2 and refused an honest edit. `(?=[\s/>])` requires the tag name to END there
        (so `<header>`, `<heading>`, `<head-x>` and `"<head"` are not matches) and `re.I`
        makes the name case-insensitive — the same flag `HEADING_TAG_RE` (`:2577`) carries.
        `<head>`, `<head >`, `<head\t>`, `<head\n>` and `<head/>` each count one, in any case.
        """
        return len(re.findall(r"<head(?=[\s/>])", rendered, re.I))

    def test_every_surface_declares_the_same_versioned_icon_set(self):
        """ONE mark on every origin — and a URL that changes when the bytes do.

        Two defects, both reported visibly as "this page has a different tab icon":

        1. **Two deployments, three marks.** `app.premiselabs.co` is a SEPARATE Pages
           project (root `website/apps/dashboard/public`), and its entry points declared
           `data:,` (a blank icon, so the browser drew its generic glyph) or `/logo.png`
           (a 400x244 wordmark, squashed into the square slot). The marketing pages had
           the real trio, so the same brand showed three different tabs (#11407).
        2. **The URL never moved when the artwork did.** `/favicon.ico` carries
           `max-age=14400` and a browser keeps a favicon far longer than any response
           header, so re-exporting the mark left old tabs — and old pages whose HTML was
           cached — drawing the superseded slab indefinitely. The header is the measurable
           part, and it is measured: `curl -sI https://tortoise.premiselabs.co/favicon.ico`
           → `cache-control: public, max-age=14400, must-revalidate`. Every href therefore
           carries `?v=<sha256[:12]>`, and the version is asserted
           against the file's OWN bytes below, so a re-export without a bump reds here.

        The `sizes` attributes are asserted for the marketing tree by
        `test_every_page_declares_the_icon_trio`, and for BOTH trees by
        `_surface_icon_violations` (which runs the same name-dispatching trio guard);
        this test is about the SET being identical across trees and self-invalidating,
        which is what harmonising means.
        """
        expected = {}
        for name in self.ICON_FILES:
            expected[f"/{name}"] = hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
        assert len(set(expected.values())) == len(expected), (
            f"two icon files are byte-identical, so one version no longer identifies one "
            f"asset: {expected}")

        # The dashboard project serves the SAME bytes: that is what stops the two
        # deployments drifting into two marks, and a re-export that forgets one tree reds.
        for name in self.ICON_FILES:
            local = self.DASHBOARD_ROOT / name
            assert local.is_file(), (
                f"the dashboard project has no {name} — app.premiselabs.co would fall back to "
                "a generic tab icon (or 404) while the marketing host shows the real one")
            assert local.read_bytes() == (self.WEB / name).read_bytes(), (
                f"website/apps/dashboard/public/{name} differs from website/{name} — the app "
                "origin and the marketing origin would show different marks")

        # ⛔ THE /admin CONSOLE IS A SURFACE TOO. `website/apps/blog-admin/index.html` is
        # staged to `dist/admin/` and served on the APP origin, so a browser there asks for
        # `/favicon.ico` — the UNVERSIONED path — unless the page declares a versioned link.
        # It declared none, so a stale console tab after a re-export was still possible while
        # every on-disk surface was covered (review round 1). Its links resolve against the app
        # root, and it may name that origin absolutely, so it grades with the app surface.
        surfaces = {
            "marketing": (self._pages(), self.WEB, self.SITE_ORIGINS),
            # `rglob`, not `glob`: a page in a SUBDIRECTORY of `public/` is served on the
            # same origin by the same project, so `glob("*.html")` would leave it silently
            # ungraded — the "a page the guard never discovers" shape. There is no nested
            # HTML today, so this changes no current result; it closes the door on one.
            "app": ([self.WEB / "apps" / "dashboard" / "index.html",
                     *sorted(self.DASHBOARD_ROOT.rglob("*.html"))],
                    self.DASHBOARD_ROOT, self.ICON_ORIGINS),
            "app-admin": ([self.WEB / "apps" / "blog-admin" / "index.html",
                           # The COMMITTED built shell. It is a TRACKED, served artifact and
                           # the repo's source-of-record (#7749: a source change without a
                           # rebuild leaves it behind for a whole release), so grading only
                           # the vite SOURCE let the deployed `/admin` document keep the
                           # pre-fix head — no icon link, hence the UNVERSIONED fallback.
                           self.WEB / "apps" / "blog-admin" / "dist" / "index.html"],
                          self.DASHBOARD_ROOT, self.ICON_ORIGINS),
        }
        # ⛔ `assert pages` ALONE IS NEARLY VACUOUS HERE: two of the three surfaces build
        # their list from LITERALS, so truthiness could only ever fail for `marketing`, and
        # the app surface's `glob("*.html")` half could shrink to nothing without a word.
        # Names are asserted instead — a page that stops being found is the failure this
        # guard exists to notice, and it must not be quieter than a page that breaks.
        expected_pages = {
            "marketing": 14,
            "app": 5,
            # 2, not 1: the vite SOURCE and the committed `dist/` shell are both graded —
            # `dist/index.html` disappearing from this list is exactly the drift #7749
            # describes, and a minimum of 1 would let it happen in silence.
            "app-admin": 2,
        }
        for label, (pages, root, origins) in surfaces.items():
            assert pages, f"no {label} pages found — the guard would pass vacuously"
            assert len(pages) >= expected_pages[label], (
                f"the {label} surface found only {len(pages)} page(s), not the "
                f"{expected_pages[label]} it declares: "
                f"{sorted(p.name for p in pages)} — a page that stopped being discovered "
                "passes every assertion below by not being graded")
            found = self._grade_surface(label, pages, root, origins, expected)
            assert not found, "\n".join(found)

        # ⛔ NO GLOB REACHES THESE — they are STRINGS in TypeScript, not pages on disk, so
        # each is graded through `_surface_icon_violations` on its own text, against the
        # root that actually SERVES it. The blog head is the surface a shared link opens.
        # The other two are on the APP origin and rendered a full `<head>` with NO icon
        # link at all, so both fell back to the UNVERSIONED `/favicon.ico` — the defect
        # this test exists to stop, one file over (review round 2).
        # ⛔ GRADED, NOT SUBSTRING-MATCHED (review round 3). The first cut asserted only
        # `href="/favicon.ico?v=<sha>" in source`. That is rel-BLIND, so `rel="preload"`
        # ("mentions the file and gives the tab no icon at all" — this file's own words),
        # every `rel="icon"` demoted to `apple-touch-icon`, and the whole tag commented
        # out all passed. Reusing the page predicate closes it with no new code: it
        # dispatches the rel by NAME, requires each of the trio, refuses `data:` and the
        # wordmark, resolves the origin, checks the file exists under the serving root, and
        # compares the `?v=` to the digest. Each source gets ITS OWN root because a self-rendered
        # document is served by a project, and asserting existence against the right one
        # is the point.
        # ⚠️ `website/functions/_middleware.ts`'s refusal page is a deliberate EXCEPTION and
        # stays unlinked: its own contract forbids an outbound request while a credential is
        # in the URL — so it is excluded by DECISION, not by oversight.
        # ⛔ EACH SOURCE CARRIES THE URL IT IS SERVED AT (review round 8 — the same defect the
        # round-3 `base` fix closed for disk pages): `_icon_ref` resolves a href the way a
        # BROWSER does, against the page that carries it, so grading these three against
        # `SITE_ROOT` alone read a document-relative `favicon.ico?v=…` in `confirm.ts` as
        # GREEN while the browser requests `/auth/favicon.ico` and 404s. All three sources use
        # root-relative hrefs today (verified), so this changes no current answer.
        self_rendered = {
            "website/functions/blog/_lib.ts": (
                self.WEB / "functions" / "blog" / "_lib.ts", self.WEB, self.SITE_ORIGINS,
                self.SITE_ROOT + "blog/"),
            "website/apps/dashboard/functions/auth/confirm.ts": (
                self.WEB / "apps" / "dashboard" / "functions" / "auth" / "confirm.ts",
                self.DASHBOARD_ROOT, self.ICON_ORIGINS, self.SITE_ROOT + "auth/"),
            "website/apps/dashboard/functions/admin/[[path]].ts": (
                self.WEB / "apps" / "dashboard" / "functions" / "admin" / "[[path]].ts",
                self.DASHBOARD_ROOT, self.ICON_ORIGINS, self.SITE_ROOT + "admin/"),
        }
        for source_label, (source, root, origins, page_url) in self_rendered.items():
            rendered = source.read_text()
            # `self._head_element_count`, not `count("<head")`: a `<header>` element would
            # have counted as a second `<head>` and refused an honest page with a diagnosis
            # ("grade each one, or list the new document") that describes a document that
            # does not exist. The helper's docstring carries the two failure directions it
            # closes; what DRIVES it is `test_a_header_element_is_not_a_second_head_document`.
            heads = self._head_element_count(rendered)
            assert heads == 1, (
                f"{source_label} renders {heads} <head> element(s), but this guard grades ONE "
                "document per source (it slices the first `<head>…</head>`). A SECOND "
                "document added to this file would ship with no icon and no `?v=` while every "
                "assertion here stayed green — grade each one, or list the new document "
                "separately, rather than letting the guard quietly cover the first only")
            violations = self._surface_icon_violations(
                "self-rendered", source_label, rendered, root, expected, origins,
                page_url=page_url)
            assert not violations, "\n".join(violations)

    def _grade_surface(self, label: str, pages, root: Path,
                       origins: frozenset[str], expected: dict[str, str]) -> list[str]:
        """Every violation on one SURFACE, driven through the real page-URL threading.

        Split out of `test_every_surface_declares_the_same_versioned_icon_set` so a test can
        drive a NESTED page through `_page_url` rather than passing `page_url=` itself: with
        the loop inlined, replacing `page_url=self._page_url(page, root)` with `page_url=None`
        — or making `_page_url` return a constant — left every assertion green (review
        round 4). A guard whose threading no test executes cannot be told from a no-op.
        """
        violations: list[str] = []
        for page in pages:
            violations += self._surface_icon_violations(
                label, page.name, page.read_text(), root, expected, origins,
                page_url=self._page_url(page, root))
        return violations

    def _page_url(self, page: Path, root: Path) -> str | None:
        """The URL a page is SERVED at — or `None` when this guard cannot know it.

        The marketing site is served from `website/`, so `website/faq.html` is `/faq.html`.
        (`website/pricing/index.html` would be `/pricing/`, and this resolves it correctly;
        the MARKETING surface does not discover nested pages at all — that is #4111, not a
        base-resolution bug.)

        ⛔ `None` IS NOT THE SITE ROOT (review round 4). A project whose staged root is not
        the page's ancestor — the dashboard's `index.html` above `public/`, the blog-admin
        console served at `/admin`, the `/auth/confirm` Function — cannot have its served
        path derived here, and returning `SITE_ROOT` for those pages resolved a DOCUMENT-
        RELATIVE href against the MARKETING root and graded it green, while the browser
        requests `/admin/favicon.ico` (answered by the admin gate Function) or
        `/auth/favicon.ico` (a 404). Measured: `href="favicon.ico?v=…"` on
        `apps/blog-admin/index.html`, on its committed `dist/index.html`, and in
        `functions/auth/confirm.ts` were all green. `None` makes the collector REFUSE a
        document-relative href instead of guessing, which is the only honest answer.
        """
        try:
            rel = page.parent.relative_to(root)
        except ValueError:
            return None
        if rel == Path("."):
            return self.SITE_ROOT
        return self.SITE_ROOT + rel.as_posix() + "/"

    def _surface_icon_violations(self, label: str, page_name: str, head: str,
                                 root: Path, expected: dict[str, str],
                                 origins: frozenset[str], *,
                                 page_url: str | None = None) -> list[str]:
        """Every way ONE page on ONE origin breaks the icon contract; empty = it passes.

        Split out of the test above so the page-level application is DRIVEN BY AN
        ASSERTION on both trees, the way `_icon_trio_violations` was split out for the
        marketing one.

        ⛔ THE REL IS GRADED HERE, BY NAME, ON BOTH TREES. The first cut folded the app
        pages through `_icon_hrefs` alone — a count, an href set, an existence check and
        a `?v=` compare — none of which reads `rel`. So replacing
        `rel="apple-touch-icon"` with `rel="icon"` on an app page left EVERY assertion
        green while `app.premiselabs.co` stopped declaring the icon iOS saves to the
        home screen (it draws a page screenshot instead). That is the href-only blind
        spot this class already names for the marketing tree — "`<link rel="preload"
        href="/favicon.ico">` mentions the file and gives the tab no icon at all" — one
        origin over, and the app origin is the tree #11407 was REPORTED against. The
        existing trio guard dispatches `apple-touch-icon` by name and checks `sizes`,
        so running it here closes both gaps with no new predicate.
        """
        png = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {i["declared"][0]
                     for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())}

        violations = self._icon_trio_violations(
            page_name, head, png, ico_sizes, apple, origins=origins)

        # ⛔ NO `len(hrefs) != 3` HERE. It refused an HONEST page that adds a fourth icon
        # link (an SVG variant, a second `sizes=192x192` slot) with a message claiming the
        # trio was missing, which is the opposite of what happened — and it added no
        # detection power over `_icon_trio_violations`, which already reds a page that omits
        # any one of the three. A guard that reds honest artwork is the guard the next person
        # weakens instead of fixes (review round 2, measured on a `/tmp` copy of signup.html).
        hrefs = self._icon_hrefs(head)
        for href in hrefs:
            if href.startswith("data:"):
                violations.append(
                    f"{page_name} blanks its icon with {href!r} — a blank icon is not a "
                    "harmonised icon, it is the browser's generic glyph")
                continue
            # ⛔ RESOLVED THROUGH `_icon_ref`, WHICH IS WHAT MAKES `origins` REAL.
            # `href.split("?", 1)[0]` DROPPED the origin instead of checking it: an
            # absolute `https://app.premiselabs.co/favicon.ico?v=…` — the exact form
            # `ICON_ORIGINS`'s own comment says the app pages may use — kept its scheme
            # and host, matched no key in `expected`, and reded a CORRECT page with "is
            # not one of the three icon files" (the file it names IS one of the three).
            # That false red was the only place `origins` could have changed an answer,
            # so before this the parameter was unobservable too: every graded page uses
            # a root-relative href, which `_icon_ref` resolves through `SITE_ORIGINS`
            # whether or not `origins` is threaded — replacing
            # `ICON_ORIGINS` with `SITE_ORIGINS`, or deleting `origins` outright, left
            # this whole file green. Resolving here grades the origin that actually
            # serves the file: another host yields "" (so a protocol-relative
            # `//evil.example/favicon.ico` can no longer pass as `/favicon.ico`), and
            # this surface's own origin is accepted in both the absolute and
            # root-relative spellings. `test_the_app_tree_accepts_an_absolute_href_on_
            # its_own_origin` drives all four directions through a real app page.
            # ⛔ A DOCUMENT-RELATIVE HREF ON A PAGE WHOSE SERVED URL IS UNKNOWN IS
            # UNGRADEABLE, NOT RESOLVABLE (review round 4). Guessing `SITE_ROOT` graded
            # `/admin/favicon.ico` and `/auth/favicon.ico` green while the browser asked the
            # admin gate Function and a 404 respectively. Say so instead.
            if page_url is None and not href.startswith("/") and "://" not in href:
                violations.append(
                    f"{page_name} declares the document-relative icon href {href!r}, but this "
                    "surface's served URL cannot be derived from the tree — use a "
                    "root-relative href (`/favicon.png?v=…`), which is what every page here "
                    "already does, or the tab request goes to a path that serves something "
                    "else (or nothing)")
                continue
            resolved = self._icon_ref(
                href, origins=origins, base=page_url or self.SITE_ROOT)
            if not resolved:
                violations.append(
                    f"{page_name} declares {href!r}, which resolves to no icon file on "
                    f"this surface's origins {sorted(origins)} — the browser fetches "
                    "that tab icon from another host (or the href is not a URL), so the "
                    "mark on the tab is not ours")
                continue
            if "/logo.png" in resolved:
                violations.append(
                    f"{page_name} uses the wordmark as a tab icon ({href!r}) — 400x244 "
                    "squashed into a square slot (#11407)")
                continue
            # ⛔ A FOURTH icon link is ALLOWED. An honest page may add an SVG variant or a
            # second `sizes=` slot, and refusing it — with a message claiming the trio was
            # MISSING, which is the opposite of what happened — was the false red the
            # count's removal was meant to end. Dropping the count alone did not end it:
            # the membership rejection below the wordmark check still refused every extra,
            # which is why the comment said "a fourth is GREEN" and the code disagreed.
            # `_icon_trio_violations` above already reds a page that OMITS any one of the
            # three, and that is the property the contract needs; an extra adds a mark, it
            # does not remove one. What an extra IS still held to is the two rules that keep
            # a tab correct: the file must EXIST under this surface's own root (or the tab
            # request 404s), and it must be versioned with ITS OWN digest (or a re-export
            # keeps the old artwork drawing — the staleness bug this file exists for).
            local = root / resolved.lstrip("/")
            if not local.is_file():
                violations.append(
                    f"{page_name} links {href!r}, which does not exist under "
                    f"{root.name}/ — the tab request 404s")
                continue
            version = self._href_version(href)
            want = expected.get(resolved)
            if want is None:
                # A non-trio icon: its digest comes from its OWN bytes, so the same
                # self-invalidating rule applies to artwork the contract did not name.
                want = hashlib.sha256(local.read_bytes()).hexdigest()[:12]
            if version is None:
                violations.append(
                    f"{page_name} links {href!r} with NO `?v=` — declare "
                    f"`?v={want}` (this file's sha256 prefix). Without it a "
                    "browser or edge holding the old icon keeps drawing it after a "
                    "re-export, which is the stale-icon bug this contract exists for")
            elif version != want:
                violations.append(
                    f"{page_name} links {href!r} but the file's sha256 starts "
                    f"{want!r} — bump the `?v=` with the bytes, or a browser "
                    "holding the old icon keeps drawing it (that is the stale-icon bug)")

        # ⛔ AN EXTRA MAY NOT RE-DECLARE A TRIO FILE UNDER THE OTHER TRIO RELATION.
        # Allowing extras is right (round 4: refusing an honest SVG variant / second
        # `sizes=` was a false red), but `_icon_hrefs` above STRIPS the rel — it returns a
        # flat list of hrefs — so that loop cannot tell WHICH relation an extra carries.
        # Measured, before this rule: adding
        #   `<link rel="apple-touch-icon" href="/favicon.png?v=<its own digest>">`
        # to an otherwise-correct page PASSED every assertion — the file exists, it is one
        # of the trio, and its `?v=` is its real digest. But iOS resolves competing
        # `apple-touch-icon` links to ONE of them and this site declares no `sizes=` on
        # that link, so the saved home-screen mark could become the ROUNDED, TRANSPARENT
        # 256 px `favicon.png` — the double-rounding §6.7 says the opaque 180 px asset
        # exists to prevent. `rel="icon"` naming `apple-touch-icon.png` is the same hole
        # and is refused for the same reason: a relation names an ASSET here, not a size
        # preference, so a cross-relation extra silently swaps a mark instead of adding
        # one. Non-trio extras (an SVG variant, a 192 px slot, a repeat of the same file
        # under the same rel) are untouched, so round 4's false red does not come back.
        # Residual, stated rather than implied: a `sizes=` declared on a non-trio PNG/ICO
        # extra is not compared with that file's real pixels — closing that needs a byte
        # probe per extra, and no extra exists today.
        apple_asset = f"/{self.ICON_APPLE}"
        for link in self._links(self._head(head)):
            tokens = link.get("rel", "").lower().split()
            if not any(token in self.ICON_RELS for token in tokens):
                continue
            href = link.get("href", "")
            # Same base as the href loop above: resolving this one against the site root
            # false-RED a nested page whose LOCAL `apple-touch-icon.png` was named under
            # `rel="icon"` — a cross-relation swap that had not happened (review round 4).
            resolved = self._icon_ref(
                href, origins=origins, base=page_url or self.SITE_ROOT)
            if not resolved:
                continue  # data:/foreign/not-a-URL is already reported above
            # ⛔ THE ASSET IS NAMED BY ITS FILE, NOT BY ITS ROOT PATH (review round 4). A
            # nested page's own `apple-touch-icon.png` resolves to `/settings/…`, which is
            # not `/apple-touch-icon.png`, so a path comparison false-RED a page that had
            # swapped nothing. The relation names an ASSET, so the file name is the test.
            if "apple-touch-icon" in tokens and Path(resolved).name != self.ICON_APPLE:
                violations.append(
                    f"{page_name} declares {href!r} under rel=\"apple-touch-icon\", but "
                    f"that relation names {apple_asset} — iOS keys the saved home-screen "
                    "mark off this relation and resolves competing links to one, so a "
                    "cross-relation extra can replace the opaque 180 px tile with a "
                    "rounded/transparent one (#11407)")
            elif "icon" in tokens and Path(resolved).name == self.ICON_APPLE:
                violations.append(
                    f"{page_name} declares {href!r} under rel=\"icon\", but that is the "
                    "apple-touch-icon asset — a relation names an ASSET here, so a "
                    "cross-relation extra swaps a mark rather than adding one (#11407)")
        return [f"{label}: {v}" for v in violations]

    def test_a_document_relative_icon_href_needs_a_known_page_url(self, tmp_path):
        """A page whose served URL cannot be derived must REFUSE a relative href.

        Three real surfaces are in that position — the blog-admin console (served at `/admin`
        and `/admin/`, and `website/apps/blog-admin/vite.config.ts` documents the RFC 3986
        merge), the dashboard's `index.html` (above `public/`), and the `/auth/confirm`
        Function (rendered at `/auth/confirm`). For all three the guard cannot know the base,
        and returning the SITE ROOT for them resolved `favicon.ico?v=…` against the MARKETING
        root and graded it green while the browser requested `/admin/favicon.ico` (answered by
        the admin gate Function) or `/auth/favicon.ico` (a 404).

        This also pins the THREADING: `_page_url` is exercised here (not bypassed with an
        explicit `page_url=`), and `_grade_surface` is the same path the real surface loop
        takes — so making `_page_url` return a constant, or dropping the argument in the loop,
        reds here.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        for name in self.ICON_FILES:
            (tmp_path / name).write_bytes((self.WEB / name).read_bytes())

        # ---- `_page_url` itself, both branches -------------------------------------
        assert self._page_url(self.WEB / "faq.html", self.WEB) == self.SITE_ROOT
        assert self._page_url(self.WEB / "pricing" / "index.html", self.WEB) == (
            self.SITE_ROOT + "pricing/")
        assert self._page_url(
            self.WEB / "apps" / "dashboard" / "index.html", tmp_path) is None, (
            "a page whose staged root is NOT its ancestor has no derivable served URL — "
            "returning the site root here is what graded /admin and /auth/favicon.ico green")

        # ---- a nested page, through the REAL surface loop --------------------------
        nested = tmp_path / "settings"
        nested.mkdir()
        for name in self.ICON_FILES:
            (nested / name).write_bytes((self.WEB / name).read_bytes())
        relative = (self.WEB / "apps" / "dashboard" / "public" / "signup.html").read_text()
        for name, digest in expected.items():
            relative = relative.replace(f'href="{name}?v={digest}"', f'href="{name.lstrip("/")}?v={digest}"')
        nested_page = nested / "index.html"
        nested_page.write_text(relative)

        assert self._grade_surface(
            "sandbox", [nested_page], tmp_path, self.ICON_ORIGINS, expected) == [], (
            "a nested page's document-relative hrefs resolve against ITS OWN directory, where "
            "the same three files are present — this is the case that proves the threading: "
            "with `page_url=None` (or a constant base) it resolves against the site root")

        # ---- and a relative href with NO derivable base is reported ---------------
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        (orphan / "index.html").write_text(relative)
        found = self._grade_surface(
            "sandbox", [orphan / "index.html"], nested, self.ICON_ORIGINS, expected)
        assert found, (
            "a document-relative href on a page whose base cannot be derived must be REFUSED, "
            "not silently resolved against the site root")
        assert "document-relative icon href" in found[0], found[0]

        # ---- and a root-relative href on the same kind of page is still fine ------
        rooted = relative
        for name, digest in expected.items():
            rooted = rooted.replace(f'href="{name.lstrip("/")}?v={digest}"', f'href="{name}?v={digest}"')
        (orphan / "index.html").write_text(rooted)
        assert self._grade_surface(
            "sandbox", [orphan / "index.html"], nested, self.ICON_ORIGINS, expected) == [], (
            "an absolute (root-relative) href needs no base, so the fallback must not refuse "
            "it — that would be a false red on every real page")

    def test_the_app_tree_grades_the_icon_rel_by_name(self):
        """NON-VACUITY for the app-tree half: a swapped `rel` must red, both directions.

        Measured on the code before this: swapping `rel="apple-touch-icon"` for
        `rel="icon"` on `website/apps/dashboard/public/signup.html` left every assertion
        in `test_every_surface_declares_the_same_versioned_icon_set` green — 3 hrefs, all
        three in `expected`, all three files present, all three `?v=` matching. The
        mutated page is CORRECT by every count and still loses the iOS home-screen mark,
        which is why the assertion has to reach the relation and not the href.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        page = self.DASHBOARD_ROOT / "signup.html"
        head = page.read_text()

        def grade(html: str) -> list[str]:
            return self._surface_icon_violations(
                "app", page.name, html, self.DASHBOARD_ROOT, expected, self.ICON_ORIGINS)

        assert grade(head) == [], (
            "the real app page must pass, or the mutants below prove nothing:\n"
            + "\n".join(grade(head)))

        # Direction 1 — the touch icon declared as a tab icon (the review's surviving mutant).
        demoted = head.replace('rel="apple-touch-icon"', 'rel="icon"', 1)
        assert demoted != head, "fixture drift: signup.html no longer declares the apple rel"
        caught = grade(demoted)
        assert any(self.ICON_APPLE in v and "apple-touch-icon" in v for v in caught), (
            "a page that declares /apple-touch-icon.png under rel=\"icon\" loses the iOS "
            "home-screen mark and every count/href/version assertion still passes — the "
            f"rel must be graded. Got: {caught}")

        # Direction 2 — every tab icon declared as a touch icon: count is still 3.
        promoted = head.replace('rel="icon"', 'rel="apple-touch-icon"')
        caught = grade(promoted)
        assert any(self.ICON_ICO in v and "icon" in v for v in caught), (
            f"a page whose tab icons all claim apple-touch-icon must red. Got: {caught}")

        # Direction 3 — a legitimate `mask-icon` is a DIFFERENT relation: it must not be
        # counted as a fourth tab icon (the false red the substring filter produced).
        png_version = expected[f"/{self.ICON_PNG}"]
        mask_link = (f'  <link rel="mask-icon" href="/{self.ICON_PNG}?v={png_version}" '
                     'color="#000000">\n')
        masked = head.replace("</head>", mask_link + "</head>", 1)
        assert masked != head
        assert grade(masked) == [], (
            "a `mask-icon` is not one of the trio and must not be graded as one; the "
            "substring filter refused this correct page:\n" + "\n".join(grade(masked)))

        # The blog's head is a STRING in the SSR function, not a page on disk, so no glob
        # reaches it — it is graded in `test_every_surface_declares_the_same_versioned_icon_set`
        # alongside the other two self-rendered documents, where the whole surface is graded.

    def test_an_extra_icon_link_is_allowed_and_a_stale_one_is_not(self, tmp_path):
        """NON-VACUITY for the fourth-link arm — the OTHER half of round 2's P2.

        Round 2 removed `len(hrefs) != 3`, which refused an honest fourth link. Round 3
        measured that the arm behind it — "is not one of the three icon files" — still
        refused BOTH forms the removal's own comment called honest: an SVG variant and a
        second `sizes=` tile. That is the difference between deleting a count and closing
        the case, and this test is what makes the difference observable.

        Graded in a sandbox root so nothing here can be mistaken for a claim about the real
        tree: the trio is copied in byte-for-byte (so `expected` still matches), an extra
        192px-style tile and an SVG are created, and the four mutants below are driven
        through the same predicate the guard uses on real pages.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        for name in self.ICON_FILES:
            (tmp_path / name).write_bytes((self.WEB / name).read_bytes())
        # a second raster tile and an SVG variant: neither is one of the trio's names
        (tmp_path / "icon-256.png").write_bytes((self.WEB / self.ICON_PNG).read_bytes())
        (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        tile_v = hashlib.sha256((tmp_path / "icon-256.png").read_bytes()).hexdigest()[:12]
        svg_v = hashlib.sha256((tmp_path / "favicon.svg").read_bytes()).hexdigest()[:12]
        page = (self.WEB / "apps" / "dashboard" / "public" / "signup.html").read_text()

        def grade(html: str, *, page_url: str | None = None) -> list[str]:
            return self._surface_icon_violations(
                "sandbox", "signup.html", html, tmp_path, expected, self.ICON_ORIGINS,
                page_url=page_url)

        assert grade(page) == [], (
            "the unmutated page must grade clean against a byte-identical sandbox root:\n"
            + "\n".join(grade(page)))

        def with_extra(href: str) -> str:
            return page.replace(
                "</head>", f'  <link rel="icon" href="{href}">\n</head>', 1)

        # THE HONEST CASES — each was RED before this contract allowed an extra at all.
        for href, label in (
            (f"/icon-256.png?v={tile_v}", "a second raster tile, versioned with its own bytes"),
            (f"/favicon.svg?v={svg_v}", "an SVG variant, versioned with its own bytes"),
        ):
            assert grade(with_extra(href)) == [], (
                f"{label} ({href}) is an honest extra icon and must not be refused:\n"
                + "\n".join(grade(with_extra(href))))

        # THE FAILURE PATH — an extra is ALLOWED, not EXEMPT: it is still an icon on a tab,
        # so the two rules that keep a tab correct bind it exactly as they bind the trio.
        unversioned = with_extra("/favicon.svg")
        red = grade(unversioned)
        assert red, "an extra icon with no version must red — it goes stale the same way"
        assert "with NO `?v=`" in red[0], red[0]

        stale = with_extra("/icon-256.png?v=ffffffffffff")
        red = grade(stale)
        assert red, "an extra icon with a stale `?v=` must red"
        assert "bump the `?v=` with the bytes" in red[0] or "bump its `?v=`" in red[0], red[0]

        missing = with_extra("/icon-512.png?v=ffffffffffff")
        red = grade(missing)
        assert red, "an extra icon pointing at a file that does not exist must red"
        assert "does not exist under" in red[0], red[0]

        # AND THE DOCUMENT-RELATIVE ARM (`page_url`): a page in a SUBDIRECTORY resolves a
        # bare `favicon.ico` against its OWN directory, which is where the browser asks.
        nested = page.replace('href="/favicon.ico?v=' + expected["/favicon.ico"] + '"',
                              'href="favicon.ico?v=' + expected["/favicon.ico"] + '"', 1)
        assert nested != page
        assert grade(nested, page_url=self.SITE_ROOT + "settings/"), (
            "a document-relative href on a page served from /settings/ requests "
            "/settings/favicon.ico, which does not exist — resolving it against the SITE "
            "ROOT reads green for a tab request that 404s")
        (tmp_path / "settings").mkdir()
        for name in self.ICON_FILES:
            (tmp_path / "settings" / name).write_bytes((self.WEB / name).read_bytes())
        assert grade(nested, page_url=self.SITE_ROOT + "settings/") == [], (
            "with the files present in the page's own directory the same href is correct")

    def test_the_app_tree_accepts_an_absolute_href_on_its_own_origin(self):
        """NON-VACUITY for `origins` — it must CHANGE AN ANSWER, in both directions.

        `ICON_ORIGINS` exists for one documented reason (its own comment): the app surface
        is a separate Pages deployment, so its pages "may name
        `https://app.premiselabs.co/...` absolutely". On the code before this test the
        href loop compared `href.split("?", 1)[0]` against `expected`, so that blessed
        form kept its scheme and host, matched no key, and a page correct by every count,
        file and version was reported as linking "which is not one of the three icon
        files" — a FALSE RED on a correct page, with a message that misnames the cause
        (the file IS one of the three).

        The same read made `origins` UNOBSERVABLE: all 21 graded pages use root-relative
        hrefs, which `_icon_ref` resolves through `SITE_ORIGINS` whether `origins` is
        passed or not, so replacing `ICON_ORIGINS` with `SITE_ORIGINS` — or deleting the
        parameter and its four call sites — left every assertion green. A check whose
        failure path cannot run is indistinguishable from a no-op, which is the case this
        file's own `_missing_icon_links` docstring says must not ship.

        All four directions are driven through a REAL app page, not a fixture, so this
        cannot drift into testing a string nobody deploys.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        page = self.DASHBOARD_ROOT / "signup.html"
        head = page.read_text()

        def grade(html: str, origins: frozenset[str]) -> list[str]:
            return self._surface_icon_violations(
                "app", page.name, html, self.DASHBOARD_ROOT, expected, origins)

        absolute = head
        for path, version in expected.items():
            absolute = absolute.replace(
                f'href="{path}?v={version}"',
                f'href="https://app.premiselabs.co{path}?v={version}"')
        for path, version in expected.items():
            assert f'href="https://app.premiselabs.co{path}?v={version}"' in absolute, (
                "fixture drift: signup.html no longer declares the versioned "
                f"{path} href, so the absolute form below tests fewer links than the "
                "page has")

        # Direction 1 — the form the comment blesses must PASS. This is the false red.
        assert grade(absolute, self.ICON_ORIGINS) == [], (
            "a page may name its own origin's icons ABSOLUTELY (`ICON_ORIGINS`'s comment "
            "says so), and grading it root-relative-only reds a correct page:\n"
            + "\n".join(grade(absolute, self.ICON_ORIGINS)))

        # Direction 2 — `origins` is OBSERVABLE: the app origin is deliberately not in
        # `SITE_ORIGINS`, so the same page graded against the marketing set must red. A
        # mutant that passes `SITE_ORIGINS` (or drops the parameter) fails here.
        assert grade(absolute, self.SITE_ORIGINS), (
            "grading the app surface against `SITE_ORIGINS` must red an absolute "
            "app-origin href — otherwise `ICON_ORIGINS` changes no answer and the "
            "parameter is dead weight")

        # Direction 3 — the protocol-relative spelling is the same origin, not another.
        protocol_relative = head
        for path, version in expected.items():
            protocol_relative = protocol_relative.replace(
                f'href="{path}?v={version}"',
                f'href="//app.premiselabs.co{path}?v={version}"')
        assert protocol_relative.count('href="//app.premiselabs.co') == 3
        assert grade(protocol_relative, self.ICON_ORIGINS) == [], (
            "`//app.premiselabs.co/favicon.ico?...` is this surface's own origin and must "
            "pass:\n" + "\n".join(grade(protocol_relative, self.ICON_ORIGINS)))

        # Direction 4 — the widening is by ORIGIN, not "any host". A foreign host still
        # reds, both as a missing declaration and as an href that names no file of ours.
        foreign = absolute.replace("https://app.premiselabs.co", "https://evil.example")
        caught = grade(foreign, self.ICON_ORIGINS)
        assert len([v for v in caught if "evil.example" in v]) == 3, (
            "every one of the three foreign hrefs must be reported, not just the first — "
            "an icon served from another host is not ours and one defect must not hide "
            f"the rest. Got: {caught}")

    def test_a_header_element_is_not_a_second_head_document(self):
        """NON-VACUITY for `_head_element_count`: `<header>` is not a second `<head>`.

        The count exists to fail closed when one file renders TWO documents — the guard
        slices the first `<head>…</head>`, so a second document would ship with no icon
        while every assertion stayed green. The first cut was `rendered.count("<head")`,
        and `<header>` begins with that substring: an honest page that gained a `<header>`
        (a nav bar — nothing to do with `<head>`) was refused with "a SECOND document …
        grade each one", a diagnosis that describes a document nobody wrote. That is a
        FALSE RED, i.e. a guard the next person weakens instead of fixes.

        Both directions, on the three REAL self-rendered sources (none of which carries a
        `<header>` today, so the fixture is the only thing that exercises it):
        """
        sources = {
            "website/functions/blog/_lib.ts":
                self.WEB / "functions" / "blog" / "_lib.ts",
            "website/apps/dashboard/functions/auth/confirm.ts":
                self.DASHBOARD_ROOT.parent / "functions" / "auth" / "confirm.ts",
            "website/apps/dashboard/functions/admin/[[path]].ts":
                self.DASHBOARD_ROOT.parent / "functions" / "admin" / "[[path]].ts",
        }
        for label, source in sources.items():
            # ⛔ `is_file()` BEFORE `read_text()`: read-first raised `FileNotFoundError` on a
            # renamed fixture, so the drift diagnosis below was unreachable in every case it
            # named — "a check whose failure path never runs cannot be told apart from a
            # no-op", which is this file's own rule (review round 8, F3).
            assert source.is_file(), f"fixture drift: {label} is gone"
            text = source.read_text()
            assert self._head_element_count(text) == 1, (
                f"{label} renders {self._head_element_count(text)} <head> element(s); this "
                "test assumes exactly one, or the directions below grade nothing")
            assert "<header" not in text, (
                f"fixture drift: {label} already has a <header>, so the mutation below is "
                "not the only one and cannot be attributed")

            # Direction 1 — the FALSE RED. A `<header>` element must not be counted.
            with_header = text.replace("</head>", "</head>\n<header>site</header>", 1)
            assert with_header != text, f"fixture drift: {label} no longer closes </head>"
            assert self._head_element_count(with_header) == 1, (
                f"{label} gained a <header> (a nav bar — not a head) and the count says it "
                "renders a second DOCUMENT, refusing an honest page: "
                f"count={self._head_element_count(with_header)}, "
                "substring hits=" + repr(with_header.count("<head")))

            # Direction 2 — a genuine SECOND document is still counted, so the loosening is
            # by tag boundary and not "stop counting": this is the red the guard exists for.
            assert self._head_element_count(text + "<head>") == 2, (
                f"{label} plus a real second <head> must count 2 — otherwise the guard "
                "covers the first document and reports green for both")

            # Direction 3 — EVERY opening spelling of the ONE element still counts exactly
            # one, in any case: `<head>`, `<head `, `<head\t>`, `<head\n`, `<head/>` are the
            # same element, and an UPPERCASE name is not a different one (HTML lowercases
            # tag names; the case-sensitive boundary counted `<HEAD>` as 0 and so let a real
            # second document through).
            for spelling in ("<head>", "<head >", "<head\t>", "<head\n>", "<head/>",
                             "<HEAD>", "<Head>"):
                assert self._head_element_count(spelling) == 1, (
                    f"{spelling!r} must count as one <head> element")

            # Direction 4 — a `<head` inside a STRING BUILDER is not an element either.
            # These three sources assemble HTML with `.replace()`/concatenation, so an honest
            # edit like the one below counted 2 and refused the page with "a SECOND
            # document" that nobody wrote.
            builder = 'x = html.replace("<head", "<head>")'
            assert self._head_element_count(builder) == 1, (
                f"a `\"<head\"` inside a string builder is not an element: {builder!r} "
                f"counted {self._head_element_count(builder)}")
            assert self._head_element_count("<head-x>") == 0, (
                "a tag name that merely STARTS with `head` is not <head> ")


# ═══════════════════════════════════════════════════════════════════════════
# #11764 — the product set is SERVED from the one marketing host
# ═══════════════════════════════════════════════════════════════════════════


class TestProductSetIsServedUnderTheOneHost:
    """Every marketing page names the URL that actually serves it (#11764, epic #11762).

    WHY THIS EXISTS. Epic #11762 moved the product surface to `premiselabs.co/tortoise`
    and turned `tortoise.premiselabs.co` into a permanent 301. The pages were not moved
    on disk (the middleware rewrites the prefix), so NOTHING about the files changed —
    which is exactly why the head could keep naming the retired host and every existing
    guard stayed green. A canonical naming a URL that 301s to the document serving it is
    a hard SEO error, and it is invisible to any check that only reads the card file.

    THE ASSERTION IS A DESTINATION, NOT A COUNT. The count of retired-host occurrences is
    a number that both wrong shapes satisfy: `== 0` reds the day a legitimate asset URL
    names it, and `<= N` stops noticing when a link is dropped. So each page is asked
    the only question that cannot be gamed — "does your canonical name the URL that
    serves YOU?" — and the retired host is refused where it would actually be wrong.
    """

    WEB = REPO_ROOT / "website"
    CONTRACT = TestSiteIconAndSocialPreviewContract
    ONE_HOST = "https://premiselabs.co"

    # page -> the URL that serves it after #11762. The product set sits UNDER the
    # prefix; the business's legal pages stay at the ROOT (the owner's fork in the
    # epic: legal governs the business, not the product).
    CANONICAL: ClassVar[dict[str, str]] = {
        "product.html": f"{ONE_HOST}/tortoise",
        "docs.html": f"{ONE_HOST}/tortoise/docs",
        "faq.html": f"{ONE_HOST}/tortoise/faq",
        "self-hosted.html": f"{ONE_HOST}/tortoise/self-hosted",
        "security.html": f"{ONE_HOST}/tortoise/security",
        "license.html": f"{ONE_HOST}/tortoise/license",
        "investors.html": f"{ONE_HOST}/tortoise/investors",
        "design-partners-intro.html": f"{ONE_HOST}/tortoise/design-partners-intro",
        "privacy.html": f"{ONE_HOST}/privacy",
        "tos.html": f"{ONE_HOST}/tos",
        "dpa.html": f"{ONE_HOST}/dpa",
        "index.html": f"{ONE_HOST}/",
        "contact.html": f"{ONE_HOST}/contact",
    }

    # The pages that MOVED region: they are served under the prefix, so a root-relative
    # link to a sibling's old path is a hop to a 301 (or, for `/`, the COMPANY homepage
    # — a different document entirely).
    MOVED = ("product.html", "docs.html", "faq.html", "self-hosted.html",
             "security.html", "license.html", "investors.html",
             "design-partners-intro.html")

    # The pages that STAYED at the root (`index.html`, `contact.html`). Their `/` link is
    # CORRECT — it is the company homepage — so the prefix rule does not apply to them,
    # but a root-relative link to a product path does not become correct by staying put:
    # `/docs` on `premiselabs.co/contact` is the retired path and now takes a hop. Leaving
    # these out of the scan is what let `contact.html` keep `href="/self-hosted"` while
    # every sibling it sits beside was rewritten (caught by the VGATE review, #11764).
    # Every page the company host serves at `/` + its own path. The legal three belong
    # here: they stayed at the root, so `/` on them is the company homepage (not the
    # product) and their links must use the prefix. A reviewer measured that they were in
    # NEITHER link population, so a future `href="/"` labelled "Back to product" on
    # `privacy.html` would have shipped green — the same miss measured on `contact.html`.
    # `tortoise-for-product-teams.html` (#13645) is served at the COMPANY ROOT
    # (`premiselabs.co/tortoise-for-product-teams`), so it belongs here on the
    # docstring's own test: its `/` is the company homepage, not the product, and
    # a link from it must carry the `/tortoise` prefix. Measured before adding it:
    # its links are already correct (no `href="/"`, no `OLD_PRODUCT_PATHS` member),
    # so this closes a #11764-shaped hole rather than fixing a live defect.
    ROOT_SERVED = ("index.html", "contact.html", "privacy.html", "tos.html", "dpa.html",
                   "tortoise-for-product-teams.html")
    # The app origin's page population, pinned for the same reason `_pages` is: the guard
    # below walks a non-recursive glob, so a page added to a subdirectory (or served from
    # `apps/dashboard/index.html`, which lives OUTSIDE `public/`) would leave coverage with
    # no red anywhere.
    APP_ORIGIN_PAGES = ("404.html", "invite-accept.html", "signup.html", "welcome.html")
    # The RETIRED path -> the path that serves it now. BOTH spellings are keys: the
    # middleware's `PRODUCT_PAGES` carries `/docs` AND `/docs.html`, so the `.html` form
    # is routed in one hop rather than 404ing — and a guard that only knew the
    # extensionless spelling would stay green on a link that still costs the visitor a
    # redirect, which is the property these guards advertise.
    OLD_PRODUCT_PATHS: ClassVar[dict[str, str]] = {
        "/product": "/tortoise", "/product.html": "/tortoise",
        "/docs": "/tortoise/docs", "/docs.html": "/tortoise/docs",
        "/faq": "/tortoise/faq", "/faq.html": "/tortoise/faq",
        "/self-hosted": "/tortoise/self-hosted", "/self-hosted.html": "/tortoise/self-hosted",
        "/security": "/tortoise/security", "/security.html": "/tortoise/security",
        "/license": "/tortoise/license", "/license.html": "/tortoise/license",
        "/investors": "/tortoise/investors", "/investors.html": "/tortoise/investors",
        "/design-partners-intro": "/tortoise/design-partners-intro",
        "/design-partners-intro.html": "/tortoise/design-partners-intro",
    }

    def _canonical(self, name: str) -> str | None:
        head = self.CONTRACT._head((self.WEB / name).read_text())
        for link in self.CONTRACT._links(head):
            if "canonical" in link.get("rel", "").lower().split():
                return link.get("href")
        return None

    def test_every_page_canonical_names_the_url_that_serves_it(self):
        """The destination, per page — a canonical must not 301 to its own document."""
        for name, expected in self.CANONICAL.items():
            assert self._canonical(name) == expected, (
                f"{name} declares canonical {self._canonical(name)!r}; it is served at "
                f"{expected!r}. A canonical naming a URL that 301s back to this document "
                "is the defect #11762 exists to remove")

    def test_404_declares_no_canonical(self):
        """An error page has no canonical URL — one would claim a document that isn't there."""
        assert self._canonical("404.html") is None, (
            "404.html declares a canonical; it is served on every host for every missing "
            "path, so any value would be a lie")

    def test_no_marketing_page_names_the_retired_product_host(self):
        """⛔ THE PROPERTY THE MOVE ACTUALLY CREATES: one host, no page naming the other.

        `tortoise.premiselabs.co` stays alive as a permanent 301 (the hostname is never
        deleted), so a page CAN name it and the link still works — through an extra hop,
        onto a canonical that disagrees with the URL the visitor is on. That is why this
        is asserted on the page text rather than left to a membership set: `SITE_ORIGINS`
        has to keep describing the ASSET tree, which really is served from both.
        """
        retired = TestSiteIconAndSocialPreviewContract.RETIRED_PRODUCT_HOST
        offenders = []
        for page in sorted(self.WEB.glob("*.html")):
            for lineno, line in enumerate(page.read_text().splitlines(), 1):
                if retired in line:
                    offenders.append(f"{page.name}:{lineno}: {line.strip()[:100]}")
        assert not offenders, (
            f"these pages still name the retired product host {retired!r}:\n  "
            + "\n  ".join(offenders)
            + "\n\nEvery one of them is a hop onto a 301, or a label that is now false. "
              "Point it at the URL that serves it (product set: /tortoise/…, legal: the root)")

    def test_a_moved_page_links_its_siblings_by_their_new_path(self):
        """A root-relative href on a moved page means the RETIRED path, not the sibling.

        This is the half a URL-rewriting sweep misses: the absolute links are obvious,
        but `href="/docs"` was correct only because the page was served FROM the product
        host's root. On `premiselabs.co/tortoise/faq` the same href resolves to
        `premiselabs.co/docs` (a 301), and `href="/"` resolves to the COMPANY homepage —
        a different document. Nothing about the file changed when the URL did.
        """
        old_paths = {"/": "/tortoise", **self.OLD_PRODUCT_PATHS}
        offenders = []
        for name in self.MOVED:
            text = (self.WEB / name).read_text()
            for href in re.findall(r'href="([^"]+)"', text):
                path = href.split("#", 1)[0].split("?", 1)[0]
                if path in old_paths:
                    offenders.append(f"{name}: href={href!r} → should be "
                                     f"{old_paths[path]!r}")
        assert not offenders, (
            "these links on moved pages still point at the RETIRED path — from under "
            "/tortoise they reach a different document (`/` is the company page) or take "
            "a hop:\n  " + "\n  ".join(offenders))

    def test_no_page_links_a_product_path_from_the_root(self):
        """A root-served page (`index`, `contact`) must use the prefix too.

        Its `/` link is right on `index.html` — that IS the company homepage — and wrong on
        `contact.html`, where the label says "product". The two cases look alike in a URL
        sweep and only one of them moved, which is exactly how `contact.html` kept
        `href="/self-hosted"` through a migration that rewrote every sibling beside it, and
        kept two `href="/"` links labelled "product" after the product left the root.
        """
        offenders = []
        for name in self.ROOT_SERVED:
            text = (self.WEB / name).read_text()
            for href in re.findall(r'href="([^"]+)"', text):
                path = href.split("#", 1)[0].split("?", 1)[0]
                if path in self.OLD_PRODUCT_PATHS:
                    offenders.append(f"{name}: href={href!r} → should be "
                                     f"{self.OLD_PRODUCT_PATHS[path]!r}")
                elif path == "/" and name != "index.html":
                    # `/` is the COMPANY page on this host. On `index.html` that is the
                    # document itself; on any other root-served page a `/` link labelled
                    # "product" sends the visitor to the company homepage.
                    offenders.append(f"{name}: href={href!r} → `/` is the company page "
                                     "here; the product is '/tortoise'")
        assert not offenders, (
            "these links on a ROOT-served page still name a pre-migration product URL "
            "(one hop onto a 301, or onto a different document entirely):\n  "
            + "\n  ".join(offenders))

    def test_an_app_origin_page_never_links_a_marketing_path_root_relative(self):
        """On `app.premiselabs.co`, `/docs` is not the docs page — it is a 404.

        ⛔ MEASURED, not assumed: the app is its OWN Pages project (`tortoise-dashboard`),
        so a root-relative href there resolves inside it, and `/docs`, `/faq`, `/license`,
        `/security`, `/privacy`, `/tos`, `/dpa`, `/blog` all answered **404** (measured
        `curl` before this change). The marketing pages therefore have to be named
        ABSOLUTELY on these four files, and `SITE_ROOT`'s own tree cannot catch it —
        nothing under `website/*.html` links into the app origin.

        `/` is deliberately NOT a marketing path here: `https://app.premiselabs.co/` serves
        the dashboard (200), so a logo pointing at `/` is correct on this origin.
        """
        marketing = {"/docs", "/faq", "/self-hosted", "/security", "/license",
                     "/privacy", "/tos", "/dpa", "/blog", "/product", "/contact",
                     "/investors", "/design-partners-intro"}
        app_pages = sorted(self.CONTRACT.DASHBOARD_ROOT.glob("*.html"))
        assert app_pages, "no app-origin pages found — the guard would pass vacuously"
        assert tuple(p.name for p in app_pages) == self.APP_ORIGIN_PAGES, (
            "the app-origin page population changed — a page that MOVED into a "
            f"subdirectory leaves this guard: {[p.name for p in app_pages]}")
        offenders = []
        for page in app_pages:
            for href in re.findall(r'href="([^"]+)"', page.read_text()):
                path = href.split("#", 1)[0].split("?", 1)[0]
                # The prefix IS a marketing URL on this origin, and so is the `.html`
                # spelling of any marketing path: `app.premiselabs.co/tortoise/docs`
                # answered 404 when measured, exactly like `/docs` did.
                under_prefix = path == "/tortoise" or path.startswith("/tortoise/")
                if path in marketing or under_prefix or path.removesuffix(".html") in marketing:
                    offenders.append(f"{page.name}: href={href!r}")
        assert not offenders, (
            "these root-relative links on an APP-ORIGIN page resolve inside the dashboard "
            "project, where they are 404s — name the marketing host absolutely:\n  "
            + "\n  ".join(offenders))

    def test_the_pages_that_moved_are_still_the_files_that_serve_them(self):
        """The decision this child took: a Function rewrites the prefix, the files stay.

        Pinned because the other shape is a silent coverage loss: filing the six pages
        into `website/tortoise/` would leave `glob("*.html")` guarding 8 of the 14
        pages then served, with no red anywhere (see `_pages`'s pin).
        """
        for name in self.MOVED:
            assert (self.WEB / name).is_file(), (
                f"{name} is no longer at website/{name} — the prefix is served by the "
                "middleware rewrite, so moving the file changes which pages the marketing "
                "guards can see")


# ═══════════════════════════════════════════════════════════════════════════════
# 11. ONE pricing source for every page that quotes a price (#13645)
# ═══════════════════════════════════════════════════════════════════════════════
# The owner's decision on #13645 recorded the defect this section exists to
# stop, in its own words: a new page that carries its own copy of the `PRICING`
# object is "two prices that can drift with nothing red". A niche campaign page
# quoting a stale price is worse than no campaign page, so the two pages are
# held to ONE source and to each other — not merely each held to a copy.
#
# The division of labour, so a later reader knows which gate answers what:
#   * `PRICING_SOURCE` (`website/assets/tortoise-pricing.js`) is the ONE table;
#   * `TestPricingSourceIsShared` (below) proves BOTH pages load it, neither
#     re-declares it, and the pricing markup a reader receives is IDENTICAL on
#     the two pages;
#   * `TestStaticPricingFallback` proves that markup equals the numbers in the
#     shared source (so the identical copy is also the correct one);
#   * `TestMirrorNumericParity` proves the shared source still mirrors
#     `product/pricing.json`; `tests/test_tier_naming.py` SEPARATELY holds the
#     tier NAMES across that file, product.html's `labels` map and the
#     dashboard's `TIER_LABELS` — it does not read `PRICING_SOURCE`.

PRODUCT_PAGES_SHARING_PRICING = (
    ("website/product.html", PRODUCT_HTML),
    ("website/tortoise-for-product-teams.html", PRODUCT_TEAMS_HTML),
)


def _without_html_comments(markup: str) -> str:
    """`markup` minus every HTML comment, including an UNTERMINATED opener.

    `_served` is the right tool when the subject is markup a READER receives,
    but it also deletes `<script>`/`<style>` bodies — and a `<script src=…>`
    tag is exactly the subject of the rows below, so `_served` would empty them.
    This variant keeps tags and script bodies and removes only what a browser
    never parses. Same lesson as `_served`'s own docstring (#13696 review,
    round 6): commenting a tag out is the natural way to disable it, and it
    must not leave the row that pins it green.
    """
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    return re.sub(r"<!--.*", " ", markup, flags=re.S)


def _live_markup(markup: str) -> str:
    """`markup` with BOTH the HTML comments and the JS comments removed.

    ⛔ Each stripper ALONE is half a stripper, and swapping between them is how
    one false PASS kept reappearing (#13696 rounds 4-6):

    - `_without_html_comments` keeps JS comments, so `// window.addEventListener(
      'tortoise:consent', …)` satisfied a presence row;
    - `_code_only` keeps HTML comments, so `<!-- <script> … </script> -->` did too
      — and commenting a tag out is THE natural way to disable it, as this
      file's own `_served` docstring says.

    MEASURED (round 6): with only one of them applied, wrapping the page's whole
    funnel `<script>` in `<!-- -->` left `test_the_three_funnel_events_are_named_
    in_the_source` and `test_the_click_delegate_counts_the_entry_points_the_legend_
    names` GREEN over a page that raised nothing. Composed, the same mutation
    reds 5 rows. Use this for every page-level PRESENCE check; keep the single
    helpers for the STRUCTURAL extraction of tags, where the tag is the subject.
    """
    return _code_only(_without_html_comments(markup))


def _pricing_source_includes(html: str) -> list[str]:
    """The `src` of every `<script>` the browser PARSES, in source order.

    TWO independent properties, kept together because either one alone leaves a
    channel unread:

    - COMMENTED-OUT tags are excluded (`_without_html_comments`): they are not
      in the page, so a row that pins them must not see them.
    - The attribute is parsed TOLERANTLY — `src="…"`, `src='…'`,
      `src = "…"` and an UNQUOTED `src=/assets/x.js` all count (unquoted
      attribute values are legal HTML5). A stricter spelling does not merely
      misread the attribute: the tag is then skipped by `_inline_scripts` (its
      `\\bsrc\\s*=` test still excludes it) and deleted by `_served`, so the
      file it loads is in NO channel at all (measured, #13696 rounds 7 and 8:
      the same price-bearing asset was read red under `src="…"` and left 219
      rows green under `src='…'` and under `src=/assets/…`).
    """
    found = re.findall(
        r"""<script\b[^>]*?\bsrc\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s"'>]+))""",
        _without_html_comments(html), re.I)
    # `findall` yields one tuple per tag with exactly one group populated; the
    # QUOTING cannot select whether the channel is read.
    return [next(g for g in groups if g) for groups in found]


#: ONE spelling of a price figure, shared by the rows that refuse a second copy
#: of it outside the shared table.
#:
#: ⛔ The tag group is load-bearing: the section slice is MARKUP, not text, and
#: both pages already style a price in parts (`<div class="price">$119<span>
#: /mo</span></div>`), so `$<b>89</b>/mo` — which a reader sees as `$89/mo` — is
#: the natural way to write a figure. Without this it matched nothing and left
#: 219 rows green (#13696 round 7). It does NOT let tags join two figures:
#: digits must follow the tag run, so `$1</div>…428` still yields only `$1`.
PRICE_FIGURE_RE = re.compile(r"\$\s?(?:<[^>]+>\s*)*\d")

#: A DOLLAR FRAGMENT: a `$` that is neither a `${…}` template interpolation nor
#: the head of a figure (a `$` followed by a digit is `PRICE_FIGURE_RE`'s job).
#:
#: ⛔ It is how JS ASSEMBLES a price — `'<div class="price">$' + t.price` — and
#: the figure then lives in the TABLE, not in the page's source text, so no
#: `$<digit>` detector can see it: the most common way to build a price in JS
#: was in no channel at all. Measured at `59bc263091` (#13696 round 11, P2):
#: appending that one line to the campaign page's inline script left all **158
#: rows GREEN** while its grid showed Builder at $25/mo beside its own $20
#: annual cards, and `/tortoise` showed $20/$15.
#:
#: Both product pages carry ZERO `$` outside a `${…}` in any channel today, so
#: this is green as written — it refuses the next one. A price a script needs
#: must still come from the shared table through `window.tortoiseFmtPrice`,
#: whose own `'$' + perMonth` is legitimate because THAT file is the source (it
#: is excluded from `_price_channels`, and its math is executed by
#: `TestSharedFormatterMathIsExecuted`).
PRICE_FRAGMENT_RE = re.compile(r"\$(?![{\d])")

#: A figure carried in a DATA ATTRIBUTE (`data-price="20"`), which no `$`
#: detector reads and which exists only to be turned into one by a script. Zero
#: on both product pages today; refused rather than gated on a spelling.
PRICE_DATA_ATTR_RE = re.compile(r"\bdata-price\s*=\s*[\"']?\d")

#: A whole figure, for the row that asks whether the SHARED SOURCE can produce
#: it. Same tag tolerance as the detector, so a figure cannot be read as
#: present-but-different; `_figures` normalises the match back to `$89` before
#: any comparison.
PRICE_ONLY_RE = re.compile(r"\$\s?(?:<[^>]+>\s*)*\d[\d,]*")

#: The tags that can sit INSIDE a figure without ending its line box. Any
#: other tag is a block boundary, where a reader sees two separate texts — so a
#: tag run spanning one must never be read as a split figure (measured, #13696
#: round 9, P2: `<div class="price">$0</div>` followed by a `<p>` was reported
#: as "splits a price figure", sending the editor to rewrite correct markup).
INLINE_TAGS = "span|b|i|em|strong|sup|sub|small|code|abbr|mark"

#: A figure whose DIGIT RUN is interrupted by markup — `$15<b>9</b>`, which a
#: reader sees as `$159` while every detector above can only read `$15`. The
#: tag group in `PRICE_ONLY_RE` sits before the digits, so it cannot span one.
#: Fail closed on the ambiguous spelling rather than guess which is meant.
PRICE_SPLIT_RE = re.compile(
    r"\$\s?(?:<(?:" + INLINE_TAGS + r")\b[^>]*>\s*)*\d[\d,]*"
    r"(?:<(?:" + INLINE_TAGS + r")\b[^>]*>\s*)+\d")


def _figures(text: str) -> list[str]:
    """Every `$` figure in `text`, inline markup removed from INSIDE the match.

    Normalised per MATCH, never over the whole document: stripping tags
    document-wide would splice `$1</div><div>428` into a figure (`$1428`) that
    neither the page nor the source states.
    """
    return [re.sub(r"<[^>]+>", "", m) for m in PRICE_ONLY_RE.findall(text)]


def _inline_scripts(html: str) -> list[str]:
    """The body of every INLINE `<script>`, comment spans cut out.

    `src=`-carrying tags are skipped: their bodies are empty in these documents
    and the code they load (`/assets/tortoise-pricing.js`, `/consent.js`) is
    graded by its own rows. Comments go through the same span-cutting rule
    `_render_code` uses — a `/* … */` block is removed but the code after its
    closing `*/` on that line is KEPT — so a `//`-strip cannot cut the
    `https://` inside the self-host anchor and hide every literal after it,
    while `/* note */ labels.pro = 'Growth';` still reads as code.
    """
    return [
        _strip_comment_lines(body)
        for attrs, body in re.findall(
            r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I)
        if not re.search(r"\bsrc\s*=", attrs, re.I)
    ]


def _style_bodies(html: str) -> list[str]:
    """The body of every `<style>` block — CSS `content:` renders text too.

    `_served` deletes style bodies (a `content:` rule is not served markup), so
    a price there is visible to the reader and invisible to every static row
    (#13696 round 6: `#beat-hero .cta::after { content: " — from $25/mo"; }`
    left every row green). Comments go through the SAME span-cutting rule the
    script channel uses: a full-line `/* … */` or `//` comment of prose may name
    a figure without reding this row, but a block comment that closes on a line
    and is followed by a declaration does NOT hide that declaration.
    """
    return [_strip_comment_lines(body)
            for body in re.findall(r"<style\b[^>]*>(.*?)</style>", html,
                                  re.S | re.I)]


def _same_origin_asset(
    name: str, ref: str, base: Path | None = None, site: Path | None = None,
) -> tuple[Path | None, str]:
    """`(path, label)` for a same-origin asset reference, or `(None, label)`.

    SAME-ORIGIN means the site's own host as well as a path: the page may write
    `https://premiselabs.co/assets/x.js`, which loads exactly the file a
    relative `src` would, so the absolute spelling must not select a channel
    (measured, #13696 round 9, P3: the absolute spelling left both price rows
    green). A cache-busting `?v=…` is stripped before the file is resolved — the
    site already serves `/favicon.ico?v=…`, so refusing a query would false-red
    a legitimate tag.

    `base` is the directory a BARE relative reference resolves against; it
    defaults to the site root (`website/`), which is right for a tag on a page.
    A CSS `@import` resolves against the SHEET THAT CARRIES IT instead, so the
    style helper passes the importing file's directory — without that,
    `@import "more.css"` inside `/assets/x.css` was looked for at
    `website/more.css`, missed, and SKIPPED, which is a fail-OPEN, not a gap
    (measured, #13696 round 10).

    `data:`/`blob:` are REFUSED rather than skipped: the bytes are inline, so
    the reader does receive them and no static channel here can read what they
    say. Both pages carry none, so the assertion is a closed door, not a
    restriction on real markup.
    """
    url = urlsplit(ref)
    if url.scheme in ("data", "blob"):
        raise AssertionError(
            f"{name} delivers bytes inline through a `{url.scheme}:` URI "
            f"(`{ref[:60]}…`), which no channel here can read — vendor it under "
            "`website/` or extend the channel deliberately")
    site = site or (REPO_ROOT / "website")
    if url.scheme or url.netloc or ref.startswith("//"):
        if url.netloc.lower().removeprefix("www.") not in SITE_HOSTS:
            return None, ref  # a genuinely foreign asset: not this site's code
        base = site
    if url.path.startswith("/"):
        # Root-relative is a PAGE url: it resolves against the site root even
        # when the reference came from a sheet in a subdirectory.
        path = site / url.path.lstrip("/")
    else:
        path = (site if base is None else base) / url.path
    return path, url.path


def _same_origin_script_bodies(
    name: str, html: str, root: Path | None = None,
) -> list[tuple[str, str]]:
    """`(src, text)` for every SAME-ORIGIN script a page loads, except the source.

    A `<script src>` tag has an EMPTY body, so `_inline_scripts` skips it and
    `_served` never sees the FILE — a price in a second asset is in no channel
    any row read (measured, #13696 round 6: adding
    `website/assets/teams-extra.js` holding
    `document.title = 'Builder from $25/mo';` plus its tag left 139 rows green).
    `/assets/tortoise-pricing.js` is EXCLUDED because it IS the source: it
    legitimately holds the figures the other rows derive from.

    `root` defaults to `website/` and exists so the channel can be exercised
    against a fixture tree (see `TestStylesheetChannelIsRead`); every caller in
    this file leaves it at the default.
    """
    out: list[tuple[str, str]] = []
    site = root or (REPO_ROOT / "website")
    for src in _pricing_source_includes(html):
        path, _label = _same_origin_asset(name, src, None, site)
        if path is None:      # a genuinely foreign asset (GSAP on cdnjs)
            continue
        if path.resolve() == PRICING_SOURCE.resolve():
            continue
        assert path.is_file(), (
            f"{name} loads `{src}` but no such file exists under `website/`, so "
            "this gate cannot read the code the page actually runs — the file "
            "moved and the row must move with it")
        # Comment lines dropped for the same reason the inline channel drops
        # them: a `// …` note may name a figure without being a second copy.
        out.append((src, _strip_comment_lines(
            path.read_text(encoding="utf-8"))))
    return out


def _stylesheet_imports(css: str) -> list[str]:
    """Every `@import` target in a stylesheet body, in source order.

    `@import` is the fourth documented way CSS reaches a document (`<link>`,
    `<style>`, `@import`, the `style` attribute — W3C CSS1 §"Linking Style
    Sheets to HTML"), so a `<style>` block or a linked FILE can pull in a second
    file that no tag in the page names. Every legal spelling is read:
    `@import "x.css"`, `@import url("x.css")`, `@import url(x.css)`.

    The text is comment-stripped FIRST, so a commented-out `@import` is not a
    channel and a `/* … */` note may name a sheet without being one.
    """
    found = re.findall(
        r"""@import\s+(?:url\(\s*)?(?:"([^"]+)"|'([^']+)'|([^\s"')]+))""",
        _strip_comment_lines(css), re.I)
    return [next(g for g in groups if g) for groups in found]


def _pricing_stylesheet_hrefs(html: str) -> list[str]:
    """The `href` of every `<link>` whose `rel` is a STYLESHEET, in source order.

    Tolerant for the reason `_pricing_source_includes` is: the QUOTING of an
    attribute must never select whether a channel is read. `rel` is a TOKEN
    LIST, so `rel="alternate stylesheet"` counts (a reader can activate it);
    `rel="preload" as="style"` does not (a preload applies no rule alone).
    Commented-out tags are excluded — a disabled tag is not in the page.
    """
    href_re = re.compile(
        r"""\bhref\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s"'>]+))""", re.I)
    refs: list[str] = []
    for tag in re.findall(r"<link\b[^>]*>", _without_html_comments(html), re.I):
        rel = re.search(
            r"""\brel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", tag, re.I)
        if not rel or "stylesheet" not in next(g for g in rel.groups() if g is not None).lower().split():
            continue
        m = href_re.search(tag)
        if m:
            refs.append(next(g for g in m.groups() if g))
    return refs


def _same_origin_style_bodies(
    name: str, html: str, root: Path | None = None,
) -> list[tuple[str, str]]:
    """`(href, text)` for every SAME-ORIGIN stylesheet a page loads — transitively.

    `<style>` is not the only channel that renders CSS text: a
    `<link rel="stylesheet">` file (or an `@import` inside one) carries a
    `content:` rule just as effectively, and `_served` never sees it — so a
    price there was in no channel at all (measured, #13696 round 9, P1: 145 rows
    green with `$25/mo` in a linked stylesheet, while the same rule inside the
    page's own `<style>` red).

    ⛔ Round 9 read ONE level of `@import`, only from a LINKED sheet, and
    resolved every reference against the site root. All three of those bounds
    were measured leaving the sheet unread at this head (#13696 round 10):

    - an `@import` inside a `<style>` BODY (`<style>@import url("/assets/x.css")`)
      — a second door into the same file, and no row looked;
    - a RELATIVE `@import "more.css"` inside `/assets/x.css` — resolved against
      the site root, so it looked for `website/more.css`, was not a file, and was
      silently SKIPPED: a fail-OPEN, not a gap;
    - a TWO-hop chain (`a.css → b.css → c.css`) — the price one hop past the
      first import;

    so the walk starts at both doors and runs to a FIXPOINT over a visited set
    (an import CYCLE terminates), with each reference resolved against the sheet
    that carries it, and a reference that names no file ASSERTS instead of being
    skipped: a stylesheet the page loads but this gate cannot read is exactly
    the state the row exists to refuse.

    Only a genuinely FOREIGN host (`cdnjs.cloudflare.com`) is skipped — a static
    reader has no bytes there — and that skip is itself pinned closed below.
    """
    site = root or (REPO_ROOT / "website")
    # `(ref, base)` — `@import` resolves against the IMPORTING SHEET's own
    # directory; a tag or a `<style>` body resolves against the site root,
    # exactly as the page URL does.
    queue: list[tuple[str, Path | None]] = [
        (href, None) for href in _pricing_stylesheet_hrefs(html)]
    for body in _style_bodies(html):
        queue += [(ref, None) for ref in _stylesheet_imports(body)]
    seen: set[Path] = set()
    out: list[tuple[str, str]] = []
    while queue:
        ref, base = queue.pop(0)
        path, _label = _same_origin_asset(name, ref, base, site)
        if path is None:            # foreign host: no bytes a static reader can read
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        assert path.is_file(), (
            f"{name} loads the stylesheet `{ref}` but no such file exists under "
            "`website/`, so this gate cannot read the CSS the page actually "
            "renders — the file moved and the row must move with it")
        text = _strip_comment_lines(path.read_text(encoding="utf-8"))
        try:
            label = "/" + str(resolved.relative_to(site.resolve()))
        except ValueError:          # a sheet outside `website/`: name it as written
            label = ref
        out.append((label, text))
        queue += [(ref2, path.parent) for ref2 in _stylesheet_imports(text)]
    return out


def _price_channels(
    name: str, markup: str, root: Path | None = None,
) -> list[tuple[str, str]]:
    """Every delivery that can put a PRICE in front of a reader, labelled.

    ONE definition, because the list of deliveries IS the invariant: five review
    rounds each closed one remembered member of an open-ended set, so the list
    now lives here and the row plus the pin class both read it — a helper that
    stops reading a spelling reds the pin instead of quietly widening a gap.

    CLOSED against the documented ways style and script reach a document:

    1. inline `<script>` bodies
    2. `<style>` bodies
    3. same-origin `<script src>` FILES
    4. same-origin `<link rel="stylesheet">` FILES and their `@import`s

    The `style` ATTRIBUTE is deliberately NOT a channel, and not by omission: it
    is served MARKUP, so the no-price-outside-the-section row already scans it,
    and `content:` has no effect in a `style` attribute — a figure there can
    reach a reader only through a rule held in one of the channels above.
    """
    channels: list[tuple[str, str]] = [
        ("inline `<script>`", body) for body in _inline_scripts(markup)]
    channels += [("`<style>`", body) for body in _style_bodies(markup)]
    channels += [(f"`{src}`", body)
                 for src, body in _same_origin_script_bodies(name, markup, root)]
    channels += [(f"`{href}` (stylesheet)", body)
                 for href, body in _same_origin_style_bodies(name, markup, root)]
    return channels



class TestCommentStrippingKeepsCode:
    """The strip may lose a COMMENT, never a byte of the code beneath it.

    This helper is the scanning instrument behind every channel row below
    (inline `<script>`, `<style>`, same-origin asset) and behind the two-page
    render compare, so anything it drops without reding a row is a channel with
    no gate — which is how a `$25/mo` literal survived a leading `/* note */`
    and left BOTH rows green (#13696 round 6, the fail-OPEN the span-cutting fix
    closed). The rows below reproduce that channel through the real pages; this
    class pins the instrument itself, so the fail-open cannot come back by a
    quiet rewrite of the helper.
    """

    def test_a_wholly_commented_line_is_dropped(self):
        assert _strip_comment_lines("// Pro is $25/mo\nrun();") == "run();"
        assert _strip_comment_lines("/* Pro is $25/mo */\nrun();") == "run();"

    def test_a_multiline_block_comment_is_dropped(self):
        assert _strip_comment_lines(
            "/* first\n still prose $25\n third */\nrun();") == "run();"

    def test_code_after_a_one_line_block_comment_is_kept(self):
        # The fail-open: dropping the line wholesale left `run('$25')` in no
        # channel, so no row could refuse it.
        assert _strip_comment_lines("/* why */ run('$25');") == "run('$25');"
        assert PRICE_FIGURE_RE.search(_strip_comment_lines(
            "/* why */ run('$25');"))

    def test_code_after_a_multiline_block_comment_closes_on_its_own_line(self):
        assert _strip_comment_lines(
            "/* why\n * prose $25\n */ run('$25');") == "run('$25');"

    def test_a_trailing_line_comment_is_kept_and_fails_closed(self):
        # Deliberate asymmetry, documented on the channel row: a trailing `//`
        # note is SCANNED, so a figure there reds and the fix is to move the
        # sentence onto its own line, never to widen the row.
        assert PRICE_FIGURE_RE.search(_strip_comment_lines(
            "run(); // pinned at $25"))


class TestStylesheetChannelIsRead:
    """The CSS delivery list, pinned independently of the two live pages.

    ⛔ A CLASS, not a row inside
    `test_no_page_quotes_a_price_inside_its_own_script`, because both product
    pages carry **zero** linked stylesheets and `website/assets/` holds **no**
    `.css` file: that row is green as written, so nothing inside it can tell
    whether this channel is read or invisible. Round 9 added the channel and
    left FOUR ways through it open, each measured green on its head (#13696
    round 10): an `@import` inside a `<style>` body, a RELATIVE `@import`
    (resolved against the site root, missed, silently skipped), a two-hop import
    chain, and a `<link>` to a stylesheet that does not exist.

    These rows drive the SAME helpers the real row uses — `_pricing_stylesheet_hrefs`,
    `_stylesheet_imports`, `_same_origin_style_bodies` and the `_price_channels`
    assembly — against a fixture tree, so a helper that stops reading a spelling
    reds here instead of quietly widening the gap on the live pages.

    The `style` ATTRIBUTE is not covered on purpose (see `_price_channels`): it
    is served markup, and `content:` cannot be declared there.
    """

    @staticmethod
    def _site(tmp_path):
        site = tmp_path / "website"
        (site / "assets").mkdir(parents=True)
        return site

    def test_every_href_and_rel_spelling_is_read(self):
        assert _pricing_stylesheet_hrefs(
            '<link rel="stylesheet" href="/a.css">') == ["/a.css"]
        assert _pricing_stylesheet_hrefs(
            "<link rel='stylesheet' href='/a.css'>") == ["/a.css"]
        assert _pricing_stylesheet_hrefs(
            '<link rel="stylesheet" href=/a.css>') == ["/a.css"]
        assert _pricing_stylesheet_hrefs(
            '<link href="/a.css" rel="stylesheet">') == ["/a.css"]
        assert _pricing_stylesheet_hrefs(
            '<LINK REL="STYLESHEET" HREF="/a.css">') == ["/a.css"]

    def test_rel_is_a_token_list_so_alternate_counts(self):
        # A reader can ACTIVATE an alternate sheet, so it renders text too.
        assert _pricing_stylesheet_hrefs(
            '<link rel="alternate stylesheet" href="/a.css">') == ["/a.css"]
        # …but a preload applies no rule by itself: not a delivery.
        assert _pricing_stylesheet_hrefs(
            '<link rel="preload" as="style" href="/a.css">') == []

    def test_a_commented_out_link_is_not_read(self):
        assert _pricing_stylesheet_hrefs(
            '<!-- <link rel="stylesheet" href="/a.css"> -->') == []

    def test_every_import_spelling_is_read(self):
        assert _stylesheet_imports('@import "a.css";') == ["a.css"]
        assert _stylesheet_imports('@import url("a.css");') == ["a.css"]
        assert _stylesheet_imports("@import url(a.css);") == ["a.css"]
        assert _stylesheet_imports("@import url( 'a.css' ) screen;") == ["a.css"]

    def test_two_imports_on_one_line_are_BOTH_read(self):
        """The per-match `next(g for g in groups if g)` correction, pinned.

        Every row above passes a SINGLE `@import`, where the old flattened form
        (`[next(g for groups in found for g in groups if g) for groups in
        found]`) and the current one are identical — so the correction was
        unpinned and a revert would be invisible (#14020 review P2.2). On two
        matches the old form returns `['a.css', 'a.css']`: the SECOND sheet is
        silently dropped from the price-channel walk that reads these imports,
        leaving the file green. Measured: revert + this test only.
        """
        assert _stylesheet_imports(
            '@import "a.css"; @import url("b.css");') == ["a.css", "b.css"]
        assert _stylesheet_imports(
            '@import "a.css";\n@import url(b.css);') == ["a.css", "b.css"]

    def test_a_commented_out_import_is_not_read(self):
        assert _stylesheet_imports('/* @import "a.css"; */') == []

    def test_a_linked_sheet_its_relative_import_and_a_chain_are_channels(
            self, tmp_path):
        site = self._site(tmp_path)
        (site / "assets" / "a.css").write_text(
            '@import "b.css";\n.a::after { content: " \u2014 from $25/mo"; }\n',
            encoding="utf-8")
        # RELATIVE, and one hop past the first import — the two bounds round 9
        # had, each measured leaving the figure unread.
        (site / "assets" / "b.css").write_text(
            '@import "/assets/c.css";\n.b::after { content: "$999"; }\n',
            encoding="utf-8")
        (site / "assets" / "c.css").write_text('.c::after { content: "$42"; }\n',
                                            encoding="utf-8")
        markup = ('<html><head><link rel="stylesheet" href="/assets/a.css">'
                  '</head><body></body></html>')
        channels = _price_channels("synthetic.html", markup, root=site)
        bodies = {where: body for where, body in channels}
        for expected in ("`/assets/a.css` (stylesheet)",
                         "`/assets/b.css` (stylesheet)",
                         "`/assets/c.css` (stylesheet)"):
            assert expected in bodies, (
                f"{expected} is in NO channel, so a figure one import away is "
                f"unread: {sorted(bodies)}")
        hits = [line for _, body in channels for line in body.splitlines()
                if PRICE_FIGURE_RE.search(line)]
        assert len(hits) == 3, (
            "the channel is read but does not reach the detector — the row "
            f"above would be vacuous over a linked sheet: {hits!r}")

    def test_an_import_inside_a_style_block_is_a_channel(self, tmp_path):
        site = self._site(tmp_path)
        (site / "assets" / "from-style.css").write_text(
            '.x::after { content: "$42"; }\n', encoding="utf-8")
        markup = ('<html><head><style>@import url("/assets/from-style.css");'
                  '</style></head><body></body></html>')
        bodies = dict(_price_channels("synthetic.html", markup, root=site))
        assert "`/assets/from-style.css` (stylesheet)" in bodies, (
            "an `@import` inside a `<style>` body is a fourth door to a "
            f"stylesheet and it is unread: {sorted(bodies)}")

    def test_an_import_cycle_terminates(self, tmp_path):
        site = self._site(tmp_path)
        (site / "assets" / "a.css").write_text(
            '@import "b.css";\n.a::after { content: "$1"; }\n', encoding="utf-8")
        (site / "assets" / "b.css").write_text(
            '@import "a.css";\n.b::after { content: "$2"; }\n', encoding="utf-8")
        markup = ('<html><head><link rel="stylesheet" href="/assets/a.css">'
                  '</head><body></body></html>')
        bodies = [w for w, _ in _price_channels(
            "synthetic.html", markup, root=site)]
        assert bodies == ["`/assets/a.css` (stylesheet)",
                          "`/assets/b.css` (stylesheet)"], (
            "a cycle must terminate and still read every file once, not "
            f"loop or silently stop: {bodies}")

    def test_a_missing_stylesheet_fails_closed(self, tmp_path):
        # Round 9 SKIPPED it, so a `<link>` whose file had moved read nothing
        # and said nothing — the fail-OPEN this row exists to refuse.
        site = self._site(tmp_path)
        markup = ('<html><head><link rel="stylesheet" href="/assets/gone.css">'
                  '</head><body></body></html>')
        try:
            _price_channels("synthetic.html", markup, root=site)
        except AssertionError as exc:
            assert "gone.css" in str(exc), exc
        else:
            raise AssertionError(
                "a `<link>` to a stylesheet that does not exist was skipped "
                "silently — the page renders CSS this gate never read, and "
                "every row stays green")

    def test_an_absolute_url_on_either_site_host_is_read(self, tmp_path):
        site = self._site(tmp_path)
        (site / "assets" / "abs.css").write_text('.x { content: "$7"; }\n')
        for spelling in ("https://premiselabs.co/assets/abs.css",
                         "https://tortoise.premiselabs.co/assets/abs.css",
                         "//www.premiselabs.co/assets/abs.css",
                         "/assets/abs.css"):
            markup = (f'<html><head><link rel="stylesheet" href="{spelling}">'
                      '</head><body></body></html>')
            assert "`/assets/abs.css` (stylesheet)" in dict(
                _price_channels("synthetic.html", markup, root=site)), (
                f"{spelling} was not resolved to the file it names")

    def test_a_cache_busted_reference_still_resolves(self, tmp_path):
        # `/favicon.ico?v=…` is this site's own convention; keeping the query in
        # the path made `is_file()` false and the row RED with a message that
        # was not true ("the file moved").
        site = self._site(tmp_path)
        (site / "assets" / "busted.css").write_text('.x { content: "$7"; }\n')
        (site / "assets" / "busted.js").write_text("run();\n")
        markup = ('<html><head>'
                  '<link rel="stylesheet" href="/assets/busted.css?v=abc">'
                  '<script src="/assets/busted.js?v=abc"></script>'
                  '</head><body></body></html>')
        bodies = dict(_price_channels("synthetic.html", markup, root=site))
        assert "`/assets/busted.css` (stylesheet)" in bodies
        assert "`/assets/busted.js?v=abc`" in bodies

    def test_a_data_uri_delivery_is_refused_not_skipped(self, tmp_path):
        site = self._site(tmp_path)
        markup = ('<html><head><link rel="stylesheet" '
                  'href="data:text/css,.x{content:\'$7\'}"></head>'
                  '<body></body></html>')
        try:
            _price_channels("synthetic.html", markup, root=site)
        except AssertionError as exc:
            assert "data:" in str(exc), exc
        else:
            raise AssertionError(
                "a `data:` URI delivers bytes inline and was skipped as if it "
                "were a foreign host — the figure in it is unread")

    def test_the_only_off_host_delivery_is_the_known_cdn(self):
        # `_same_origin_asset` returns `None` for a foreign host and the row
        # then reads NOTHING from it — legitimate only because that set is the
        # CDN both pages load GSAP from. A new external delivery is a channel
        # no row reads, so it must red HERE rather than slip past.
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            markup = path.read_text(encoding="utf-8")
            refs = (_pricing_source_includes(markup)
                    + _pricing_stylesheet_hrefs(markup)
                    + [ref for body in _style_bodies(markup)
                       for ref in _stylesheet_imports(body)])
            foreign = [ref for ref in refs
                       if _same_origin_asset(name, ref)[0] is None]
            assert foreign == [r for r in foreign
                              if urlsplit(r).netloc.lower()
                              == "cdnjs.cloudflare.com"], (
                f"{name} loads script/style from a host this gate cannot read "
                f"and does not know about: {foreign!r}. A price there is "
                "ungraded — vendor the file under `website/` or extend the "
                "channel deliberately")


class TestPricingSourceIsShared:
    """#13645: `/tortoise` and `/tortoise-for-product-teams` cannot disagree.

    The assertion is deliberately structural rather than "each page contains
    the right numbers": a test of the second kind passes on two copies that
    happen to match today, which is exactly the state the defect looks like
    until someone edits one of them.
    """

    def test_both_product_pages_load_the_same_pricing_source(self):
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            html = path.read_text(encoding="utf-8")
            srcs = _pricing_source_includes(html)
            hits = [s for s in srcs if s == "/assets/tortoise-pricing.js"]
            assert len(hits) == 1, (
                f"{name} loads the shared pricing source {len(hits)} time(s) "
                f"(script srcs: {srcs!r}). Both product pages must render "
                "prices from /assets/tortoise-pricing.js — ONE source — which "
                "is what makes the two pages' prices agree by construction "
                "instead of by a copy that can rot.")
            # ⛔ WHICH TAG, and WHEN it runs — not just that the src string
            # exists somewhere. #13696 review cycle 2 (P1, measured): adding
            # `defer` to this tag on BOTH pages left 248 rows green, while in a
            # browser the INLINE script (which does `const PRICING =
            # window.TORTOISE_PRICING; … if (PRICING) { … renderPricing(); }`)
            # executes FIRST — so `PRICING` is undefined, `renderPricing()` never
            # runs, the toggle is dead and the reader keeps the static cards.
            # The sibling `/consent.js` tag IS deferred, so this is a natural
            # edit rather than a contrived one.
            scripts = list(re.finditer(
                r"<script\b([^>]*)>(.*?)</script>",
                _without_html_comments(html), re.S | re.I))
            loader = next(
                (m for m in scripts
                 if "/assets/tortoise-pricing.js" in m.group(1)),
                None)
            assert loader is not None, (
                f"{name} has no `<script src=\"/assets/tortoise-pricing.js\">` "
                "tag — the src is present but not on a tag a browser parses")
            assert not re.search(r"\b(?:defer|async)\b", loader.group(1), re.I), (
                f"{name} loads the shared pricing source with `defer`/`async`. "
                "The INLINE render script reads `window.TORTOISE_PRICING` "
                "synchronously, so a deferred load leaves it undefined and the "
                "JS render silently never runs (#13696 review cycle 2)")
            readers = [m for m in scripts
                       if not re.search(r"\bsrc\s*=", m.group(1), re.I)
                       and "window.TORTOISE_PRICING" in _code_only(m.group(2))]
            assert readers, (
                f"{name} has no inline script reading `window.TORTOISE_PRICING` "
                "— this gate no longer knows which tag must precede it")
            assert loader.start() < min(m.start() for m in readers), (
                f"{name} reads `window.TORTOISE_PRICING` BEFORE the tag that "
                "defines it loads, so the table is undefined at read time and "
                "the page falls back to the static cards (#13696 review cycle 2)")
        assert PRICING_SOURCE.is_file(), (
            "the shared pricing source is gone; both product pages would "
            "render no prices at all")

    def test_neither_page_declares_its_own_tier_table(self):
        """The defect itself: a second `PRICING` object inside a page.

        Three shapes are refused, because any one of them is a price table a
        page could drift on: re-assigning `window.TORTOISE_PRICING`, a
        `tiers: {` map, and a `price: <number>` entry. The render code that
        READS the table (`t.price`, `fmtPrice(t.price)`) is untouched by all
        three, so a legitimate refactor of either page's render is not redded
        here.
        """
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            html = path.read_text(encoding="utf-8")
            assert "TORTOISE_PRICING = {" not in html, (
                f"{name} assigns `window.TORTOISE_PRICING` itself. The ONE "
                "source is /assets/tortoise-pricing.js; a page that re-declares "
                "the table is the drift this gate exists to stop (#13645).")
            for pattern, what in (
                (r"\btiers\s*:\s*\{", "a `tiers: {...}` table"),
                (r"\bprice\s*:\s*\d", "a `price: <number>` entry"),
                (r"\bfeatures\s*:\s*\[", "a `features: [...]` list"),
            ):
                assert re.search(pattern, html) is None, (
                    f"{name} carries {what}, so it has its own copy of the "
                    "price table. Both product pages must render from "
                    "/assets/tortoise-pricing.js only (#13645).")

    def test_the_two_pages_render_the_same_pricing_markup(self):
        """The prices a READER gets are identical — that is 'the pages agree'.

        The static cards are what a no-JS reader, a crawler and a link-preview
        scraper see, and they are the copy most likely to rot; the usage line
        and the self-host block are the two other facts the section states.
        Compared as SERVED markup (`_served`), so a comment or a `<script>` that
        no reader receives cannot satisfy it.
        """
        by_name = {name: _served(path.read_text(encoding="utf-8"))
                   for name, path in PRODUCT_PAGES_SHARING_PRICING}
        left_name, right_name = (name for name, _ in PRODUCT_PAGES_SHARING_PRICING)
        left, right = by_name[left_name], by_name[right_name]
        for slice_fn, what in (
            (_static_pricing_grid, "the static pricing cards"),
            (_static_usage_line, "the overage line"),
            (_static_selfhost_block, "the self-hosted block"),
        ):
            assert slice_fn(left).strip() == slice_fn(right).strip(), (
                f"{what} differs between {left_name} and {right_name}. The "
                "pricing section is forked, not rewritten: both pages must show "
                "one set of prices from the shared source (#13645).\n"
                f"--- {left_name} ---\n{slice_fn(left)}\n"
                f"--- {right_name} ---\n{slice_fn(right)}")
        # ⛔ AND THE WHOLE SECTION, not just the three slices. Named slices leave
        # everything else in `#pricing-section` unread: a price claim in the
        # `.pricing-sub` paragraph ABOVE `#pricing-grid` — outside all three —
        # stayed green while quoting "Team from $12/mo, billed annually" on the
        # campaign page (measured at `4886f8d097`). The slices above are kept for
        # a precise failure message; this row is what makes the section as a whole
        # covered by construction, so a later addition is compared without anyone
        # remembering to add it to a list.
        assert _pricing_section(left).strip() == _pricing_section(right).strip(), (
            f"the `#pricing-section` markup differs between {left_name} and "
            f"{right_name} — somewhere OUTSIDE the cards, the overage line and "
            "the self-host block (those are compared separately above, and each "
            "passes). The whole section is one copied block: a price, a badge or "
            "a sub-head that lives only on one page is the fork #13645 exists to "
            "prevent.\n"
            f"--- {left_name} ---\n{_pricing_section(left)}\n"
            f"--- {right_name} ---\n{_pricing_section(right)}")

    def test_neither_page_mutates_the_shared_table(self):
        """The fourth shape of "a price table this page could drift on": a WRITE.

        `test_neither_page_declares_its_own_tier_table` refuses a page that
        RE-DECLARES the table; it says nothing about a page that MUTATES the
        shared one at runtime, which produces the same divergent price with one
        fewer line. Measured at `4886f8d097`: prepending
        `PRICING.tiers.pro.price = 25; /* experiment drift */` to the campaign
        page's inline script left all **136 rows GREEN** while that page rendered
        a different price for the same tier than `/tortoise` (#13696 review 3).

        A READ is untouched: `PRICING.tiers[name].price`,
        `PRICING.display.annual_discount_pct` and the local alias declaration
        `const PRICING = window.TORTOISE_PRICING;` all have no assignment after a
        property chain, and all three are used legitimately on both pages.

        ⛔ THE GUARD BINDS THE PROPERTY PATH, NOT THE ROOT IDENTIFIER. Binding on
        `PRICING` / `window.TORTOISE_PRICING` was the first version and it is one
        alias away from useless: `const PRICING = window.TORTOISE_PRICING;` is
        already both pages' established pattern, so `const _P = …; _P.tiers.pro
        .price = 25;` — or `const {tiers} = …; tiers.pro.price = 25;` — reached the
        same object and left all **137 rows GREEN** while the campaign page
        rendered a different price than `/tortoise` (measured at `b9d6bcdf4c`,
        #13696 review 4 finding 2). The key list is DERIVED from the shared source
        (`_extract_pricing_object`) rather than retyped, so a new schema field is
        covered the day it lands; a write has to end at one of those keys however
        the object was named, and a READ (`fmtPrice(t.price)`,
        `PRICING.tiers[name].price`, `PRICING.tiers.free.price === 0`) still has
        no `=` after the path.
        """
        pricing = _extract_pricing_object()
        keys = set(pricing) | {"tiers", "display"} | set(pricing["display"])
        for tier in pricing["tiers"].values():
            keys |= set(tier)
        key_alt = "|".join(sorted(map(re.escape, keys)))
        ident = r"[A-Za-z_$][\w$]*"
        rest = rf"(?:\s*\.\s*{ident}|\s*\[[^\]]*\])*"
        write = re.compile(
            rf"\.\s*(?:{key_alt})\b{rest}\s*"
            rf"(?:[-+*/%|&^]|\*\*|<<|>>|&&|\|\||\?\?)?=(?!=)")
        other = re.compile(
            rf"\bdelete\s+{ident}(?:\s*\.\s*{ident})*\s*\.\s*(?:{key_alt})\b"
            rf"|\bObject\.assign\(\s*(?:{ident}\s*\.\s*)*(?:{key_alt})\b")
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            hits = [ln.strip()
                    for ln in path.read_text(encoding="utf-8").splitlines()
                    if write.search(ln) or other.search(ln)]
            assert not hits, (
                f"{name} MUTATES the shared pricing table:\n  " + "\n  ".join(hits)
                + "\nThe table is ONE object shared by both product pages, so a "
                "write here changes the price BOTH pages render — whichever page "
                "it lands on, the other one is now quoting a different number "
                "with no red anywhere (#13645). A rename of the local alias does "
                "not make the write legitimate. Change the price in "
                "`product/pricing.json` (the source of truth) instead, so both "
                "pages move together — or, if this page genuinely needs its own "
                "figure, that is a decision, not an edit.")

    def test_no_page_quotes_a_price_outside_the_pricing_section(self):
        """The invariant's last unread scope: prices OUTSIDE `#pricing-section`.

        Every row above is scoped to the pricing section — the whole-section
        compare included — so a price figure anywhere ELSE in either document was
        read by nothing. Measured at `b9d6bcdf4c`: adding
        `<p class="hero-price">Pro from $25/mo</p>` to the campaign page's hero
        left all **137 rows GREEN** while that page quoted $25/mo for Builder
        against `/tortoise`'s $20 monthly / $15 annual (#13696 review 4 finding
        1). Both pages carry ZERO price figures outside the section today, so
        this row is green as written — it refuses the next one rather than fixing
        a live divergence.

        A page that genuinely needs a price elsewhere is making a decision, not
        an edit: put the figure inside the section, or change this row together
        with what the section serves.
        """
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            html = path.read_text(encoding="utf-8")
            # `unescape` FIRST: `&#36;` renders as `$` to a reader, but
            # `_served` does not decode entities, so a figure written that way
            # was in no served text this row scanned while the reader saw
            # `$89/mo` (measured, #13696 round 6).
            served = unescape(_served(html))
            section = unescape(_pricing_section(html))
            assert section in served, (
                f"the `#pricing-section` slice is not part of {name}'s served "
                "markup, so this row cannot tell inside from outside — the "
                "section compare above is the thing that finds the boundary")
            outside = served.replace(section, "", 1)
            hits = [outside[max(0, m.start() - 50):m.end() + 20].strip()
                    for m in re.finditer(PRICE_FIGURE_RE, outside)]
            assert not hits, (
                f"{name} quotes a price OUTSIDE the pricing section:\n  "
                + "\n  ".join(hits)
                + "\nEvery price on both product pages must come from the ONE "
                "shared table, and the section is where it is rendered — a "
                "figure above the fold is a second copy that no row compares "
                "against `/tortoise`, so the two pages can quote different "
                "numbers with nothing red (#13645).")

    def test_no_page_quotes_a_price_inside_its_own_script(self):
        """The channel `_served` cannot see: a price literal in the page's JS.

        `test_no_page_quotes_a_price_outside_the_pricing_section` reads the
        SERVED markup, and `_served` deletes `<script>` bodies — deliberately,
        because injected copy is not what a no-JS reader receives. The render
        rows close the other end (`test_the_two_pages_render_from_the_same_code`
        compares the five functions, `test_both_pages_init_to_the_same_billing_mode`
        the init statement), but a literal ANYWHERE ELSE in the inline script is
        read by nothing: `_served` has removed it and no render comparison
        covers it.

        Measured at `fb6b1afc68`: prepending
        `document.title = 'Pro from $25/mo';` to the campaign page's inline
        script left all **138 rows GREEN** while that page quoted $25/mo for
        Builder against `/tortoise`'s $20 monthly / $15 annual (#13696 round 5).
        Both pages carry ZERO price literals in their inline scripts today, so
        this row is green as written — it refuses the next one rather than
        fixing a live divergence.

        ⛔ Round 6 measured THREE channels this row still did not read, each
        leaving every suite green: a same-origin `<script src>` FILE (the tag's
        body is empty and `_served` never sees the file), a `<style>` body (a
        CSS `content:` rule is rendered text), and an entity-encoded figure
        (`&#36;89` reads as `$89/mo`). A list of remembered channels is what
        produced those gaps, so the row now reads every channel a reader can
        RECEIVE that is not served text — see the helpers below.

        A price a script needs must come from the shared table through
        `window.tortoiseFmtPrice` (or `PRICING`); a literal is a second copy of
        the number that no row compares against `/tortoise`.

        A line that is WHOLLY a comment is dropped before the scan, in EVERY
        channel below — the inline `<script>` bodies, the `<style>` bodies and
        the same-origin asset files — so prose may still mention a figure. A
        TRAILING comment on a code line is not — the line is scanned as written
        — which fails closed and deliberately: the fix is to move the sentence
        onto its own line, never to widen this row.
        """
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            markup = path.read_text(encoding="utf-8")
            scripts = _inline_scripts(markup)
            assert scripts, (
                f"{name} carries no inline `<script>` body, so this row would "
                "pass without reading anything — the channel it exists to read "
                "has moved and the row must move with it")
            channels = _price_channels(name, markup)
            hits = [
                f"{where}: {line.strip()}"
                for where, body in channels
                for line in body.splitlines()
                if PRICE_FIGURE_RE.search(line)
            ]
            assert not hits, (
                f"{name} quotes a price in a channel that is not served text:\n  "
                + "\n  ".join(hits)
                + "\nA price a script or style renders must come from the ONE "
                "shared "
                "table via `window.tortoiseFmtPrice` / `PRICING` — a literal is "
                "a second copy that no static row can see (`_served` deletes "
                "script and style bodies, and never sees a separate asset "
                "file) and no render row compares, so this page can "
                "quote a different number than `/tortoise` with every suite "
                "green (#13645).\n"
                "(A line that is wholly a comment is skipped, so prose may name "
                "a figure; a TRAILING comment on a code line is scanned with "
                "the code — move the sentence onto its own line.)")

    # The five functions that turn the shared table into the prices a JS
    # visitor actually sees. See `test_the_two_pages_render_from_the_same_code`.
    RENDER_FUNCTIONS = (
        "fmtPrice", "checkSvg", "renderPricing", "renderSelfHost", "setBilling",
    )

    def test_the_two_pages_render_from_the_same_code(self):
        """…and the JS RENDER agrees too, not just the static fallback.

        #13696 review finding 1. Both pages carry their own `fmtPrice` /
        `renderPricing` / `renderSelfHost` / `setBilling`. Every row above
        reads the STATIC cards, and
        `test_the_pages_format_prices_through_the_shared_function` only asserts
        that the string `window.tortoiseFmtPrice(` OCCURS — so no row read the
        CAMPAIGN page's render at all (product.html's bodies are read elsewhere,
        the niche page's were read by nothing). Measured at
        `1608357fc7`, before this row: changing the campaign page's call to
        `window.tortoiseFmtPrice(price, currentBilling, 0)` left all 133 rows
        GREEN while that page quoted $19/$25/$149 "annually" against
        `/tortoise`'s $15/$20/$119 — the exact divergence this PR exists to
        make impossible, one token away, with nothing red. A label or tier-order
        edit drifts the same way.

        Held as equality rather than derived per page because the invariant is
        that there is ONE render: a second copy that is merely *also* correct
        today is the state the defect looks like until someone edits one of
        them. `_render_code` drops comment lines only, so the two pages may keep
        explaining themselves differently.
        """
        by_name = {name: {fn: _render_code(path.read_text(encoding="utf-8"), fn, name)
                          for fn in self.RENDER_FUNCTIONS}
                   for name, path in PRODUCT_PAGES_SHARING_PRICING}
        left_name, right_name = (name for name, _ in PRODUCT_PAGES_SHARING_PRICING)
        for fn in self.RENDER_FUNCTIONS:
            left, right = by_name[left_name][fn], by_name[right_name][fn]
            assert left == right, (
                f"{fn}() differs between {left_name} and {right_name}. The "
                "pricing RENDER is a second copy that no other row reads, so a "
                "divergence here is invisible: a different formatter argument "
                "prints another page's prices, and a different `labels` map "
                "prints another page's tier names (#13645, #13696 review 1).\n"
                f"--- {left_name} ---\n{left}\n"
                f"--- {right_name} ---\n{right}")

    def test_each_page_passes_the_shared_discount_to_the_formatter(self):
        """A twin comparison cannot see a defect BOTH pages share.

        #13696 review cycle 2 (P1, measured). Every render row above compares
        the two pages to EACH OTHER, and `test_the_static_cards_on_both_pages_…`
        reads the STATIC cards — which `renderPricing()` overwrites via
        `grid.innerHTML = …`. So changing BOTH pages' call from
        `window.tortoiseFmtPrice(price, currentBilling,
        PRICING.display.annual_discount_pct)` to `…, 0)` made every JS visitor of
        both pages read $19/$25/$149 "billed annually" instead of
        $15/$20/$119 — and left **253 rows GREEN**. Two pages that are
        identically wrong are indistinguishable from two that are right.

        So the render is bound to the SOURCE here, not to its twin: the page's
        `fmtPrice` must hand `window.tortoiseFmtPrice` the shared discount, and
        the pair it therefore prints is held to the Python mirror of the same
        shared source (`_fmt_price`), never to truthiness — see the loop's own
        comment (#14020 review P2.1).
        """
        pricing = _extract_pricing_object()
        disc = pricing["display"]["annual_discount_pct"]
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            body = _render_code(path.read_text(encoding="utf-8"), "fmtPrice", name)
            call = re.search(r"window\.tortoiseFmtPrice\(\s*([^)]*)\)", body)
            assert call is not None, (
                f"{name}'s `fmtPrice` does not call `window.tortoiseFmtPrice` "
                "(#13696)")
            args = [a.strip() for a in call.group(1).split(",")]
            assert args == [
                "price", "currentBilling",
                "PRICING.display.annual_discount_pct",
            ], (
                f"{name}'s `fmtPrice` calls `window.tortoiseFmtPrice({', '.join(args)})` "
                "— it must pass the SHARED annual discount "
                "(`PRICING.display.annual_discount_pct`). A literal (or `0`) here "
                "quotes different money to every JS visitor while both pages "
                "stay identical to each other, so no twin comparison can see it "
                "(#13696 review cycle 2, P1)")
            # …and the numbers that call produces are the shared source's own:
            # the pair EXECUTED from the page's call is held to the Python
            # mirror of the same source. ⛔ It used to be `assert big and total`,
            # a TAUTOLOGY: every tier in every mode returns a non-empty pair
            # (`_js_fmt_price(0, …)` -> (`$0`, `Free forever`)), so the loop could
            # only fail on EMPTY output and never on DIFFERENT MONEY, while its
            # docstring claimed the numbers were checked. Measured at #14020
            # review P2.1: the loop survived any price change in either formatter.
            for tier in ("free", "solo", "pro", "team"):
                price = pricing["tiers"][tier]["price"]
                for mode in ("annual", "monthly"):
                    big, total = _js_fmt_price(price, mode, disc)
                    mirrored = _fmt_price(price, mode, disc)
                    assert (big, total) == mirrored, (
                        f"the shared formatter EXECUTED from {name}'s call and "
                        f"the Python mirror disagree for {tier} at ${price} "
                        f"({mode} mode): source={(big, total)!r} "
                        f"mirror={mirrored!r} — one of them now quotes different "
                        "money than the other (#14020 review P2.1)")

    def test_both_pages_init_to_the_same_billing_mode(self):
        """The render is compared; the statement that SEEDS it was not.

        #13696 review 2, finding 4. `setBilling` is held identical above, but the
        top-level `if (PRICING.display.annual_default) setBilling(...)` that
        chooses its argument sits OUTSIDE every captured function, and the only
        row that read it (`test_init_wiring_honors_annual_default`) reads
        `product.html` alone. Measured before this row: flipping the campaign
        page's init to `setBilling('monthly')` left all 135 rows GREEN while
        that page opened on $19/$25/$149 against `/tortoise`'s $15/$20/$119
        annual — the same divergence one statement earlier in the same file.
        """
        by_name = {name: _pricing_init_code(path.read_text(encoding="utf-8"), name)
                   for name, path in PRODUCT_PAGES_SHARING_PRICING}
        left_name, right_name = (name for name, _ in PRODUCT_PAGES_SHARING_PRICING)
        left, right = by_name[left_name], by_name[right_name]
        assert left == right, (
            f"the billing default is wired differently in {left_name} and "
            f"{right_name}, so a visitor lands on different prices depending "
            "on which product page they arrived at (#13645):\n"
            f"--- {left_name} ---\n{left}\n"
            f"--- {right_name} ---\n{right}")

    def test_the_static_cards_on_both_pages_carry_the_shared_numbers(self):
        """…and the identical copy is also the CORRECT one.

        `TestStaticPricingFallback` derives product.html's cards from the
        shared source; this holds the other page's cards to the same source
        directly, so a drift where BOTH pages are edited together (and so stay
        identical) still reds here rather than needing the mirror test to
        notice a page it does not read.

        ⛔ #13696 round 16, P1: the pair is derived from `_js_fmt_price`, which
        EXECUTES `website/assets/tortoise-pricing.js` — the code both pages
        load — rather than from the Python mirror. That makes this row the
        "page's rendered pair vs. the formatter's own templates" binding: an
        edited `return { big: …, total: … }` in the source now reds HERE, on the
        numbers a visitor reads, and not only in the mirror-agreement rows.
        """
        pricing = _extract_pricing_object()
        disc = pricing["display"]["annual_discount_pct"]
        mode = "annual" if pricing["display"]["annual_default"] else "monthly"
        order = ("free", "solo", "pro", "team")
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            html = path.read_text(encoding="utf-8")
            cards = _static_cards(html)
            assert len(cards) == len(order), (
                f"{name} renders {len(cards)} static cards for {len(order)} tiers")
            for tier_name, card in zip(order, cards, strict=True):
                tier = pricing["tiers"][tier_name]
                big, total = _js_fmt_price(tier["price"], mode, disc)
                assert (big, total) == _fmt_price(tier["price"], mode, disc), (
                    f"the formatter the pages LOAD and the Python mirror disagree "
                    f"for {tier_name} at ${tier['price']} ({mode}): "
                    f"source={ (big, total)!r} mirror="
                    f"{_fmt_price(tier['price'], mode, disc)!r} — one of them now "
                    "quotes a different number than the other (#13645)")
                assert card["big"] == big, (
                    f"{name} / {tier_name}: static price {card['big']!r} != "
                    f"{big!r} from the shared source ({mode})")
                assert card["billing"] == total, (
                    f"{name} / {tier_name}: billing line {card['billing']!r} != "
                    f"{total!r} from the shared source")
                assert card["included"] == tier["features"], (
                    f"{name} / {tier_name}: features {card['included']!r} != the "
                    f"shared source's {tier['features']!r}")
                assert card["excluded"] == tier["excluded"], (
                    f"{name} / {tier_name}: exclusions {card['excluded']!r} != the "
                    f"shared source's {tier['excluded']!r}")

    def test_every_figure_inside_the_section_is_one_the_source_has(self):
        """…and a figure the source does NOT have is refused on BOTH pages.

        The whole-section compare above asserts the two pages AGREE, and
        `test_the_static_cards_on_both_pages_carry_the_shared_numbers` derives
        the three slices it knows how to parse. Everything ELSE inside
        `#pricing-section` — `.pricing-sub`, the `<h2>`, the toggle label, the
        ToS sentence, any paragraph added later — was compared to the other page
        and to nothing else, so a price added to BOTH sections could ship on both
        pages quoting a number the source does not contain (measured, #13696
        round 6: `<p class="pricing-sub">Team from $89/mo, billed annually.</p>`
        in both sections left 139 rows green, while the source says Team is $149
        monthly / $119 annual — and the block's own comment tells the next
        editor to change both together).

        So this row takes the COMPLEMENT of the parsed slices: every `$` figure a
        reader can receive from the section must be a figure the shared source
        can produce in SOME mode. A figure that is not is a price nobody
        approved, and no other row would say so.
        """
        pricing = _extract_pricing_object()
        disc = pricing["display"]["annual_discount_pct"]
        allowed: set[str] = set()
        for tier in pricing["tiers"].values():
            for mode in ("monthly", "annual"):
                for rendered in _fmt_price(tier["price"], mode, disc):
                    allowed.update(_figures(rendered))
        for key in ("overage_line", "license_self_hosted"):
            allowed.update(_figures(pricing["display"][key]))
        assert allowed, "the shared source yields no figure at all — this row is vacuous"
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            section = unescape(_served(_pricing_section(
                path.read_text(encoding="utf-8"))))
            # …and the OTHER half of the same vacuity (#13696 round 7, P2 #7):
            # `allowed` being non-empty says nothing about what the page
            # RENDERS. A section that stopped carrying `$` figures at all left
            # this row GREEN while it read nothing — `stray` is then empty by
            # construction, so the row cannot disagree with the source. The
            # neighbour rows spell this guard out; this one did not.
            rendered_figures = _figures(section)
            assert rendered_figures, (
                f"{name} renders NO `$` figure inside `#pricing-section`, so "
                "this row reads nothing and cannot disagree with the shared "
                "source — it would pass over a section that renders no price "
                "at all. Either the section stopped carrying its figures (a "
                "render regression no other row names) or the extractor is "
                "reading the wrong slice (#13696 round 7, P2 #7).")
            # A figure SPLIT by markup is refused rather than read as its
            # prefix: `$15<b>9</b>` renders as `$159` to a reader but matched
            # only `$15`, which IS an allowed figure — so an unapproved $159
            # was invisible while both pages agreed on it (#13696 round 8).
            # Fail closed: the ambiguous spelling must be written as one run.
            split = PRICE_SPLIT_RE.search(section)
            assert split is None, (
                f"{name} splits a price figure across inline markup "
                f"({split.group(0)!r}): a reader sees the digits joined, while "
                "every row here can only read the prefix — so a figure the "
                "shared source does not contain can ship. Write the number as "
                "one run and wrap the WHOLE figure if it needs styling.")
            stray = sorted(set(rendered_figures) - allowed)
            assert not stray, (
                f"{name} quotes {stray!r} inside `#pricing-section`, and the "
                f"shared source can produce only {sorted(allowed)!r}. Both "
                "pages match each other and neither matches the table, so every "
                "row that compares the pages is satisfied while both quote a "
                "price nobody approved (#13645). Change the price in "
                "`product/pricing.json` and `website/assets/tortoise-pricing.js` "
                "— together — instead of typing it into the section.")

    def test_the_pages_format_prices_through_the_shared_function(self):
        """The rounding contract is ONE implementation, too.

        Two pages can share a table and still disagree if each formats it: the
        annual per-month figure is `round(price * 12 * (1 - disc) / 12)` with
        the total DERIVED from it, and a page that re-derives it differently
        prints `$15 / $180` beside `$15 / $186`. So both pages call
        `window.tortoiseFmtPrice`, and the source defines it.
        """
        assert "window.tortoiseFmtPrice = function" in PRICING_SOURCE.read_text(
            encoding="utf-8"), (
            "the shared source no longer defines `window.tortoiseFmtPrice` — "
            "the rounding contract has no home, so each page would re-implement it")
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            html = path.read_text(encoding="utf-8")
            # ⛔ CODE, not the document. #13696 review cycle 2 (P1, measured):
            # reading raw `html` let a page replace `fmtPrice` with a local
            # re-implementation and keep this row GREEN by leaving the sentence
            # `// window.tortoiseFmtPrice( … ) is the shared contract` in a
            # comment — 248 rows green over exactly the defect four earlier
            # rounds fixed elsewhere in this file.
            code = _code_only("\n".join(_inline_scripts(html)))
            assert "window.tortoiseFmtPrice(" in code, (
                f"{name} does not call `window.tortoiseFmtPrice` — it formats "
                "the shared numbers itself, which is the drift one step past "
                "the table (#13645)")
            # Whitespace-insensitive on purpose: the literal refused above is
            # only ONE spelling of the same re-derivation, so `price*12` (or
            # `price *12`) would slip past a plain substring check — and the
            # re-derivation, not its spacing, is the defect.
            assert not re.search(r"Math\.round\(\s*price\s*\*\s*12", code), (
                f"{name} re-derives the annual per-month price instead of "
                "delegating to `window.tortoiseFmtPrice`")

    def test_a_price_is_not_assembled_from_a_dollar_fragment(self):
        """A figure built by CONCATENATION is in no `$<digit>` channel.

        ⛔ #13696 round 11, P2. `PRICE_FIGURE_RE` needs a digit right after the
        `$`, which is the one shape a concatenated price never has: the figure
        lives in the table and the source text carries only `'$' + t.price`.
        Measured at `59bc263091`: appending
        `document…insertAdjacentHTML('beforeend', '<div class="price">$' + t.price + '<span>/mo</span></div>')`
        to the campaign page's inline script left all **158 rows GREEN** while
        its grid showed Builder at $25/mo beside its own $20 annual cards and
        `/tortoise` showed $20/$15. `String.fromCharCode(36)` reaches the same
        markup, but it reaches NO detector either — that is a decision about
        what a static gate can see, not a spelling to chase.

        Read over EVERY channel `_price_channels` lists (inline script, `<style>`,
        same-origin files, stylesheets), because a fragment is exactly as
        invisible there as a figure. The shared source is excluded from that
        list BY DESIGN — it is where a price is legitimately built.
        """
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            markup = path.read_text(encoding="utf-8")
            hits = [f"{where}: {line.strip()}"
                    for where, body in _price_channels(name, markup)
                    for line in body.splitlines()
                    if PRICE_FRAGMENT_RE.search(line)]
            assert not hits, (
                f"{name} assembles a price from a bare `$` fragment:\n  "
                + "\n  ".join(hits)
                + "\nA figure concatenated onto `'$'` carries no digit in the "
                "source, so no price row can read the number it renders and "
                "this page can quote a price no other page does with every "
                "suite green (#13645, #13696 round 11). Get the WHOLE figure "
                "from `window.tortoiseFmtPrice(…)` — it returns `big` already "
                "carrying the `$` — or from `PRICING`.")
            attrs = [m.group(0) for m in PRICE_DATA_ATTR_RE.finditer(markup)]
            assert not attrs, (
                f"{name} carries a price in a DATA ATTRIBUTE ({attrs!r}). A "
                "figure stored as `data-price` is turned into a visible price "
                "by a script, so it is a second copy of the number that no "
                "static row reads and that the shared table does not own "
                "(#13645). Render it from `window.tortoiseFmtPrice` instead.")

    def test_the_shared_table_is_FROZEN_against_a_reflective_write(self):
        """…and the runtime half of the mutation gate.

        ⛔ #13696 round 11, P2. `test_neither_page_mutates_the_shared_table`
        refuses the STATIC spellings of a write (`x = …`, `delete x.y`,
        `Object.assign`), and a list of spellings is what the reflective forms
        defeat: `Object.defineProperty(window.TORTOISE_PRICING.tiers.pro,
        'price', { value: 26 })` and `Reflect.set(…)` both left all **158 rows
        GREEN** at `59bc263091`, re-pricing the tier for BOTH pages at runtime.
        The fix is not more regex — it is that the table cannot be written at
        all: the shared source deep-freezes it, so the write is REFUSED. What
        that means in a page's classic (sloppy-mode) script, measured with node
        against the frozen file: `Object.defineProperty(…)` and
        `features.push(…)` throw, while `x.price = 26` and
        `Reflect.set(…, 'price', 26)` are silently refused (`Reflect.set`
        returns false) — the tier still prices at 25 and the formatter still
        returns `$20` / `$240 billed annually`. The write does not take effect
        either way, which is the invariant; a throw is not required.

        The freeze IS the mechanism, so what this row can do is keep the freeze
        in place and DEEP (`Object.keys(table.tiers).forEach`, so a new tier is
        covered the day it lands, plus each tier's `features`/`excluded` arrays
        and `display`).
        """
        code = _code_only(PRICING_SOURCE.read_text(encoding="utf-8"))
        assert re.search(r"Object\.keys\(\s*table\.tiers\s*\)\.forEach\(", code), (
            "the shared source no longer freezes every tier by ITERATION, so a "
            "tier added to the table is writable while the ones named in a "
            "list are not (#13696 round 11)")
        for target in ("tier.features", "tier.excluded", "tier",
                       "table.display", "table.tiers", "table"):
            assert re.search(
                rf"Object\.freeze\(\s*{re.escape(target)}\s*\)", code), (
                f"the shared source no longer `Object.freeze({target})`, so "
                "that part of the ONE table both pages render is writable at "
                "runtime — `Object.defineProperty` / `Reflect.set` then change "
                "the price `other` pages quote with nothing red (#13696 "
                "round 11).")

    #: What makes a served page a RENDERER of the shared table.
    TABLE_RENDER_MARKERS = (
        "TORTOISE_PRICING", "tortoise-pricing.js",
        'id="pricing-grid"', 'class="price"',
    )

    def test_the_population_is_every_page_that_renders_the_table(self):
        """The set is hand-maintained on purpose — and VERIFIED, not trusted.

        ⛔ #13696 round 11, P3. `PRODUCT_PAGES_SHARING_PRICING` is a two-page
        tuple, while `website/assets/tortoise-pricing.js` told an editor that "a
        page that inlines its own tier table reds the shared-source test". It
        did not: giving `website/index.html` its own
        `window.TORTOISE_PRICING = {…}` left all **158 rows GREEN**, because
        every row in this class iterates the tuple. The list stays explicit — a
        page joins the shared table by DECISION — but the population is now
        derived from the pages themselves, so a page that starts rendering the
        table without joining reds here instead of shipping a second copy.

        The marker list is deliberately about RENDERING the table, not about
        mentioning a number: `license.html`/`faq.html` quote legitimate fees, and
        a `$<digit>` marker would draft them into a set that requires loading
        the shared source.
        """
        members = {(name, path.resolve()) for name, path in
                   PRODUCT_PAGES_SHARING_PRICING}
        population = TestSiteIconAndSocialPreviewContract.PAGES_AT_11762
        for page in sorted((REPO_ROOT / "website").glob("*.html")):
            text = page.read_text(encoding="utf-8")
            if not any(marker in text for marker in self.TABLE_RENDER_MARKERS):
                continue
            rel = f"website/{page.name}"
            assert (rel, page.resolve()) in members, (
                f"{rel} renders the shared pricing table "
                f"({[m for m in self.TABLE_RENDER_MARKERS if m in text]!r}) but is "
                "not in `PRODUCT_PAGES_SHARING_PRICING`, so NONE of the rows "
                "that hold a page to the ONE source and to the other pages' "
                "render covers it — it can quote a different price with every "
                "suite green (#13645, #13696 round 11). Either load "
                "`/assets/tortoise-pricing.js` and join the tuple, or stop "
                "rendering a price table on this page.")
            assert page.name in population, (
                f"{rel} renders the shared pricing table but is missing from "
                "`PAGES_AT_11762` (the pinned served-page population), so the "
                "head/card guards do not cover it either")
        for name, path in PRODUCT_PAGES_SHARING_PRICING:
            text = path.read_text(encoding="utf-8")
            assert any(marker in text for marker in self.TABLE_RENDER_MARKERS), (
                f"{name} is in `PRODUCT_PAGES_SHARING_PRICING` but renders none "
                "of the table's markers — it no longer renders the shared "
                "table, so the set is stale and the rows above pin a page "
                "that has moved on")


class TestPriceFragmentDetector:
    """`PRICE_FRAGMENT_RE` pinned on its own, so the channel row cannot go blind.

    The row above reads the LIVE pages, which carry no fragment today: nothing
    inside it can tell a working detector from a dead one. These rows drive the
    regex directly.
    """

    def test_a_concatenated_price_is_flagged(self):
        assert PRICE_FRAGMENT_RE.search(
            "d.innerHTML = '<div class=\"price\">$' + t.price + '</div>';")
        assert PRICE_FRAGMENT_RE.search("document.title = 'from $' + t.price;")

    def test_a_template_interpolation_is_not_a_fragment(self):
        assert not PRICE_FRAGMENT_RE.search(
            "`<div class=\"price\">${fmtPrice(t.price).big}</div>`")

    def test_a_whole_figure_is_left_to_the_figure_detector(self):
        # `$25` / `$0` / `$1,428` are `PRICE_FIGURE_RE`'s subject; this detector
        # must not double-report them (or it reds correct markup on sight).
        assert not PRICE_FRAGMENT_RE.search("'$25', '$0', '$1,428'")
        assert not PRICE_FRAGMENT_RE.search("<div class=\"price\">$119<span>/mo</span></div>")


class TestConsentSeam:
    """#13696 review finding 1: the `tortoise:consent` seam has an assertion.

    `website/consent.js` gained a real behaviour for #13645: it dispatches
    `tortoise:consent` with `{ state: "granted" | "denied" }`, and the niche
    page's only page-view capture listens for it
    (`window.addEventListener('tortoise:consent', trackView)`). The seam is
    load-bearing in the PR's own words: without it a visitor who accepts the
    banner AFTER the page ran has their page view dropped, so the experiment
    has clicks with no denominator.

    Nothing asserted it. The page-side rows only prove the page LISTENS
    (`test_the_three_funnel_events_are_named_in_the_source`), which a module
    that never emits satisfies; and the guard that owns this file
    (`tests/e2e/test_legal_pages.py::test_consent_js_served_and_banner_present`)
    pinned no line of it. Deleting the dispatch therefore reddened zero rows.

    Two halves, because they fail differently:

    * `test_the_module_emits_the_event_the_page_listens_for` — the event NAME
      is shared between two files, so it is asserted in both rather than
      trusted to stay equal.
    * `test_the_granted_event_is_dispatched_after_the_opt_in` — the ORDER is
      the contract ("dispatched AFTER the opt-in/opt-out has been applied"),
      and order is invisible to any assertion that only looks for the string.
      A reordering re-introduces the pre-consent beacon the seam exists to
      prevent, which is a privacy defect, not a cosmetic regression.
    """

    EVENT = "tortoise:consent"

    #: Each `announceConsent("granted")` call site is the LAST statement of a
    #: granted path, so its enclosing function also holds the opt-in it must
    #: follow. Named rather than counted: a path that disappears reds here, and
    #: a THIRD path added later is not refused by an expected-count pin.
    GRANTED_PATHS = ("onAccept", "init")

    @staticmethod
    def _function_body(src: str, name: str) -> str:
        """The body of the module-level `function <name>(…)`, by indentation.

        consent.js is one IIFE whose own functions sit at two-space indent, so a
        block runs from `  function <name>(` to the next line that opens a
        sibling. Sliced by indentation rather than brace-counting: braces inside
        the module's regex/string literals would break a naive counter.
        """
        starts = [m for m in re.finditer(r"^  function (\w+)\(", src, re.M)]
        for i, m in enumerate(starts):
            if m.group(1) != name:
                continue
            end = starts[i + 1].start() if i + 1 < len(starts) else len(src)
            # ⛔ CODE ONLY. Every caller decides presence with `str.find`, and
            # `find` matches a MENTION inside a comment as readily as the call
            # itself: `opt_in_capturing()` deleted from `onAccept` while a
            # `// opt_in_capturing() is now handled inside
            # loadConsentedAnalytics()` sentence remained passed all three
            # `TestConsentSeam` rows (measured, #13696 round 6). That deletion
            # is not cosmetic — consent.js loads PostHog OPTED OUT and
            # `loadConsentedAnalytics()` only loads GTM/Meta, so every accepting
            # visitor's page view was dropped by an instrument that stayed green.
            # The file's own `_strip_comment_lines` is the exact instrument and
            # is already used by `_render_code`/`_inline_scripts` for this hazard.
            return _code_only(src[m.start():end])
        raise AssertionError(
            f"no `function {name}(` at the module's own indent in consent.js — "
            "this gate's subject moved, so it is no longer asserting order")

    def test_the_page_half_of_the_consent_seam_does_not_depend_on_a_CDN(self):
        """The funnel must register even when the GSAP CDN is blocked.

        #13696 review cycle 2 (P2, measured by evaluating the page's inline
        script with `gsap` undefined): the funnel block used to be the TAIL of
        the gsap `<script>`, whose first statement is
        `gsap.registerPlugin(ScrollTrigger)`. A blocked cdnjs — a common
        corporate-proxy condition, under which the page still renders fine on
        its static cards — therefore threw BEFORE
        `window.addEventListener('tortoise:consent', trackView)` and the click
        delegate ran, so all three funnel events went to zero with no signal.
        That is exactly the "blocked requests read as 'nobody clicked'" hazard
        the page's own comment names.

        Pinned structurally rather than by position: the script that registers
        the listener must contain no GSAP use, and it must appear BEFORE any
        `<script src>` that loads gsap.
        """
        html = _product_teams_html()
        scripts = list(re.finditer(
            r"<script\b([^>]*)>(.*?)</script>", _without_html_comments(html),
            re.S | re.I))
        listener = [m for m in scripts
                    if "addEventListener('tortoise:consent'" in _code_only(m.group(2))]
        assert listener, (
            "the page no longer registers a `tortoise:consent` listener at all "
            "(#13696)")
        assert len(listener) == 1, (
            f"the consent listener is registered in {len(listener)} scripts — "
            "this row cannot tell which one must precede the CDN tags")
        reg = listener[0]
        assert "gsap." not in _code_only(reg.group(2)), (
            "the script that registers the consent listener also uses GSAP, so "
            "a blocked/failed GSAP CDN throws before the listener is "
            "registered and every funnel event silently goes to zero "
            "(#13696 review cycle 2)")
        gsap_srcs = [m for m in scripts
                     if re.search(r"gsap", m.group(1), re.I)]
        if gsap_srcs:
            assert reg.start() < min(m.start() for m in gsap_srcs), (
                "the consent listener is registered AFTER a `<script src>` that "
                "loads GSAP — a blocked CDN then kills the funnel "
                "(#13696 review cycle 2)")

    def test_the_module_emits_the_event_the_page_listens_for(self):
        # ⛔ CODE, not the file: `_code_only` cuts comment spans, so a module
        # whose dispatch was commented out — leaving the event NAME in prose —
        # is RED here. #13696 review cycle 2 (P1, measured): reading the raw
        # file left all three `TestConsentSeam` rows green over exactly that
        # mutation, which is the "clicks with no denominator" defect this class
        # exists to catch.
        js = _code_only(CONSENT_JS.read_text(encoding="utf-8"))
        assert f'"{self.EVENT}"' in js, (
            f"consent.js never dispatches `{self.EVENT}`. The niche page's only "
            "page-view capture waits for it, so with no emit every visit that "
            "does not re-decide consent is silently uncounted — clicks with no "
            "denominator (#13645)")
        assert f"'{self.EVENT}'" in _live_markup(_product_teams_html()), (
            f"the niche page no longer listens for `{self.EVENT}` — its page "
            "view would then fire only when the decision happens mid-visit")

    def test_the_granted_event_is_dispatched_after_the_opt_in(self):
        js = CONSENT_JS.read_text(encoding="utf-8")
        for path in self.GRANTED_PATHS:
            body = self._function_body(js, path)
            granted_m = GRANTED_STMT.search(body)
            assert granted_m is not None, (
                f"`{path}` grants consent but never dispatches "
                f"`{self.EVENT}` with state `granted`: a visitor who accepts "
                "the banner here has their page view dropped (#13645)")
            # ⛔ A STATEMENT, not a substring. `str.find` was satisfied by a
            # mention in a comment, and then by a mention in a STRING LITERAL
            # (`throw new Error("opt_in_capturing() is handled by the caller")`)
            # and by a `//` that directly followed a colon — so a deleted call
            # stayed green through three successive spellings (#13696 rounds 6,
            # 8, 9). An anchored pattern matches the call and nothing else.
            opt_in_m = OPT_IN_STMT.search(body)
            assert opt_in_m is not None, (
                f"`{path}` no longer calls `opt_in_capturing()` — the path "
                "this gate measures the ordering against moved")
            assert opt_in_m.start() < granted_m.start(), (
                f"`{path}` dispatches `{self.EVENT}` with state `granted` "
                "BEFORE calling `opt_in_capturing()`. The event tells a page "
                "consent has been applied, so announcing first is the "
                "pre-consent beacon this seam exists to prevent — the page "
                "would capture before the opt-in landed")
            # ⛔ THE PRESENCE HALF IS ASSERTED FIRST, because the ordering
            # comparison is VACUOUS without it: an absent call yields no match,
            # so deleting the loader call from a granted path would pass a
            # comparison-only row while GTM/Meta never get activated.
            loaders_m = LOADERS_STMT.search(body)
            assert loaders_m is not None, (
                f"`{path}` grants consent but never calls "
                "`loadConsentedAnalytics()`: the consent-gated GTM/Meta loaders "
                "are never activated on the accept path, so a visitor who "
                "explicitly opted in is measured by nothing downstream "
                "(#13645)")
            assert loaders_m.start() < granted_m.start(), (
                f"`{path}` dispatches `{self.EVENT}` before activating the "
                "consent-gated loaders (GTM/Meta), so a listener acts on a "
                "decision the tags have not followed yet")

    @staticmethod
    def _page_function(src: str, name: str) -> str:
        """The body of the page's own `function <name>(…)`, by indentation.

        The page's inline script declares its helpers at six-space indent and
        closes each at the same indent, so the body runs from the declaration
        to the matching close brace. Sliced by indentation rather than
        brace-counting: the literals inside these functions are the one place a
        naive counter would drift.

        Comments are cut first (`_without_html_comments`), so a function that a
        `<!-- … -->` comment has disabled is not read as if the browser still
        ran it — otherwise this gate is satisfied by `function track(` inside a
        comment while the page beacons nothing.
        """
        src = _without_html_comments(src)
        m = re.search(
            rf"^( *)function {re.escape(name)}\(.*?\n\1\}}", src, re.S | re.M)
        assert m, (
            f"no `function {name}(` at the page's script indent — this gate's "
            "subject moved, so it is no longer asserting the consent gate")
        # ⛔ JS comments cut too, for the SAME reason `_function_body` uses
        # `_code_only`: the rows below decide presence with `str.find`, which
        # matches a MENTION in a comment. Without this, commenting out
        # `if (!consented()) return;` in `track()` kept every row green — i.e.
        # the pre-consent beacon this gate exists to prevent shipped green
        # (#13696 round 8, P1).
        return _code_only(m.group(0))

    def test_the_page_gates_every_capture_on_the_consent_state(self):
        """#13696 review finding 2: the page half of the seam is behavioural.

        `track()` and `trackView()` are the page's ONLY path to a beacon
        (`window.posthog.capture` is called nowhere else in the page's script),
        and `trackView()` is reached from the consent event.

        Two independent mutants survive every presence-only row:

        * deleting `if (!consented()) return;` from `track()` ships an analytics
          request from a visitor who never decided — the pre-consent beacon the
          PR body itself calls a privacy defect;
        * deleting `!consented()` from `trackView()` is the same defect one hop
          earlier, and worse: the `viewed` latch would be set by the undecided
          visit, so the later `granted` event would find `viewed === true` and
          the page view would be lost for good.

        `test_the_three_funnel_events_are_named_in_the_source` cannot see
        either: it asserts the identifiers occur, and both mutants leave every
        identifier in place.
        """
        track = self._page_function(_product_teams_html(), "track")
        gate_m = NOT_CONSENTED_STMT.search(track)
        assert gate_m is not None, (
            "the page's `track()` has no `consented()` gate, so an undecided "
            "visitor's click or page view reaches `posthog.capture` — the "
            "pre-consent beacon this seam exists to prevent (#13645)")
        gate = gate_m.start()
        capture = track.find("posthog.capture(")
        assert capture != -1, (
            "this gate's subject moved: `track()` no longer calls "
            "`posthog.capture`, so it is no longer the beacon path it pins")
        assert gate < capture, (
            "`track()` calls `posthog.capture` BEFORE checking `consented()`: "
            "the check is no longer a gate, it is a post-capture filter")

        view = self._page_function(_product_teams_html(), "trackView")
        vgate = view.find("!consented()")
        assert vgate != -1, (
            "the page's `trackView()` has no `consented()` gate — the "
            "`tortoise:consent` listener would capture a page view on a "
            "decision that was never granted (#13645)")
        latch = view.find("viewed = true")
        assert latch != -1, (
            "this gate's subject moved: `trackView()` no longer sets the "
            "`viewed` latch")
        assert vgate < latch, (
            "`trackView()` latches `viewed = true` BEFORE checking "
            "`consented()`: an undecided visit consumes the latch, so the "
            "visitor's later `granted` event finds `viewed === true` and the "
            "page view is lost silently — the exact undercount this seam "
            "exists to prevent (#13645)")
        assert vgate < view.find("track(EVENTS.view"), (
            "`trackView()` calls `track()` BEFORE checking `consented()`, so "
            "the gate is no longer the only thing between an undecided "
            "visitor and a beacon (#13645)")

    def test_the_click_delegate_counts_the_entry_points_the_legend_names(self):
        """The funnel's only conversion event must have a pinned selector.

        Nothing else binds the delegate. MEASURED (round 4): retargeting
        `ev.target.closest('[data-cta], #pricing-section a[href^="/auth"]')`
        to a bare `[data-cta]` left the whole file green, while the four tier
        cards' and the self-host block's `/auth` links stopped being counted as
        `product_teams_cta_primary` — the campaign's one conversion event,
        silently zeroed on the page whose stated purpose is to carry its own
        denominator. The legend above the handler would have kept describing
        the old behaviour with no red anywhere.
        """
        code = _live_markup(_product_teams_html())
        assert '[data-cta], #pricing-section a[href^="/auth"]' in code, (
            "the click delegate no longer selects the funnel's `/auth` entry "
            "points: the tier cards' and the self-host block's links would "
            "raise no event, and nothing else in this suite would notice")
        assert "el.getAttribute('data-cta') === 'primary'" in code, (
            "the hero's primary CTA is no longer classified as "
            "`product_teams_cta_primary`")
        assert "el.closest('#pricing-section')" in code, (
            "the `/auth` links inside `#pricing-section` are no longer "
            "classified as `product_teams_cta_primary`")

        # The legend's decomposition, derived from the MARKUP rather than from
        # the prose it is written in: four tier cards, plus the self-host
        # block's one `/auth` link.
        pricing = _element_by_id(_product_teams_html(), "pricing-section")
        cards = len(re.findall(r'class="[^"]*\bpricing-card\b', pricing))
        auth = len(re.findall(r'href="/auth"', pricing))
        assert cards == 4, (
            f"#pricing-section carries {cards} tier cards; the funnel legend "
            "names four — update the legend and this row together")
        assert auth == cards + 1, (
            f"#pricing-section carries {auth} `/auth` links but {cards} tier "
            "cards. The legend says 'the four tier cards and the self-host "
            "block's \"Migrate to cloud anytime\", the only self-host CTA "
            "that reaches /auth' — a new entry point must be named there too")


# ═══════════════════════════════════════════════════════════════════════════════
# 12. The niche product page's identity and its owner copy (#13645)
# ═══════════════════════════════════════════════════════════════════════════════

#: The page's stable identity attribute. Pinned rather than trusted because the
#: repo keys guards on `data-page`, and this page must stay distinguishable from
#: `data-page="product"` (the tortoise-host root marker). ⛔ Nothing else reads
#: this value at this head — `deploy-pages.yml`'s preflight greps only
#: `data-page="product"` — so THIS pin is the whole guard for it: a silent loss
#: reddens here and nowhere else.
PRODUCT_TEAMS_DATA_PAGE = "product-teams"

#: The URL the owner asked for, and the one the sitemap advertises.
PRODUCT_TEAMS_URL = "https://premiselabs.co/tortoise-for-product-teams"

#: The three funnel events the page names in its own source (owner decision,
#: 2026-11-12: the experiment must carry a denominator).
PRODUCT_TEAMS_EVENTS = (
    "product_teams_view",
    "product_teams_cta_primary",
    "product_teams_cta_secondary",
)


def _product_teams_html() -> str:
    return PRODUCT_TEAMS_HTML.read_text(encoding="utf-8")


def _element_by_id(html: str, element_id: str) -> str:
    """The whole element whose `id` is `element_id`, brace-counted by TAG.

    Sliced by balanced tags rather than "to the next `</section>`": the beats
    contain `<div>`s, and a slice that stopped at the first close would hide
    most of the beat from a copy pin.
    """
    served = _served(html)
    marker = f'id="{element_id}"'
    assert marker in served, f"no element with {marker} — this gate's subject moved"
    at = served.index(marker)
    start = served.rindex("<", 0, at)
    tag = re.match(r"<([a-zA-Z0-9]+)", served[start:])
    assert tag, f"could not read the opening tag before {marker}"
    name = tag.group(1)
    depth = 0
    for m in re.finditer(rf"<{name}\b|</{name}>", served[start:]):
        depth += 1 if m.group(0) != f"</{name}>" else -1
        if depth == 0:
            return served[start:start + m.end()]
    raise AssertionError(f"<{name} id={element_id}> has no balanced closing tag")


class TestProductTeamsIdentity:
    """The repo obligations that fail QUIETLY when a new page omits them.

    Each assertion below is one of them, named with the failure it prevents.
    The head/card/icon walkers in this file cover this page automatically (it is
    in `PAGES_AT_11762`); what follows is the part that is specific to a page
    that is ADDED ALONGSIDE another one rather than replacing it.
    """

    def test_the_hero_is_one_section_element(self):
        """⛔ `#beat-hero` on this page must not nest a `<section>`.

        The sibling gates in this file slice a beat to the FIRST `</section>`
        (`_pricing_section`, `_automate_beat_markup`), so a nested section would push
        most of the hero outside the slice and leave any copy pin over it
        silently passing on the remainder. `_element_by_id` is tag-balanced, so
        it sees the whole hero and a nested section is what makes the count 2.
        """
        hero = _element_by_id(_product_teams_html(), "beat-hero")
        assert hero.count("<section") == 1, (
            "website/tortoise-for-product-teams.html nests a <section> inside "
            "#beat-hero — the hero must stay ONE section element, because a "
            "slice that stops at the first </section> would then cover only "
            "part of it. Re-scope the nested claim instead of nesting it.")

    def test_the_identity_attribute_is_pinned(self):
        root = _root_element(_product_teams_html(), "website/tortoise-for-product-teams.html")
        assert f'data-page="{PRODUCT_TEAMS_DATA_PAGE}"' in root, (
            "website/tortoise-for-product-teams.html lost "
            f'data-page="{PRODUCT_TEAMS_DATA_PAGE}" from its <html> tag '
            f"({root!r}). That attribute is how a page is identified without "
            "reading its copy. Losing it is caught HERE and nowhere else: the "
            "deploy preflight greps only `data-page=\"product\"`, and no E2E "
            "keys on this page's value.")

    def test_the_identity_attribute_does_not_collide_with_the_product_root(self):
        """`product-teams` must not satisfy the product ROOT marker's grep.

        `PRODUCT_ROOT_MARKER` is the literal `data-page="product"`, and the
        deploy preflight greps for it with `grep -F`. A prefix collision would
        make the preflight pass on THIS page, so the tortoise-host suite would
        run its product assertions against a page that is not the product page.
        """
        marker = _product_root_marker()
        assert marker not in _product_teams_html(), (
            f'the niche page carries the product ROOT marker ({marker!r}), so '
            "the tortoise-host preflight and `_assert_serves_product` cannot "
            "tell the two pages apart")
        assert f'data-page="{PRODUCT_TEAMS_DATA_PAGE}"' not in PRODUCT_HTML.read_text(
            encoding="utf-8"), (
            "website/product.html carries the niche page's identity attribute")

    def test_the_canonical_names_the_url_that_serves_it(self):
        # `_served`, not the raw document: a canonical inside `<!-- … -->` is
        # one no browser parses, so the page would serve NO canonical while
        # this row stayed green — and variant URLs of a link-tagged campaign
        # page (`?utm_source=…`) would each index separately, which is the one
        # thing a canonical exists to prevent here (#13696 round 7).
        html = _served(_product_teams_html())
        canon = re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', html)
        assert canon, "the page declares no canonical"
        assert canon.group(1) == PRODUCT_TEAMS_URL, (
            f"canonical is {canon.group(1)!r}, not {PRODUCT_TEAMS_URL!r}. The "
            "page is served at the company ROOT by Pages' pretty-URL "
            "resolution — NOT under `/tortoise`, so a `/tortoise/...` canonical "
            "would name a URL the middleware 404s.")

    def test_the_sitemap_advertises_it_and_the_decision_is_indexed(self):
        """The index/noindex decision, in the three places it has to agree.

        INDEXED on purpose (an experiment nobody can find has no traffic to
        measure), so the page must NOT declare noindex and a sitemap must carry
        its URL. The reverse mutation — flipping the page to noindex and
        leaving the `<loc>` — is caught by
        `test_website_docs_consistency.py::test_no_sitemap_advertises_a_page_
        that_is_deleted_or_noindex`; this is the half that reads the DECISION
        itself rather than the sitemap.
        """
        html = _product_teams_html()
        assert not re.search(
            r'<meta\s[^>]*name\s*=\s*["\']?robots["\']?[^>]*noindex', html, re.I), (
            "the page declares noindex. That is a legitimate decision, but it "
            "is not the one recorded for #13645 (a GTM experiment needs to be "
            "findable): if it is being changed, remove the sitemap <loc> and "
            "this assertion together, and say why.")
        locs = [loc for sm in sorted((REPO_ROOT / "website").glob("sitemap-*.xml"))
                for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>",
                                      sm.read_text(encoding="utf-8"))]
        assert PRODUCT_TEAMS_URL in locs, (
            f"no sitemap advertises {PRODUCT_TEAMS_URL} — an indexable page "
            "with no <loc> is invisible to a crawler")

    def test_the_routing_decision_is_recorded_in_the_architecture_doc(self):
        """The THIRD place the decision lives — and the one nothing read.

        #13696 review finding 3: the PR body says the decision is "recorded in
        three places kept in sync … pinned together by a test". Two of the three
        were pinned (the page's own `<meta>` and the sitemap, above); the host-map
        entry in `website/website_architecture.md` was prose only, so deleting it
        reddened no row.

        That entry is the one a later reader consults BEFORE re-cutting the routing
        map, and it carries the two facts that are not visible anywhere else: this
        page is served at the ROOT and is deliberately NOT a `PRODUCT_PAGES` member
        (membership would 301 it to a 404), and it is INDEXED on purpose. With the
        entry gone, the next routing change is free to "tidy" it into the product
        prefix — the exact failure that would put the campaign URL at a 404 — so it
        is held to the same decision as the meta and the sitemap rather than trusted
        to stay in sync.
        """
        architecture = (REPO_ROOT / "website" / "website_architecture.md").read_text(
            encoding="utf-8")
        route = PRODUCT_TEAMS_URL.removeprefix("https://premiselabs.co")
        # ⛔ BIND ON THE ROUTE, NOT ON THE STRING. The route is a SUBSTRING of the
        # page's file name (`website/tortoise-for-product-teams.html`), so a
        # `route in line` test is satisfied by a bullet that names only the FILE
        # — the route can then disappear from the host map and this gate stays
        # green (measured at the previous head: rewriting the bullet's first line
        # to start with the file name left it PASSING). The doc writes routes in
        # backticks, so the bullet's own heading is the route in backticks; and
        # binding to a line that STARTS with `- ` stops an earlier mention of the
        # route elsewhere in the document from stealing the binding.
        marker = f"`{route}`"
        lines = architecture.splitlines()
        start = next((i for i, ln in enumerate(lines)
                      if ln.startswith("- ") and marker in ln), None)
        assert start is not None, (
            f"`website/website_architecture.md` has no host-map bullet headed "
            f"{marker} — the host map is where a later reader learns this page is "
            "served at the COMPANY ROOT and is deliberately not a "
            "`PRODUCT_PAGES` member. Without it the routing record makes the "
            "prefix look like the place this page belongs, and 301ing it there "
            "is a 404 on the URL the owner asked for.")
        # The recorded decision is a MULTI-LINE bullet: bind to the bullet, not to
        # the line, so re-wrapping the prose does not red this row.
        # ⛔ A BLANK LINE ENDS THE BULLET. Skipping blanks so that a re-wrap "does
        # not red" was tried and REVERTED: it lets a fact be moved OUT of the
        # bullet into an ordinary paragraph below it and still satisfy the
        # assertions below (measured green at `4886f8d097`). Ending the bullet on a
        # blank line fails closed — the cost is that an editor who splits the
        # bullet into two paragraphs of one list item gets a red, which is the
        # direction this gate should err in.
        bullet = [lines[start]]
        for ln in lines[start + 1:]:
            if ln.startswith(("- ", "#", "|")) or not ln.strip():
                break
            bullet.append(ln)
        entry = "\n".join(bullet)
        assert re.search(r"\*\*INDEXED\*\*", entry), (
            f"the architecture doc's {route} entry no longer states the page is "
            "INDEXED. `noindex` and `index` differ in whether the experiment can "
            "be measured at all, so the recorded decision must name which one is "
            "in force — the same reason the page's `<meta>` and the sitemap `<loc>` "
            "are pinned above. NOTE: this row reads the ENTRY, and a blank line "
            "ENDS the entry — if the sentence is still in the document but sits "
            "below a blank line inside the bullet, move it back up rather than "
            "adding a second copy.")
        # ⛔ POLARITY, not a substring. `"INDEXED" in entry` is satisfied by its
        # own negation — measured (#13696 round 9): `It is **NOT INDEXED** …`
        # left 246/246 green across both website suites while the page still
        # served `content="index,follow"` and the sitemap still carried the
        # `<loc>`, i.e. the three surfaces disagreed with nothing red — and THIS
        # doc is what a later lane reads before re-cutting the routing map, so
        # it would then delete the experiment's only traffic channel.
        #
        # ⛔ SENTENCE-SCOPED, because an adjacency regex is not enough: measured
        # (#13696 round 10), `It is **NOT** the case that this page is
        # **INDEXED** …` has no negation TOUCHING the token, so it passed while
        # negating the decision. Every sentence that names the decision must
        # carry the positive token and must not negate or `noindex` it.
        # NOTE: a bare `\bNO\b` here is WRONG — the baseline sentence contains
        # "no traffic to measure", which is a reason, not a negation. Only
        # `NOT` / `NEVER` / `noindex` are refused.
        # ⛔ CASE-INSENSITIVE membership, because a contradicting sentence that
        # spells the word in lower case escaped the guard entirely — measured
        # (#13696 round 11): baseline + `It is NOT the case that this page is
        # indexed.` left 247 passed, i.e. the doc could state the decision and
        # then deny it with nothing red. `upper()` puts that sentence in scope
        # for the positive-token assert below, which it then fails.
        naming = [
            s for s in re.split(r"(?<=[.!?])\s+", " ".join(entry.split()))
            if "INDEXED" in s.upper()
        ]
        assert naming and all(re.search(r"\*\*INDEXED\*\*", s) for s in naming), (
            f"the architecture doc's {route} entry names the INDEXED decision in a "
            "sentence that does not carry the positive `**INDEXED**` token — so "
            "the sentence can say `NOT … INDEXED`, or `noindex`, while this row "
            "still passes. The page serves `content=\"index,follow\"` and the "
            "sitemap carries its `<loc>`; the entry has to state the SAME decision "
            "(#13645).")
        assert not re.search(
            r"\b(?:NOT|NEVER)\b|noindex|\b(?:NOT|NO|NON|UN)[- ]?INDEXED\b",
            " ".join(naming), re.I,
        ), (
            f"the architecture doc's {route} entry NEGATES the decision while "
            "naming it — the entry must say INDEXED, not `NOT INDEXED`: the page "
            "serves `<meta name=\"robots\" content=\"index,follow\">` and the "
            "sitemap carries its `<loc>`, so a negation here is the doc "
            "contradicting both other surfaces with no row red (#13645).")
        assert "PRODUCT_PAGES" in entry, (
            f"the architecture doc's {route} entry no longer says why this page is "
            "NOT a `PRODUCT_PAGES` member. That membership is a 301 to "
            "`/tortoise/tortoise-for-product-teams`, which does not exist; the "
            "reason has to live beside the route or the next reader removes it. "
            "(Same caveat as the row above: a blank line ends the entry, so a "
            "sentence moved below one is not read here.)")

    def test_the_page_loads_the_consent_module(self):
        """Every named event must be gated, so the gate must be loaded."""
        srcs = _pricing_source_includes(_product_teams_html())
        assert "/consent.js" in srcs, (
            "the page does not load /consent.js, so its three funnel events "
            "have no consent gate: a pre-consent beacon is both a privacy "
            "defect and an undercount (blocked requests read as nobody clicked)")

    def test_the_three_funnel_events_are_named_in_the_source(self):
        # `_code_only`, not `_without_html_comments`: that helper strips HTML
        # comments only, so a JS comment quoting the three event names left this
        # row green over a page whose `EVENTS` table was commented out — every
        # CTA click and the consent event then threw `ReferenceError` and the
        # experiment raised nothing, with no red anywhere (#13696 round 5, P1).
        html = _live_markup(_product_teams_html())
        for event in PRODUCT_TEAMS_EVENTS:
            assert f"'{event}'" in html, (
                f"the page never names the `{event}` event. The owner decision "
                "on #13645 requires the funnel to be visible in the page's OWN "
                "source — page view with the campaign parameters, primary-CTA "
                "click, secondary-CTA click — so a later reader does not have "
                "to open a dashboard config to learn what is measured.")
        assert "tortoise:consent" in html, (
            "the page does not wait for /consent.js's decision event, so a "
            "visitor who accepts the banner AFTER the page ran would have their "
            "page view dropped — clicks with no denominator")
        assert "consentState" in html, (
            "the page never reads the consent state itself; every capture must "
            "be gated on it, not merely deferred")

    def test_the_campaign_tags_point_at_the_canonical_url(self):
        # `_served`, not the raw document, and that is load-bearing: the page's
        # own inline JS states the convention in a COMMENT
        # (`utm_campaign=tortoise-for-product-teams + utm_source + utm_medium`),
        # so a raw substring read is satisfied by that prose alone
        # (#13696 round 7).
        #
        # ⛔ AND IT IS READ PER LINK, not once anywhere: a single `in html`
        # still passes when THREE OF THE FOUR tagged links lose their tag
        # (measured, #13696 round 8) — one entry path's attribution disappears
        # silently, which is the owner's stated reason for tagging at all
        # ("without that the experiment has no denominator").
        html = _served(_product_teams_html())
        tagged, untagged = [], []
        for tag in re.findall(r"<a\b[^>]*>", html, re.I):
            if "data-cta=" not in tag:
                continue
            m = re.search(r'href="([^"]+)"', tag)
            if not m:
                untagged.append(f"{tag} (no href)")
                continue
            href = m.group(1)
            if href in self.OFF_SITE_CTAS:
                # Exempt BY NAME, not by "anything off-site is exempt": that
                # weaker rule also excuses a campaign CTA retargeted off our
                # funnel, which is the defect one step further along.
                continue
            if all(p in href for p in self.CAMPAIGN_PARAMS):
                tagged.append(href)
            else:
                untagged.append(href)
        assert not untagged, (
            "these CTAs do not carry the full campaign tag set "
            f"({', '.join(self.CAMPAIGN_PARAMS)}) in their OWN href: {untagged!r}. "
            "Each is an entry path whose traffic then cannot be separated from "
            "/tortoise's in the same PostHog project — the experiment would "
            "count that path with no denominator (#13645).")
        assert tagged, (
            "no `data-cta` link carries the campaign tags — this row's subject "
            "moved (the hero CTAs are found by `data-cta`, so zero of them "
            "means the markup changed and this gate now asserts nothing)")

    #: The page's ONE off-site CTA: the repo link. GitHub takes no campaign
    #: tags, so it is exempt by name — see the row above for why ``any off-site
    #: link`` is NOT the rule.
    OFF_SITE_CTAS = ("https://github.com/daniel-ospina/tortoise",)

    #: The three parameters the owner's decision requires on every link we
    #: control; each one answers a different question in the dashboard.
    CAMPAIGN_PARAMS = (
        "utm_campaign=tortoise-for-product-teams",
        "utm_source=tortoise-for-product-teams",
        "utm_medium=",
    )

    def test_the_page_links_to_the_canonical_product_page(self):
        """The owner's rule: this page does not re-tell /tortoise's story."""
        # ⛔ `_served`, not `_without_html_comments`: the latter deliberately
        # KEEPS `<script>` bodies, so an `href="/tortoise?utm_…"` string inside
        # an inline-JS COMMENT satisfied this row on its own (#13696 round 9:
        # deleting the real footer CTA and leaving that string green-lit all
        # 167 rows, with the funnel's only tagged entry to the canonical page
        # gone). The sibling campaign-tag row already reads `_served`; the two
        # halves of this invariant must agree on what "reads a link" means.
        html = _served(_product_teams_html())
        raw_hrefs = re.findall(r'href="([^"]+)"', html)
        hrefs = [h.split("?", 1)[0] for h in raw_hrefs]
        assert "/tortoise" in hrefs, (
            "the niche page never links to /tortoise, whose full product story "
            "it does not duplicate. The owner's decision requires the canonical "
            "page to stay reachable from here (footer → Product).")
        # …and the ONE that IS the campaign entry point into the canonical page
        # must carry the tags. The other `/tortoise/<sub>` links are internal
        # navigation and are deliberately untagged, so this binds the route, not
        # a substring: retargeting the footer link, or dropping its tags, reds.
        home = [h for h in raw_hrefs if h.split("?", 1)[0] == "/tortoise"]
        assert home, (
            "no footer link to the clean `/tortoise` route — only sub-page "
            "navigation remains, so the canonical product page is one hop "
            "further than the owner's decision allows")
        assert all(
            "utm_campaign=tortoise-for-product-teams" in h for h in home
        ), ("the footer's link to `/tortoise` carries no utm_campaign, so the "
            "traffic this page sends to the canonical page is counted as if it "
            "had arrived from anywhere — the experiment's two arms stop being "
            "separable (#13645)")

    def test_the_three_step_story_replaced_the_fourth_beat(self):
        """`#beat-automate` is ABSENT here and PRESENT on `/tortoise`.

        Both halves are asserted, because this is the question
        `TestAutomateBeatCopy` was retired-or-not on: the PDF has THREE steps,
        and this page is a NEW file alongside `/tortoise` rather than a rewrite
        of it. So the webhook beat (and its owner-ruled copy) is untouched on
        the canonical page and simply not part of this one. ⛔ That copy is an
        owner ruling (#8466 OVERRIDES): do not "fix" it here or there.
        """
        teams = _product_teams_html()
        assert 'id="beat-automate"' not in teams, (
            "the niche page carries the `04 · Automate` beat, but the owner's "
            "PDF has three steps — so either the copy was invented or a beat "
            "was copied from /tortoise")
        assert 'id="beat-automate"' in PRODUCT_HTML.read_text(encoding="utf-8"), (
            "website/product.html lost its `#beat-automate` beat. That beat "
            "carries the owner's webhook paragraph (#8466 OVERRIDES) and is "
            "pinned by TestAutomateBeatCopy — this page's three steps do not "
            "retire it, because /tortoise is not being rewritten")


class TestProductTeamsCopy:
    """The owner's copy, section by section — with the departures recorded as departures.

    This is the pin the page most needs: the copy is the owner's prose, the
    build was instructed to reproduce it unchanged, and nothing else in this
    repo reads it (the product page's guard deliberately stopped pinning copy
    in #9652).

    ⛔ THE COPY MAY NOT BE "CORRECTED" WITHOUT THE RULING ON THE ARTIFACT — and
    the owner HAS now ruled, so the two readings ship corrected and these pins
    assert the ruled forms. The ruling is recorded here, not asserted:
    https://github.com/daniel-ospina/tortoise/issues/13645#issuecomment-7047852104
    It names the two readings as typos and states that, for those two strings
    only, it supersedes the earlier "must survive the build unchanged"
    instruction. The onboarding heading's trailing full stop is a THIRD
    departure (#13696 item 4), recorded as one rather than folded into the copy
    ruling.

    ⛔ AND THE OWNER RE-WROTE PART OF THE PAGE ON 2026-11-23 (#13645 items 2-5):
    the compute beat gained the title "Organisational Intelligence" and reads
    "over YOUR context", and step 03 is retitled "03 · Reason & Learn" with its
    body REPLACED — the PDF's step-03 paragraph no longer appears on the page at
    all. Those are the owner's EDITS, not a builder's paraphrase, and the pins
    below assert the NEW text: restoring the PDF's step-03 paragraph is now a
    revert of an owner edit, exactly as re-spelling `superintelligence` was a
    revert of a ruling. The ruling on the two typos stands on #13645 for anyone
    who does restore that paragraph. The edits are relayed onto the artifact at
    https://github.com/daniel-ospina/tortoise/issues/13645#issuecomment-7108810457

    ⛔ THE LABELS BELOW ARE THAT COMMENT'S OWN NUMBERS, qualified by issue —
    `#13645 item 2` is the title, `#13645 item 3` is "your context", `#13645
    items 4-5` are step 03's heading and body, and `#13645 item 6` is the
    pricing-CTA baseline. They used to read `owner item 1` / `owner item 2`,
    which collided with THIS repo's other owner item set: `#13696 item 3` is
    the outcomes/lead measure overhang (`test_the_outcomes_grid_is_never_wider_
    than_the_prose_above_it`, `test_the_lead_resolves_the_shared_measure_in_the_
    prose_font`), and `#13696 item 2` is the deleted hairline
    (`test_the_intro_opens_on_the_lead_and_carries_no_rule`). A bare `grep
    "owner item 3"` returned both fixes as one undifferentiated set, so an
    artifact that records four different owner items under two labels could not
    be resolved back to the decision that owns it. Named rows, not line
    numbers: a citation into this file rots on the next edit above it.

    ⛔ AND THE TYPOS' ABSENCE IS PINNED SEPARATELY
    (`test_the_pdfs_two_typos_are_absent_from_the_rendered_page`). Without that
    row, reverting the page's copy AND these constants together would stay
    green — the two move as a pair, so a pair-revert is exactly the shape a
    stale instruction would produce. An earlier revision of this branch did
    revert the pair, correctly, while the ruling was not on the artifact;
    `ba53339f58` set the condition, and this docstring is where a future lane
    reads it instead of re-deriving it.
    """

    #: page 1 — hero. The h1's second half is wrapped in a <span> for the accent
    #: colour, so the pin is the rendered text either way.
    H1 = "A Superbrain for Founders & Product Teams"
    HERO_SUB = "A million moving pieces. One coherent & logical picture."

    #: page 1 — the intro block. The first line is a `.lead` since #13696 (it
    #: reads as the section's title) and the PDF's hairline rule above it was
    #: removed on the owner's instruction — neither changes the text.
    INTRO = (
        "Free up time from repetitive explaining, supervision, and managing dependencies.\n"
        "When agents (and people) understand goal cascades, root causes, trade-offs, "
        "and dependencies, they autonomously and reliably advance your business.\n"
        "Tortoise builds a logical model of your business, product, and code. It "
        "connects goals, insights and analytics, decisions, and execution so you, "
        "your team, and agents have the whole context, current and logical."
    )

    #: page 2 — the compute claim + the six ↑/↓ labels, in the PDF's ROW order.
    #: ⛔ `decisions` (PLURAL) is the owner's RULING, not a builder's tidy-up:
    #: #13645#issuecomment-7047852104 records it, and the PDF's singular is the
    #: typo it corrects (#14020 review P1 at `73dbbf66ea`). `your context` (not
    #: the PDF's `this context`) is the owner's separate edit of 2026-11-23,
    #: `#13645 item 3`.
    COMPUTE = (
        "Tortoise then computes over your context, analysing hundreds or even "
        "millions of variables so you and your agents make decisions with "
        "superintelligence."
    )
    #: The compute beat's section TITLE — the owner's addition of 2026-11-23
    #: (`#13645 item 2`), not the PDF's: the PDF opens that beat with the claim.
    COMPUTE_H2 = "Organisational Intelligence"
    OUTCOMES_UP = ("Agent Autonomy & Token efficiency", "Decision quality", "Alignment")
    OUTCOMES_DOWN = ("Constant Supervision", "Drift & Regressions", "Slack Noise & Meetings")

    #: pages 2-3 — the three steps, then onboarding.
    STEPS = (
        ("beat-ingest", "01 · Ingest Any Source",
         "Automatically ingest agent sessions from any harness, GitHub issues, "
         "documents, chat messages, systems of record, BI, or any other data you "
         "want to connect."),
        ("beat-organize", "02 · Organize Your Knowledge",
         "Our ontology engine builds a logical model of how your business, product, "
         "and code work, turning loose facts into knowledge. With deduplication and "
         "superseding. This logical model enables your agents to not just retrieve "
         "facts but understand your logic, eliminating drift and regressions."),
        ("beat-reason", "03 · Reason & Learn",
         "Thanks to an algorithm inspired by interconnected prediction markets, "
         "Tortoise can compute hundreds or even millions of data points, "
         "calculating confidence with business logic and learning from new "
         "evidence."),
    )
    #: Where each step's pinned copy actually comes from. Steps 01-02 are the
    #: PDF reproduced verbatim; step 03's heading AND body are the owner's
    #: 2026-11-23 rewrites (`#13645 items 4-5`), so naming the PDF as the source
    #: for that beat was false on both branches of the failure message — and the
    #: PDF's paragraph is pinned ABSENT below, so "restore the PDF's copy" is a
    #: revert of an owner edit, not a return to verbatim. (`fdd3a6a60f` reverted
    #: it once already, which is why the message has to name the right source.)
    STEP_SOURCES: ClassVar[dict[str, str]] = {
        "beat-ingest": "the PDF",
        "beat-organize": "the PDF",
        "beat-reason": (
            "the owner's 2026-11-23 rewrite of step 03 (`#13645 items 4-5`)"),
    }
    ONBOARD_H2 = "Onboard in 5 minutes, without learning another UI"
    ONBOARD_P = (
        "Tortoise connects via MCP or SDK, so you and your agents can use it in "
        "Codex, Claude Code & Web, ChatGPT, Cursor, Pi, or any other harness."
    )

    def test_the_hero_carries_the_owners_headline_and_subtitle(self):
        hero = _element_by_id(_product_teams_html(), "beat-hero")
        h1 = re.search(r"<h1>(.*?)</h1>", hero, re.S)
        assert h1, "#beat-hero has no <h1>"
        assert _beat_text(h1.group(1)) == self.H1, (
            f"the hero h1 reads {_beat_text(h1.group(1))!r}; the owner's page 1 "
            f"reads {self.H1!r}. The copy is the owner's prose — reproduce it, "
            "do not rewrite it.")
        assert self.HERO_SUB in " ".join(_beat_text(p) for p in
                                        re.findall(r"<p[^>]*>.*?</p>", hero, re.S)), (
            f"the hero subtitle is not {self.HERO_SUB!r}")
        assert "Connect your agent →" in _beat_text(hero), (
            "the hero lost the owner's primary CTA label (`Connect your agent →`, "
            "bracketed in the PDF = a button)")

    def test_the_intro_block_carries_the_three_paragraphs(self):
        intro = _element_by_id(_product_teams_html(), "beat-intro")
        paragraphs = [_beat_text(p) for p in re.findall(r"<p[^>]*>.*?</p>", intro, re.S)]
        assert "\n".join(paragraphs) == self.INTRO, (
            f"the intro block reads {paragraphs!r}; the owner's page 1 reads "
            f"{self.INTRO!r}")

    def test_the_intro_opens_on_the_lead_and_carries_no_rule(self):
        """#13696 item 2: the hairline rule is GONE, the first line IS the lead.

        Both halves read the SERVED markup. Without this row the owner's item 2
        could be reverted silently and the whole suite stay green — measured:
        re-inserting `<div class="rule" aria-hidden="true"></div>` and dropping
        `class="lead"` reds nothing else here, because
        `test_the_intro_block_carries_the_three_paragraphs` counts <p>s (a div
        is invisible to it) and the lead is still a <p>.
        """
        intro = _element_by_id(_product_teams_html(), "beat-intro")
        assert 'class="rule"' not in intro, (
            "the intro still renders a `class=\"rule\"` element — the owner "
            "asked for the hairline divider to be DELETED (#13696 item 2), and "
            "it also outdented 32.8px past the text at 390px wide")
        first = re.search(r"<p[^>]*>", intro)
        assert first, "#beat-intro has no paragraph"
        assert 'class="lead"' in first.group(0), (
            f"the intro's first paragraph reads {first.group(0)!r}; it must "
            "carry `class=\"lead\"` so the opening line reads as the section's "
            "title rather than as body copy (#13696 item 2)")

    def test_the_lead_resolves_the_shared_measure_in_the_prose_font(self):
        """The lead's first line may not be WIDER than the prose under it.

        #14020 review P1, measured: `--prose-measure: min(660px, 74ch)` was
        declared on `#beat-intro` and consumed by `.lead`. A custom property is
        substituted as a TOKEN STREAM, so its `ch` resolves at the CONSUMER —
        `.lead`, whose `font-size` was the serif clamp (1.3–1.9rem) — where 74ch
        is ~710–780px, so `min()` picked the flat 660px while the paragraphs
        below computed 605.625px (13.6px mono). 54px wider, ~27px left of the
        prose, every box flex-centred: #13696 item 3's defect one element up.
        Headless Chrome at 768x900: `.lead` 660px @ 20.8px vs `p` 605.625px.

        Three things keep it resolved in the PROSE font, and each is asserted
        separately so a revert names itself: the `<p class="lead">` declares no
        font-size (the serif lives on its inner `<span>`), it declares no
        max-width (it INHERITS the shared measure rather than restating it), and
        no custom property carries a `ch` measure — that is the trap itself.
        """
        css = _product_teams_html()

        lead_rules = _css_rules_with_scope(css, "#beat-intro .lead")
        assert lead_rules, "`#beat-intro .lead` declares no rule"
        for body, inside_media in lead_rules:
            where = "inside an @media block" if inside_media else "at top level"
            assert "font-size" not in body, (
                "`#beat-intro .lead` declares a `font-size` "
                f"({where}): {body.strip()!r}. `max-width: min(660px, 74ch)` on "
                "this <p> resolves `74ch` against THAT size — the serif clamp "
                "makes it ~710–780px, so `min()` picks the flat 660px while the "
                "prose below is 605.6px: the intro's first line 54px wider and "
                "~27px left of it (#14020 review P1). Keep the serif on the "
                "inner <span>.")
            assert "max-width" not in body, (
                "`#beat-intro .lead` declares its own `max-width` "
                f"({where}): {body.strip()!r}. The lead must INHERIT the shared "
                "`min(660px, 74ch)` from `#beat-intro p` — a second declaration "
                "is a second measure, and a `var(--…)` one resolves its `ch` "
                "at this <p> anyway (#14020 review P1)")
            # `ch` is the advance of `0` in the CONSUMING element's font, so the
            # FONT FAMILY matters as much as the size: guarding `font-size`
            # alone left this row blind. Measured (#14020 review P1): adding
            # `#beat-intro .lead { font-family: Georgia, serif }` made the lead
            # 502.97px against the prose's 605.63px at 768x900 — 51.3px the
            # OTHER way, the alignment defect restored — with this row green.
            assert not re.search(r"(?:^|;)\s*(?:font-family|font)\s*:", body), (
                "`#beat-intro .lead` switches its own font "
                f"({where}): {body.strip()!r}. `max-width: min(660px, 74ch)` is "
                "resolved in THIS element's font — `ch` is the advance of `0` — "
                "so a serif or mono switch moves the lead's measure away from "
                "the prose's even at the same size (measured with Georgia: "
                "502.97px vs 605.63px at 768x900, #14020 review P1). The serif "
                "belongs on the inner <span>.")

        span_rules = [body for body, _inside_media in
                      _css_rules_with_scope(css, "#beat-intro .lead > span")]
        assert span_rules, (
            "`#beat-intro .lead > span` declares no rule — the lead's serif has "
            "to live on an inner element, or it lands back on the element whose "
            "font-size resolves `74ch` (#14020 review P1)")
        # …read through the COMMENT-BLANKED reader (`_css_rules_with_scope`):
        # a raw-sheet reader would let a declaration wrapped in `/* … */` inside
        # its own rule — the shape an editor leaves while debugging — keep this
        # row GREEN while the lead loses its serif (#14020 review P2).
        assert any("font-family: var(--serif)" in b for b in span_rules), (
            "the lead's inner <span> does not take the serif; #13696 item 2 has "
            "the opening line read as the section's title, not as body copy")
        # …and the <span> has to EXIST. The serif moved onto it, so deleting it
        # from the markup drops the lead back to body copy with every CSS row
        # above still green: `_beat_text` strips tags, so the copy pins cannot
        # see it, and the rule for `> span` simply never applies (#14020 review
        # P1, measured: span removed, nothing reds).
        lead_markup = re.search(
            r'<p[^>]*\bclass="lead"[^>]*>(.*?)</p>',
            _element_by_id(_product_teams_html(), "beat-intro"), re.S)
        assert lead_markup, 'the page carries no `<p class="lead">`'
        assert re.match(r"\s*<span[\s>]", lead_markup.group(1)), (
            f"the lead's markup reads {lead_markup.group(0)[:160]!r}; the serif "
            'is declared on `#beat-intro .lead > span`, so the markup must stay '
            '<p class="lead"><span>…</span></p> — without the inner <span> the '
            "serif silently disappears and no CSS row above reds (#14020 "
            "review P1)")

        # ...and nothing may move the lead's font through a selector that does
        # not NAME it. `.lead` is a <p> inside `#beat-intro`, so any
        # `#beat-intro`-rooted rule without a child combinator can reach it:
        # `#beat-intro p:first-of-type { font-size: 2rem }` resolved the lead's
        # `74ch` past the flat 660px while EVERY row in this class stayed green
        # (#14020 review cycle 3, P1 — measured, not reasoned).
        reachable = [
            (prelude, _effective_decl(body, "font-size"))
            for prelude, body, inside_media in _css_all_rules(css)
            if not inside_media
            and re.search(r"(?:^|;)\s*(?:font-size|font-family|font)\s*:", body)
            for part in (p.strip() for p in prelude.split(","))
            if part.startswith("#beat-intro") and ">" not in part
        ]
        assert reachable == [], (
            f"rules set the lead's font without naming `.lead`: {reachable!r}. "
            "`.lead` is a <p> inside `#beat-intro`, so these reach it — and "
            "`max-width: min(660px, 74ch)` resolves in ITS font, so changing "
            "that size moves the lead's measure to ~the flat 660px while every "
            "row above stays green (#14020 review cycle 3 P1). The serif and "
            "any size live on `#beat-intro .lead > span`.")

        # ⛔ `\bch\b` can NEVER match a CSS `ch` unit: the unit always follows a
        # digit, and digit -> letter is not a word boundary, so the pin this row
        # advertised was dead (#14020 review cycle 5, measured GREEN with
        # `--prose-measure: min(660px, 74ch)` back in the sheet). Match the unit
        # as it is written: a number, then `ch`.
        ch_property = re.search(
            r"--[\w-]+\s*:[^;{}]*?\d+(?:\.\d+)?ch\b", _css_blanked(css))
        assert ch_property is None, (
            f"a custom property carries a `ch` measure: {ch_property.group(0)!r}. "
            "`ch` in a custom property resolves against whatever element "
            "CONSUMES the var(), not the one that declares it, so this reads as "
            "a shared measure and is not one — measured #14020 review P1: "
            "declared on `#beat-intro`, picked up as the flat 660px at `.lead`. "
            "Declare the measure on the rule that applies it.")

    def test_the_outcomes_grid_is_never_wider_than_the_prose_above_it(self):
        """#13696 item 3: ONE measure, and no flat override in the narrow block.

        The owner's item 3 was a grid WIDER than the prose above it: on
        `origin/main` (`5ff1466ddc`) `.outcomes` carried a FLAT `max-width:
        760px` (:349) while the prose carried a FLAT `max-width: 660px`
        (`#beat-intro p { max-width: 660px }`, :336) — no `ch` term existed
        anywhere, so from 660px up the grid box stood 54.1px wider than the text
        above it (27.1px out on each side).

        The shared `min(660px, 74ch)` closes that, and the shape it regressed
        into ONCE — inside this branch's own first fix commit (`e11914c020d`) —
        was a FLAT `.outcomes { max-width: 660px }` inside `@media (max-width:
        760px)`, which restored the same 54.1px overhang at 760. ⛔ Those are two
        different states and the attribution matters: the owner reported the
        `origin/main` one; the media-block one was ours. So it is pinned twice:
        the shared measure must exist, and no max-width block may re-declare
        one.
        """
        css = _product_teams_html()
        shared = _css_declarations(
            css, "#beat-intro p, #beat-compute > p, .outcomes")
        assert shared is not None, (
            "no shared-measure rule for `#beat-intro p, #beat-compute > p, "
            ".outcomes` — the prose and the ↑/↓ grid must stand on one measure")
        # The WINNING declaration, not the presence of the string: `min(660px,
        # 74ch)` followed by `max-width: 660px` in the same block leaves a
        # substring search green while the effective measure is the flat value
        # the message forbids (#14020 review cycle 3, P2 — measured).
        assert _effective_decl(shared, "max-width") == "min(660px, 74ch)", (
            f"the shared measure's winning `max-width` is "
            f"{_effective_decl(shared, 'max-width')!r} (block: {shared!r}); it "
            "must BE `min(660px, 74ch)` — a flat 660px keeps 80 mono characters "
            "per line at the 13.6px font floor (the band is 45–75ch), and a "
            "later declaration in the block wins whatever the earlier one says")
        for width, body in _max_width_media_blocks(css):
            for m in re.finditer(r"\.outcomes\s*\{([^}]*)\}", body):
                assert "max-width" not in m.group(1), (
                    f"`@media (max-width: {width}px)` re-declares a FLAT "
                    f"`max-width` for `.outcomes` ({m.group(1).strip()!r}). "
                    "That overrides the shared `min(660px, 74ch)` for every "
                    "width in the band and makes the grid wider than the prose "
                    "it sits under — #13696 item 3. Delete the override "
                    "and let the grid inherit the shared measure.")
        # ⛔ THE MEDIA-ONLY SWEEP ABOVE IS NOT THE INVARIANT, and reading only
        # `@media (max-width: Npx)` bodies left the BASE block unread: appending
        # `.outcomes { max-width: 660px; }` at top level restored item 3's
        # 54.1px overhang at 760px with every assertion above GREEN (#14020
        # review P2.3, measured). The invariant is the EFFECTIVE measure, so
        # read every rule whose selector LIST contains `.outcomes` — base and
        # media alike — and allow exactly one declaration: the shared measure.
        with_measure = [
            (prelude, body) for prelude, body in _css_rule_preludes(css, ".outcomes")
            if "max-width" in body]
        assert len(with_measure) == 1, (
            "`.outcomes` gets its measure from "
            f"{len(with_measure)} rules: "
            f"{[p for p, _ in with_measure]!r}. Exactly ONE may declare it — a "
            "second override (base OR media) is #13696 item 3 again: the grid "
            "is wider than the prose above it whenever that rule applies "
            "(#13696, #14020 review P2.3)")
        prelude, body = with_measure[0]
        assert _effective_decl(body, "max-width") == "min(660px, 74ch)", (
            f"the `.outcomes` measure is declared on `{prelude}` as "
            f"{body.strip()!r}; the grid's ONLY max-width must be the shared "
            "`min(660px, 74ch)` — a flat value is the 54.1px overhang at 760px "
            "(#13696)")
        # ⛔ The DECLARATION being right is not the MEASURE being right: `74ch`
        # is only the same length for the grid and the prose if both resolve `ch`
        # against the same font-size (`ch` = advance of `0` in the CONSUMING
        # element's font). `.outcomes` used to inherit the body's 14px while
        # `.beat p` carries the clamp below, so the shared term computed 623.7px
        # for the grid against 605.9px for the prose at 768 — item 3 again, 18px
        # the other way. Measured (#14020 review P2.4): `font-size: 14px` on
        # `.outcomes` reds no other row in this file.
        prose_rules = [
            (b, inside) for b, inside in _css_rules_with_scope(css, ".beat p")
            if not inside and "font-size" in b]
        assert prose_rules, "`.beat p` declares no base `font-size` to mirror"
        want = _effective_decl(prose_rules[-1][0], "font-size")
        # ⛔ Base AND media: `ch` resolves at the consuming element, so a
        # `font-size` on `.outcomes` inside `@media (max-width: 760px)` moves the
        # grid's `74ch` off the prose's at exactly the widths item 3 is about —
        # and the media block is the natural place to edit, because it already
        # owns `.outcomes`. Measured (#14020 review cycle 5): `font-size: 14px`
        # added there left this row GREEN (623.7px vs 605.9px at the clamp
        # floor), which is why the filter is no longer base-only.
        grid_rules = [
            (b, inside) for b, inside in _css_rules_with_scope(css, ".outcomes")
            if "font-size" in b]
        assert grid_rules, (
            "`.outcomes` declares no base `font-size`, so it inherits the body's "
            "14px: its `74ch` is then NOT the prose's `74ch` and the grid measures "
            "623.7px against the prose's 605.9px at 768 — #13696 item 3, 18px the "
            "other way (#14020 review P2.4). Mirror `.beat p`'s clamp.")
        for body_, inside_ in grid_rules:
            got = _effective_decl(body_, "font-size")
            assert got == want, (
                f"`.outcomes` resolves `ch` at `{got}` "
                f"({'inside an @media block' if inside_ else 'at base'}) while "
                f"`.beat p` resolves it at `{want}` — so the shared "
                "`min(660px, 74ch)` is two different measures at those widths "
                "and the grid can sit wider than the prose above it (#14020 "
                "review P2.4, cycle 5, #13696 item 3)")

    def test_the_hero_copy_column_shares_one_left_edge(self):
        """#13696 item 1: `.beat p` centring may not leak into the hero.

        `.beat p` sets `text-align: center` and caps at 560px, and `.hero-sub` /
        `.hero-links` ARE <p> elements — so without these overrides the h1/CTA
        sat on the column's left edge while the link row started 71px right of
        it (measured at 1440x900: h1/CTA x=220, links x=291), in three axes.
        """
        css = _product_teams_html()
        # The last BASE rule, not the first and not the media blocks:
        # `@media (max-width: 760px)` deliberately re-centres the stacked hero
        # (`.doc-copy`/`.hero-sub` -> centre), which is correct on one column.
        # What must never regress is the EFFECTIVE DESKTOP alignment — so
        # `copy_rules[0]` (the first rule) is the wrong reader: appending a
        # later `text-align: center` at top level leaves it green while the
        # cascade is centred, which IS the 71px-off-axis defect (#14020 review
        # P2.4, measured).
        for selector, why in (
            ("#beat-hero .doc-copy",
             "it must declare `text-align: left`, or `.beat p`'s centring leaks "
             "into `.hero-sub` and `.hero-links` — the 71px-off-axis defect of "
             "#13696 item 1"),
            ("#beat-hero .hero-sub",
             "it is a <p>, so it inherits `.beat p`'s centred alignment "
             "(#13696 item 1)"),
            ("#beat-hero .hero-links",
             "it is a <p>, so it inherits `.beat p`'s centred alignment "
             "(#13696 item 1)"),
        ):
            scoped = _css_rules_with_scope(css, selector)
            assert scoped, f"{selector} declares no rule"
            base = [body for body, inside_media in scoped if not inside_media]
            assert base, (
                f"{selector} declares no BASE rule — every one of its "
                f"{len(scoped)} rule(s) sits inside an `@media` block")
            assert _effective_decl(base[-1], "text-align") == "left", (
                f"the last base `{selector}` rule's winning `text-align` is "
                f"{_effective_decl(base[-1], 'text-align')!r} (block: "
                f"{base[-1].strip()!r}); {why}. A later top-level rule wins the "
                "cascade even when an earlier one says `left`, and a later "
                "DECLARATION in this same block wins too — read the value that "
                "wins, not whether the string appears (#14020 review P2.4, "
                "cycle 3 P1)")
        # Comment-blanked, like the alignment rows above: a raw-sheet reader
        # would let `#beat-hero .hero-links { /* display: flex; */ }` — a
        # commented-out declaration inside its own rule — satisfy this row
        # while the row stops being a row (#14020 review P2).
        # ⛔ BASE rule only, like the separator row below and for the same
        # reason: joining every body lets a narrow-viewport `display: flex`
        # mask a base rule that is no longer a row — the desktop links stop
        # being a row and the `·` separators can lead a wrapped line, with this
        # row green (#14020 review cycle 5, measured).
        link_rules = [body for body, inside_ in
                      _css_rules_with_scope(css, "#beat-hero .hero-links")
                      if not inside_]
        assert link_rules, (
            "`#beat-hero .hero-links` declares no BASE rule — every one of its "
            "rule(s) sits inside an `@media` block")
        links = link_rules[-1]
        assert _effective_decl(links, "display") in {"flex", "inline-flex"}, (
            "`.hero-links` is not a flex row on the copy column's left edge — "
            "the `·` separators are `::after` on non-last links, a layout that "
            "only holds as a row (#13696 item 1)")
        # The separator must keep BOTH declarations, in this order: the plain one
        # is the fallback for engines that drop alt-text syntax at parse time;
        # the alt-text one removes `·` from the accessible name. Reverting to the
        # plain form alone silently re-leaks `·` into all three names, and
        # dropping the fallback makes the dot VANISH on Chrome<77 / Firefox<128 /
        # Safari 16–17.3 (measured, #14020 review).
        # ⛔ BASE rule only, read through the SCOPE-AWARE, comment-blanked
        # reader. Joining every body for the selector is scope-blind: moving both
        # declarations into the narrow-viewport block (where a `content: none`
        # also lives) satisfied the assertions while Chromium rendered NO dot on
        # desktop and one on mobile — the exact inversion of the intent
        # (#14020 review).
        sep_scoped = _css_rules_with_scope(
            css, "#beat-hero .hero-links a:not(:last-child)::after")
        sep_base = [body for body, inside_media in sep_scoped if not inside_media]
        assert sep_base, "the hero `::after` separator rule has no base rule"
        sep_all = "".join(sep_base)
        assert 'content: "\\00b7" / ""' in sep_all, (
            "the separator lost its CSS ALT TEXT (`content: \"\\00b7\" / \"\"`), "
            "so `·` is back in every link's accessible name")
        assert 'content: "\\00b7";' in sep_all, (
            "the separator has no single-value fallback declaration. Without it "
            "an engine that cannot parse alt-text syntax drops the declaration "
            "entirely and the `·` disappears (Chrome<77, Firefox<128, Safari "
            "16–17.3) — the fallback is not redundant")
        assert sep_all.index('content: "\\00b7";') < sep_all.index('content: "\\00b7" / ""'), (
            "the fallback declaration must come BEFORE the alt-text one, or it "
            "overrides it and `·` returns to the accessible name")

    def test_the_compute_claim_carries_the_owner_ruled_correction(self):
        """The PDF's singular "make decision" ships as "make decisions" — RULED.

        The PDF reads "so you and your agents make decision with
        superintelligence." The singular is a typo a copy editor would flag, and
        the owner ruled it so. ⛔ THE RULING IS ON THE ARTIFACT, which is what
        makes this pin legitimate rather than a builder's preference:
        https://github.com/daniel-ospina/tortoise/issues/13645#issuecomment-7047852104
        An earlier revision of this branch reverted this correction — correctly
        — because the ruling was only in a conversation, and `ba53339f58`
        recorded exactly that condition: "the copy stays verbatim until the
        ruling is ON the artifact". The ruling is now on the artifact, so copy
        and pin move together, as that commit required.

        Flipping this pin back to the PDF's spelling is therefore not
        "honouring verbatim" — it re-lands a typo the owner ruled on.
        """
        compute = _element_by_id(_product_teams_html(), "beat-compute")
        first = re.search(r"<p[^>]*>(.*?)</p>", compute, re.S)
        assert first, "#beat-compute has no paragraph"
        assert _beat_text(first.group(1)) == self.COMPUTE, (
            f"the compute claim reads {_beat_text(first.group(1))!r}; the "
            f"owner's page 2, as ruled, reads {self.COMPUTE!r}. ⛔ `make "
            "decisions` (plural) is the owner's ruling, recorded on "
            "#13645#issuecomment-7047852104 — restoring the PDF's singular "
            "re-lands a typo the owner ruled on; `your context` is the owner's "
            "edit of 2026-11-23 (#13645 item 3).")

    def test_the_compute_beat_carries_the_owners_section_title(self):
        """#13645 item 2: the beat opens on a TITLE, not on the claim.

        The PDF's compute beat has no heading — it opens with the paragraph — so
        the owner asked for one, and it is an ADDITION to the PDF's text rather
        than a correction of it. Nothing else here reads it: the copy pin above
        takes the FIRST `<p>`, which an `<h2>` never displaces, and the heading
        outline only counts levels. Without this row the title could be deleted
        and every copy row would stay green.
        """
        compute = _element_by_id(_product_teams_html(), "beat-compute")
        # `<h2\b[^>]*>` — the title is the ordinary thing to style, and
        # `<h2 class="beat-title">` is a `<h2>` that must not read as "has no
        # <h2>". Fail-closed either way, but a message that denies what is on
        # the page is the same misattribution class as the rest of this round.
        h2 = re.search(r"<h2\b[^>]*>(.*?)</h2>", compute, re.S)
        assert h2, (
            "#beat-compute has no <h2> — the compute claim reads as a "
            "continuation of the intro above it, which is the defect "
            "#13645 item 2 fixes")
        assert _beat_text(h2.group(1)) == self.COMPUTE_H2, (
            f"the compute beat's title reads {_beat_text(h2.group(1))!r}; the "
            f"owner asked for {self.COMPUTE_H2!r} (#13645 item 2)")
        # `group(0)` — the WHOLE `<h2 …>…</h2>`, attributes included. Reading
        # `group(1)` looked only at the inner HTML, so a `.beat-num` carried ON
        # the heading (`<h2 class="beat-num">…`) was GREEN while the message
        # below asserts the title carries one (#14406 review round 5, finding 3).
        assert "beat-num" not in h2.group(0), (
            "the compute beat's title carries a `.beat-num`. The numbered beats "
            "are the three STEPS (01/02/03); this one is a claim, and a number "
            "on it would invent a fourth step")
        # ⛔ POSITION, not just presence (#14406 review round 17). `re.search`
        # above accepts the title ANYWHERE in the beat, so moving it BELOW the
        # claim — the one placement that reinstates the defect item 2 exists to
        # fix — left the whole website guard GREEN (measured: 209 passed). The
        # docstring says it: "the beat opens on a TITLE, not on the claim".
        claim = re.search(r"<p\b", compute)
        assert claim and compute.index("<h2") < claim.start(), (
            "#beat-compute's <h2> comes AFTER the claim. #13645 item 2 exists "
            "because the claim read as a continuation of the intro above it; a "
            "title below it leaves exactly that defect in place, with the copy "
            "rows and the heading outline all still green")

    def test_the_outcomes_grid_carries_the_pdfs_six_labels_in_row_order(self):
        compute = _element_by_id(_product_teams_html(), "beat-compute")
        up = re.findall(r'<ul class="up">(.*?)</ul>', compute, re.S)
        down = re.findall(r'<ul class="down">(.*?)</ul>', compute, re.S)
        assert up and down, (
            "#beat-compute has no `.up`/`.down` lists — the ↑/↓ grid is the "
            "investors.html `.outcomes` component and the PDF's own figure")
        def labels(chunk: str) -> tuple[str, ...]:
            return tuple(_beat_text(m) for m in
                         re.findall(r'<span class="a">[^<]*</span><span>(.*?)</span>',
                                    chunk, re.S))
        assert labels(up[0]) == self.OUTCOMES_UP, (
            f"the ↑ column reads {labels(up[0])!r}; the PDF reads "
            f"{self.OUTCOMES_UP!r} (row order matters — each ↑ is paired with "
            "the ↓ beside it)")
        assert labels(down[0]) == self.OUTCOMES_DOWN, (
            f"the ↓ column reads {labels(down[0])!r}; the PDF reads "
            f"{self.OUTCOMES_DOWN!r}")

    def test_the_three_steps_carry_the_pinned_copy(self):
        """Each step against ITS OWN source — the PDF for 01/02, the owner for 03.

        ⛔ THE NAME AND THE MESSAGE ARE THE GUARD, not decoration: this row used
        to be `test_the_three_steps_are_verbatim` and to tell the maintainer, on
        red, that "the PDF reads …" — true for steps 01-02 and FALSE for step
        03, whose heading and body the owner replaced on 2026-11-23. In the same
        breath it printed the pinned paragraph (the owner's) beside the PDF's
        and warned about a `superintelligence` typo that paragraph no longer
        contains. The row a maintainer hits first when touching step 03 was
        therefore instructing them to perform the exact revert the edit
        superseded — the shape that already cost this branch `fdd3a6a60f`. The
        source is now named per beat.
        """
        html = _product_teams_html()
        for section_id, heading, paragraph in self.STEPS:
            source = self.STEP_SOURCES[section_id]
            beat = _element_by_id(html, section_id)
            h2 = re.search(r"<h2\b[^>]*>(.*?)</h2>", beat, re.S)
            assert h2, f"#{section_id} has no <h2>"
            assert _beat_text(h2.group(1)) == heading, (
                f"#{section_id}'s heading reads {_beat_text(h2.group(1))!r}; "
                f"{source} reads {heading!r}")
            body = [_beat_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", beat, re.S)]
            assert body == [paragraph], (
                f"#{section_id}'s paragraph reads {body!r}; {source} reads "
                f"[{paragraph!r}]")

    def test_the_pdfs_two_typos_are_absent_from_the_rendered_page(self):
        """The PDF's spellings must NOT reach the reader — pinned as ABSENCE.

        ⛔ Every other copy row in this class compares the page to a CONSTANT on
        this class, so reverting the page's copy AND the constant together keeps
        all of them green — and a PAIR-REVERT is exactly the shape a stale
        "the copy is verbatim, unchanged" instruction produces (it happened on
        this branch once, at `fdd3a6a60f`). This row reads the SERVED markup, so
        it reds on the pair, and `_served` drops HTML comments so the header
        note that QUOTES the PDF's spelling cannot satisfy or defeat it.

        The ruling that makes the corrected forms correct:
        https://github.com/daniel-ospina/tortoise/issues/13645#issuecomment-7047852104

        ⛔ STEP 03'S PARAGRAPH IS NO LONGER ON THE PAGE AT ALL: the owner
        REPLACED it on 2026-11-23 (#13645 item 5), so the second typo pair has
        nothing left to guard. It is pinned ABSENT as a whole sentence instead —
        both spellings — because a lane that "restores the PDF's copy" would put
        that paragraph back, and restoring it is now a revert of an owner edit
        rather than a return to verbatim. The ruling stands on #13645 for anyone
        who does restore it; the ruled spelling stays asserted here so it is the
        spelling that comes back.
        """
        served = _served(_product_teams_html())
        # ⛔ AND THE RETITLE IS PINNED BY PRESENCE, not only by the `STEPS`
        # comparison (#14406 review round 17). `STEPS` compares the page TO a
        # constant on that class, so reverting the page's `· Reason & Learn` to
        # the PDF's `· Reason` AND the constant together left every row green
        # (measured: 763 passed) — the pair-revert this row exists to close, and
        # the other two text edits already red on it. `_served` keeps the entity
        # literal, so the pin is on the served bytes.
        assert "Reason &amp; Learn" in served, (
            "step 03's heading does not read '… · Reason & Learn'. The owner "
            "retitled it on 2026-11-23 (#13645 item 4) — with no presence pin, "
            "reverting the page AND `STEPS` together stays green, which is the "
            "pair-revert these pins exist to close")
        # The compute beat is where the ruled typo pair still lives.
        for typo in ("make decision with superintelligence",):
            assert typo not in served, (
                f"the rendered page contains the PDF's spelling {typo!r}. The "
                "owner ruled it a typo and the corrected string is what ships "
                "(#13645#issuecomment-7047852104) — restoring it is a revert "
                "of a recorded ruling, not a return to verbatim copy.")
        for ruled in ("make decisions with superintelligence",):
            assert ruled in served, (
                f"the rendered page is missing the ruled string {ruled!r} "
                "(#13645#issuecomment-7047852104)")
        for pdf_step_03 in ("super intelligence, grounded",
                            "superintelligence, grounded",
                            "Improve decision-making with superintelligence"):
            assert pdf_step_03 not in served, (
                f"the rendered page contains {pdf_step_03!r}, part of the "
                "step-03 paragraph the owner REPLACED on 2026-11-23 (#13645 "
                "item 5). The PDF's paragraph is gone from this page by the "
                "owner's instruction — restoring it reverts an owner edit, not "
                "a builder's paraphrase.")
        # ⛔ THE OTHER 2026-11-23 EDIT HAD NO SUCH PIN, so reverting BOTH the page
        # (`…html:840`) and `COMPUTE` to "over this context" kept every row green
        # (measured on `fb5514dcdc`: 16 passed) — while the same pair-revert for
        # step 03 reds 2. `COMPUTE` catches the page-only revert; this catches the
        # PAIR, for the same reason the PDF's paragraph is pinned absent above.
        # `this context` occurs only in the constant and in the page, so nothing
        # else can satisfy or defeat the literal.
        assert "over this context" not in served, (
            "the rendered page reads \"over this context\". The owner changed it "
            "to \"your context\" on 2026-11-23 (#13645 item 3, "
            "#issuecomment-7108810457) — restoring the old phrase reverts an "
            "owner edit, and a page+constant pair-revert would otherwise stay "
            "green")
        # The onboarding heading is the THIRD departure (#13696 item 4) and its
        # pair-revert was unpinned: `ONBOARD_H2` has no full stop, so reverting
        # page and constant together kept every copy row green. Pinned ABSENT
        # here for the same reason as the two spellings above.
        assert "without learning another UI." not in served, (
            "the onboarding heading renders a trailing full stop. The owner "
            "asked for it to go (#13696 item 4) — restoring it reverts an owner "
            "item, and a page+constant pair-revert would otherwise stay green")

    def test_the_onboarding_beat_is_verbatim(self):
        beat = _element_by_id(_product_teams_html(), "beat-onboard")
        h2 = re.search(r"<h2>(.*?)</h2>", beat, re.S)
        assert h2, "#beat-onboard has no <h2>"
        assert _beat_text(h2.group(1)) == self.ONBOARD_H2, (
            f"the onboarding heading reads {_beat_text(h2.group(1))!r}; the PDF "
            f"reads {self.ONBOARD_H2!r}")
        body = [_beat_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", beat, re.S)]
        assert body == [self.ONBOARD_P], (
            f"the onboarding paragraph reads {body!r}; the PDF reads "
            f"[{self.ONBOARD_P!r}]")

    def test_the_beat_order_is_the_pdfs_section_order(self):
        """Hero → intro → compute → 01 → 02 → 03 → onboarding, then pricing.

        A re-order is invisible to every other assertion here (each beat's copy
        is pinned in isolation) and to the heading outline (all h2s), so the
        narrative could be shuffled with nothing red.
        """
        html = _served(_product_teams_html())
        order = re.findall(r'<section id="(beat-[a-z]+)"', html)
        assert order == ["beat-hero", "beat-intro", "beat-compute", "beat-ingest",
                         "beat-organize", "beat-reason", "beat-onboard"], (
            f"the beats are ordered {order!r}. The PDF's section order is hero, "
            "intro, compute + the ↑/↓ block, 01, 02, 03, onboarding — the "
            "narrative the owner wrote runs in that order.")
        # The beat list lives in the SCRIPT, which `_served` strips — read the
        # raw document for it.
        raw = _product_teams_html()
        listed = BEAT_LIST_RE.search(raw)
        assert listed, "the script declares no `const beats = [...]` list"
        assert re.findall(r"'([a-z-]+)'", listed.group(1)) == order, (
            f"the script's scrubbed beat list {listed.group(1)!r} does not "
            f"match the beat order {order!r}, so the wrong panel fades in for "
            "the scroll position")


#: Every property that can set a box's BLOCK-END border WIDTH.
BORDER_END_WIDTH_PROPS = ("border", "border-width", "border-block",
                          "border-block-width", "border-bottom",
                          "border-block-end", "border-bottom-width",
                          "border-block-end-width")

#: Every property that can set the block-END border STYLE — the axis that
#: decides whether a stated WIDTH is used at all. The style axis is separate
#: from the width axis because CSS resolves them separately: a `none` style in
#: one declaration zeroes a width declared in ANOTHER one.
BORDER_END_STYLE_PROPS = ("border-style", "border-block-style",
                          "border-bottom-style", "border-block-end-style")

#: Border-style keywords that mean "no border" and therefore a WIDTH of zero.
_BORDER_NONE_STYLES = ("none", "hidden")

#: The border SHORTHANDS that set their side's block-END STYLE as well as its
#: width. `border-width` and the `border-*-width` longhands are NOT here: they
#: set widths only and say nothing about the style axis.
_BORDER_STYLE_SHORTHANDS = ("border", "border-block", "border-bottom",
                            "border-block-end")

#: CSS-wide keywords that RESET a property to its INITIAL value. A shorthand
#: that resets sets every sub-property it covers, so `border-bottom: initial`
#: is a `none` STYLE — i.e. a 0px border — not "this rule says nothing".
_CSS_WIDE_RESET = ("initial", "unset", "revert", "revert-layer")

#: A declaration the cascade MATCHED but could not resolve to a number/style.
#: A sentinel rather than `None`, because `None` already means "this
#: declaration states nothing for this axis" and the two must rank differently:
#: an unresolvable declaration that WINS the cascade makes the whole read
#: unknown, where a silent one lets an earlier rule's value stand (#14406
#: review round 23).
_UNREADABLE = object()

#: Every border-style keyword (CSS Backgrounds and Borders 3 §4.1). A style
#: token found in a declaration is what the style axis ranks; anything outside
#: this tuple is not a style (e.g. a colour name or `var(--accent)`).
_BORDER_STYLES = ("none", "hidden", "solid", "dashed", "dotted", "double",
                  "groove", "ridge", "inset", "outset")


def _css_border_end_style(prop: str, value: str) -> str | None:
    """The block-END border STYLE `prop` states, lowercased, or `None`.

    Same positional rule as `_css_border_end_length`: `border`/`border-style`
    are ordered lists whose THIRD value is block-end for 3 and 4 values, and
    `border-block`/`border-block-style` are `(start, end)`; the single-side
    longhands name their side. A shorthand (`border: 2px solid #f00`,
    `border-bottom: 4px none`) states a style for every side it sets, so it is
    an entry on this axis too — otherwise a LATER `border-bottom-style: solid`
    could not restore a border a shorthand removed.

    ⛔ AND AN OMITTED STYLE IN A SHORTHAND IS `none`, not silence: `border: 3px`
    and `border-bottom: 2px` take the initial value of every sub-property they
    omit, and `border-style`'s initial value is `none` — so a width-only
    shorthand REMOVES the border. A CSS-wide reset (`initial`/`unset`/`revert`)
    lands on the same value, on a shorthand or on the style longhand
    (#14406 review round 23).
    """
    styles = [t.lower() for t in _css_tokens(_without_important(value))
              if t.lower() in _BORDER_STYLES]
    if not styles:
        # ⛔ A VALID SHORTHAND THAT STATES A WIDTH AND OMITS THE STYLE RESETS IT
        # TO `none` (CSS Backgrounds and Borders 3: an omitted sub-property of
        # a shorthand takes its INITIAL value, and `border-style`'s initial
        # value is `none`), so `border-bottom: 2px` REMOVES the border while
        # stating a 2px width. Returning None here read that as "says nothing",
        # the earlier `solid` survived, and the reader reported a 2px border a
        # browser does not draw (#14406 review round 23, measured). A shorthand
        # this reader cannot resolve at all (`border: var(--x)`, `border: thin`)
        # still returns None and the caller fails rather than guesses.
        if prop in _BORDER_STYLE_SHORTHANDS:
            tokens = _css_tokens(_without_important(value))
            if any(token.lower() in _CSS_WIDE_RESET for token in tokens):
                return "none"
            # …and only a shorthand this reader can see is VALID: `border:
            # 1px 2px 0` and `border-bottom: 2px 3px` are not legal shorthands
            # at all (the browser DROPS the declaration, so an earlier style
            # stands), and reading them as `none` would be a removal this
            # reader invented. `border-block` is the one shorthand whose
            # short form takes two widths.
            widths = [token for token in tokens
                      if _BORDER_WIDTH_PX.fullmatch(token)
                      or (_BARE_ZERO_LENGTH.fullmatch(token)
                          and float(token) == 0.0)]
            if 0 < len(widths) <= (2 if prop == "border-block" else 1):
                return "none"
            return None
        if prop in BORDER_END_STYLE_PROPS and any(
                token.lower() in _CSS_WIDE_RESET
                for token in _css_tokens(_without_important(value))):
            # A CSS-wide reset on the STYLE LONGHAND is `none` for every keyword
            # here: `border-style` is not inherited, so `initial`, `unset` and
            # `revert` all land on the initial value. `inherit` is deliberately
            # NOT in `_CSS_WIDE_RESET` — it takes the parent's style, which this
            # reader cannot see (#14406 review round 23).
            return "none"
        return None
    if prop in ("border", "border-style"):
        return styles[2] if len(styles) >= 3 else styles[0]
    if prop in ("border-block", "border-block-style"):
        return styles[-1] if len(styles) > 1 else styles[0]
    return styles[0]


def _css_border_end_length(prop: str, value: str) -> float | None:
    """The BLOCK-END border WIDTH `prop` states, in px, ignoring its STYLE.

    Style-blind on purpose: whether a stated width is USED is the style axis's
    decision, and the two axes resolve independently (see
    `_border_end_width_cascade`). Folding the style into this read is what made
    `border-bottom: 2px none` followed by `border-bottom-style: solid` read 0
    on a page a browser draws with 2px.
    """
    tokens = _css_tokens(_without_important(value))
    lengths: list[float] = []
    for token in tokens:
        match = _BORDER_WIDTH_PX.fullmatch(token)
        if match:
            lengths.append(float(match.group(1)))
        elif _BARE_ZERO_LENGTH.fullmatch(token) and float(token) == 0.0:
            lengths.append(0.0)
    if not lengths:
        return None
    if prop in ("border", "border-width"):
        chosen = lengths[2] if len(lengths) >= 3 else lengths[0]
    elif prop in ("border-block", "border-block-width"):
        chosen = lengths[-1] if len(lengths) > 1 else lengths[0]
    else:
        chosen = lengths[0]
    return chosen


def _css_border_end_width(prop: str, value: str) -> float | None:
    """The BLOCK-END border width `prop` sets, in px, or `None` if it sets none.

    The border-width FAMILY, for the same reason `BASE_PAD_PROPS` exists: the
    compensation relation's other operand was read from the `border` shorthand
    alone, so `border-bottom-width`, `border-block-end` and
    `border-width: a b c` were invisible and re-split the four CTAs with every
    row GREEN (measured #14406 review round 19: `.pricing-card.popular
    { border-bottom-width: 3px }` put its CTA at 6414.78 against 6415.78 —
    1px, the exact #13645 item 6 signature — 209 passed).

    `border`/`border-width` are ordered lists — 1 value sets all four sides, 2
    are `(block, inline)`, 3 are `(block-start, inline, block-end)` and 4 are
    `(block-start, inline-end, block-end, inline-start)` — so block-end is the
    THIRD for 3 and 4 values and the FIRST otherwise, the positional rule
    `_css_block_end` already applies to `padding`. `border-block` is
    `(start, end)`; the single-side properties name their side.

    Widths are picked out by the px pattern rather than by position, because a
    `border:` shorthand interleaves style and colour tokens
    (`2px solid var(--accent)`) and those are not lengths.

    ⛔ A ZERO WIDTH IS A WIDTH, and dropping it was a fail-open (#14406 review
    round 20, measured): the width list kept only `px` tokens, so the commonest
    way to REMOVE a border — `border-bottom: 0`, `border-bottom: none`,
    `border-bottom-width: 0` — produced no length at all, the declaration was
    skipped entirely, and the cascade fell back to an EARLIER rule's width. On
    the shipped page that re-derives `extra_px = 1` where a browser applies -1,
    i.e. the popular CTA moves 2px and every row stays GREEN — precisely the
    compensation's own signature. The positions are kept too: in
    `border-width: 1px 0 3px` the zero is the THIRD value and block-end is 0,
    so a list of only the px tokens would read the wrong side.

    `border-block-width` is in the family for the same reason as the other
    longhands (#14406 review round 20): it sets the block-END width and it was
    absent from `BORDER_END_WIDTH_PROPS`, so `border-block-width: 3px` was
    invisible and the row compared the compensation to itself. Its 1/2 values
    are `(start, end)`, like `border-block`.

    ⛔ AND A `none`/`hidden` STYLE ZEROES THE WIDTH EVEN WHEN A LENGTH IS
    STATED (CSS Backgrounds and Borders 3 — a side's USED border width is 0
    when its style is `none` or `hidden`, whatever the width property says), so
    `border-bottom: 4px none #f00` is a 0px border, not a 4px one.

    ⛔ AND THE STYLE IS ITS OWN AXIS, so this single-declaration read is only
    half the rule (#14406 review round 22, measured). A declaration that sets
    the STYLE LONGHAND zeroes the width just as the shorthand does — but it is
    a different declaration, and reading only width properties made the removal
    invisible: `.pricing-card.popular { border-bottom-style: none }` (and
    `hidden`, `border-style`, `border-block-end-style`) left the reader at the
    shipped 1px while a browser applies -1, the exact fail-open round 20 found
    for `border-bottom: 0`. `_border_end_width_cascade` now resolves this axis
    against the whole cascade; this function keeps the one-declaration form
    for callers and assertions that hand it a single value.
    """
    if _css_border_end_style(prop, value) in _BORDER_NONE_STYLES:
        # `none`/`hidden` state a width of zero and OVERRIDE a stated length.
        # Unlike `thin`, whose px width this file cannot convert, so it stays
        # None below and the caller fails rather than guesses.
        return 0.0
    return _css_border_end_length(prop, value)


def _border_end_width_cascade(css: str, compound: str,
                              skip_popular: bool) -> float | None:
    """The block-END border width a browser applies to `compound`.

    Same ranking as `_css_block_end_cascade` — `!important`, then specificity,
    then source order — and the same reach (`_css_rightmost_selects`, the
    element a bare rule selects, and `*`), because this is the OTHER operand of
    a relation whose `padding` half is already read that way: a reader narrower
    than the one it is compared against turns a stale expectation into a green
    row (#14406 review round 19).

    `skip_popular` drops the parts that NAME `.popular`, so the BASE operand is
    the border the three sibling cards get. The popular operand keeps them on
    purpose: its own rule out-ranks the base one and the cascade decides.

    ⛔ TWO AXES, RESOLVED SEPARATELY (#14406 review round 22). CSS computes a
    side's border STYLE and WIDTH independently, so a `none`/`hidden` style
    zeroes a width stated in ANOTHER declaration — `.pricing-card.popular
    { border-bottom-style: none }` is a 0px border with the shipped 2px width
    untouched — and a later `border-bottom-style: solid` restores a border a
    shorthand had removed. Both reads therefore rank in their own list and only
    the RESOLVED pair decides. Folding the style into the width read (the
    round-21 shape) got both directions wrong: it kept the removal invisible
    when the style was a longhand, and read 0 where a browser draws 2px when a
    shorthand's dead style was later restored.

    ⛔ AND A DECLARATION THAT MATCHES IS NEVER SILENT (#14406 review round 23,
    measured). Every matched declaration is ranked on its axis; one this reader
    cannot resolve ranks as `_UNREADABLE` and, if it WINS, makes the read
    UNKNOWN (`None`, which the caller turns into a failure). Dropping it instead
    let an EARLIER rule's value stand, which is how `border-bottom-width: thin`
    (1px in a browser) and `border-bottom: 2px` (a shorthand that RESETS the
    style to `none`, so no border at all) both read the shipped 1px and left the
    relation GREEN over a popular CTA off the shared baseline.
    """
    ranked: list[tuple[tuple, float | object]] = []
    styles: list[tuple[tuple, str | object]] = []
    element = _subject_element(css, compound)
    for rule_index, (prelude, body, _condition) in enumerate(_css_desktop_rules(css)):
        for part in (p.strip() for p in prelude.split(",")):
            if "<" in part:
                continue
            rightmost = _css_rightmost_compound(part)
            if not _css_rightmost_selects(rightmost, compound) and not (
                    rightmost.startswith("*")) and not (
                    element is not None
                    and _css_compound_matches_element(rightmost, element)) and not (
                    _compound_selects_every_subject(rightmost, compound)):
                continue
            # The BASE operand skips the rules that REQUIRE a class its three
            # sibling cards do not carry (`.pricing-card.popular`) — which is
            # what `skip_popular` means, NOT “the text `.popular` appears
            # anywhere in the part”. The text test is fail-OPEN for
            # `.pricing-card:not(.popular)` and fail-OFF for an ancestor that
            # names `.popular`; `_compound_requires_a_foreign_class` carries the
            # measured table (#14406 review round 23).
            if skip_popular and _compound_requires_a_foreign_class(
                    rightmost, compound):
                continue
            specificity = _css_specificity(part) or (9, 9, 9)
            for decl_index, decl in enumerate(_css_blanked(body).split(";")):
                name, sep, value = decl.partition(":")
                prop = name.strip().lower()
                if not sep or prop not in (BORDER_END_WIDTH_PROPS
                                           + BORDER_END_STYLE_PROPS):
                    continue
                important = bool(_CSS_IMPORTANT.search(value))
                rank = (important, specificity, rule_index, decl_index)
                # The two axes are RESOLVED SEPARATELY and only then combined:
                # CSS computes a side's style and width independently, so a
                # `none` style zeroes a width stated in another declaration —
                # and a later `solid` restores one a shorthand had removed.
                #
                # ⛔ AND AN UNRESOLVED DECLARATION IS NOT SILENCE (#14406 review
                # round 23, measured). Every MATCHED declaration enters its
                # axis's list, with `_UNREADABLE` as the value when this reader
                # cannot resolve it, because dropping it let an EARLIER rule's
                # value stand: `.pricing-card.popular { border-bottom-width:
                # thin }` (1px in a browser, extra 0) and `… { border-bottom:
                # 2px }` (style reset to `none`, extra −1) both read the shipped
                # 1px and left the row GREEN over a popular CTA off the shared
                # baseline. If the winner is unresolvable the read is UNKNOWN and
                # the caller fails, which is the direction this file fails in.
                if prop in BORDER_END_WIDTH_PROPS:
                    width = _css_border_end_length(prop, value)
                    ranked.append((rank, width if width is not None
                                   else _UNREADABLE))
                if prop in BORDER_END_STYLE_PROPS or (
                        prop in _BORDER_STYLE_SHORTHANDS):
                    style = _css_border_end_style(prop, value)
                    styles.append((rank, style if style is not None
                                   else _UNREADABLE))
    ranked.sort(key=lambda entry: entry[0])
    styles.sort(key=lambda entry: entry[0])
    style = styles[-1][1] if styles else None
    if style is _UNREADABLE:
        return None
    if style in _BORDER_NONE_STYLES:
        return 0.0
    width = ranked[-1][1] if ranked else None
    return None if width is _UNREADABLE else width


def _popular_border_extra_px(css: str) -> float:
    """How much MORE of the bottom content box the popular card's border eats.

    ⛔ BOTH BORDERS ARE THE PROPERTY'S OWNER, NOT A SELECTOR'S LAST MATCH
    (#14406 review round 13). These two reads were `[...][-1]` — the last rule
    whose PRELUDE is the selector — so a later `.pricing-card { … }` rule that
    repeats no `border` hid the rule that declares it, and the row above red a
    page whose border was untouched (measured: `.pricing-card { border-color:
    #fff }`, CTA baselines unmoved, `_border_px_of` receiving "").
    """
    base_card = _border_end_width_cascade(css, ".pricing-card",
                                         skip_popular=True)
    assert base_card is not None, (
        "no rule the three sibling cards obey gives `.pricing-card` a "
        "block-END border width, so this relation has no base operand")
    popular_card = _border_end_width_cascade(css, ".pricing-card.popular",
                                             skip_popular=False)
    assert popular_card is not None, (
        "no rule reaching `.pricing-card.popular` gives it a block-END border "
        "width, so its thicker border — the reason the compensation exists — "
        "is gone")
    return popular_card - base_card


#: Every property that can set a box's BLOCK-END inset, in one place so the
#: base-inset reader and the CTA padding rows cannot drift apart.
BASE_PAD_PROPS = ("padding", "padding-block", "padding-bottom",
                  "padding-block-end")


def _base_block_end_pad(css: str) -> str | None:
    """The BASE card's block-END inset, resolved by the CASCADE.

    ⛔ ONE reader (#14406 review round 16): `_expected_popular_block_end_pad`
    and the popular-card cascade row both call this, because the row read the
    `padding` shorthand itself and so red a `padding-block`/`padding-inline`
    split whose geometry is identical (CTA tops unchanged at 745.78 x4,
    measured) with "declares `padding`" — while this helper sat above it saying
    there was "no fifth copy to pin".

    ⛔ AND THE ORDER IS THE CASCADE, not "longhand before shorthand" (#14406
    review round 16, measured). The first version walked
    `padding-block-end`/`padding-bottom` before `padding-block`/`padding`, so a
    base rule declaring the LONGHAND hid a LATER rule declaring the shorthand:
    with `padding-block: 1.75rem` on `.pricing-card` and the existing
    `@media (min-width: 1200px) { .pricing-card { padding: 2rem } }` probe, it
    derived `calc(1.75rem - 1px)` where a browser applies 2rem — the same
    stale-expectation fail-open round 16 closed in `_css_last_declaring_body`,
    one function over. CSS resolves by `!important`, then specificity, then
    SOURCE ORDER; "longhand beats shorthand" is not a cascade axis at all.
    Every rule read here names `.pricing-card` ITSELF, so specificity ties by
    construction and what remains is `!important` over source order — the
    author-origin cascade in full.

    Reach is the CASCADE's, not an exact-selector's (#14406 review round 19):
    this reader used to accept only rules whose selector LIST contained
    `.pricing-card` verbatim, so an ancestor- or type-qualified override of the
    base inset moved what a browser renders while the expectation stayed at the
    base rule's value — measured `.pricing-grid .pricing-card { padding: 2rem }`
    and `div.pricing-card { padding: 2rem }` at 4px off the shared baseline with
    the whole module GREEN (209 passed). It is one operand of a relation whose
    other side (`_css_block_end_cascade`) is fully wide, so a narrow read here
    makes a stale expectation indistinguishable from a correct one. The parts
    that NAME `.popular` are skipped instead: those are the OTHER operand.

    `!important` is a PRIORITY, not part of the value (#14406 review round 19):
    the longhand branch of `_css_block_end` returns its value verbatim, so
    `padding-bottom: 1.75rem !important` produced the expectation
    `calc(1.75rem !important - 1px)` — not a CSS value at all — and reded six
    rows on a page that renders identically (measured: CTA tops 6415.78 x4).
    It is stripped before the side is read.
    """
    ranked: list[tuple[tuple, str]] = []
    element = _element_carrying_class(css, "pricing-card")
    for rule_index, (prelude, body, _condition) in enumerate(_css_desktop_rules(css)):
        for part in (p.strip() for p in prelude.split(",")):
            if "<" in part:
                continue
            rightmost = _css_rightmost_compound(part)
            if not _css_rightmost_selects(rightmost, ".pricing-card") and not (
                    rightmost.startswith("*")) and not (
                    element is not None
                    and _css_compound_matches_element(rightmost, element)):
                continue
            if ".popular" in part:
                continue
            specificity = _css_specificity(part) or (9, 9, 9)
            for decl_index, decl in enumerate(_css_blanked(body).split(";")):
                name, sep, value = decl.partition(":")
                prop = name.strip().lower()
                if not sep or prop not in BASE_PAD_PROPS:
                    continue
                important = bool(_CSS_IMPORTANT.search(value))
                ranked.append(((important, specificity, rule_index, decl_index),
                               _css_block_end(
                                   prop, _without_important(value.strip()))))
    ranked.sort(key=lambda entry: entry[0])
    return ranked[-1][1] if ranked else None


def _expected_popular_block_end_pad(css: str) -> str:
    """The popular card's bottom inset as a RELATION, never a remembered constant.

    ⛔ #14406 review round 12: this value was pinned as the literal string
    `calc(1.75rem - 1px)` in FOUR sibling rows of
    `TestCssDesktopReachAndSpecificity`, so the relation-preserving edit
    (`padding: 2rem` together with `calc(2rem - 1px)`) — geometrically identical,
    because the popular inset is the base inset less the extra border pixel —
    red the FILE and the messages claimed "the page is already wrong". Round 12
    fixed the one row it was looking at; the reviewer then measured the same
    false RED three more times. One helper, so there is no fifth copy to pin.
    """
    base_pad = _base_block_end_pad(css)
    assert base_pad, (
        "no rule naming `.pricing-card` declares a block-END padding as "
        "`padding`, `padding-block`, `padding-bottom` or `padding-block-end`, "
        "so the base inset the compensation is derived from is gone — same "
        "reader as the border reads (#14406 review round 13: `.pricing-card "
        "{ align-self: stretch }` appended as its own rule left this reading "
        "an empty padding)")
    extra_px = _popular_border_extra_px(css)
    assert extra_px > 0, (
        "the popular card's bottom border is not WIDER than its siblings', so "
        f"the compensation this helper derives is stale (measured {extra_px:g}px, "
        "expected a positive difference)")
    return f"calc({base_pad} - {extra_px:g}px)"


class TestProductTeamsPricingCardCtaBaseline:
    """#13645 item 6 — the four pricing CTAs share ONE baseline.

    The report was that the buttons sit at different heights because the tier
    copy has different lengths. The BOXES
    were never the problem — `.pricing-grid`'s default `align-items: stretch`
    already gives all four one height (measured 1440x900: every card ran
    304.33 -> 815.72) — the defect was where the CTA sat INSIDE its box:
    `.btn` tops read 675.58 / 675.58 / 697.05 / 745.78, three distinct
    baselines 70.2px apart. After the two declarations pinned below all four
    read 745.78 at 1440x900 (measured, headless Chromium).

    ⛔ THE REPORT IS A SCREENSHOT THE OWNER PASTED, so it is item 6 of the
    relayed decision rather than a filename this repo can hold:
    https://github.com/daniel-ospina/tortoise/issues/13645#issuecomment-7108810457
    (`clipboard-2026-11-23-121001` exists nowhere in this repo, and the
    screenshots are deliberately outside it). Without that record a later lane
    meeting `display: flex` + `padding-bottom: calc(1.75rem - 1px)` has only an
    ephemeral filename behind it, cannot tell an owner-requested fix from a
    builder's preference, and reverting unexplained CSS is an ordinary edit —
    which is the asymmetry this class existed to close for the copy items.

    ⛔ WHY A STATIC PIN FOR A LAYOUT FIX: the failure is invisible to every
    other row here — no copy, no price and no markup changes — so a revert of
    the two declarations would be green everywhere else and the owner would
    have to report it a second time.

    ⛔ WHAT THE READER ACTUALLY BUYS (the first draft of this docstring claimed
    the comment beside the rule says `display: flex` and `margin-top: auto`, so
    blanking was what stopped the prose satisfying the rows — MEASURED FALSE:
    the comment says "FLEX COLUMN", and the only `margin-top: auto` in prose is
    LEADING, above `.pricing-card`, outside any rule body `_effective_decl`
    reads). Two properties are load-bearing instead: `_effective_decl` returns
    the LAST declaration in a MATCHED rule body, so an appended override
    inside the block reds; and matching the prelude keeps the value tied to
    the selector that owns it.

    ⛔ AND A SUBSTRING SEARCH WOULD BE GREEN ON A BROKEN PAGE: `.pricing-grid`
    and `.pricing-toggle` both literally carry `display: flex` (`…html:200`,
    `:204`), so "the sheet contains `display: flex`" is true with
    `.pricing-card` never made a container at all.

    ⛔ `[-1]` IS LAST-IN-SOURCE-ORDER, NOT THE CASCADE WINNER — specificity is
    never consulted by `_css_rules_with_scope`, so a higher-specificity rule
    that names a DIFFERENT selector is invisible here. Measured (#14406 review
    round 3): appending `.pricing-grid .pricing-card .btn { margin-top: 0 }`
    — (0,3,0) against the pinned (0,2,0), winning at any order — left all three
    rows of this class GREEN while the four CTAs split again. `[-1]` is still
    right for the rule that OWNS the declaration (an appended duplicate of the
    same selector must win); `test_no_other_rule_takes_the_ctas_slack` closes
    the competing-selector half — for ancestor selectors, the logical aliases
    and `min-width` media blocks too, each of which was a measured false PASS
    in that row's first draft (#14406 review round 4, G1-G3).

    ⛔ ONE ROW RESOLVES THE CASCADE INSTEAD OF ASKING FOR AGREEMENT, and the
    difference is not cosmetic: the popular card's `padding-bottom`
    compensation must OVERRIDE the base `.pricing-card`'s `padding: 1.75rem`, so
    an agreement invariant reds a correct page there while still missing a
    TYING rule appended later. The popular-card row therefore reads
    `_css_block_end_cascade` — the whole `padding` family (both longhands, both
    shorthands) ranked by `!important`, specificity and source order — and
    asserts what a browser applies (#14406 review round 5, finding 1, measured
    with two cascade-valid mutants that both left every row GREEN).

    ⛔ THAT ROW SITS ON `_css_specificity`, SO ITS COUNTER IS PART OF THE GUARD.
    The first version subtracted BOTH pseudo-elements and every TYPE selector
    from the class count, so a type selector cancelled a class and a
    TYPED-QUALIFIED override ranked BELOW the pinned rule: appending
    `section .pricing-card.popular { padding-bottom: 1.75rem }` (0,2,1) or
    `div.pricing-card.popular { padding-bottom: 1.75rem }` (0,2,1) — both of
    which a browser applies at any source order, both of which put the popular
    CTA back 1px high — left EVERY row in this class GREEN (measured, #14406
    review round 6 finding 1). The counter now follows the standard's columns
    (`TestCssDesktopReachAndSpecificity` pins it), and the two mutants above are
    pinned there: shrinking the counter back to "pseudo minus all elements" reds
    both that class and the cascade row.
    """

    def test_each_card_is_a_flex_column_and_its_cta_takes_the_slack(self):
        css = _product_teams_html()
        # ⛔ THE PROPERTY'S OWNER, NOT THE LAST RULE WITH THE SELECTOR (#14406
        # review round 13). `_css_rules_with_scope(...)[-1]` is the last rule
        # whose PRELUDE is `.pricing-card`, so any later card rule that does not
        # repeat `display` shadows the rule that does — measured on the tree at
        # `f091cc8dedb2`: appending `.pricing-card { align-self: stretch }` (the
        # edit round 12 declared GREEN, written as its own rule instead of
        # inside the block) or even `.pricing-card { border-color: #fff }` red
        # this row with "is not a flex container" about a card that is one.
        # `_css_last_declaring_body` picks the last rule that DECLARES the
        # property, so the override shape still loses none of its teeth:
        # `.pricing-card { display: block }` still owns `display` and still reds.
        card_display = _css_last_declaring_body(css, ".pricing-card", "display")
        assert card_display is not None, (
            "no rule naming `.pricing-card` declares `display`, so this row "
            "would compare None to 'flex' — the card's container is gone "
            "(#13645 item 6)")
        # ⛔ `!important` is a PRIORITY, not part of the value, so it is stripped
        # before the comparison: `display: flex !important` is this pin's own
        # intent, only stronger, and comparing the raw declaration reded a
        # correct page (#14406 review round 12).
        assert _without_important(
            _effective_decl(card_display, "display") or "") == "flex", (
            "`.pricing-card` is not a flex container, so `margin-top: auto` on "
            "its CTA has no free space to absorb and the four buttons go back "
            "to their content-length heights (three baselines, 70.2px apart at "
            "1440x900 — #13645 item 6)")
        # ⛔ THE FAMILY, NOT THE LITERAL LONGHAND (#14406 review round 16).
        # `flex-flow: column nowrap` IS this pin's intent written as the
        # shorthand: it renders the card pixel-identically (measured at
        # 1440x900 — all four CTA tops 745.78, cards 304.33 -> 815.72), and
        # reading only `flex-direction` red it with "declares `flex-direction` …"
        # — a message naming a property the page does not have. One reader, not
        # two: `FLEX_DIRECTION_PROPS` is the family the disagreement loop below,
        # the grid row and `_css_flex_direction` already resolve. It adds no
        # fail-open, because that loop separately requires every `flex-flow`
        # that REACHES the card to resolve to `column` — so a declared shorthand
        # can never be the row layout and still pass here.
        card_direction = _css_last_declaring_body(
            css, ".pricing-card", "flex-direction")
        card_shorthand = _css_last_declaring_body(
            css, ".pricing-card", "flex-flow")
        assert card_direction is not None or card_shorthand is not None, (
            "no rule naming `.pricing-card` declares `flex-direction` or its "
            "`flex-flow` shorthand, so the children stack along the browser "
            "default `row` (#13645 item 6)")
        for owner, prop in ((card_direction, "flex-direction"),
                            (card_shorthand, "flex-flow")):
            if owner is None:
                continue
            declared = _effective_decl(owner, prop) or ""
            assert _css_flex_direction(prop, declared) == "column", (
                f"`.pricing-card` sets `{prop}: {declared}`, a flex ROW — its "
                "children sit side by side instead of stacking, so the card "
                "renders as one line of text (#13645 item 6)")
        # ⛔ THE BLOCK-START SIDE, read as the FAMILY it is (#14406 review round
        # 15). Reading only `margin-top` reded a page that spells the same thing
        # `margin-block-start: auto` — which renders IDENTICALLY (measured: all
        # four CTAs at 6415.78 at 1440x900) — with "does not take the leftover
        # height". The same file already resolves `padding-block-end` first for
        # this reason; the logical alias is the property `margin-top` delegates
        # to, and the shorthands reset it.
        cta_start = None
        for prop in ("margin-block-start", "margin-top"):
            owner = _css_last_declaring_body(css, ".pricing-card .btn", prop)
            if owner is not None:
                cta_start = _without_important(
                    _effective_decl(owner, prop) or "")
                break
        if cta_start is None:
            for prop in ("margin-block", "margin"):
                owner = _css_last_declaring_body(css, ".pricing-card .btn", prop)
                if owner is not None:
                    tokens = _css_tokens(_without_important(
                        _effective_decl(owner, prop) or ""))
                    cta_start = tokens[0] if tokens else None
                    break
        assert cta_start is not None, (
            "no rule naming `.pricing-card .btn` declares a block-start margin "
            "(`margin-top`, `margin-block-start`, `margin` or `margin-block`), "
            "so the CTA does not take the leftover height (#13645 item 6)")
        # ⛔ `!important` is a PRIORITY, not part of the value: `margin-top: auto
        # !important` is this pin's own intent, only stronger, and comparing the
        # raw declaration reded a correct page (#14406 review round 12).
        assert cta_start == "auto", (
            "the card's CTA does not take the leftover height, so it sits "
            "wherever its tier's copy ends and the four buttons do not share a "
            "baseline (#13645 item 6). `margin-top: auto` is the mechanism; "
            "without it `display: flex` alone changes nothing a reader sees")

        # The slack half of this cascade question is
        # `test_no_other_rule_takes_the_ctas_slack`'s (a NEGATIVE invariant).
        # These three declarations had no such row, so a higher-specificity
        # selector under a different prelude overrode each one with every row
        # in this class GREEN — measured on the tree before the loop:
        # `.pricing-grid .pricing-card { display: block }` and
        # `.pricing-grid .pricing-card.popular { padding-bottom: 1.75rem }`.
        # `_css_rules_with_scope` cannot see either, so every base rule that
        # can SELECT the subject must AGREE with the pinned value.
        for compound, prop, expected in (
                (".pricing-card", "display", "flex"),
                (".pricing-card", "flex-direction", "column"),
                # `flex-flow` is the shorthand for `flex-direction` + `flex-wrap`
                # and RESETS the component it omits, so `flex-flow: row` and
                # even the direction-free `flex-flow: nowrap` both override the
                # pinned `column` (initial value `row`). Measured (#14406 review
                # round 8): both left every row of this class GREEN while the
                # rendered CTA left the shared baseline. `_css_flex_direction`
                # reads the direction out of either the longhand or the shorthand.
                (".pricing-card", "flex-flow", "column")):
            disagreeing = [(p, v) for p, v in _css_reaching_decls(
                css, prop, compound)
                if _css_flex_direction(prop, v) != expected]
            assert disagreeing == [], (
                f"a rule that can select `{compound}` declares "
                f"`{prop}: {disagreeing[0][1]}` ({disagreeing[0][0]!r}), "
                f"overriding the pinned `{prop}: {expected}`. `_effective_decl` "
                "reads the winning declaration of the rule that NAMES the "
                "selector, so a higher-specificity override under ANOTHER "
                "prelude does not red it — specificity, not source order, picks "
                "the winner (#14406 review round 3 P2, the same hole "
                "`test_no_other_rule_takes_the_ctas_slack` closes for the slack)")

    #: Every declaration that sets the CTA's VERTICAL slack. `margin-block-start`
    #: is the LOGICAL alias `margin-top` delegates to, so pinning only the
    #: physical property left `margin-block-start: 0` invisible (measured —
    #: #14406 review round 4, finding G2). `margin` and `margin-block` are the
    #: shorthands that RESET it, and in the CSS grammar the FIRST token is the
    #: block-start side (one value = all sides; two = block, inline; three =
    #: top, inline, bottom), so `margin: 0 auto` is a zero top margin and is
    #: refused with `margin: 0` — which is correct, it splits the baselines.
    SLACK_PROPS = ("margin-top", "margin-block-start", "margin", "margin-block")

    def test_the_grid_keeps_the_four_boxes_one_height(self):
        """`.pricing-grid`'s `align-items: stretch` is LOAD-BEARING for the one baseline.

        The class docstring states the precondition and nothing asserted it: the
        boxes were "never the problem" because the grid's DEFAULT
        `align-items: stretch` gives all four of them one height, and the defect
        was only where the CTA sat INSIDE its box. Once the boxes stop being
        equal height, `margin-top: auto` pushes each button to its OWN card's
        bottom and the baselines separate again — the exact #13645 item 6
        signature, back, from a one-word edit.

        Measured (#14406 review round 11) — each of these left ALL SEVEN rows of
        this class GREEN while the rendered CTAs split:

            .pricing-grid { align-items: center }      boxes to content height
            .pricing-grid { align-items: flex-start }  tops aligned, bottoms not
            .pricing-card { align-self: flex-start }   the popular card alone
            .pricing-grid { place-items: center }      the same, via the shorthand

        `align-items: center` on a pricing grid is the single most likely
        "tidy the cards up on a laptop" edit, so it is pinned rather than
        trusted. `normal` is allowed beside `stretch` because it behaves as
        `stretch` on a flex container; anything else, and any value this reader
        cannot classify, reds.

        ⛔ THE LAYOUT MODE IS PART OF THE PRECONDITION, and the align family
        alone does not pin it (#14406 review round 12, measured). `align-items:
        stretch` only stretches the items of a FLEX ROW: on a non-flex container
        the declared value is inert, and in a flex COLUMN it stretches the CROSS
        axis — which is horizontal — so every card keeps its own content height.
        Both restore the exact #13645 item 6 signature with ALL NINE rows of this
        class GREEN:

            .pricing-grid { display: block }                       3 baselines
            .pricing-grid { display: flex; flex-direction: column } 3 baselines
            #pricing-grid { display: block }    (the bare-element reach — see
                                                 PINNED_OWNER_SELECTOR)

        So the grid's `display` and its direction are read as AGREEMENT
        invariants beside the align family, `flex-flow` included because it is
        the shorthand that resets the direction.
        """
        css = _product_teams_html()
        for compound, prop, allowed in (
                (".pricing-grid", "align-items", {"stretch", "normal"}),
                (".pricing-grid", "place-items", {"stretch", "normal"}),
                # ⛔ `stretch`/`normal` are a CARD's flex default inside a
                # `stretch` container (#14406 review round 12). The docstring
                # above keeps `normal` for the grid "because it behaves as
                # stretch"; the same reasoning applies to these two, and not
                # applying it here was a measured FALSE RED: a page carrying
                # `align-self: stretch` on `.pricing-card` renders identically
                # (boxes one height, CTAs on the shared baseline) and this row
                # red it, while refusing only the values that MOVE the box is
                # what the row is for.
                (".pricing-card", "align-self", {"auto", "stretch", "normal"}),
                (".pricing-card", "place-self", {"auto", "stretch", "normal"})):
            bad = [(prelude, value)
                   for prelude, value in _css_reaching_decls(css, prop, compound)
                   if _css_align_keyword(prop, value) not in allowed]
            assert bad == [], (
                f"a rule that can select `{compound}` declares "
                f"`{prop}: {bad[0][1] if bad else ''}`, so the four pricing "
                "boxes stop sharing one height and `margin-top: auto` aligns "
                "each CTA to its OWN card's bottom — three baselines 70.2px "
                "apart at 1440x900 (#13645 item 6), with every other row in "
                "this class still GREEN")

        # The layout MODE — `display` and the direction — beside the align
        # family. Without it `display: block` and `flex-direction: column` both
        # restore the defect with every row green (measured, #14406 review
        # round 12); `align-items: stretch` is inert on the first and applies to
        # the CROSS axis on the second.
        #
        # ⛔ AND AN EXPLICIT CROSS SIZE BEATS `stretch`, so a definite `height`
        # or `max-height` on the card re-opens the split from the other side
        # (#14406 review round 12, measured GREEN):
        #
        #       .pricing-card { height: fit-content }    each card content-height
        #       .pricing-card { max-height: 700px }      capped below the stretch
        #
        # `min-height` is deliberately NOT refused: it raises a floor that
        # `stretch` still applies above, all four cards carry the same one, and
        # refusing it would red a correct page — a named residual, not a silent
        # one.
        capped = [(prop, value, prelude)
                  for prop in ("height", "max-height", "block-size",
                               "max-block-size")
                  for prelude, value in _css_reaching_decls(
                      css, prop, ".pricing-card")]
        assert capped == [], (
            f"a rule that can select `.pricing-card` declares `{capped[0][0]}: "
            f"{capped[0][1] if capped else ''}` ({capped[0][2]!r}), and an "
            "explicit cross size BEATS the grid's `stretch` — so the four boxes "
            "stop sharing one height and `margin-top: auto` aligns each CTA to "
            "its own card's bottom (#13645 item 6), with every other row in "
            "this class still GREEN")
        # ⛔ ONE FLEX LINE, not four (#14406 review round 15). `align-items:
        # stretch` applies PER LINE, so the four cards share a height only while
        # they are on the SAME line — and the grid carries `flex-wrap: wrap`.
        # Measured: each of these wrapped the grid onto two lines and left every
        # row of this class GREEN, with the popular CTA 515-575px below its
        # siblings at 1440x900:
        #
        #     .pricing-card { min-width: 280px }        1192 > 1100
        #     .pricing-grid { gap: 4rem }                1112 > 1100
        #     .pricing-grid { max-width: 900px }          992 >  900
        #
        # The fit is DERIVED from the page's own numbers rather than restated:
        # `flex: 1` gives each card a base size of 0, so a line holds all four
        # while `4 x min-width + 3 x column-gap <= the grid's max-width`. A
        # length this reader cannot convert FAILS rather than skips.
        root = _css_root_px(css)

        def _length(prop: str, compound: str, what: str) -> float:
            found = [value for _prelude, value
                     in _css_reaching_decls(css, prop, compound)]
            assert found, (f"`{compound}` declares no `{prop}`, so the one-line "
                           f"fit cannot be derived ({what})")
            px = _css_length_px(found[-1], root)
            assert px is not None, (
                f"`{compound}` declares `{prop}: {found[-1]}`, which this "
                "reader cannot convert to px (only `px` and `rem` are read, and "
                "`rem` needs an unset root font-size) — the one-line fit is a "
                "FAIL, not a skip")
            return px

        grid_max = _length("max-width", ".pricing-grid", "the line's budget")
        card_min = _length("min-width", ".pricing-card", "a card's floor")
        column_gaps = [value for _prelude, value
                       in _css_reaching_decls(css, "column-gap", ".pricing-grid")]
        if column_gaps:
            gap_px = _css_length_px(column_gaps[-1], root)
            assert gap_px is not None, (
                f"`.pricing-grid` declares `column-gap: {column_gaps[-1]}`, "
                "which this reader cannot convert to px — the one-line fit is a "
                "FAIL, not a skip")
        else:
            gap_px = _length("gap", ".pricing-grid", "the gap between cards")
        needed = 4 * card_min + 3 * gap_px
        assert needed <= grid_max, (
            f"the four pricing cards no longer fit on ONE line at desktop: "
            f"4 x {card_min:g}px + 3 x {gap_px:g}px = {needed:g}px against the "
            f"grid's {grid_max:g}px budget, so `flex-wrap: wrap` puts them on "
            "two lines — `align-items: stretch` stretches PER LINE, so the "
            "boxes on different lines stop sharing a height and "
            "`margin-top: auto` aligns each CTA to its own card's bottom "
            "(#13645 item 6), with every other row in this class still GREEN")
        for prop, allowed in (("display", {"flex", "inline-flex"}),
                              ("flex-direction", {"row", "row-reverse"}),
                              ("flex-flow", {"row", "row-reverse"})):
            bad = [(prelude, value)
                   for prelude, value
                   in _css_reaching_decls(css, prop, ".pricing-grid")
                   if _css_flex_direction(prop, value) not in allowed]
            where = repr(bad[0][0]) if bad else ""
            assert bad == [], (
                f"a rule that can select `.pricing-grid` declares "
                f"`{prop}: {bad[0][1] if bad else ''}` ({where}). ⛔ This row pins "
                "the FLEX IMPLEMENTATION the page ships — not a claim that every "
                "other layout renders differently. A 4-column grid with "
                "`grid-template-columns: repeat(4, minmax(0, 1fr))` really does "
                "stretch its items to one height (measured, #14406 review round "
                "15) and still reds here, because this reader classifies no track "
                "counts; a flex COLUMN does split the baselines, since its cross "
                "axis is horizontal. So `margin-top: auto` stops aligning to a "
                "shared bottom and the three-baseline / 70.2px split of #13645 "
                "item 6 is back with every other row in this class GREEN (#14406 "
                "review rounds 12/15). Migrating to `display: grid` deliberately "
                "means re-cutting this row with its track pins, not widening "
                "`allowed` here.")

    def test_no_rule_gives_the_cta_a_block_end_margin(self):
        """The CTA's block-END margin must stay 0 — `auto` there SHARES the slack.

        `SLACK_PROPS` pins the block-START half, because that is what
        `margin-top: auto` is. But a second `auto` on the block-END side does not
        add to it — the two `auto` margins SPLIT the card's free space, so the
        button is centred in each card's leftover instead of pushed to its
        bottom. Each card's leftover differs (that is the whole premise of item
        6), so the four baselines split again.

        Measured (#14406 review round 11) — each left ALL SEVEN rows of this
        class GREEN while the rendered CTAs split:

            .pricing-card .btn { margin-top: auto; margin-bottom: auto }
            .pricing-card .btn { margin-top: auto; margin-block-end: auto }

        This is the INVERSE of `SLACK_PROPS` on purpose: there `auto` is the
        pinned value and any other reds; here a NON-ZERO block-end margin is the
        refused one. Sharing the loop would have made the same word mean both
        things.

        ⛔ IT REFUSES EVERY NON-ZERO VALUE, not only `auto` (#14406 review round
        15). The docstring above claimed the block-end margin "must stay 0" while
        the assertion only refused `auto`, so `.pricing-card.popular .btn {
        margin-bottom: 0.5rem }` — a plain 8px shove — left this row and all 205
        tests in the module GREEN with the popular CTA measured 8px high. `auto`
        is the dramatic case; any length is the same defect at a smaller scale.
        """
        css = _product_teams_html()
        bad = [(prop, value, prelude)
               for prelude, prop, value in _css_block_end_cascade(
                   css, ".btn", BLOCK_END_MARGIN_PROPS)
               if _css_length_px(_css_block_end(prop, value), 16.0) != 0.0]
        assert bad == [], (
            "a rule that can select `.pricing-card .btn` gives the CTA a "
            f"NON-ZERO block-END margin (`{bad[0][0]}: {bad[0][1]}`, "
            f"{bad[0][2]!r}); any such margin moves the button up off the shared "
            "baseline, and two `auto` margins SPLIT the card's free space "
            "instead of stacking — so the button centres in each card's own "
            "leftover (#13645 item 6), with every other row in this class still "
            "GREEN")

    def test_no_rule_gives_a_card_a_block_end_margin(self):
        """A CARD's block-end margin moves the edge `margin-top: auto` aligns to.

        The rows above pin the card's cross SIZE (`height`/`max-height`), its
        `align-self`, and the CTA's own margins — but a bottom margin on the CARD
        shrinks the stretched border box inside its flex line, so the edge the
        CTA is pushed against moves up by that margin while the boxes keep their
        one height. Measured (#14406 review round 15): `.pricing-card.popular {
        margin-bottom: 1rem }` put the popular CTA at 6399.78 against
        6415.78 for the other three — a 16px split — with all 205 tests in the
        module GREEN.

        The page declares no margin on `.pricing-card`, so refusing every
        non-zero block-end value costs no false RED.
        """
        css = _product_teams_html()
        bad = [(prop, value, prelude)
               for prelude, prop, value in _css_block_end_cascade(
                   css, ".pricing-card", BLOCK_END_MARGIN_PROPS)
               if _css_length_px(_css_block_end(prop, value), 16.0) != 0.0]
        assert bad == [], (
            "a rule that can select `.pricing-card` gives the card a NON-ZERO "
            f"block-END margin (`{bad[0][0]}: {bad[0][1]}`, {bad[0][2]!r}), "
            "which shrinks the stretched box inside its flex line — so the edge "
            "`margin-top: auto` aligns the CTA to moves up by that much and the "
            "four baselines split (#13645 item 6), with every other row in this "
            "class still GREEN")

    def test_nothing_lets_the_cta_take_the_slack_with_its_own_box(self):
        """The CTA must not GROW into the slack its `margin-top: auto` needs.

        ⛔ THE MARGIN FAMILY IS NOT THE ONLY WAY TO TAKE THE SLACK. Every row
        above pins the mechanism (`margin-top: auto` present, nothing else
        touching it) and the card's own box — but a flex ITEM that grows, or
        whose block size is a percentage of its card, absorbs the very space
        the auto margin would have taken: the button stops being pinned to the
        card's bottom edge, and because each card's content is a different
        height (that is #13645 item 6) the four tops re-split.

        Measured at `bc6525269cba`, 1440x900, headless Chromium, the page
        restored byte-identically (`sha256 d74e7c9dd20d`) after every mutant —
        CTA tops, from the shipped page's four equal `6415.78`:

            .pricing-card .btn { flex: 1 }               6345.58 6345.58 6367.05 6415.78
            .pricing-card .btn { flex-grow: 0.5 }        6380.69 6380.69 6391.42 6415.78
            .pricing-card .btn { flex-basis: 100% }      the same 3-baseline split
            .pricing-card .btn { height: 100% }          the same
            .pricing-card .btn { min-block-size: 100% } the same

        — and ALL NINE rows of this class stayed GREEN on every one of them.
        `margin-top: 0` (already RED) produces the same three values, so this is
        the class's own defect signature rather than a tolerance argument. The
        first spelling is the one a maintainer reaches for when they mean "make
        the button fill the leftover": it does, and it breaks the invariant
        this class exists to hold.

        `flex: none` / `flex: initial` / `flex: 0 1 auto` / `flex-grow: 0` /
        `height: 3rem` / `height: 50vh` / `flex-basis: 0%` are measured to leave
        the four tops EQUAL, so the reader permits them — refusing every size
        declaration would red correct pages (`_cta_takes_the_slack_by_sizing`
        carries the full measured table, both directions).
        """
        offenders = _cta_takes_the_slack_by_sizing(_product_teams_html())
        assert offenders == [], (
            "a rule that can select `.pricing-card .btn` lets the CTA's own box "
            f"take the card's leftover space (`{offenders[0][1]}: "
            f"{offenders[0][2]}`, {offenders[0][0]!r}). Growth and a "
            "container-relative block size starve the pinned `margin-top: "
            "auto` of the free space it absorbs, so the button is no longer "
            "pushed to its card's bottom edge — and each card's content is a "
            "different height, so the four baselines split to three, 70.2px "
            "apart (#13645 item 6), with every other row in this class still "
            "GREEN (#14406 review round 17, measured: `.pricing-card .btn "
            "{ flex: 1 }` reads 6345.58 / 6345.58 / 6367.05 / 6415.78)")

    def test_no_other_rule_takes_the_ctas_slack(self):
        """The card CTA's ONLY vertical slack is the pinned `margin-top: auto`.

        `_css_rules_with_scope` matches a prelude that NAMES the exact selector
        and returns them in source order, so `[-1]` cannot see a rule that
        reaches the same element through a DIFFERENT selector. Measured
        (#14406 review round 3): appending `.pricing-grid .pricing-card .btn
        { margin-top: 0 }` — (0,3,0) against the pinned (0,2,0), so it wins at
        any source order — left all three rows of this class GREEN while the
        rendered CTAs split again.

        The first version of THIS row only looked at a prelude containing both
        `.pricing-card` and `.btn`, only at `margin-top`/`margin`, and only
        outside `@media`. Three measured false PASSes followed (#14406 review
        round 4) — the same 3-baseline / 70.2px signature with every row green:

        * **G1 — ancestor selectors.** `.pricing-grid .btn { margin-top: 0 }`
          (0,2,0) is tied with the pinned rule and later in source, so it wins;
          the filter required `.pricing-card` in the same prelude and never read
          it.
        * **G2 — the logical property.** `margin-block-start: 0`, including
          INSIDE the pinned rule's own body, where `_effective_decl(body,
          "margin-top")` returns `None`.
        * **G3 — `min-width` media.** `@media (min-width: 1024px) { … }` applies
          at 1440x900; the `inside_media` skip threw it away. A `max-width`
          hatch is the opposite case (the cards stack there by design) and is
          still skipped.

        So the reach here is `_css_reaching_decls`'s — rightmost-compound
        matching (ancestors included), logical aliases and shorthands, and
        `min-width` media — and the invariant is the one the page needs: every
        rule that can select the CTA must either not touch the slack or say
        `auto`. It does not re-implement the cascade; it fails closed on any
        disagreement, because specificity — not source order — picks the winner
        and a reader of source order cannot.
        """
        css = _product_teams_html()
        competing = []
        for prop in self.SLACK_PROPS:
            for prelude, value in _css_reaching_decls(css, prop, ".btn"):
                # `!important` is a priority marker, not part of the value: an
                # `margin-top: auto !important` is the PINNED intent, stronger,
                # and reading it as a competitor reded a correct page.
                bare = _without_important(value)
                # The block-start side is the first token for the shorthands,
                # read by the SAME helper the positive rows use.
                start_side = _css_block_start(prop, bare)
                if start_side == "auto":
                    continue
                competing.append((prelude, prop, value))
        assert competing == [], (
            "another rule sets the pricing CTA's vertical slack, and can starve "
            f"the pinned `margin-top: auto`: {competing!r}. Specificity, not "
            "source order, decides which one a browser applies, so "
            "`.pricing-grid .pricing-card .btn { margin-top: 0 }` (0,3,0), "
            "`.pricing-grid .btn { margin-top: 0 }` (0,2,0, later in source), "
            "`margin-block-start: 0` (the logical alias) and the same inside a "
            "`@media (min-width: …)` block all out-rank or by-pass the pinned "
            "rule and split the four baselines to three, 70.2px apart "
            "(#13645 item 6)")

    def test_the_popular_cards_thicker_border_does_not_move_its_cta(self):
        """The 2px border eats ONE more px than its siblings' 1px — measured.

        With the flex column in place the popular card's CTA landed 1px high
        (744.78 against 745.78, at 1440x900): `box-sizing: border-box` means the
        2px border removes one more pixel of content box at the bottom than a
        1px border does, and `margin-top: auto` aligns to that inner edge. The
        `padding-bottom` compensation is the thing that makes the four y values
        EQUAL rather than 1px apart, so a reader who deletes it as a mystery
        number must red here instead of shipping an alignment that is "close".
        """
        css = _product_teams_html()
        popular = [body for body, inside_media
                   in _css_rules_with_scope(css, ".pricing-card.popular")
                   if not inside_media]
        assert popular, "`.pricing-card.popular` declares no rule"
        # ⛔ NO LITERAL HERE ANY MORE (#14406 review round 12). Round 11 added the
        # DERIVED half below and left this assert in place, so the row still
        # pinned the constant: a relation-preserving edit — `padding: 2rem`
        # together with `calc(2rem - 1px)` — is geometrically identical (the
        # popular card's inset stays the base card's less the extra border
        # pixel) and still red here, with the message saying it "does not
        # compensate for its 2px border" when it does. The derivation owns the
        # row: it grades the cascade WINNER against `expected_pad`, and
        # `extra_px > 0` keeps a stale-direction edit a FAIL rather than a skip.
        # Same cascade caveat as the flex row above, but this one is answered by
        # RESOLVING the cascade instead of demanding agreement: `.pricing-card`'s
        # `padding` shorthand (0,1,0) and this rule (0,2,0) disagree ON PURPOSE,
        # so "every rule that can select it must agree" would red a correct page
        # — while `.pricing-grid .pricing-card { padding-bottom: 1.75rem }`
        # (0,2,0) or `#pricing-section .pricing-card.popular { padding: 1.75rem }`
        # (1,2,0) override the compensation with every row in this class GREEN
        # (#14406 review round 5, finding 1 — the same 1px defect, measured).
        # `_css_block_end_cascade` reads the whole `padding` FAMILY (both
        # longhands, both shorthands, block-end side) and returns it in cascade
        # order, so the row asserts what a browser actually applies.
        # Measured mutants that must be caught here (each left every row GREEN
        # before the round-6 specificity fix, #14406):
        #   section .pricing-card.popular { padding-bottom: 1.75rem }   (0,2,1)
        #   div.pricing-card.popular     { padding-bottom: 1.75rem }   (0,2,1)
        #   .pricing-grid .pricing-card  { padding-bottom: 1.75rem }   (0,2,0, later)
        #   #pricing-section .pricing-card.popular { padding: 1.75rem } (1,2,0)
        #   .pricing-card.popular { padding: 1.75rem }   (shorthand in the same block)
        cascade = _css_block_end_cascade(css, ".pricing-card")
        assert cascade, "no rule sets the pricing card's block-END inset"
        prelude, prop, winner = cascade[-1]

        # ⛔ THE LITERAL IS DERIVED, NOT REMEMBERED (#14406 review round 11).
        # Pinning `calc(1.75rem - 1px)` as a string made the row a claim about a
        # constant instead of about the RELATION the constant exists to encode:
        # the popular card's bottom inset is the base card's, less the extra
        # border width its 2px border eats. Measured — with the literal pinned
        # and ALL SEVEN rows GREEN:
        #
        #   .pricing-card { padding: 2rem }            popular CTA 4px LOW
        #   .pricing-card.popular { border: 3px … }     popular CTA 1px HIGH
        #
        # Both are "a mystery number left behind by an edit above it", which is
        # the failure the pin was written to prevent. Read the two numbers the
        # `1px` is the DIFFERENCE OF, and require the winner to be their relation.
        # The relation lives in `_expected_popular_block_end_pad` (module level)
        # because FOUR sibling rows need the same value and each had its own copy
        # of the literal (#14406 review round 12).
        #
        # ⛔ AND EVERY OPERAND IS A PROPERTY'S OWNER, NOT A SELECTOR'S LAST MATCH
        # (#14406 review round 13). `_css_rules_with_scope(...)[-1]` is the last
        # rule whose PRELUDE is the selector, so a later `.pricing-card { … }`
        # rule that repeats none of `border`/`padding` shadows the rule that
        # does — measured on the tree at `f091cc8dedb2`: `.pricing-card {
        # border-color: #fff }` (an unrelated card edit, CTA baselines unmoved)
        # red this row with "declares `border: `, whose bottom width cannot be
        # read", and `.pricing-card { padding: 2rem }` with its compensation
        # derived from a padding the page no longer has.
        base_border = _border_end_width_cascade(css, ".pricing-card",
                                               skip_popular=True)
        assert base_border is not None, (
            "no rule the sibling cards obey gives `.pricing-card` a block-END "
            "border width, so the compensation relation has no base operand")
        popular_border = _border_end_width_cascade(
            css, ".pricing-card.popular", skip_popular=False)
        assert popular_border is not None, (
            "no rule reaching `.pricing-card.popular` gives it a block-END "
            "border width, so its thicker border — the reason the "
            "compensation exists — is gone")
        # ⛔ THE WHOLE `padding` FAMILY, through the ONE helper
        # (#14406 review round 16). Reading only the shorthand here red a
        # `padding-block`/`padding-inline` split whose geometry is identical —
        # the exact false RED `_expected_popular_block_end_pad` was fixed for,
        # left behind as a fifth copy of the derivation it documents.
        base_pad = _base_block_end_pad(css)
        assert base_pad is not None, (
            "no rule naming `.pricing-card` declares a block-END padding as "
            "`padding`, `padding-block`, `padding-bottom` or "
            "`padding-block-end`, so the base inset the compensation is "
            "derived from is gone")
        extra_px = _popular_border_extra_px(css)
        assert extra_px > 0, (
            "the popular card's bottom border is not WIDER than its siblings', "
            f"so the 1px compensation this row exists for is stale (measured "
            f"{extra_px:g}px, expected a positive difference)")
        popular_border_px = _border_end_width_cascade(
            css, ".pricing-card.popular", skip_popular=False)
        base_border_px = _border_end_width_cascade(
            css, ".pricing-card", skip_popular=True)
        assert popular_border_px is not None and base_border_px is not None, (
            "the compensation relation's border operands cannot be read from "
            "this page, so its message would quote a number it does not have")
        expected_pad = _expected_popular_block_end_pad(css)

        assert winner == expected_pad, (
            f"the declaration that WINS the popular card's bottom inset is "
            f"`{prop}: {winner}` ({prelude!r}), not the compensation that makes "
            f"the four y values EQUAL. Its {popular_border_px:g}px border eats "
            f"{extra_px:g}px more of the content box at the bottom than its "
            f"siblings' {base_border_px:g}px, so its inset must be the base "
            f"card's block-end padding ({base_pad!r}) less that difference: "
            f"`{expected_pad}`. Measured at 1440x900, a mismatch "
            "moves this CTA off the shared baseline (744.78 vs 745.78 is ONE "
            "pixel — #13645 item 6). The losing declarations, weakest first: "
            f"{cascade[:-1]!r}")

    def test_no_rule_resets_the_cards_declarations_with_all(self):
        """`all` is the shorthand for EVERY property, so no value of it agrees.

        `all: revert` on `.pricing-card` (or `all: unset`, or `all: initial`)
        resets `display` to the browser default `block` and `flex-direction` to
        `row`, and on `.btn` it resets `margin-top: auto` to `0` — the exact
        3-baseline split of #13645 item 6, from ONE declaration. It is not a
        property the flex row or the slack row queries, so neither could see it:
        measured (#14406 review round 8), `all: revert` appended to the
        `.pricing-card` rule left all four rows of this class GREEN.

        There is no value to agree with, which is why this is a NEGATIVE row
        rather than another entry in the families above: ANY declaration of
        `all` that reaches either subject displaces whatever the pinned
        declarations say.
        """
        css = _product_teams_html()
        for compound in (".pricing-card", ".btn"):
            resets = _css_reaching_decls(css, "all", compound)
            assert resets == [], (
                f"a rule that can select `{compound}` declares the `all` "
                f"shorthand (`{resets[0][0]!r}` → `all: {resets[0][1]}`), which "
                "resets every property in the pinned CSS — `all: revert` on "
                "the card drops `display: flex`/`flex-direction: column`, and "
                "on the CTA it drops `margin-top: auto` — with every other row "
                "in this class GREEN (#14406 review round 8, measured)")

    def test_no_rule_reorders_the_cta_inside_its_card(self):
        """`order` moves the CTA without moving the markup the row above reads.

        `test_the_cta_is_the_last_child_in_both_the_static_cards_and_the_render`
        asserts the CTA is the last child in the DOM of both render paths —
        which is the invariant `margin-top: auto` depends on, since auto margins
        align the LAST item. `order: -1` on `.pricing-card .btn` makes the
        anchor the FIRST flex item while the markup still ends on it, so the
        last-child row stays green and the rendered CTA sits above `.features`. 
        Measured (#14406 review round 8): `order: -1` appended to the CTA rule
        left all four rows of this class GREEN.

        `order`'s initial value is `0`, so the invariant is "every reaching rule
        either does not declare it or declares `0`" — the same AGREEMENT shape
        the slack row uses.

        ⛔ THE SUBJECT IS EVERY DIRECT CHILD OF THE CARD, NOT THE CTA ALONE
        (#14406 review round 12, measured). `order` reorders SIBLINGS, so the
        button stops being the item `margin-top: auto` aligns just as surely
        when a SIBLING is pushed past it — and those rules name a selector this
        row never asked about. Each of these restored the three-baseline split
        with ALL NINE rows of this class GREEN:

            .pricing-card .features { order: 1 }   the CTA is no longer last
            .pricing-card .price    { order: 1 }   same, one child up
            .pricing-card div       { order: 1 }   the same, by TAG
            .pricing-card > div     { order: 1 }   the same, child combinator
            #pricing-grid div       { order: 1 }   the same, from the grid

        The subject set therefore comes from the MARKUP's containment
        (`_card_direct_children`), and each child is matched both by every class
        it carries and by its ELEMENT — which is what makes the two TAG-selector
        mutants above visible. A rule that reorders a grandchild (`.feature`
        inside `.features`) cannot move the CTA and is not red here, which is
        why the set is the card's DIRECT children and not its whole subtree.
        """
        css = _product_teams_html()
        reordering = [(p, v) for p, v in _css_reaching_decls(css, "order", ".btn")
                      if v.strip() not in ("0", "")]
        assert reordering == [], (
            f"a rule that can select the CTA declares `order: "
            f"{reordering[0][1]}` ({reordering[0][0]!r}), which moves the button "
            "inside its card without changing the markup — the last-child row "
            "reads the DOM and stays GREEN while the rendered CTA is no longer "
            "the item `margin-top: auto` aligns (#14406 review round 8, "
            "measured: `order: -1` passed every row)")

        children = _card_direct_children(
            _static_pricing_grid(_served(_product_teams_html())))
        assert len(children) >= 8, (
            f"only {len(children)} direct children were read out of the four "
            "pricing cards — `_card_direct_children` has lost the markup shape "
            "it parses, and the sibling sweep below would pass over nothing")
        moved = []
        for prelude, body, _condition in _css_desktop_rules(css):
            order = _effective_decl(body, "order")
            if order is None or order.strip().lower() in ("0", ""):
                continue
            for part in (candidate.strip() for candidate in prelude.split(",")):
                rightmost = _css_rightmost_compound(part)
                if any(
                        _css_rightmost_selects(rightmost, f".{name}")
                        for _tag, attrs in children
                        for name in attrs.get("class", "").split()) or any(
                        _css_compound_matches_element(rightmost, child)
                        for child in children):
                    moved.append((part, order))
                    break
        assert moved == [], (
            f"a rule declares `order: {moved[0][1]}` ({moved[0][0]!r}) on a "
            "child of a pricing card, so the CTA is no longer the LAST item its "
            "card's flex column lays out — `margin-top: auto` then aligns the "
            "button against a taller sibling instead of the card's bottom edge, "
            "and the four baselines split to three, 70.2px apart (#13645 item "
            "6) with every other row in this class GREEN (#14406 review round "
            "12, measured with `.pricing-card .features { order: 1 }` and with "
            "the same rule written as a TAG selector)")

    def test_no_card_carries_an_inline_style(self):
        """An inline `style` out-ranks every rule in the sheet, and no reader sees it.

        A declaration in a `style="…"` attribute wins over ANY selector — it
        sits above the author-origin cascade that every row in this class
        resolves — and it is not in the `<style>` block the readers parse, so
        `<div class="pricing-card" style="display:block">` splits the CTAs back
        to three baselines with every row here GREEN. Measured (#14406 review
        round 8): that one attribute left all four rows of this class passing.

        The row is scoped to the pricing grid and the `renderPricing()` template
        (the two places a card or its CTA is authored), so the page's unrelated
        inline styles stay legal — there are two of them today, both outside the
        grid, and a whole-page version of this row would red them.

        ⛔ THE ATTRIBUTE IS READ BY `_inline_style_attributes`, NOT BY THE LITERAL
        `style="`. Round 8 wrote the row as `re.findall(r'style="[^"]*"', …)`,
        which is ONE of the four spellings an HTML parser accepts: a
        single-quoted `style='display:block'`, a spaced `style =
        "display:block"` and the unquoted `style=display:block` all out-rank the
        pinned CSS exactly as the double-quoted form does — and all left every
        row of this class GREEN (#14406 review round 9, measured against the
        pre-fix reader).
        """
        served = _served(_product_teams_html())
        grid = _static_pricing_grid(served)
        assert grid, "the pricing grid slice is empty — this row would pass vacuously"
        inline = _inline_style_attributes(grid)
        assert inline == [], (
            f"a pricing card or its CTA carries an inline `style` attribute "
            f"({inline[0]!r}). An inline declaration out-ranks every rule in "
            "the sheet, so the pinned `display: flex` / `flex-direction: "
            "column` / `margin-top: auto` no longer apply and the four "
            "baselines split — while this file's CSS rows, which parse the "
            "`<style>` block, cannot see it at all (#14406 review round 8, "
            "measured)")
        template = re.search(r"return `<div class=\"pricing-card[^`]*`",
                             _product_teams_html(), re.S)
        assert template, "renderPricing()'s card template changed shape — re-point this gate"
        template_inline = _inline_style_attributes(template.group(0))
        assert template_inline == [], (
            "renderPricing()'s template builds a card or CTA with an inline "
            f"`style` attribute ({template_inline[0]!r}), which out-ranks the "
            "pinned card CSS on the path a real visitor sees while the static "
            "cards stay aligned (#14406 review round 8)")

    def test_the_cta_is_the_last_child_in_both_the_static_cards_and_the_render(self):
        """`margin-top: auto` aligns the LAST item — so nothing may follow the CTA.

        The static cards are the no-JS fallback and `renderPricing()` replaces
        them on load, so BOTH shapes have to end on the anchor; a link or a
        footnote added after it would take the slack and the buttons would
        misalign on one path only (JS visitors) or the other (crawlers).
        """
        chunks = _static_pricing_grid(_served(_product_teams_html())).split(
            '<div class="pricing-card')[1:]
        assert len(chunks) == 4, (
            "the split found " f"{len(chunks)} card chunk(s) for four cards — it "
            "needs the opening tag to read `<div class=\"pricing-card`, so a "
            "card written `<div id=\"card-free\" class=\"pricing-card\">` is "
            "absorbed into the previous chunk and its CTA is never checked. The "
            "siblings in this class assert their rule count for the same reason; "
            "this row iterated a shrinking population silently")
        for chunk in chunks:
            # Cut the chunk at the CARD's own balanced close: the slice handed in
            # ends at the usage paragraph, so the last chunk would otherwise
            # carry `<p class="pricing-usage">` into the tail and the row would
            # pass by looking past it.
            depth, body = 1, chunk
            for tag in re.finditer(r"</?div\b[^>]*>", chunk):
                depth += -1 if tag.group(0).startswith("</") else 1
                if depth == 0:
                    body = chunk[:tag.end()]
                    break
            assert depth == 0, "a static pricing card has an unbalanced <div>"
            cta = re.search(r'<a href="[^"]*" class="btn[^"]*">.*?</a>', body, re.S)
            assert cta, "a static pricing card has no CTA anchor"
            tail = body[body.index("</a>", cta.start()) + 4:]
            assert re.fullmatch(r"(?:\s*</div>)+\s*", tail), (
                "a static pricing card carries markup after its CTA "
                f"({tail.strip()!r}). `.pricing-card .btn {{ margin-top: auto }}` "
                "aligns the last child, so anything below the button steals the "
                "slack and the four baselines split again (#13645 item 6)")
        template = re.search(r"return `<div class=\"pricing-card[^`]*`", _product_teams_html(), re.S)
        assert template, "renderPricing()'s card template changed shape — re-point this gate"
        assert re.search(r'</a>\s*</div>`$', template.group(0)), (
            "renderPricing()'s template does not end on the CTA anchor, so the "
            "JS-rendered cards (what a real visitor sees) would take the slack "
            "somewhere other than the button while the static cards stay "
            "aligned (#13645 item 6)")


class TestCssDesktopReachAndSpecificity:
    """The readers `TestProductTeamsPricingCardCtaBaseline` stands on.

    All are guards-of-the-guard: a cascade row is only as good as the
    specificity counter, the media-reach predicate and the markup readers
    behind it, and each of those had a measured FALSE PASS that no page-level
    row could see, because the mutant lives in the guard's own arithmetic
    (#14406 review rounds 6, 8 and 9). The rows below mutate the PAGE TEXT the
    way an editor would — appending a real rule, or an attribute in another
    quoting spelling — and assert the reader SEES it.
    """

    #: The standard's columns (MDN, Specificity: the CLASS column holds classes,
    #: attribute selectors and PSEUDO-CLASSES; the TYPE column holds type
    #: selectors and PSEUDO-ELEMENTS). Before the round-6 fix the counter
    #: subtracted `pseudo + type selectors` from the class count, so every type
    #: selector CANCELLED a class — `p::before` even went NEGATIVE.
    SPECIFICITY_CASES = (
        ("div.pricing-card", (0, 1, 1)),
        ("div.pricing-card.popular", (0, 2, 1)),
        ("section .pricing-card.popular", (0, 2, 1)),
        (".pricing-card.popular", (0, 2, 0)),
        (".pricing-grid .pricing-card", (0, 2, 0)),
        ("p::before", (0, 0, 2)),
        (".pricing-card:hover", (0, 2, 0)),
        ("#pricing-section .pricing-card.popular:hover", (1, 3, 0)),
        ("[data-tier='pro'].pricing-card", (0, 2, 0)),
        ("*", (0, 0, 0)),
    )

    def test_specificity_puts_type_selectors_in_the_type_column(self):
        for selector, expected in self.SPECIFICITY_CASES:
            assert _css_specificity(selector) == expected, (
                f"`{selector}` is {expected} by the standard's columns; "
                f"`_css_specificity` says {_css_specificity(selector)}. A type "
                "selector subtracted from the CLASS count ranks a "
                "type-qualified override BELOW the rule it beats in a browser, "
                "which is a false PASS in every row that resolves a cascade "
                "(#14406 review round 6, finding 1)")
        for unmodellable in (":is(.a, .b)", ":where(.a)", ":not(.a)", ":has(a)"):
            assert _css_specificity(unmodellable) is None, (
                f"`{unmodellable}` changes specificity in ways this counter "
                "does not model — it must return None so the one caller ranks "
                "it maximally specific (fail closed), never guess")

    @staticmethod
    def _page_with(rule: str) -> str:
        """The page with `rule` appended inside its FIRST `<style>` block.

        Inserted rather than concatenated: appended to the whole document the
        rule's prelude would carry the closing markup, and
        `_css_block_end_cascade` skips a prelude containing `<` (it has to, or
        the document head's hex colours count as ids and the `*` reset wins the
        cascade). That guard is correct; the mutant has to look like a real edit.
        """
        page = _product_teams_html()
        assert "</style>" in page, "the page has no <style> block to extend"
        return page.replace("</style>", f"\n{rule}\n</style>", 1)

    def test_a_later_rule_that_declares_another_property_does_not_shadow_the_pin(self):
        """The pinned reads resolve the PROPERTY's owner, not the last selector match.

        `_css_rules_with_scope(...)[-1]` is the last rule whose PRELUDE is the
        selector, so a later rule for the same selector that says nothing about
        the property being pinned hides the rule that does — and the row reports
        the property as missing rather than as shadowed.

        Measured (#14406 review round 13) at `f091cc8dedb2`, each mutant applied
        to the tracked page and restored byte-identically:

            .pricing-card { align-self: stretch }        FLEX ROW RED "is not a flex container"
            .pricing-card { border-color: #fff }         FLEX ROW RED "is not a flex container"
            .pricing-card .btn { border-radius: 2rem }   FLEX ROW RED "does not take the leftover height"

        None of those moves a CTA — the card is a flex container, its CTA does
        take the slack — and the first is the edit round 12 fixed, measured
        written INSIDE the existing block; as its own rule it still red. A
        guard that reds the page it ships is the one a maintainer deletes.

        The LAST-WINS half is what keeps this from weakening the guard:
        `_effective_decl` reports the winning declaration in a body, and this
        test's non-vacuity arm proves an appended `.pricing-card { display:
        block }` still becomes the owner — so the override shape the rows exist
        for is untouched.
        """
        pairs = ((".pricing-card", ("display",)),
                 (".pricing-card", FLEX_DIRECTION_PROPS),
                 (".pricing-card .btn", ("margin-top", "margin-block-start")),
                 (".pricing-card", ("border",)))

        def owner(css: str, selector: str, props: tuple[str, ...]):
            # ⛔ THE FAMILY (#14406 review round 16): `flex-direction` and
            # `margin-top` are each ONE spelling of the thing this row makes
            # non-vacuous, so requiring the longhand made the whole row red on
            # a page written with `flex-flow: column nowrap` or
            # `margin-block-start: auto` — both pixel-identical (measured at
            # 1440x900: CTA tops 745.78 x4).
            for prop in props:
                body = _css_last_declaring_body(css, selector, prop)
                if body is not None:
                    return _effective_decl(body, prop)
            return None

        pristine = {(sel, props): owner(_product_teams_html(), sel, props)
                    for sel, props in pairs}
        missing = [k for k, v in pristine.items() if v is None]
        assert missing == [], (
            f"the unmutated page declares nothing for {missing!r}, so the rows "
            "below cannot tell a caught shadow from a page that never had the "
            "declaration")

        for mutant, shadow_selector, hidden in (
                (".pricing-card { align-self: stretch }",
                 ".pricing-card", ("display",)),
                (".pricing-card { border-color: #fff }",
                 ".pricing-card", ("display",)),
                (".pricing-card .btn { border-radius: 2rem }",
                 ".pricing-card .btn", ("margin-top", "margin-block-start"))):
            mutated = self._page_with(mutant)
            # The mutant really does shadow the pre-fix `[-1]` read — otherwise
            # this arm would pass on the reader it exists to replace.
            shadow = [body for body, inside_media
                      in _css_rules_with_scope(mutated, shadow_selector)
                      if not inside_media][-1]
            assert all(_effective_decl(shadow, prop) is None
                       for prop in hidden), (
                f"`{mutant}` does not shadow the last-selector read for "
                f"`{hidden}` (its body still declares it), so this arm is "
                "VACUOUS — pick a mutant that actually hides the pinned "
                "declaration")
            for selector, prop in pairs:
                assert owner(mutated, selector, prop) == pristine[(selector, prop)], (
                    f"appending `{mutant}` changed the resolved `{prop}` on "
                    f"`{selector}` from {pristine[(selector, prop)]!r} to "
                    f"{owner(mutated, selector, prop)!r}. The rule does not "
                    "touch "
                    f"`{prop}`, so the page still renders identically — the "
                    "reader resolved the LAST selector match instead of the "
                    "last rule that DECLARES the property (#14406 review "
                    "round 13, measured)")

        # Non-vacuity: an appended rule that DOES declare the property is still
        # its owner, so the override shape the pinned rows exist for still reds.
        assert owner(self._page_with(".pricing-card { display: block }"),
                     ".pricing-card", ("display",)) == "block", (
            "an appended `.pricing-card { display: block }` is no longer read "
            "as the owner of `display`, so the row that pins the card's flex "
            "container is blind to the ordinary override (measured RED before "
            "and after this reader)")
        assert owner(self._page_with(".pricing-card { border: 2px solid #f00 }"),
                     ".pricing-card", ("border",)) == "2px solid #f00", (
            "an appended `.pricing-card { border: … }` is no longer read as the "
            "owner of `border`, so the popular card's compensation relation "
            "would be derived from the border the page no longer has")

    def test_the_growth_reader_separates_a_growing_cta_from_a_sized_one(self):
        """`_sizing_takes_the_slack` must red GROWTH and permit a definite size.

        Unlike every other reader in this class this one has to decide between
        two spellings that look alike — `height: 100%` (the size is derived from
        the card, so the CTA's top moves to the card's content bottom and the
        baselines split) and `height: 3rem` (the same box on all four cards, so
        the tops stay equal). A reader that refused both would red a correct
        page, and one that refused neither is the fail-open found at
        `bc6525269cba`; `TestProductTeamsPricingCardCtaBaseline` carries the
        browser measurement, and this row pins the DECISION on the page text.

        Both arms are non-vacuous by construction: every refused mutant is
        first shown to really reach the CTA through `_css_reaching_decls` with
        the property it declares, and every permitted one is shown to reach it
        too — so "not an offender" cannot pass on a rule the reader never saw.
        """
        reaching = (
            ".pricing-card .btn { %s }",
            ".pricing-grid .btn { %s }",
            ".pricing-card > .btn { %s }",
        )
        cases = (
            # (declaration, refused?) — measured at 1440x900: the refused rows
            # are the three-baseline split, the permitted rows leave all four
            # CTA tops equal (see `_cta_takes_the_slack_by_sizing`).
            (("flex: 1", True), ("flex: auto", True),
             ("flex: 1 1 0%", True), ("flex: 0 0 100%", True),
             ("flex-grow: 1", True), ("flex-grow: 0.5", True),
             ("flex-basis: 100%", True), ("flex-basis: 50%", True),
             ("height: 100%", True), ("height: calc(100% - 2rem)", True),
             ("block-size: 100%", True), ("min-height: 100%", True),
             ("min-block-size: 100%", True),
             ("flex: none", False), ("flex: initial", False),
             ("flex: 0 1 auto", False), ("flex-grow: 0", False),
             ("flex-basis: auto", False), ("flex-basis: 0%", False),
             ("flex: 0 1 0%", False), ("height: 3rem", False),
             ("height: 50vh", False), ("min-height: 0%", False),
             ("max-height: 100%", False), ("inline-size: 100%", False))
        )
        for template in reaching:
            for declaration, refused in cases:
                prop, _sep, value = declaration.partition(":")
                rule = template % declaration
                mutated = self._page_with(rule)
                seen = _css_reaching_decls(mutated, prop.strip(), ".btn")
                assert (rule.strip("{} ").split("{")[0].strip(), value.strip()) in seen, (
                    f"`{rule}` does not reach `.btn` through "
                    f"`_css_reaching_decls` (got {seen!r}), so this arm would "
                    "be VACUOUS — the reader could not see the declaration "
                    "under any policy")
                offenders = _cta_takes_the_slack_by_sizing(mutated)
                got = any(offender_prop == prop.strip()
                          and _without_important(offender_value) == value.strip()
                          for _prelude, offender_prop, offender_value in offenders)
                assert got is refused, (
                    f"`{rule}` was expected to be "
                    f"{'REFUSED' if refused else 'PERMITTED'} by "
                    f"`_cta_takes_the_slack_by_sizing`, and it was "
                    f"{'permitted' if refused else 'refused'} "
                    f"(offenders={offenders!r}). "
                    + ("A growing CTA, or one sized as a percentage of its "
                       "card, takes the space `margin-top: auto` needs and "
                       "re-splits the four baselines (#14406 review round "
                       "17, measured)." if refused else
                       "This spelling leaves the four CTA tops EQUAL "
                       "(measured), so refusing it reds a correct page — a "
                       "guard a maintainer deletes."))
        # The shipped page is not an offender, so the row it backs is a real
        # invariant rather than one the page already violates.
        assert _cta_takes_the_slack_by_sizing(_product_teams_html()) == []

    def test_the_owner_reader_sees_a_desktop_media_block_and_skips_a_narrow_hatch(self):
        """A media CONDITION, not `inside_media`, decides what owns the property.

        `_css_last_declaring_body` kept only rules at TOP LEVEL, so an override
        written inside `@media (min-width: 1200px) { … }` — which a browser
        applies at the 1440x900 viewport every measurement in this PR is taken
        at — was invisible to it. Measured (#14406 review round 16) at
        `2d1958c1b9bb`, each mutant appended inside the page's `<style>` (after
        every base rule, the position where it really wins the cascade), the
        nine rows of `TestProductTeamsPricingCardCtaBaseline` run, the page
        restored byte-identically (`sha256 d74e7c9dd20d`):

            @media (min-width: 1200px) { .pricing-card.popular { border: 3px solid #f00 } }
            @media (min-width: 1200px) { .pricing-card { padding: 2rem } }
                -> 9 passed / 9 passed (fail-OPEN) before this fix, 1 failed / 1 failed after.

        The third shape that looked like the same defect — a media
        `padding-bottom: 1.75rem` on the popular card — was ALREADY RED at that
        head: the popular row resolves the `padding` family through
        `_css_block_end_cascade`, which is media-aware since round 5. Only the
        reads that go through this helper were blind.

        This is round 6's finding one reader out: that round fixed the same
        `max-` prefix confusion in `_css_desktop_rules` and left this reader
        re-deciding `inside_media` for itself.
        """
        # The reader IS the helper the rest of the family derives from, so a
        # change here moves `_popular_border_extra_px` and
        # `_expected_popular_block_end_pad` with it — asserted on the helper,
        # not only on the body text.
        assert _popular_border_extra_px(_product_teams_html()) == 1.0, (
            "the shipped page's popular card eats exactly one extra border "
            f"pixel; the helper derives {_popular_border_extra_px(_product_teams_html()):g}")

        desktop_media = self._page_with(
            "@media (min-width: 1200px) { .pricing-card.popular { border: 3px solid #f00 } }")
        assert _popular_border_extra_px(desktop_media) == 2.0, (
            "a `min-width: 1200px` block applies at the measured 1440x900 "
            "viewport, so the popular card's border is 3px there and the "
            f"compensation must be 2px; the reader derives "
            f"{_popular_border_extra_px(desktop_media):g}. Before this fix it "
            "derived 1px and every row stayed GREEN over a page whose CTA was "
            "off the baseline (#14406 review round 16, measured)")
        # ⛔ THE BASE INSET IS DERIVED FROM THE PAGE UNDER TEST, never spelled
        # as a literal (#14406 review round 16, measured): this expectation read
        # `calc(1.75rem - 2px)`, so the relation-preserving edit
        # (`padding: 2rem` together with `padding-bottom: calc(2rem - 1px)` —
        # the same CTA y values) red it with "the relation the row pins is
        # stale" about a page whose relation is exactly right. Same class as
        # the four literals round 12 removed from its sibling rows.
        base_inset = _base_block_end_pad(_product_teams_html())
        assert base_inset is not None, (
            "the shipped page declares no block-END base inset, so this row "
            "has no operand to derive its expectation from")
        assert _expected_popular_block_end_pad(desktop_media) == (
                f"calc({base_inset} - 2px)"), (
            "the derived inset still subtracts the base 1px while the browser "
            "applies a 3px border, so the relation the row pins is stale: "
            f"{_expected_popular_block_end_pad(desktop_media)!r}")

        # …and the base inset, not only the border: this is the second measured
        # fail-open, and it goes through the OTHER helper (`_css_block_end` on
        # `padding`) so pinning the border alone would leave it open.
        base_pad_media = self._page_with(
            "@media (min-width: 1200px) { .pricing-card { padding: 2rem } }")
        assert _expected_popular_block_end_pad(base_pad_media) == "calc(2rem - 1px)", (
            "a `min-width: 1200px` block that moves the base card's inset to "
            "2rem applies at 1440, so the popular card's compensation must be "
            f"derived from 2rem; the helper derives "
            f"{_expected_popular_block_end_pad(base_pad_media)!r} (measured "
            "GREEN over the split baseline before this fix, #14406 review "
            "round 16)")

        # A `max-width` at or ABOVE the desktop floor applies at 1440 too —
        # inclusive, and 1440 is the measured viewport (round 6, finding 4).
        at_floor = self._page_with(
            "@media (max-width: 1440px) { .pricing-card.popular { border: 3px solid #f00 } }")
        assert _popular_border_extra_px(at_floor) == 2.0, (
            "`max-width: 1440px` INCLUDES 1440, so this block owns the border "
            "at the measured viewport; reading it as a narrow hatch is the "
            "round-6 defect in this reader")

        # …and the direction that must NOT move: the page's own viewport
        # hatches cannot apply at 1440, so they cannot become the owner. A
        # reader that reds a correct phone layout is the guard a maintainer
        # deletes.
        for hatch, expected in (
                ("@media (max-width: 760px) { .pricing-card.popular { border: 9px solid #f00 } }", 1.0),
                ("@media (max-height: 820px) { .pricing-card.popular { border: 9px solid #f00 } }", 1.0)):
            mutated = self._page_with(hatch)
            assert _popular_border_extra_px(mutated) == expected, (
                f"`{hatch}` cannot apply at 1440x900, so the shipped "
                f"compensation is still correct there; the reader derives "
                f"{_popular_border_extra_px(mutated):g} instead of {expected:g} "
                " — a FALSE RED on a page that renders as designed")

    def test_a_type_qualified_override_of_the_popular_cards_inset_wins_the_cascade(self):
        """`section .pricing-card.popular` is (0,2,1) — a browser applies it.

        Both mutants below restore the owner-reported 1px defect (the popular
        CTA back to 744.78 against 745.78) and BOTH left every row of
        `TestProductTeamsPricingCardCtaBaseline` GREEN before the round-6
        specificity fix — measured with the helper itself, which ranked the
        pinned `calc(1.75rem - 1px)` as the winner in both.
        """
        baseline = _css_block_end_cascade(_product_teams_html(), ".pricing-card")
        assert baseline and baseline[-1][2] == _expected_popular_block_end_pad(
            _product_teams_html()), (
            f"the unmutated page's winning block-end inset reads {baseline[-1:]!r}, "
            "so the rows below cannot distinguish a caught mutant from a page "
            "that is already wrong")
        for mutant in (
                "section .pricing-card.popular { padding-bottom: 1.75rem }",
                "div.pricing-card.popular { padding-bottom: 1.75rem }",
        ):
            cascade = _css_block_end_cascade(self._page_with(mutant), ".pricing-card")
            assert cascade[-1][2] == "1.75rem", (
                f"appending `{mutant}` leaves the cascade reader's winner at "
                f"{cascade[-1][2]!r} ({cascade[-1][0]!r}), but in a browser that "
                "rule (0,2,1) out-ranks `.pricing-card.popular`'s (0,2,0) calc "
                "and the popular CTA drops 1px below the other three "
                "(#13645 item 6, #14406 review round 6 finding 1)")

    def test_a_zeroed_border_is_read_as_the_zero_width_a_browser_applies(self):
        """A border REMOVED with `0`/`none` is a width of zero, not silence.

        `_css_border_end_width` collected only `px` tokens, so the commonest way
        to remove a border produced no length at all: the declaration was
        skipped, the cascade fell back to an EARLIER rule's width, and the
        relation compared itself. Measured at `ef58aef247` (#14406 review round
        20), each rule appended to the tracked page and the reader evaluated:

            .pricing-card.popular { border-bottom: 0 }             browser -1, read 1
            .pricing-card.popular { border-bottom: none }          browser -1, read 1
            .pricing-card.popular { border-bottom-width: 0 }       browser -1, read 1
            .pricing-card.popular { border: 0 }                    browser -1, read 1
            .pricing-card { border-bottom: 0 }                     browser  2, read 1
            .pricing-card.popular { border-block-width: 3px }      browser  2, read 1
            .pricing-card.popular { border-block-width: 2px 3px }  same

        `border-block-width` was absent from `BORDER_END_WIDTH_PROPS` entirely,
        so the whole longhand was invisible; the zero shapes were read as "this
        rule says nothing about the border". Both re-split the four CTAs by
        1–2px — the compensation's own signature — with every row GREEN.

        ⛔ ROUND 22: THE STYLE IS A SECOND AXIS, and the width family could not
        see it (#14406 review round 22, measured at `674f4a8366`). A `none`
        style stated as a LONGHAND zeroes the width exactly as the shorthand
        does, but it arrives in a declaration that names no width at all:

            .pricing-card.popular { border-bottom-style: none }      browser -1, read 1
            .pricing-card.popular { border-bottom-style: hidden }    browser -1, read 1
            .pricing-card.popular { border-style: none }             browser -1, read 1
            .pricing-card.popular { border-block-end-style: none }   browser -1, read 1
            .pricing-card         { border-bottom-style: none }      browser  2, read 1

        So the style and width reads are ranked in their OWN cascade lists and
        only the resolved PAIR decides. Resolving the style inside the width
        read (the round-21 shape) got the mirror case wrong in the other
        direction: `border-bottom: 2px none` with a LATER
        `border-bottom-style: solid` is a 2px border in a browser, and read 0.
        """
        shipped = _product_teams_html()
        assert _popular_border_extra_px(shipped) == 1.0, (
            "the unmutated page must read 1px, or the rows below cannot tell a "
            "caught mutant from a page that is already wrong; it reads "
            f"{_popular_border_extra_px(shipped):g}")

        # The FAMILY membership is asserted on the helper: a page mutant cannot
        # prove a property that is absent from the tuple itself.
        assert "border-block-width" in BORDER_END_WIDTH_PROPS, (
            "`border-block-width` sets the block-END width, so leaving it out "
            "of the family makes the whole longhand invisible to the relation")
        for prop, value, expected in (
                ("border", "none", 0.0),
                ("border", "hidden", 0.0),
                ("border", "0 solid #f00", 0.0),
                ("border", "4px none #f00", 0.0),
                ("border-bottom", "4px hidden", 0.0),
                ("border-width", "1px 2px 0", 0.0),
                ("border-block-width", "2px 3px", 3.0),
                ("border-block-width", "4px", 4.0),
                ("border-bottom", "0", 0.0),
                ("border-bottom", "0px", 0.0),
        ):
            assert _css_border_end_width(prop, value) == expected, (
                f"`{prop}: {value}` gives block-end {expected:g}px in a "
                f"browser; the reader says {_css_border_end_width(prop, value)!r}")

        # The STYLE is its own axis (#14406 review round 22): a `none`/`hidden`
        # style is what makes a stated width unused, and it can arrive in a
        # longhand the width family cannot see at all.
        assert "border-bottom-style" in BORDER_END_STYLE_PROPS, (
            "`border-bottom-style` is how a maintainer REMOVES the popular "
            "card's 2px border, so leaving it out of the style family makes "
            "the removal invisible to the relation")
        for prop, value, expected in (
                ("border-bottom-style", "none", "none"),
                ("border-bottom-style", "hidden", "hidden"),
                ("border-bottom-style", "solid", "solid"),
                ("border-block-end-style", "NONE", "none"),
                ("border-style", "solid dashed dotted double", "dotted"),
                ("border-style", "dashed solid", "dashed"),
                ("border", "2px solid var(--accent)", "solid"),
                ("border", "4px none #f00", "none"),
                ("border-bottom-width", "3px", None),
                ("border-width", "1px 2px 0", None),
        ):
            assert _css_border_end_style(prop, value) == expected, (
                f"`{prop}: {value}` states block-end style {expected!r}; the "
                f"style axis reads {_css_border_end_style(prop, value)!r}, so "
                "the `none`/`hidden` zeroing fires on the wrong declarations")
        # …and a width this file CANNOT read still fails rather than being read
        # as zero: `thin` is a keyword, not a length.
        assert _css_border_end_width("border", "thin") is None, (
            "`border: thin` states a width this file cannot convert to px, so "
            "the reader must return None and let the caller fail — treating it "
            "as zero would be a guess in the compensation's own arithmetic")

        # The relation itself, on the shapes a maintainer actually writes. The
        # expectation is the browser's: extra = popular's block-END minus base's.
        for mutant, expected in (
                (".pricing-card.popular { border-bottom: 0 }", -1.0),
                (".pricing-card.popular { border-bottom: none }", -1.0),
                (".pricing-card.popular { border-bottom-width: 0 }", -1.0),
                (".pricing-card.popular { border: 0 }", -1.0),
                (".pricing-card.popular { border-bottom: 0px }", -1.0),
                (".pricing-card { border-bottom: 0 }", 2.0),
                (".pricing-card { border: none }", 2.0),
                (".pricing-card.popular { border-block-width: 3px }", 2.0),
                (".pricing-card.popular { border-block-width: 2px 3px }", 2.0),
                (".pricing-card.popular { border-bottom: 1px solid #f00 }", 0.0),
                (".pricing-card.popular { border-bottom: 4px none #f00 }", -1.0),
                # A `none`/`hidden` STYLE LONGHAND is the same removal via a
                # declaration the width family cannot see (#14406 round 22).
                (".pricing-card.popular { border-bottom-style: none }", -1.0),
                (".pricing-card.popular { border-bottom-style: hidden }", -1.0),
                (".pricing-card.popular { border-block-end-style: none }", -1.0),
                (".pricing-card.popular { border-style: none }", -1.0),
                (".pricing-card { border-bottom-style: none }", 2.0),
                # …and the style axis is RESOLVED, not per-declaration: a later
                # `solid` restores the border, an `!important` removal holds
                # against it, and the mirror case (a shorthand whose dead style
                # is later restored) keeps the width it states.
                (".pricing-card.popular { border-bottom-style: none }\n"
                 ".pricing-card.popular { border-bottom-style: solid }", 1.0),
                (".pricing-card.popular { border-bottom-style: none !important }\n"
                 ".pricing-card.popular { border-bottom-style: solid }", -1.0),
                (".pricing-card.popular { border-bottom: 2px none }\n"
                 ".pricing-card.popular { border-bottom-style: solid }", 1.0),
        ):
            got = _popular_border_extra_px(self._page_with(mutant))
            assert got == expected, (
                f"appending `{mutant}` makes the browser apply {expected:g}px "
                f"more block-end border on the popular card than on its three "
                f"siblings, so the compensation relation needs {expected:g}; "
                f"the reader derives {got:g} and the CTA baseline rows stay "
                "GREEN over a page whose CTAs are off the shared baseline "
                "(#14406 review round 20)")

        # NO OVER-REACH: a rule that states no width at all cannot become the
        # operand (round 13's shape), and an EQUAL border is 0 extra, not 1.
        for quiet, expected in (
                (".pricing-card.popular { border-color: #fff }", 1.0),
                (".pricing-card { border-color: #fff }", 1.0),
                (".pricing-card.popular { border-bottom-style: solid }", 1.0),
                (".pricing-card.popular { border-bottom-style: dashed }", 1.0),
        ):
            got = _popular_border_extra_px(self._page_with(quiet))
            assert got == expected, (
                f"`{quiet}` declares no border WIDTH, so the shipped 1px "
                f"compensation is still correct; the reader derives {got:g} "
                "instead of 1 — a FALSE RED on a page that renders as designed")

    def test_an_unresolved_border_declaration_is_not_read_as_silence(self):
        """A matched declaration this reader cannot resolve must not be DROPPED.

        Two measured fail-opens at `c692e6e8` (#14406 review round 23), both on
        the property the compensation exists because of, both leaving the relation
        at the shipped 1px while a browser applies something else:

            .pricing-card.popular { border-bottom-width: thin }    browser  0, read 1
            .pricing-card.popular { border-bottom-width: 2em }     browser 31, read 1
            .pricing-card.popular { border-bottom: 2px }           browser -1, read 1
            .pricing-card.popular { border: 3px }                  browser -1, read 2
            .pricing-card.popular { border-bottom: initial }       browser -1, read 1
            .pricing-card.popular { border-bottom-style: initial } browser -1, read 1

        Root 1: an unresolvable WIDTH was skipped, so an EARLIER rule's width
        stood — `thin`, `calc()`, `pt`, `em`, `var()` all read as "this rule says
        nothing about the border" (the same shape round 20 found for `0`/`none`).
        Root 2: a SHORTHAND that states a width and omits the style RESETS the
        style to `none` (CSS Backgrounds and Borders 3 — an omitted sub-property
        takes its initial value), so `border-bottom: 2px` REMOVES the border; the
        reader contributed no style entry and the earlier `solid` survived.

        The cascade now ranks every MATCHED declaration on its axis and lets the
        winner decide. A winner this reader cannot resolve makes the read UNKNOWN
        — the caller fails, this file's fail-closed direction — because guessing
        a value is what let both roots look GREEN.
        """
        shipped = _product_teams_html()
        assert _popular_border_extra_px(shipped) == 1.0, (
            "the unmutated page must read 1px, or the rows below cannot tell a "
            "caught mutant from a page that is already wrong; it reads "
            f"{_popular_border_extra_px(shipped):g}")

        # Root 2 on the ONE-DECLARATION read: a shorthand that states a width and
        # no style is a `none` style, i.e. a 0px border.
        for prop, value, expected in (
                ("border", "3px", "none"),
                ("border-bottom", "2px", "none"),
                ("border-block-end", "2px", "none"),
                ("border-block", "2px 3px", "none"),
                ("border-bottom", "initial", "none"),
                ("border-bottom-style", "initial", "none"),
                ("border-bottom-style", "unset", "none"),
        ):
            assert _css_border_end_style(prop, value) == expected, (
                f"`{prop}: {value}` leaves the block-end style at {expected!r} "
                "in a browser (an omitted or reset sub-property takes its "
                f"initial value); the reader says "
                f"{_css_border_end_style(prop, value)!r}, so the removal is "
                "invisible to the relation")
        assert _css_border_end_width("border-bottom", "2px") == 0.0, (
            "`border-bottom: 2px` states a 2px width with no style, and no style "
            "means the used width is 0 — the reader must not hand the cascade a "
            "2px border a browser does not draw")

        # …and a declaration this reader CANNOT resolve still returns silence
        # from the single-declaration read: the caller fails rather than guesses
        # (there is no `thin`→px conversion in this file, by decision).
        assert _css_border_end_width("border", "thin") is None, (
            "`border: thin` states a width this file cannot convert to px and no "
            "style, so the single-declaration read returns None and the caller "
            "fails rather than guessing a zero")
        assert _css_border_end_style("border-bottom-style", "inherit") is None, (
            "`inherit` takes the PARENT's style, which this reader cannot see — "
            "it must not be modelled as `none`")
        assert _css_border_end_style("border-width", "3px") is None, (
            "`border-width` sets widths only and says nothing about the style "
            "axis, so it must not contribute a `none` style")

        # Root 1 — the relation itself. The expectation is the BROWSER's: the
        # popular card's block-END border minus its siblings', in px.
        for mutant, expected in (
                (".pricing-card.popular { border-bottom: 2px }", -1.0),
                (".pricing-card.popular { border: 3px }", -1.0),
                (".pricing-card.popular { border-block: 3px }", -1.0),
                (".pricing-card.popular { border-block-end: 2px }", -1.0),
                (".pricing-card.popular { border-bottom: initial }", -1.0),
                (".pricing-card.popular { border-bottom-style: initial }", -1.0),
                (".pricing-card.popular { border-style: unset }", -1.0),
                (".pricing-card { border-bottom: 2px }", 2.0),
                (".pricing-card { border: 3px }", 2.0),
        ):
            got = _popular_border_extra_px(self._page_with(mutant))
            assert got == expected, (
                f"appending `{mutant}` makes the browser apply {expected:g}px "
                f"more block-end border on the popular card than on its three "
                f"siblings; the reader derives {got:g}, so the CTA baseline "
                "rows stay GREEN over a page whose popular CTA is off the "
                "shared baseline (#14406 review round 23)")

        # …and an UNRESOLVABLE width on either operand is UNKNOWN, not stale: the
        # earlier rule's value is not the answer, so the caller must fail.
        for mutant in (
                ".pricing-card.popular { border-bottom-width: thin }",
                ".pricing-card.popular { border-bottom-width: calc(1px) }",
                ".pricing-card.popular { border-bottom-width: 1pt }",
                ".pricing-card.popular { border-bottom-width: 2em }",
                ".pricing-card.popular { border-bottom-width: var(--w) }",
                ".pricing-card.popular { border-bottom-style: inherit }",
                ".pricing-card { border-bottom-width: thin }",
        ):
            try:
                got = _popular_border_extra_px(self._page_with(mutant))
            except AssertionError:
                continue
            raise AssertionError(
                f"appending `{mutant}` leaves the block-END border width "
                f"unresolvable to this reader (a keyword, a relative unit, a "
                f"`calc`/`var`, or an `inherit`), so the read is UNKNOWN and the "
                f"caller must fail — reading {got:g} is an EARLIER rule's value "
                "presented as this declaration's, which is how rounds 20-22's "
                "fail-opens looked (#14406 review round 23)")

        # NO OVER-REACH: a width shorthand says nothing about the style, a
        # readable shorthand keeps its style, and an unresolvable WIDTH on a card
        # whose style is readable is still the WIDTH declaration we cannot read.
        for quiet, expected in (
                (".pricing-card.popular { border-width: 3px }", 2.0),
                (".pricing-card.popular { border-bottom: 2px solid #f00 }", 1.0),
                (".pricing-card.popular { border-bottom: 1px solid #f00 }", 0.0),
                (".pricing-card.popular { border-bottom: 0.5px solid #f00 }", -0.5),
                (".pricing-card.popular { border-color: #fff }", 1.0),
                (".pricing-card.popular { border-bottom-style: solid }", 1.0),
                (".pricing-card { border-color: #fff }", 1.0),
        ):
            got = _popular_border_extra_px(self._page_with(quiet))
            assert got == expected, (
                f"`{quiet}` changes no block-end border a browser draws, so the "
                f"shipped 1px compensation still holds; the reader derives "
                f"{got:g} instead of {expected:g} — a FALSE RED on a page that "
                "renders as designed")

    def test_a_pseudo_class_or_an_exclusion_does_not_hide_the_border_reach(self):
        """The relation's two operands must read the SAME rule set — pseudos too.

        The class-set reach reader (`_compound_selects_every_subject`) bailed on
        any `:`, and the two other reach readers miss those selectors as well:
        `_css_rightmost_selects` because the COMPOUND's text is not a substring
        of them, `_css_compound_matches_element` because it refuses class-named
        compounds. A rule that reached the popular card *only* through such a
        selector was therefore invisible to BOTH operands, so the relation kept
        reading the shipped 1px while a browser applied more.

        Measured at `1b64dd9364` (#14406 review round 23) — one rule appended to
        the tracked page, `_popular_border_extra_px` evaluated:

            .popular:nth-child(2) { border-bottom-width: 3px }        browser 2, read 1
            .pricing-card:not(.popular) { border-bottom-width: 0 }     browser 2, read 1
            #pricing-section div.pricing-card { border-bottom-width: 3px }
                                                                     browser 0, read -1

        The first two are fail-OPEN — the row stays GREEN over a page whose
        popular CTA sits 1px off the shared baseline (#13645 item 6) — and the
        third is the mirror false RED on a page that renders as designed. The
        base operand's own exclusion is the sharpest: `.pricing-card
        :not(.popular)` sets the THREE SIBLINGS' border and says nothing about
        the popular card's, and the old `skip_popular` test was the string
        `.popular in part`, so the part was dropped from the base operand.
        """
        # `expected` is what CSS applies to the page; `before` is what the reader
        # derived BEFORE round 23's fix, so this row is non-vacuous by
        # construction (each of the three fails on the pre-fix helper).
        for mutant, expected, before in (
                (".popular:nth-child(2) { border-bottom-width: 3px }", 2.0, 1.0),
                (".pricing-card:not(.popular) { border-bottom-width: 0 }",
                 2.0, 1.0),
                ("#pricing-section div.pricing-card "
                 "{ border-bottom-width: 3px }", 0.0, -1.0),
        ):
            got = _popular_border_extra_px(self._page_with(mutant))
            assert got == expected, (
                f"appending `{mutant}` makes the browser apply {expected:g}px "
                f"more block-end border on the popular card than on its three "
                f"siblings, so the compensation relation needs {expected:g}; "
                f"the reader derives {got:g} (it read {before:g} before round "
                "23's fix) and the CTA baseline rows stay GREEN over a page "
                "whose CTAs are off the shared baseline (#14406 review round "
                "23)")

        # NO OVER-REACH: a rule that names a class the base cards do NOT carry
        # still belongs to the popular operand alone, and an exclusion naming a
        # class nobody carries selects EVERY card — so it zeroes both operands
        # and the relation reports 0. That last one reds the relation's own
        # `extra_px > 0` guard with the right message instead of reporting the
        # popular card's untouched 2px.
        for quiet, expected in (
                (".pricing-card.popular:nth-child(2) "
                 "{ border-bottom-width: 3px }", 2.0),
                ("div.pricing-card.popular { border-bottom-width: 3px }", 2.0),
                (".pricing-card:not(.featured) { border-bottom-width: 0 }", 0.0),
        ):
            got = _popular_border_extra_px(self._page_with(quiet))
            assert got == expected, (
                f"`{quiet}` resolves to {expected:g}px extra on a browser; the "
                f"reader derives {got:g} — a FALSE RED on a page that renders "
                "as designed, which is the failure mode a maintainer deletes "
                "the guard over (#14406 review round 23)")

    def test_a_max_width_block_at_or_above_the_desktop_floor_is_reached(self):
        """`@media (max-width: 1440px)` applies at 1440 — so the guard must see it.

        `max-width` is INCLUSIVE (`width <= N`), which is why the `max-` PREFIX
        is the wrong predicate: the first draft skipped every `max-` block, so
        `@media (max-width: 1440px) { .pricing-card .btn { margin-top: 0 } }`
        applied at the very viewport every measurement in this PR uses and was
        invisible — three baselines, 70.2px apart, every row GREEN (#14406
        review round 6, finding 4).
        """
        seen = "@media (max-width: 1440px) { .pricing-card .btn { margin-top: 0 } }"
        reached = [(p, v) for p, v in
                   _css_reaching_decls(self._page_with(seen), "margin-top", ".btn")]
        assert any(v.strip() == "0" for _p, v in reached), (
            "the reach reader did not see `.pricing-card .btn { margin-top: 0 }` "
            "inside `@media (max-width: 1440px)`, which applies at 1440x900 "
            f"(width <= 1440). Reached: {reached!r}")

        # ...and the page's OWN viewport hatch stays excluded: at 1440x900 a
        # `max-width: 760px` block cannot apply, and the cards stack there by
        # design, so pulling it in would be a false RED on a correct page.
        hatch = "@media (max-width: 760px) { .pricing-card .btn { margin-top: 0 } }"
        reached = [(p, v) for p, v in
                   _css_reaching_decls(self._page_with(hatch), "margin-top", ".btn")]
        assert not any(v.strip() == "0" for _p, v in reached), (
            "the page's own narrow-viewport hatch (`max-width: 760px`) is now "
            "read as a desktop rule — the floor comparison has drifted below "
            f"the page's ~907px stack point. Reached: {reached!r}")

    def test_a_comma_separated_media_list_is_decided_by_any_query(self):
        """A query LIST is an OR, so its SECOND query can make it apply.

        `@media (max-width: 760px), (min-width: 900px)` is *true* at 1440x900
        through `min-width: 900px` — "a media query list is true if any
        component query is true and false only when all component queries are
        false" (W3C Media Queries 4 §3; MDN, "Using media queries").

        The round-6 predicate compared the threshold of every `max-` it found in
        the whole condition STRING and decided from that one number, so the
        second query was never evaluated: the block read as a narrow hatch.
        Measured (#14406 review round 7) — the mutant below left all four rows
        of `TestProductTeamsPricingCardCtaBaseline` GREEN while a browser
        applied it and split the rendered CTAs back to three baselines 70.2px
        apart.
        """
        # The list decides by its BEST query, not by its narrowest.
        for applying in (
                "(max-width: 760px), (min-width: 900px)",
                "(min-width: 900px), (max-width: 760px)",
                "(max-width: 760px), print",
                "(max-width: 760px), (max-width: 1440px)",
        ):
            assert _media_applies_at_desktop(applying), (
                f"`@media {applying}` applies at 1440x900 through one of its "
                "queries, but the reach predicate says it does not — the rule "
                "inside it is invisible to every cascade row (#14406 review "
                "round 7)")

        # ...and a list of ONLY narrow queries is still a narrow hatch, or the
        # page's own `max-width: 760px` blocks would become false REDs.
        for excluded in (
                "(max-width: 760px)",
                "(max-width: 760px), (max-width: 480px)",
                "(max-width: 760px) and (max-height: 600px)",
        ):
            assert not _media_applies_at_desktop(excluded), (
                f"`@media {excluded}` cannot apply at 1440x900, but the reach "
                "predicate says it does — the cards stack at those widths by "
                "design, so this is a false RED on a correct page")

        # A comma inside `var()`/`calc()` is not a list separator.
        assert _media_query_components("(max-width: 760px), (min-width: 900px)") == [
            "(max-width: 760px)", "(min-width: 900px)"], (
            "the comma split did not split a real query list")
        assert _media_query_components("(max-width: var(--narrow, 760px))") == [
            "(max-width: var(--narrow, 760px))"], (
            "a comma INSIDE the parens of `var(--narrow, 760px)` was treated as "
            "a list separator, which invents a second query that does not exist")

    def test_a_comma_list_override_of_the_ctas_slack_is_reached(self):
        """The page-level mutant that the round-6 predicate left GREEN.

        This reads through `_css_reaching_decls`, the reader
        `test_no_other_rule_takes_the_ctas_slack` stands on — and that row now
        REDs on the mutant below (it passed all four before this fix). The
        padding cascade (`_css_block_end_cascade`) shares the same predicate via
        `_css_desktop_rules`, so it gains the reach too; it is not asserted here
        because a `.pricing-card`-specificity mutant is out-ranked by the
        popular card's (0,2,0) rule on a CORRECT page, which would not
        distinguish the reach from the specificity.
        """
        mutant = ("@media (max-width: 760px), (min-width: 900px) { "
                  ".pricing-card .btn { margin-top: 0 } }")
        reached = [(p, v) for p, v in
                   _css_reaching_decls(self._page_with(mutant), "margin-top", ".btn")]
        assert any(v.strip() == "0" for _p, v in reached), (
            "`.pricing-card .btn { margin-top: 0 }` inside "
            "`@media (max-width: 760px), (min-width: 900px)` applies at "
            "1440x900 (`min-width: 900px` is true there) but the reach reader "
            "did not see it, so `test_no_other_rule_takes_the_ctas_slack` "
            f"stays GREEN over a 3-baseline split. Reached: {reached!r}")

    def test_a_flex_flow_override_is_read_as_a_direction(self):
        """`flex-flow` IS `flex-direction` + `flex-wrap`, and it resets both.

        The shorthand's components are ORDER-INSENSITIVE
        (`<'flex-direction'> || <'flex-wrap'>`: W3C CSS Flexible Box Layout 1
        §5.1; MDN, "flex-flow" — *"the components can appear in either order"*),
        and the direction it omits takes its INITIAL value `row` (same page:
        *"its defaults are row and nowrap"*). So all four of `flex-flow: row`,
        `flex-flow: column`, `flex-flow: wrap column` and `flex-flow: nowrap`
        set a direction, and only `column`/`column-reverse` agrees with the pin.

        Measured (#14406 review round 8): `flex-flow: row` and
        `flex-flow: nowrap` appended to the card rule left ALL FOUR rows of
        `TestProductTeamsPricingCardCtaBaseline` GREEN while a browser laid the
        card out as a row and the CTA left the shared baseline.
        """
        assert _css_flex_direction("flex-flow", "row") == "row"
        assert _css_flex_direction("flex-flow", "column") == "column"
        assert _css_flex_direction("flex-flow", "wrap column") == "column", (
            "`flex-flow` is order-insensitive, so the direction keyword is not "
            "necessarily the first token")
        assert _css_flex_direction("flex-flow", "nowrap") == "row", (
            "an OMITTED direction in `flex-flow` is reset to its initial value "
            "`row` — reading the shorthand as 'no direction here' is the hole "
            "this row closes")
        assert _css_flex_direction("flex-direction", "column") == "column"

        for mutant in (".pricing-card { flex-flow: row }",
                       ".pricing-card { flex-flow: nowrap }"):
            reached = [(p, v) for p, v in _css_reaching_decls(
                self._page_with(mutant), "flex-flow", ".pricing-card")
                if _css_flex_direction("flex-flow", v) != "column"]
            assert reached, (
                f"appending `{mutant}` overrides the card's `flex-direction: "
                "column` in a browser, but the flex row's agreement sweep "
                "found no disagreement — the rendered CTA leaves the shared "
                "baseline with every row GREEN (#14406 review round 8)")

    def test_a_class_attribute_selector_reaches_the_class_it_names(self):
        """`[class~="x"]` IS the `.x` shorthand, so it must be reached.

        `~=` is the whitespace-separated-word match and a class is one word of
        the `class` attribute, which is exactly what the `.x` shorthand denotes
        — so a rule whose rightmost compound names its own class without the dot
        selects the same elements. The readers matched the literal `.pricing-card`
        text only.

        Measured (#14406 review round 8): `[class~="pricing-card"] {
        display: block }` appended to the sheet left all four rows of
        `TestProductTeamsPricingCardCtaBaseline` GREEN.
        """
        assert _css_rightmost_selects('[class~="pricing-card"]', ".pricing-card")
        assert _css_rightmost_selects(".grid > [class*='pricing']", ".pricing-card")
        assert _css_rightmost_selects("[class^='pricing']", ".pricing-card")
        # Fail-closed: an attribute value this reader cannot read may match.
        assert _css_rightmost_selects("[class=var(--c)]", ".pricing-card")
        # ...and a genuinely different class does NOT reach, or the readers
        # would red every unrelated card rule on a correct page.
        assert not _css_rightmost_selects('[class~="card"]', ".pricing-card")
        assert not _css_rightmost_selects(".feature", ".pricing-card")

        mutant = '[class~="pricing-card"] { display: block }'
        reached = [(p, v) for p, v in _css_reaching_decls(
            self._page_with(mutant), "display", ".pricing-card")]
        assert any(v.strip() == "block" for _p, v in reached), (
            "`[class~=\"pricing-card\"] { display: block }` selects the same "
            "elements as `.pricing-card` (a class IS a word of the `class` "
            "attribute) and overrides the pinned `display: flex`, but the "
            f"reach reader did not see it. Reached: {reached!r}")

        # `all` is the shorthand for every property, so it must be reachable
        # through the SAME predicate the family sweeps use — on both subjects.
        for compound, subject in ((".pricing-card", '[class~="pricing-card"]'),
                                  (".btn", '[class~="btn"]')):
            reached = [(p, v) for p, v in _css_reaching_decls(
                self._page_with(f"{subject} {{ all: revert }}"), "all", compound)]
            assert any(v.strip() == "revert" for _p, v in reached), (
                f"`{subject} {{ all: revert }}` resets every property on "
                f"`{compound}`, but the `all` reach found nothing — the "
                "negative row would stay GREEN over a one-declaration revert "
                "of the pinned CSS (#14406 review round 8)")
    def test_an_exact_class_attribute_match_reaches_the_class_it_names(self):
        """`=` is an operator too, and attribute NAMES are case-insensitive.

        Round 8 modelled `~= |= ^= $= *=` and read the operator inside its OWN
        regex, so `[class="btn"]` never matched the pattern and fell through to
        `False` — not to the fail-closed arm, which only covered a VALUE the
        reader could not parse. A browser applies it: three of the four pricing
        cards carry `class="btn"` EXACTLY, so `[class="btn"] { margin-top: 0 }`
        is `.pricing-card .btn { margin-top: 0 }` by another name and restores
        the three-baseline / 70.2px split. Measured (#14406 review round 9)
        against the pre-fix reader: every row of
        `TestProductTeamsPricingCardCtaBaseline` stayed GREEN, `_css_reaching_decls`
        returned no competing declaration at all, and
        `[class="pricing-card"].popular { padding-bottom: 1.75rem }` — (0,2,0),
        later in source, so it TAKES the cascade — left the popular-card row
        reading `calc(1.75rem - 1px)`.

        HTML attribute NAMES are case-insensitive (`[CLASS="btn"]` selects the
        same elements), so a lowercase-only match is the same hole in a
        different case; both are asserted below.
        """
        # The operator itself, in every spelling that means "exactly this value".
        for selector in ('[class="btn"]', "[class='btn']",
                         '[CLASS="btn"]', '[ class = "btn" ]'):
            assert _css_rightmost_selects(selector, ".btn"), (
                f"`{selector}` is the `=` (exact) class-attribute match, which "
                "selects every element whose class attribute is exactly `btn` "
                "— the pattern the pricing CTAs carry — but the reach reader "
                "says it selects nothing (#14406 review round 9)")
        # ...and it is not a licence to reach EVERY class.
        assert not _css_rightmost_selects('[class="btn-secondary"]', ".btn"), (
            "`=` is an EXACT match, so `[class=\"btn-secondary\"]` does not "
            "select `.btn` — reading it as a reach would false-RED a correct "
            "page")
        assert not _css_rightmost_selects('[classname="btn"]', ".btn"), (
            "`classname` is a different attribute; only `class` carries the "
            "class list")
        # A class LIST is space-separated, so `=` against a multi-class value is
        # exactly that element's attribute — the popular card's own markup.
        assert _css_rightmost_selects('[class="pricing-card popular"]',
                                      ".pricing-card"), (
            "`=` compares the WHOLE attribute value and a class list is "
            "space-separated, so `[class=\"pricing-card popular\"]` IS the "
            "popular card — with `!important` it takes the cascade, and an "
            "equality test between the two strings misses it (#14406 review "
            "round 9)")

        # Page-level: the two mutants the pre-fix reader could not see.
        for mutant, prop, compound, pinned in (
                ('[class="btn"] { margin-top: 0 }', "margin-top", ".btn", "auto"),
                ('[class="pricing-card"] { display: block }',
                 "display", ".pricing-card", "flex")):
            disagreeing = [(p, v) for p, v in _css_reaching_decls(
                self._page_with(mutant), prop, compound)
                if v.strip() != pinned]
            assert disagreeing, (
                f"`{mutant}` selects the same elements as `{compound}` and "
                f"overrides the pinned `{prop}: {pinned}`, but no agreement "
                "sweep sees it — the CTA/flex rows stay GREEN over a rendered "
                "3-baseline split (#14406 review round 9)")

        cascade = _css_block_end_cascade(
            self._page_with(
                '[class="pricing-card"].popular { padding-bottom: 1.75rem }'),
            ".pricing-card")
        assert cascade[-1][2] == "1.75rem", (
            "`[class=\"pricing-card\"].popular` is (0,2,0) — TIED with the "
            "pinned `.pricing-card.popular` and later in source, so a browser "
            "applies its `padding-bottom: 1.75rem` and the popular CTA drops "
            f"1px. The cascade reader's winner is {cascade[-1]!r} "
            "(#14406 review round 9)")

    def test_a_bare_element_selector_that_outranks_the_pin_reaches_the_subject(self):
        """A selector can select the CTA WITHOUT naming `.btn` — and win.

        Round 9 declared this a residual: `#pricing-grid a`, `a[href="/auth"]`
        and a bare `a` select the page's own CTA tag and destination, and all
        three left every row of `TestProductTeamsPricingCardCtaBaseline` GREEN.
        Round 10 settles it with the subject's ELEMENT in the reader's contract
        (`_element_carrying_class` → `_css_compound_matches_element`) plus the
        cascade's own specificity floor (`_css_element_reaches`), and this row
        pins BOTH directions, because each half alone is a broken guard:

        * without the element test, `#pricing-grid a { margin-top: 0 }` —
          (1,0,1) against the pinned `.pricing-card .btn` (0,2,0), so it wins at
          ANY source order — moves the CTA with every row GREEN;
        * without the floor, the page's own `*, *::before, *::after { margin: 0
          }` and `.selfhost a { … }` would red a CORRECT page, and a guard that
          reds the page it ships is a guard a maintainer deletes.
        """
        page = _product_teams_html()
        cta = _element_carrying_class(page, "btn")
        card = _element_carrying_class(page, "pricing-card")
        assert cta and cta[0] == "a", (
            f"the pricing CTA's element is {cta!r} — the reader's subject moved")
        assert card and card[0] == "div", (
            f"the pricing card's element is {card!r} — the reader's subject "
            "moved")

        # Identity, not styling: tag, id and attributes decide. A class is the
        # OTHER reader's question and must not be answered twice.
        assert _css_compound_matches_element("a", cta)
        assert _css_compound_matches_element('a[href="/auth"]', cta)
        assert _css_compound_matches_element("*", cta)
        assert not _css_compound_matches_element("p", cta), (
            "the page declares `p { … }` rules; a naked `p` cannot select an "
            "`<a>`, so matching it would false-RED a correct page")
        assert not _css_compound_matches_element('a[href="/signup"]', cta)
        assert not _css_compound_matches_element("#pricing-section", cta)
        assert not _css_compound_matches_element(".btn", cta)

        # The mutants a browser applies — all four left every row of the class
        # GREEN before this round (measured against the pre-fix reader).
        for mutant, prop, compound, pinned in (
                ("#pricing-grid a { margin-top: 0 }", "margin-top", ".btn",
                 "auto"),
                ("#pricing-grid div { display: block }", "display",
                 ".pricing-card", "flex"),
                (".pricing-grid a[href] { margin-top: 0 }", "margin-top",
                 ".btn", "auto"),
                # `!important` out-ranks ANY non-important declaration, so it is
                # admitted whatever the selector's weight.
                ("a { margin-top: 0 !important }", "margin-top", ".btn",
                 "auto")):
            disagreeing = [
                (p, v) for p, v in _css_reaching_decls(
                    self._page_with(mutant), prop, compound)
                if v.replace("!important", "").strip() != pinned]
            assert disagreeing, (
                f"`{mutant}` selects the subject and out-ranks the pinned "
                f"`{prop}: {pinned}` — (1,0,1)/(0,2,1) beat (0,2,0) at any "
                "source order, and `!important` beats it outright — but no "
                "agreement sweep sees it, so the rows stay GREEN over the "
                "3-baseline / 70.2px split (#13645 item 6, #14406 round 10)")

        # ...and the OTHER direction, on the same reader: a bare-element rule
        # that CANNOT win the cascade must not make a correct page red.
        for mutant in ("a { margin-top: 0 }",          # (0,0,1)
                       '* { margin-top: 0 }',           # (0,0,0)
                       'a[href="/auth"] { margin-top: 0 }',   # (0,1,1)
                       ".selfhost a { margin-top: 0 }"):  # (0,1,1)
            competing = [
                (p, v) for p, v in _css_reaching_decls(
                    self._page_with(mutant), "margin-top", ".btn")
                if v.strip() != "auto"]
            assert competing == [], (
                f"`{mutant}` cannot out-rank the pinned `.pricing-card .btn "
                "`margin-top: auto`)` (its specificity is below (0,2,0) and it "
                "is not `!important`), so a browser keeps the pinned value — "
                f"counting it reds a correct page. Competing: {competing!r}")

        # The cascade reader has no floor on purpose: it ORDERS, so a weak
        # bare-element rule is ranked below the pin instead of being excluded —
        # and a strong one (1,0,1) must become the winner, or the 1px defect
        # ships under a GREEN popular-card row.
        assert _css_block_end_cascade(
            self._page_with("p { padding-bottom: 1.75rem }"),
            ".pricing-card")[-1][2] == _expected_popular_block_end_pad(
            self._page_with("p { padding-bottom: 1.75rem }")), (
            "a `p` rule does not select the card, so it must not become the "
            "cascade winner (and the page's own `p` rules must stay harmless)")
        assert _css_block_end_cascade(
            self._page_with("#pricing-grid div { padding-bottom: 1.75rem }"),
            ".pricing-card")[-1][2] == "1.75rem", (
            "`#pricing-grid div` is (1,0,1) — it out-ranks the popular card's "
            "(0,2,0) compensation, so a browser applies its `padding-bottom: "
            "1.75rem` and the popular CTA drops 1px; the cascade reader's "
            "winner must be that declaration (#14406 review round 10)")

    def test_an_id_only_compound_reaches_the_element_it_names(self):
        """`#pricing-grid` is a compound, not a tag called "#pricing-grid".

        `_css_compound_matches_element` checks the ids on the way in and then
        compared the REMAINDER — with the `#id` text still in it — against the
        tag, so an id-ONLY rightmost compound matched the subject's id and was
        discarded one line later. Round 10's mutants all put the id in an
        ANCESTOR position (`#pricing-grid a`, `#pricing-grid div`), where the
        rightmost compound is the bare tag and the bug cannot show, so the
        guard-of-the-guard row added by that round could not see it.

        Measured (#14406 review round 12) — each left ALL NINE rows of
        `TestProductTeamsPricingCardCtaBaseline` GREEN:

            #pricing-grid { display: block }        3 baselines
            #pricing-grid { align-items: center }   boxes to content height
            #pricing-grid { place-items: center }   the same, via the shorthand

        Both directions are pinned: the id must MATCH, and a wrong id must still
        be refused — otherwise "strip the remainder" would be a blanket True.
        """
        page = _product_teams_html()
        grid = _element_carrying_class(page, "pricing-grid")
        assert grid and grid[0] == "div" and grid[1].get("id") == "pricing-grid", (
            f"the `.pricing-grid` element is {grid!r} — this row's subject moved")
        for compound in ("#pricing-grid", "div#pricing-grid"):
            assert _css_compound_matches_element(compound, grid), (
                f"`{compound}` selects the `.pricing-grid` element by its own "
                "id, so a rule written that way out-ranks `.pricing-grid` "
                "(1,0,0 vs 0,1,0) at ANY source order — and the reader says it "
                "does not reach (#14406 review round 12, measured)")
        for compound in ("#pricing-section", "#nope", "span#pricing-grid"):
            assert not _css_compound_matches_element(compound, grid), (
                f"`{compound}` does NOT select the `.pricing-grid` element, so "
                "counting it reds a correct page")

        # ...and the whole reader, end to end: each mutant must be REACHED, and
        # the same declarations with the pinned values must stay silent.
        for mutant in ("#pricing-grid { align-items: center }",
                       "#pricing-grid { place-items: center }",
                       "#pricing-grid { display: block }"):
            prop = ("align-items" if "align-items" in mutant else "place-items")
            if "display" in mutant:
                prop = "display"
            reached = _css_reaching_decls(self._page_with(mutant), prop,
                                          ".pricing-grid")
            assert any("#pricing-grid" in prelude for prelude, _v in reached), (
                f"`{mutant}` is (1,0,0) and a browser applies it at 1440x900, "
                "but `_css_reaching_decls` does not reach it — the grid's "
                "equal-height pin is blind to the id spelling of its own "
                f"subject. Reached: {reached!r}")
        for control in ("#pricing-grid { align-items: stretch }",
                        "#pricing-grid { display: flex }"):
            prop = "display" if "display" in control else "align-items"
            bad = [(p, v) for p, v in _css_reaching_decls(
                self._page_with(control), prop, ".pricing-grid")
                if v.strip().lower() not in ("stretch", "flex", "normal")]
            assert bad == [], (
                f"`{control}` declares the PINNED value from a stronger "
                f"selector, so it must not count as a disagreement: {bad!r}")

    def test_the_inline_style_reader_sees_every_quoting_spelling(self):
        """An inline `style` attribute has FOUR legal spellings, not one.

        The round-8 row read the attribute as the literal `style="…"`. The
        HTML parser accepts a single-quoted value, whitespace around the `=`,
        and an unquoted value, and every one of them out-ranks the whole sheet:
        `<div class="pricing-card" style='display:block'>` splits the four CTAs
        back to three baselines exactly as the double-quoted form does.
        Measured (#14406 review round 9) against the pre-fix row: the
        single-quoted, spaced and unquoted mutants all left every row of
        `TestProductTeamsPricingCardCtaBaseline` GREEN.
        """
        for mutant, pre_fix_reader_sees_it in (
                ('style="display:block"', True),
                ("style='display:block'", False),
                ('style = "display:block"', False),
                ("style=display:block", False),
        ):
            markup = f'<div class="pricing-card popular" {mutant}>'
            # This pins the FINDING, not just the fix: if the pre-fix reader's
            # coverage ever changes, the claim in this docstring is wrong and
            # this row says so instead of asserting against a memory.
            assert bool(re.findall(r'style="[^"]*"', markup)) is pre_fix_reader_sees_it, (
                f"the PRE-FIX reader's coverage of {mutant!r} is not what this "
                "row records — re-derive the finding before trusting the fix")
            assert _inline_style_attributes(markup) == [mutant], (
                f"`{mutant}` is a legal inline-style attribute and out-ranks "
                "every rule in the sheet, but the reader does not see it "
                f"(got {_inline_style_attributes(markup)!r}) — the "
                "no-inline-style row stays GREEN over a split baseline "
                "(#14406 review round 9)")

        # A differently-NAMED attribute is not an inline style, or the row would
        # false-RED a correct page that carries one.
        assert _inline_style_attributes('<div data-style="x" x-style="y">') == []

        # The page's own inline styles are real, and the reader sees them — a
        # reader that returned `[]` unconditionally would pass every row above.
        assert _inline_style_attributes(_served(_product_teams_html())), (
            "the page carries inline `style` attributes of its own, so a reader "
            "that finds none is not reading the attribute at all")
        assert _inline_style_attributes(
            _static_pricing_grid(_served(_product_teams_html()))) == [], (
            "the pricing grid itself now carries an inline `style` attribute — "
            "that is the defect `test_no_card_carries_an_inline_style` exists "
            "for, and this row would mask it by staying green here")

    def test_a_wrapped_selector_is_still_read_by_the_cascade(self):
        """A selector that WRAPS is still a selector — the cascade skipped it.

        `_css_block_end_cascade` skipped any comma part containing a newline,
        a defence against the document `<head>` that the first rule's prelude
        carries. The defence was too wide: it also threw away a selector an
        editor had simply WRAPPED, which is what a long selector does at a line
        limit — and the wrapped form is (1,2,0), so a browser applies it and the
        popular card's 1px compensation is gone. Measured (#14406 review round
        9) on this tree before the fix: every mutant below left the cascade
        reader's winner at `calc(1.75rem - 1px)` — the guard GREEN over the
        owner-reported 744.78-vs-745.78 defect, with every row of
        `TestProductTeamsPricingCardCtaBaseline` passing.

        The head is now cut at the `<style>` tag where it is CREATED
        (`_css_desktop_rules`), so no downstream reader has to skip newlines and
        the junk-prefix defence is still in place for a prelude the cut could
        not reach.
        """
        baseline = _css_block_end_cascade(_product_teams_html(), ".pricing-card")
        assert baseline[-1][2] == _expected_popular_block_end_pad(
            _product_teams_html()), (
            f"the unmutated page's winning block-end inset is {baseline[-1:]!r}, "
            "so these rows could not tell a caught mutant from a page that is "
            "already wrong")
        for mutant in (
                # the descendant combinator on its own line
                "#pricing-section\n  .pricing-card.popular { padding-bottom: 1.75rem }",
                # ...as the wrapped part of a selector LIST
                ".pricing-grid .pricing-card,\n#pricing-section\n  .pricing-card.popular "
                "{ padding-bottom: 1.75rem }",
                # ...and with the brace on its own line, tying on specificity
                ".pricing-grid .pricing-card,\n.pricing-card.popular\n"
                "{ padding-bottom: 1.75rem }",
        ):
            cascade = _css_block_end_cascade(self._page_with(mutant), ".pricing-card")
            assert cascade[-1][2] == "1.75rem", (
                f"`{mutant.replace(chr(10), ' ')}` is a WRAPPED selector: (1,2,0) "
                "in a browser, so it takes the popular card's bottom inset and "
                "its CTA drops 1px — but the cascade reader's winner is still "
                f"{cascade[-1]!r}. A newline is not a reason to skip a "
                "selector (#14406 review round 9, measured)")

        # ...and dropping the newline skip did not turn EVERY wrapped rule into
        # a reach: a wrapped rule that does not select the card is still not a
        # candidate, or a correct page would be red for an unrelated line break.
        unrelated = self._page_with(
            "#pricing-section\n  .pricing-grid { padding-bottom: 1.75rem }")
        assert _css_block_end_cascade(unrelated, ".pricing-card")[-1][2] == (
            _expected_popular_block_end_pad(unrelated)), (
            "`#pricing-section\n  .pricing-grid` does not select the pricing "
            "card, so it must not become the cascade winner — dropping the "
            "newline skip must widen the reader's ENUMERATION, not its "
            "matching")

    def test_an_or_condition_is_the_same_logical_or_as_a_query_list(self):
        """`or` is a top-level OR too — the round-7 splitter only knew commas.

        Media Queries 4 §3 spells the OR twice: a comma-separated query LIST,
        and `<media-condition> or <media-condition>` inside a condition. `and`
        binds tighter than `or`, so the split belongs on `or` exactly as it
        belongs on a comma — and the round-7 fix split only on commas, which
        left the same fail-open one spelling over: the whole
        `(max-width: 760px) or (min-width: 900px)` came back as ONE component,
        the reader took `max-width: 760px` out of it and called the block a
        narrow hatch. Measured on this tree before the split was widened:
        `_css_reaching_decls` did not see `.pricing-card .btn { margin-top: 0 }`
        inside it (`reached=False`), while a browser applies the rule at
        1440x900 through the second operand.
        """
        for applying in (
                "(max-width: 760px) or (min-width: 900px)",
                "(min-width: 900px) or (max-width: 760px)",
                "(max-width: 760px) or (max-width: 1440px)",
                "(max-width: 760px) or (min-width: 900px) or print",
                "(max-width: 760px) OR (min-width: 900px)",
        ):
            assert _media_applies_at_desktop(applying), (
                f"`@media {applying}` is true at 1440x900 through one of its "
                "`or` operands, but the reach predicate says it does not — the "
                "rule inside it is invisible to every cascade row (#14406 "
                "round-7 class, the `or` spelling)")

        # ...and an `or` whose operands are ALL narrow is still a hatch.
        for excluded in (
                "(max-width: 760px) or (max-width: 480px)",
                "(max-width: 760px) and (min-height: 400px)",
        ):
            assert not _media_applies_at_desktop(excluded), (
                f"`@media {excluded}` cannot apply at 1440x900, but the reach "
                "predicate says it does — the cards stack at those widths by "
                "design, so this is a false RED on a correct page")

        assert _media_query_components(
            "(max-width: 760px) or (min-width: 900px)") == [
            "(max-width: 760px)", "(min-width: 900px)"], (
            "the `or` split did not split a real `or` condition")
        # `or` as a SUBSTRING of a word is not an operator, and neither is an
        # `or` inside the parens of a function value.
        assert _media_query_components("(color: orange)") == ["(color: orange)"], (
            "`orange` was split as `or` + `ange`")
        assert _media_query_components("(max-width: var(--or, 760px))") == [
            "(max-width: var(--or, 760px))"], (
            "an `or` INSIDE the parens of `var(--or, 760px)` was treated as an "
            "operator, inventing two conditions that do not exist")

    def test_a_negated_media_query_is_reachable_not_a_hatch(self):
        """`not` inverts the query, so a `max-width` inside it can be DESKTOP.

        `@media not all and (max-width: 760px)` is TRUE at 1440x900: the inner
        `(max-width: 760px)` is false there, and `not` flips it. The reader
        modelled only the threshold, so it called the block a narrow hatch and
        hid the rule. Measured on this tree before the fix: the mutant
        `@media not all and (max-width: 760px) { .pricing-card .btn {
        margin-top: 0 } }` returned `reached=False`, so all four rows of
        `TestProductTeamsPricingCardCtaBaseline` stayed GREEN while a browser
        applied it and split the rendered CTAs back to three baselines.

        Negation is NOT modelled here, so the predicate fails CLOSED — any
        `not` query is treated as reaching a desktop viewport. That is
        deliberately conservative in the over-block direction: it can red a
        page that hides a rule behind `not (min-width: …)`, but it cannot hide
        an override, which is the direction that ships a defect.
        """
        for reached in (
                "not all and (max-width: 760px)",
                "not (min-width: 900px)",
                "NOT all and (max-width: 760px)",
                # Round 8's spelling: the negated operand need not be the
                # `max-` query at all — `print` is false at 1440x900, so
                # negating the conjunction makes the block apply there.
                "not print and (max-width: 760px)",
        ):
            assert _media_applies_at_desktop(reached), (
                f"`@media {reached}` carries a `not`, so its `max-` threshold "
                "does not mean what this reader reads it as — an unmodelled "
                "negation must fail CLOSED (reaching), never be treated as a "
                "narrow hatch (#14406 round-7 class, the `not` spelling)")

        # ...and the page's OWN un-negated hatches stay excluded, or this fix
        # would turn every narrow-viewport rule into a false RED.
        assert not _media_applies_at_desktop("(max-width: 760px)")
        assert not _media_applies_at_desktop("(max-height: 820px)")

    def test_an_or_and_a_negated_media_override_are_reached(self):
        """The page-level mutants the comma-only splitter left GREEN.

        Both rules apply at 1440x900 in a browser and both restore the
        #13645 item 6 signature — three CTA baselines, 70.2px apart — while
        `test_no_other_rule_takes_the_ctas_slack` and the flex-column row stayed
        GREEN, because the reach reader never yielded the declaration at all.
        """
        for mutant in (
                "@media (max-width: 760px) or (min-width: 900px) { "
                ".pricing-card .btn { margin-top: 0 } }",
                "@media not all and (max-width: 760px) { "
                ".pricing-card .btn { margin-top: 0 } }",
        ):
            reached = [(p, v) for p, v in
                       _css_reaching_decls(self._page_with(mutant), "margin-top", ".btn")]
            assert any(v.strip() == "0" for _p, v in reached), (
                f"`{mutant}` applies at 1440x900 but the reach reader did not "
                "see `.pricing-card .btn { margin-top: 0 }`, so the CTA rows "
                f"stay GREEN over a 3-baseline split. Reached: {reached!r}")
