# tests/test_dashboard_unknown_address.py
"""#3523: app.premiselabs.co must answer an unknown address honestly.

The dashboard is hash-routed (`src/main.jsx` reads `location.hash`; the tabs
ride the hash), so it owns a small, closed set of pathnames. Before this fix
the Pages project had no top-level `404.html`, so Cloudflare Pages treated it
as a single-page app and served the root document with HTTP 200 for EVERY
path — `/admin` was byte-identical to `/`, and the address the visitor asked
for was silently discarded (the soft-404 anti-pattern).

These pin the deployed routing inputs in `public/`, because the two ways the
soft-404 comes back are both invisible in a diff: deleting `404.html`, or
adding a catch-all rewrite (`/* / 200`) to "fix" a 404. They also pin the
`/index.html` rewrite footgun — Pages normalizes a `.html` destination with
its own redirect, so a 200 rewrite to `/index.html` becomes a 308 to `/` and
silently drops the pathname the SPA branches on.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PUBLIC = (
    Path(__file__).resolve().parents[1]
    / "website" / "apps" / "dashboard" / "public"
)
REDIRECTS = PUBLIC / "_redirects"
NOT_FOUND = PUBLIC / "404.html"
SRC = PUBLIC.parent / "src"

# This origin's Functions (website/apps/dashboard/functions). A matching
# Function is consulted BEFORE `_redirects` on inbound routing — verified on the
# real runtime, not inferred (#4104) — so a Function is a routing owner in its
# own right, and a `_redirects` rule on the same pathname never fires for a
# visitor. Routing has two mechanisms; this guard must know both, or it reports
# a served pathname as unrouted.
FUNCTIONS = PUBLIC.parent / "functions"

# Pathnames that are app routes even though they are not the root — they are
# not derived from main.jsx (Stripe builds /team from the server side), so
# they are stated here as the closed set the routing must preserve.
SERVER_BUILT_ROUTES = ("/team", "/team/")

# 404.html's ONLY script, pinned EXACTLY. It exists to name the requested
# address, and it must stay read-only. The pin is what closes the family a
# lexical check cannot: `window["loc"+"ation"]="/"`, `\u006cocation="/"` and
# `eval(atob(…))` contain no `location` token to match, but they DO change the
# script — so they fail this test instead of slipping past a substring scan
# (#4006 review, cycle 4). Any edit here must be reviewed.
REVIEWED_404_SCRIPT = (
    'document.getElementById("requested").textContent='
    'location.pathname+(location.search||"");'
)

# Elements that can execute or navigate on their own. A not-found page needs
# none of them, and `<iframe srcdoc="<script>top.location.replace('/')</script>">`
# navigated a real browser past every other check (#4006 review, cycle 6).
#
# `noscript` is the one element where Python's parser and the browser parse the
# SAME bytes with DIFFERENT rules: the tokenizer raw-texts its content when
# scripting is enabled (the state the page is served in), while `html.parser`
# does not — so `<noscript><style></noscript><script>top.location="/"</script></style>`
# had the guard's parser swallow the script into style data and see only the
# reviewed snippet, while Chromium ended the style at the browser's
# `</noscript>` and RAN it. Verified to navigate top-level (#4006 review, cycle 10).
FORBIDDEN_ELEMENTS = frozenset({
    "iframe", "object", "embed", "applet", "frame", "frameset", "portal",
    "svg", "math", "noscript",
})

# Attributes whose value a browser resolves as a URL. A scheme check must
# normalize the way the URL parser does: ASCII tab/newline/CR are STRIPPED
# before the scheme is read, so `java\tscript:…` IS `javascript:…` (#4006
# review, cycle 6).
URL_ATTRS = frozenset({
    "href", "src", "srcdoc", "data", "action", "formaction", "poster",
    "ping", "background", "cite", "longdesc", "usemap", "manifest",
    "xlink:href",
})

# The URL standard strips LEADING/TRAILING C0 control or space (and tab, LF and
# CR anywhere) before a URL's scheme is read, so `\x01javascript:…` IS
# `javascript:…`. Python's str.strip() removes only a subset — `\x01`–`\x08`
# survived the first version of this check and Chromium executed the payload on
# a click (#4006 review, cycle 7).
_URL_SPLIT_CHARS = re.compile(r"[\t\n\r]")
_URL_EDGE_STRIP = "".join(chr(code) for code in range(0x21)) + "\x7f"


def _rules() -> list[tuple[str, str, int]]:
    """Parse `[source] [destination] [code]` lines, ignoring comments."""
    rules = []
    for raw in REDIRECTS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 3, f"malformed _redirects line: {raw!r}"
        source, destination, code = parts
        rules.append((source, destination, int(code)))
    return rules


class _NotFoundDoc(HTMLParser):
    """Structural facts about 404.html, from a real parse.

    A substring list cannot enumerate the equivalent spellings of a redirect:
    HTML attribute names and the `refresh` keyword are ASCII case-insensitive,
    attributes may be unquoted, and JS can reach the same effect through bracket
    notation, split strings, unicode escapes or `eval` (#4006 review, cycles
    3-4). So the guard constrains the SHAPE of the page: NO element that can
    navigate on its own (meta refresh, form submit, base rebase) and no inline
    event handler — plus an exact pin on the page's single script, which is the
    only scripting surface a not-found page needs.
    """

    def __init__(self) -> None:
        # `scripting=True` is the state the page is actually SERVED in: the
        # browser raw-texts `<noscript>` content and shows it never. Leaving
        # Python's default (False) made this parser read markup the browser
        # treats as inert — a divergence in the wrong direction. The element is
        # also banned outright, which is what closes the scripting-OFF case
        # where a `<noscript><meta http-equiv=refresh>` would still be honoured.
        super().__init__(convert_charrefs=True, scripting=True)
        self.meta_http_equiv: list[str] = []
        self.bases: list[str] = []
        self.forms: list[str] = []
        self.event_handlers: list[str] = []
        self.script_srcs: list[str] = []
        self.blocked_elements: list[str] = []
        self.url_attrs: list[tuple[str, str, str]] = []
        self.scripts: list[str] = []
        self._buf: list[str] = []
        self._in_script = False

    def unclosed(self) -> bool:
        """True when a `<script>` was never closed — its body is never collected,
        so it would otherwise be invisible to the pin (#4006 review, cycle 5)."""
        return self._in_script

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in FORBIDDEN_ELEMENTS:
            self.blocked_elements.append(f"<{tag}>")
        for name, value in attrs:
            lowered = name.lower()
            # HTMLParser lowercases attribute names, so `ONLOAD` is caught too.
            if lowered.startswith("on"):
                self.event_handlers.append(f"<{tag} {name}>")
            if lowered in URL_ATTRS:
                self.url_attrs.append((tag, lowered, value or ""))
            if lowered == "src" and tag == "script":
                # A script that carries its body out-of-band has no text to pin,
                # and the empty entry vanished from the joined string — so
                # `<script src="data:text/javascript,location.replace('/')">`
                # redirected a real browser past the pin (#4006 review, cycle 5).
                self.script_srcs.append(value or "")
        if tag == "meta":
            for name, value in attrs:
                if name.lower() == "http-equiv":
                    self.meta_http_equiv.append((value or "").strip().lower())
        elif tag == "base":
            self.bases.append(f"<base {attrs!r}>")
        elif tag == "form":
            self.forms.append(f"<form {attrs!r}>")
        elif tag == "script":
            self._in_script = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            self.scripts.append("".join(self._buf))
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._buf.append(data)


DOCTYPE_ONLY = re.compile(r"(?is)<!doctype[^>]*>")


def test_top_level_404_html_uses_no_markup_declaration_besides_doctype() -> None:
    """Every `<!` declaration except the doctype is refused, not modelled.

    Python's HTMLParser and the HTML5 tokenizer disagree about the whole family,
    and each disagreement hid a payload the browser then ran or acted on — the
    element never reached `handle_starttag`, so the script pin, the element bans
    and the URL checks never saw it (#4006 review, cycles 8-9):

    * `<!--` — Python closes a comment only at `--` + optional space + `>`, the
      tokenizer ALSO at `<!-->` (abrupt-empty) and `<!--->` (abrupt-closing), and
      at `--!>`; so a following `<script>` or `<meta http-equiv="refresh">` was
      swallowed into one comment while Chromium ended it and complied;
    * `<![CDATA[…]]>` (and any `<![name[…]]>`) — Python consumes through the
      first `]]>`, but in HTML content the tokenizer raises a
      cdata-in-html-content parse error and emits a BOGUS COMMENT ending at the
      first `>`, so it resumes parsing and honours the markup Python swallowed;
    * and the REVERSE holds too — a `<!--` inside a quoted attribute value is
      plain attribute text to the browser, so a guard that treats it as a
      comment start would hide a real `<meta http-equiv="refresh">` from itself.

    Each spelling was fixable one at a time; the class is not, because it is the
    guard's own parse that is wrong. A not-found page needs exactly one `<!`
    declaration — the doctype, whose `>`-terminated reading the two agree on — so
    the guard refuses the rest outright instead of modelling them. The page's own
    `#3523` rationale lives in this file and in the script's comment, not in HTML
    comments. (A spec-accurate tokenizer or a real browser is the durable answer;
    filed as #4073.)
    """
    body = NOT_FOUND.read_text()
    # Strip the one declaration both parsers read identically: the doctype ends
    # at its first `>` in Python AND in the tokenizer's DOCTYPE state.
    remainder = DOCTYPE_ONLY.sub("", body)
    assert "<!" not in remainder, (
        "404.html carries a markup declaration other than the doctype (an HTML "
        "comment, a `<![…[` marked section, or any other `<!`). Python's "
        "HTMLParser and the browser disagree about every one of these — the "
        "browser parses and acts on markup Python swallows — so declarations "
        "are refused rather than modelled (#4006 review): "
        f"{remainder[remainder.find('<!'):][:40]!r}"
    )


def _first_wins() -> dict[str, tuple[str, int]]:
    """First-match lookup over `_redirects`.

    Cloudflare Pages applies the TOP-MOST matching rule, so a later duplicate
    does NOT override an earlier one. A plain dict comprehension is LAST-wins
    and therefore hides a shadowing duplicate (#4006 review): `/admin / 200`
    placed ABOVE the real 301 restores the exact soft-404 this change removes,
    while the last-wins view still reports the 301.
    """
    routed: dict[str, tuple[str, int]] = {}
    for source, destination, code in _rules():
        routed.setdefault(source, (destination, code))
    return routed


def _function_routes() -> tuple[set[str], set[str]]:
    """Pathname shapes owned by a Function on this origin.

    Routing SHAPES, never a blanket prefix. `[[path]].ts` catches its whole
    subtree, but a plain `foo.ts` serves `/foo` (and its trailing-slash twin
    `/foo/`) ONLY — `/foo/bar` is a 404. Modelling a file Function as a subtree
    made this guard fail OPEN: a branch on a nested pathname under any existing
    Function prefix (`/auth/totally-unrouted`) was reported as routed while
    Cloudflare answered 404, verified against `wrangler pages dev dist`
    (#4104 review, cycle 2).

    Returns (exact, subtree).
    """
    exact: set[str] = set()
    subtree: set[str] = set()
    for path in FUNCTIONS.rglob("*.ts"):
        parts = list(path.relative_to(FUNCTIONS).parts)
        # `_shared/`, `_middleware.ts` are not routes.
        if any(part.startswith("_") for part in parts):
            continue
        name = parts[-1]
        if len(parts) > 1 and name == "[[path]].ts":
            subtree.add("/" + "/".join(parts[:-1]))
        elif name == "index.ts":
            base = "/" + "/".join(parts[:-1])
            exact.update((base, base + "/"))
        else:
            base = "/" + "/".join([*parts[:-1], name[: -len(".ts")]])
            exact.update((base, base + "/"))
    return exact, subtree


def _is_routed(pathname: str) -> bool:
    """True when the pathname is SERVED rather than handed to 404.html.

    `/` is the index.html asset and never needs a rule. Everything else is
    routed by a `_redirects` rule, an exact Function (plus its trailing-slash
    twin), or a catch-all Function's subtree.
    """
    if pathname in ("/", ""):
        return True
    if pathname in _first_wins():
        return True
    exact, subtree = _function_routes()
    if pathname in exact:
        return True
    return any(
        pathname == prefix or pathname.startswith(prefix + "/")
        for prefix in subtree
    )


def test_no_duplicate_rule_sources() -> None:
    """Pages is FIRST-match, so a duplicated source is ambiguous: the higher
    line wins and the lower one is dead. That shadowing pair is how the bug
    comes back while the file still looks like it routes correctly."""
    sources = [source for source, _dest, _code in _rules()]
    duplicates = sorted({s for s in sources if sources.count(s) > 1})
    assert not duplicates, (
        f"duplicate rule source(s) {duplicates!r} — Cloudflare Pages applies "
        "the FIRST match, so the lower line is dead and a duplicate above a "
        "real rule silently overrides it (#4006 review)"
    )


def test_top_level_404_html_exists() -> None:
    """Without a top-level 404.html, Pages reverts to SPA fallback: every
    unmatched path answers 200 with the root document. The file IS the fix."""
    assert NOT_FOUND.is_file(), (
        "website/apps/dashboard/public/404.html is missing — Cloudflare Pages "
        "will again serve the dashboard overview (HTTP 200) for every unknown "
        "path (#3523)"
    )
    body = NOT_FOUND.read_text()
    assert "noindex" in body, "the not-found page must not be indexed"
    # The page must not RE-REDIRECT the visitor: a not-found page that bounces
    # to `/` discards the requested address exactly as the soft-404 did — the
    # symptom this change exists to remove, wearing a 404 status.
    #
    # Enumerating forbidden SPELLINGS does not work (#4006 review, cycles 3-4):
    # `<meta http-equiv=REFRESH>` (unquoted, different case),
    # `location['href']='/'`, `window['location']='/'`, `open('/','_self')`,
    # `<body onload="location='/'">`, `<form>.submit()` and `<base href>` each
    # defeat a substring list, and the split-string / `\u006cocation` /
    # `eval(atob(…))` family contains no token to match at all. So the guard
    # constrains the SHAPE of the page instead.
    doc = _NotFoundDoc()
    # Parse the RAW body: the guard refuses comment syntax outright (see
    # `test_top_level_404_html_uses_no_comment_syntax`), so there is no comment
    # for this parser and the browser to disagree about.
    doc.feed(body)
    assert not doc.meta_http_equiv, (
        "404.html carries a meta http-equiv. A not-found page has no use for one "
        "here — security headers are set at the edge (public/_headers) — and the "
        "one http-equiv that DOES belong to a page like this is a refresh, which "
        "redirects and discards the address that was not found "
        f"(#4006 review): {doc.meta_http_equiv!r}"
    )
    assert not doc.bases, (
        "404.html carries a <base> — it re-points the page's own links (here, "
        f"the only CTA) at another origin: {doc.bases!r} (#4006 review)"
    )
    assert not doc.forms, (
        "404.html carries a <form> — a submit navigates the top-level document "
        f"and discards the requested address: {doc.forms!r} (#4006 review)"
    )
    assert not doc.event_handlers, (
        "404.html carries an inline event handler, which can navigate and which "
        f"no attribute-level scan can enumerate: {doc.event_handlers!r} (#4006 review)"
    )
    assert not doc.script_srcs, (
        "404.html loads an external script — an out-of-band body cannot be pinned, "
        "and a `data:` URL executes on a page with no CSP: "
        f"{doc.script_srcs!r} (#4006 review)"
    )
    assert not doc.blocked_elements, (
        "404.html carries an element that can execute or navigate on its own "
        f"(<iframe srcdoc>, <object data>, SVG script…): {doc.blocked_elements!r} "
        "— a not-found page needs none of them (#4006 review)"
    )
    unsafe_urls = [
        (tag, attr, value)
        for tag, attr, value in doc.url_attrs
        # Normalize exactly as the URL parser does before reading the scheme:
        # tab/LF/CR are removed anywhere and C0-control/space/DEL are stripped
        # from the ends, so `java\tscript:…` and `\x01javascript:…` both resolve
        # to the scripting scheme (#4006 review, cycles 6-7).
        if _URL_SPLIT_CHARS.sub("", value).strip(_URL_EDGE_STRIP).lower().startswith(
            ("javascript:", "vbscript:")
        )
    ]
    assert not unsafe_urls, (
        "404.html links to a scripting URL — a click on the not-found page's own "
        f"link must not run code: {unsafe_urls!r} (#4006 review)"
    )
    joined = "\n".join(doc.scripts)
    # EXACTLY one script, and it is the reviewed inline snippet. Counting matters:
    # `<script src=…>` contributes an EMPTY entry, and the whitespace strip below
    # erased it, so a source-only script rode the pin (#4006 review, cycle 5).
    assert len(doc.scripts) == 1, (
        "404.html must carry exactly ONE inline script (the reviewed snippet), "
        f"found {len(doc.scripts)} (#4006 review)"
    )
    assert not doc.unclosed(), (
        "404.html has an unterminated <script> — its body never reaches the pin "
        "(#4006 review)"
    )
    # NO `<` anywhere in script data. A browser's script-data ESCAPED / DOUBLE-
    # ESCAPED states do not close the element at the same `</script>` this
    # parser does, so `REVIEWED + "<!--<script></script>\ntop.location=…"`
    # executed in Chromium while the pin saw only the reviewed text (#4006
    # review, cycle 6). The reviewed snippet contains no `<`, so banning it
    # removes the whole state family by construction rather than by enumeration.
    assert "<" not in joined, (
        "404.html's script contains `<` — HTML-like script data can keep the "
        "element open in the browser past the point this parser closes it "
        f"(#4006 review): {joined.strip()!r}"
    )
    # ASCII ONLY in the code (see below).
    # The page's scripting surface is EXACTLY one reviewed, read-only snippet
    # (it names the requested address so the visitor can see what was not
    # found). Comments and whitespace are ignored; ANY other change fails, which
    # is what closes the families that carry no `location` token to match.
    # Comment stripping ends at EVERY JS line terminator, not just `\n`.
    compact = re.sub(r"//[^\n\u2028\u2029]*", "", joined)
    compact = re.sub(r"\s+", "", compact)
    # The CODE (comments excluded — they may carry typographic punctuation, as
    # this page's own comment does) must be pure ASCII. That removes the whole
    # lexer-vs-browser disagreement family by construction: U+2028/U+2029 end a
    # `//` comment in the browser but not in a regex, so logic hidden after one
    # used to ride the pin (#4006 review, cycle 7).
    assert compact.isascii(), (
        "404.html's script CODE contains non-ASCII characters, which the JS "
        "lexer and this guard can disagree about (U+2028/U+2029 end a `//` "
        f"comment): {compact!r} (#4006 review)"
    )
    assert compact == REVIEWED_404_SCRIPT, (
        "404.html's script is not the reviewed read-only snippet — a not-found "
        "page needs no other script, and anything else it can do is navigate "
        f"away from the address that was not found (#4006 review): {compact!r}"
    )
    # It is an honest not-found page, not a second copy of the app shell. Check
    # the DEPLOYED shell markers too, not just the vite dev entry: the built
    # document references /assets/index-<hash>.js, not /src/main.jsx, so a
    # copied dist/index.html would sail past a dev-path-only assertion.
    shell_markers = (
        "/src/main.jsx",              # vite dev entry
        "/assets/",                   # built bundle + stylesheet
        "supabase-session.js",        # the shell's synchronous session bridge
        "supabase-2.112.2.min.js",    # the shell's vendored supabase UMD build
        "readValidSession",           # the shell's auth-gate helper
    )
    copied = [marker for marker in shell_markers if marker in body]
    assert not copied, (
        "404.html must be a standalone not-found page, not a copy of the app "
        f"shell — found {copied!r}"
    )


def test_no_catch_all_spa_rewrite() -> None:
    """A catch-all rewrite silently restores the soft-404 (every path 200s
    with the root document) — that is the bug, not a fix for it."""
    offenders = [rule for rule in _rules() if rule[0] in ("/*", "/:splat", "*")]
    assert not offenders, (
        f"catch-all rewrite re-introduces the #3523 soft-404: {offenders!r}"
    )


def test_no_index_html_rewrite_destination() -> None:
    """Pages normalizes a `.html` rewrite destination with its own redirect:
    a 200 to `/index.html` becomes a 308 to `/`, dropping the pathname the
    SPA branches on (and looping in wrangler). The destination is `/`."""
    offenders = [
        rule for rule in _rules() if rule[2] == 200 and rule[1].endswith(".html")
    ]
    assert not offenders, (
        "rewrite to a .html asset — Pages will redirect-strip it and the "
        f"pathname will be lost: {offenders!r}"
    )


def test_admin_is_owned_by_its_function_not_a_redirect_rule() -> None:
    """/admin genuinely means the console (#3501/#3952) — and since #4104/#4171
    the console is SERVED ON THIS ORIGIN by the gate, not redirected to the
    marketing origin.

    This replaces an assertion that /admin carried a 301 to
    https://tortoise.premiselabs.co/admin/. That rule both pointed the opposite
    way from the architecture this change implements AND was dead code: a
    matching Function is consulted before `_redirects`, so it never fired. The
    gate existing is the load-bearing half; a rule here would only be a second,
    losing owner.
    """
    gate = FUNCTIONS / "admin" / "[[path]].ts"
    assert gate.is_file(), (
        "website/apps/dashboard/functions/admin/[[path]].ts is missing — /admin "
        "has no owner on this origin, so it falls through to 404.html and the "
        "console is unreachable (#3523/#4171)"
    )
    body = gate.read_text()
    assert "verifySession" in body and "isAdmin" in body, (
        "the /admin gate no longer gates — expected the session and admin "
        "checks to still be there (#3501/#4171)"
    )
    admin_rules = [s for s in _first_wins() if s.startswith("/admin")]
    assert not admin_rules, (
        f"public/_redirects carries {admin_rules!r} for a pathname a Function "
        "already owns. The Function wins inbound, so the rule is dead for "
        "visitors; and a rule crossing to another origin is exactly the "
        "cross-origin bounce this change removes (#4104)"
    )


def test_function_ownership_models_routing_shapes_not_subtrees() -> None:
    """A plain `foo.ts` Function owns `/foo` and `/foo/` — NOT `/foo/bar`.

    Regression pin. The first version of `_is_routed` treated EVERY Function
    prefix as a subtree, so it answered True for `/auth/totally-unrouted` and
    `/welcome/foo` while Cloudflare Pages answered 404 — and because
    `test_every_pathname_the_app_branches_on_is_routed` asks it the same
    question, the anti-drift gate FAILED OPEN for the whole class "a pathname
    nested under an existing Function prefix", which is the drift it exists to
    catch. Verified at runtime against `wrangler pages dev dist`: `/welcome` and
    `/welcome/` → 302 (served), `/welcome/foo` and `/auth/xyz` → 404 (#4104
    review, cycle 2).
    """
    # Subtree owners: the dynamic catch-alls, and only those.
    assert _is_routed("/admin/anything"), "admin/[[path]].ts owns its subtree"
    assert _is_routed("/api/v1/graphs/1"), "api/v1/[[path]].ts owns its subtree"
    # Exact owners, plus their trailing-slash twin.
    assert _is_routed("/welcome") and _is_routed("/welcome/")
    assert _is_routed("/auth/reset")
    # …and NOTHING deeper under an exact owner: these all 404 in production.
    for unrouted in ("/welcome/foo", "/auth/xyz", "/welcome/foo/bar"):
        assert not _is_routed(unrouted), (
            f"{unrouted!r} is served by no Function and no rule — Cloudflare "
            "answers 404, so reporting it as routed makes the anti-drift guard "
            "fail open (#4104)"
        )


def test_valid_app_routes_still_serve_the_app() -> None:
    """The app's own pathnames must keep serving the app, with the URL intact —
    /welcome is what welcome mode keys on, and /team carries the Stripe
    ?session_id= handoff in the query string.

    /welcome is served by `functions/welcome.ts`, NOT by a rule, and that
    distinction is load-bearing rather than cosmetic. The Function's recovery
    branch calls `env.ASSETS.fetch`, which re-enters the ASSET router — where
    `_redirects` DOES apply, unlike inbound routing. A `/welcome / 200` rewrite
    therefore answered the Function itself with the shell document instead of
    `welcome.html`, and the reset panel silently disappeared (#4104).

    /team keeps its rule: Stripe builds that page from the server side, so it is
    a 200 rewrite of the app document, not a Function.
    """
    routed = _first_wins()
    for source in SERVER_BUILT_ROUTES:
        assert source in routed, f"valid app pathname {source} is not routed"
        dest, code = routed[source]
        assert code == 200, (
            f"{source} must serve the app at its own URL, got {code} → {dest} "
            "(a redirect would drop the pathname or the query)"
        )
        assert dest == "/", f"{source} must serve the app document, got {dest!r}"
    welcome_rules = [s for s in routed if s.startswith("/welcome")]
    assert not welcome_rules, (
        f"public/_redirects routes {welcome_rules!r}. `functions/welcome.ts` owns "
        "/welcome and wins on inbound routing, so the rule is dead for visitors "
        "— but not for the Function's own `env.ASSETS.fetch`, which re-enters "
        "the asset router where `_redirects` applies. That is how the recovery "
        "landing lost its reset panel (#4104)"
    )
    assert _is_routed("/welcome"), "functions/welcome.ts must own /welcome"


def test_every_pathname_the_app_branches_on_is_routed() -> None:
    """Anti-drift: the app decides behavior from location.pathname, and any
    such pathname is an app route. Adding a branch in ANY source module
    without a routing owner would 404 the user who lands on it.

    "Routed" now means a `_redirects` rule OR a Function on this origin — a
    Function is consulted first, so it is an owner in its own right. A
    `_redirects`-only view reported /welcome as unrouted while
    `functions/welcome.ts` was serving it (#4104).

    SCOPE, stated so this is not read as more than it is (#4006 review): the
    matcher recognises the `pathname === '<literal>'` form ONLY. A branch
    written with `startsWith`, `!==`, a `switch`, or a pathname built from a
    constant is NOT detected — give it a routing owner by hand, and add the
    pathname to SERVER_BUILT_ROUTES if the server builds it.
    """
    # Non-vacuity: if the Function scan found nothing, every branch would be
    # reported missing and this guard would be asserting the wrong thing.
    # Both shapes must be present, or the owner check is only half-modelled.
    exact, subtree = _function_routes()
    assert exact and subtree, (
        f"the Function scan is incomplete ({len(exact)} exact, {len(subtree)} "
        "subtree) — the routing-owner check would misreport"
    )
    branches: set[str] = set()
    # Scope to the module extensions the app ships, and exclude every test
    # flavour. `*.js*` also matched `.json` and missed `.test.tsx` (#4006
    # review).
    sources = sorted(
        p for p in SRC.rglob("*")
        if p.is_file()
        and p.suffix in (".js", ".jsx", ".ts", ".tsx")
        and ".test." not in p.name
    )
    for source in sources:
        branches |= set(re.findall(
            r"""pathname\s*===\s*['"]([^'"]+)['"]""", source.read_text()
        ))
    assert branches, "expected the app to branch on location.pathname"
    missing = sorted(b for b in branches if not _is_routed(b))
    assert not missing, (
        f"the app branches on {missing!r} but neither public/_redirects nor a "
        "Function on this origin serves it — that pathname would 404 (#3523)"
    )
