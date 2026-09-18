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

# The console the /admin path genuinely means (#3501/#3952).
CONSOLE = "https://tortoise.premiselabs.co/admin/"

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
        super().__init__(convert_charrefs=True)
        self.meta_http_equiv: list[str] = []
        self.bases: list[str] = []
        self.forms: list[str] = []
        self.event_handlers: list[str] = []
        self.script_srcs: list[str] = []
        self.javascript_urls: list[str] = []
        self.scripts: list[str] = []
        self._buf: list[str] = []
        self._in_script = False

    def unclosed(self) -> bool:
        """True when a `<script>` was never closed — its body is never collected,
        so it would otherwise be invisible to the pin (#4006 review, cycle 5)."""
        return self._in_script

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            # HTMLParser lowercases attribute names, so `ONLOAD` is caught too.
            if name.lower().startswith("on"):
                self.event_handlers.append(f"<{tag} {name}>")
            if name.lower() == "href" and (value or "").strip().lower().startswith("javascript:"):
                self.javascript_urls.append(f"<{tag} href={value!r}>")
            if name.lower() == "src" and tag == "script":
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
    doc.feed(body)
    assert not doc.meta_http_equiv, (
        "404.html carries a meta http-equiv — the only reason a not-found page "
        "needs one in this deployment is a refresh/redirect, which discards the "
        f"requested address instead of reporting it (#4006 review): {doc.meta_http_equiv!r}"
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
    assert not doc.javascript_urls, (
        "404.html links to a `javascript:` URL — a click on a not-found page's "
        f"own link must not run code: {doc.javascript_urls!r} (#4006 review)"
    )
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
    # The page's scripting surface is EXACTLY one reviewed, read-only snippet
    # (it names the requested address so the visitor can see what was not
    # found). Comments and whitespace are ignored; ANY other change fails, which
    # is what closes the families that carry no `location` token to match.
    joined = "\n".join(doc.scripts)
    compact = re.sub(r"//[^\n]*", "", joined)
    compact = re.sub(r"\s+", "", compact)
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


def test_admin_redirects_to_the_console() -> None:
    """/admin genuinely means the console (#3501/#3952) — it must send the
    visitor there, not render the dashboard overview."""
    admin_rules = {source: rule for source, rule in _first_wins().items()
                   if source.startswith("/admin")}
    for source in ("/admin", "/admin/", "/admin/*"):
        assert source in admin_rules, f"no routing rule for {source}"
        dest, code = admin_rules[source]
        assert code == 301, f"{source} must be a permanent redirect, got {code}"
        assert dest.startswith(CONSOLE), (
            f"{source} must target the console {CONSOLE!r}, got {dest!r}"
        )
    # A prefix redirect without the splat would drop the console sub-path.
    assert admin_rules["/admin/*"][0] == CONSOLE + ":splat"


def test_valid_app_routes_still_serve_the_app() -> None:
    """The app's own pathnames must keep serving the app (200), with the URL
    intact — /welcome is what welcome mode keys on, and /team carries the
    Stripe ?session_id= handoff in the query string."""
    routed = _first_wins()
    for source in ("/welcome", "/welcome/", *SERVER_BUILT_ROUTES):
        assert source in routed, f"valid app pathname {source} is not routed"
        dest, code = routed[source]
        assert code == 200, (
            f"{source} must serve the app at its own URL, got {code} → {dest} "
            "(a redirect would drop the pathname or the query)"
        )
        assert dest == "/", f"{source} must serve the app document, got {dest!r}"


def test_every_pathname_the_app_branches_on_is_routed() -> None:
    """Anti-drift: the app decides behavior from location.pathname, and any
    such pathname is an app route. Adding a branch in ANY source module
    without a routing rule would 404 the user who lands on it.

    SCOPE, stated so this is not read as more than it is (#4006 review): the
    matcher recognises the `pathname === '<literal>'` form ONLY. A branch
    written with `startsWith`, `!==`, a `switch`, or a pathname built from a
    constant is NOT detected — add its rule to `_redirects` by hand, and add
    the pathname to SERVER_BUILT_ROUTES if the server builds it.
    """
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
    # `/` is served by the index.html asset, never by a rule — always routed.
    routed = {source for source in _first_wins()} | {"/", ""}
    missing = sorted(b for b in branches if b not in routed)
    assert not missing, (
        f"the app branches on {missing!r} but public/_redirects has no rule — "
        "that pathname would 404 (#3523)"
    )
