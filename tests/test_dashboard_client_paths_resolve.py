# tests/test_dashboard_client_paths_resolve.py — #4446.
#
# WHY THIS FILE EXISTS
# --------------------
# The dashboard reaches the hosted API only through the same-origin BFF proxy
# (`website/apps/dashboard/functions/api/v1/[[path]].ts`), which rebuilds the upstream
# URL as `${API_ORIGIN}/v1/${rest}`. Nothing connected the paths the client actually
# requests to the routes the server actually serves. #4144 was that gap firing: the
# Backups tab asked for `/api/backups` (a path the proxy cannot build) and the card
# read as empty while backups existed.
#
# A 1418-line static guard was written for it in #4345 and hardened through seven
# review rounds — nearly all of them on the guard rather than on the fix — and was
# dropped from the merge (`ed0079b73`), its job filed as #4446. Every residual it kept
# finding was an artefact of reading `hosted_api.py` as TEXT: a path could be correct in
# the source and unreachable in the app; a verb could be read from a variable; a
# method-blind Pages half could certify a 405.
#
# WHAT IS PINNED, AND WHY IT IS DERIVED
# -------------------------------------
# 1. `/api/v1/<rest>` must be served by BOTH halves: the Pages proxy file must exist AND
#    `app.routes` must carry a route whose TEMPLATE matches the upstream path. The route
#    table is the object that answers requests, so a route deleted from the app cannot
#    stay "present" in a comment, and the methods compared against are the route's own.
# 2. `/api/<rest>` (no `/v1`) and `/auth/<rest>` are served by Cloudflare Pages
#    Functions, whose routing IS the file tree: the candidate resolves to the file that
#    serves it, and that file's `onRequest<Verb>` exports decide which methods it
#    accepts — the dropped guard's method-blind Pages half, closed by deriving the verbs
#    from the exported handler names instead of assuming every function answers every
#    verb.
# 3. Non-vacuity. Every assertion below runs over a set derived from source; an
#    extractor that quietly stopped matching would leave them all passing over an empty
#    set (#4207: a floor, not a pin).
#
# DECLARED RESIDUALS — disclosed, not silent (#4446)
# --------------------------------------------------
# * A trailing hole is genuinely ambiguous at the call site: `${q}` is a query string on
#   `/v1/team/keys` and `${id}` is a segment of `/v1/sessions/{id}`, and the two are
#   indistinguishable without evaluating the hole. This file therefore accepts EITHER
#   reading for a trailing hole and DECLARES each site that needed the elastic one
#   (`TRAILING_HOLE_SITES`), so a new ambiguity is visible rather than absorbed. The
#   window that leaves open (a segment appended to a route that exists only without it)
#   is real and stated here.
# * A path whose value is built at runtime cannot be decided statically at all. That is
#   what the browser lane is for; this repo has no dashboard browser lane (the
#   dashboard's own runner is `node --test src/*.test.js`), which is why #4446 carries
#   it as follow-up rather than growing a second static scanner here.
# * Call sites whose first argument is not a literal are enumerated and PINNED, so a new
#   one reds this file and asks for a human decision instead of being skipped.
# * Pages ASSETS (`public/`) are out of scope: this file reads the two seams that reach
#   a server (`api(…)` and `fetch(…)` under `${API_BASE}`, plus `authAction(…)` under
#   the site root), not navigation URLs.
from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

from tortoise.hosted_api import app

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "website" / "apps" / "dashboard"
SRC = DASH / "src"
FUNCTIONS = DASH / "functions"
API_BASE = "/api"
PROXY = FUNCTIONS / "api" / "v1" / "[[path]].ts"

SEAMS = ("api", "authAction", "fetch")
HOLE = "\x00"

# Compiled once and matched AT AN OFFSET (`pattern.match(text, i)`) rather than against
# `text[i:]`: slicing per character copies the whole remaining file, which turned a
# 400 KB source into ~77 s of copying per test before this was fixed. The suite's own
# runtime is part of its cost here — a guard that is slow enough to notice gets disabled.
_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_METHOD = re.compile(r"\bmethod\s*:\s*('[^']*'|\"[^\"]*\"|`[^`]*`)")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_AUTH_DEF = re.compile(r"(?:async\s+)?function\s+authAction\s*\(")
_ON_REQUEST = re.compile(r"export\s+(?:const|function|async\s+function)\s+(onRequest\w*)")

# Call sites whose first argument is not a literal path, pinned by (call, argument) so a
# NEW one is a red rather than a silent gap. Each entry names why it cannot be read here:
# * `api(path, opts)` / `authAction(path, body)` — the wrappers' own bodies; the value
#   arrives from the call sites, which ARE checked.
# * `fetch(path, …)` — the body of `authAction`, same reason.
# * `api(url, …)` — the forward inside `startBoundedPoll(ref, { url, … })`. Its three
#   callers pass the path as a `url:` PROPERTY (main.jsx:3283, 3324, 3363), which is not
#   a call argument and so is not read here. That is a REAL, declared gap: those three
#   paths are unverified until the browser lane (#4446) or a `url:`-property seam covers
#   them. Declared rather than chased — a fourth seam is what the dropped guard kept
#   adding, one review cycle at a time.
UNVERIFIABLE_SITES = {("api", "path"), ("authAction", "path"), ("fetch", "path"), ("api", "url")}

# Every site whose trailing hole had to be read elastically (the hole is a query string,
# not a segment — the declared ambiguity in the docstring). Each entry is a real call
# site whose literal path exists without the hole; a NEW one reds the shape test.
TRAILING_HOLE_SITES = {
    ("api", "/v1/backups", "GET"),
    ("api", "/v1/index/docs", "POST"),
    ("api", "/v1/index/github/re-poll", "POST"),
    ("api", "/v1/onboarding/github/connect", "POST"),
    ("api", "/v1/onboarding/github/repos", "GET"),
    ("api", "/v1/onboarding/github/status", "GET"),
    ("api", "/v1/onboarding/state", "GET"),
    ("api", "/v1/onboarding/state", "PATCH"),
    ("api", "/v1/onboarding/state/checkpoint", "POST"),
    ("api", "/v1/sessions", "GET"),
    ("api", "/v1/team", "GET"),
    ("api", "/v1/team/keys", "GET"),
    ("api", "/v1/team/keys", "POST"),
    ("fetch", "/v1/graphs/trash", "GET"),
    ("fetch", "/v1/graphs/trash", "POST"),
}


# ── reading the client, without scanning string bodies as if they were calls ─────────
def _skip_quoted(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text) and text[i] != quote:
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _skip_hole(text: str, i: int) -> int:
    """Skip a `${ … }` hole, honouring nested braces, strings and templates (a nested
    template inside a hole is what defeated the dropped guard's regex)."""
    depth = 1
    while i < len(text) and depth:
        c = text[i]
        if c in "'\"":
            i = _skip_quoted(text, i)
            continue
        if c == "`":
            i = _skip_template(text, i)
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return i


def _skip_template(text: str, i: int) -> int:
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i + 1
        if text[i] == "$" and text[i + 1 : i + 2] == "{":
            i = _skip_hole(text, i + 2)
            continue
        i += 1
    return i


def _skip_braces(text: str, i: int) -> int:
    depth = 0
    while i < len(text):
        c = text[i]
        if c in "'\"":
            i = _skip_quoted(text, i)
            continue
        if c == "`":
            i = _skip_template(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _regex_starts(text: str, i: int) -> bool:
    """Whether the `/` at `i` opens a regex literal rather than a division: true when the
    last significant character cannot end an expression. Skipping regexes matters here —
    a pattern containing a quote (`/_/g`) would otherwise open a phantom string that
    swallows real call sites, and a silent coverage loss is worse than a false red."""
    j = i - 1
    while j >= 0 and text[j] in " \t\n":
        j -= 1
    return j < 0 or text[j] in "(,=:[!&|?{};+*%~^<>"


def _skip_regex(text: str, i: int) -> int:
    i += 1
    in_class = False
    while i < len(text) and text[i] != "\n":
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "[":
            in_class = True
        elif text[i] == "]":
            in_class = False
        elif text[i] == "/" and not in_class:
            return i + 1
        i += 1
    return i


def _find_calls(text: str) -> list[tuple[str, int]]:
    """(seam name, offset) for every `api(`/`authAction(`/`fetch(` in CODE context.

    Comments, string bodies and template bodies are skipped, so the text of a literal
    can never be mistaken for a live call — the dropped guard's false red.
    """
    found: list[tuple[str, int]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"":
            i = _skip_quoted(text, i)
        elif c == "`":
            i = _skip_template(text, i)
        elif c == "/" and _regex_starts(text, i):
            i = _skip_regex(text, i)
        else:
            m = _CALL.match(text, i)
            if m and m.group(1) in SEAMS:
                found.append((m.group(1), m.end()))
                i = m.end()
            else:
                i += 1
    return found


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return i


def _first_argument(text: str, i: int) -> tuple[str, list[str], int]:
    """(kind, segments, end) for the first argument at `i`.

    kind is 'string' (one literal segment), 'template' (literal segments with HOLE where
    a `${…}` sat), or 'dynamic' (not a literal at all — reported, never silently
    skipped).
    """
    i = _skip_ws(text, i)
    if i >= len(text):
        return ("dynamic", [""], i)
    if text[i] in "'\"":
        end = _skip_quoted(text, i)
        return ("string", [text[i + 1 : end - 1]], end)
    if text[i] == "`":
        end = _skip_template(text, i)
        parts, buf, j = [], [], i + 1
        while j < end - 1:
            if text[j] == "\\":
                buf.append(text[j + 1])
                j += 2
                continue
            if text[j] == "$" and text[j + 1 : j + 2] == "{":
                parts.append("".join(buf))
                buf = []
                j = _skip_hole(text, j + 2)
                continue
            if text[j] == "`":
                break
            buf.append(text[j])
            j += 1
        parts.append("".join(buf))
        return ("template", parts, end)
    m = _IDENT.match(text, i)
    # The END of the argument, not its start: `_http_method` reads the `opts` object that
    # follows it, and returning the start made every dynamic call read as GET.
    return ("dynamic", [m.group(0) if m else text[i : i + 1]], i + len(m.group(0)) if m else i + 1)


def _http_method(text: str, i: int) -> str:
    """The method the call asks for: `opts.method` when the second argument is an object
    literal, else GET — read from the call site, so the comparison is against what the
    client sends rather than what a helper defaults to."""
    i = _skip_ws(text, i)
    if i >= len(text) or text[i] != ",":
        return "GET"
    i = _skip_ws(text, i + 1)
    if i >= len(text) or text[i] != "{":
        return "GET"
    m = _METHOD.search(text, i, _skip_braces(text, i))
    return m.group(1).strip("'\"` ").upper() if m else "GET"


class _Call:
    __slots__ = ("kind", "line", "method", "name", "segments")

    def __init__(self, name: str, segments: list[str], method: str, line: int, kind: str):
        self.name, self.segments, self.method, self.line, self.kind = (
            name,
            segments,
            method,
            line,
            kind,
        )

    @property
    def path(self) -> str:
        """The addressed path with each hole rendered `{}`.

        Segments carry their own slashes (`/v1/sessions/` + hole), so they are joined by
        the hole marker rather than by `/` — joining with a slash would turn a trailing
        query hole into a path segment (`/v1/backups${q}` → `/v1/backups/{}`), which is
        the shape this file exists to tell apart.
        """
        return HOLE.join(self.segments).replace(HOLE, "{}")

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.name}({self.path!r}, {self.method}) @ main.jsx:{self.line}"


def _is_api_call(call: _Call) -> bool:
    """Whether this call reaches a server through the seams this file owns.

    `api(...)` and `authAction(...)` always do. A `fetch(...)` does only when its
    argument starts at `${API_BASE}` — the BFF base — which is the shape every API
    fetch in this client uses; a fetch of an absolute origin is not this contract.
    """
    if call.name == "authAction":
        return True
    if call.name == "api":
        return True
    return call.kind == "template" and call.segments[0] == ""


def _auth_action_method() -> str:
    """The method `authAction` sends, read from the wrapper's own `fetch(path, …)` body.

    Derived rather than assumed: if that call stopped sending POST, every `authAction`
    candidate below would be compared against the new method instead of silently
    checking the wrong verb — the method-blindness the dropped guard had.
    """
    for source in sorted(list(SRC.glob("*.jsx")) + list(SRC.glob("*.js"))):
        text = source.read_text()
        m = _AUTH_DEF.search(text)
        if not m:
            continue
        for name, offset in _find_calls(text[m.end() :]):
            if name == "fetch":
                _, _, end = _first_argument(text, m.end() + offset)
                return _http_method(text, end)
    return "GET"


def _client_calls() -> list[_Call]:
    """Every server-reaching call site in the dashboard client whose path is a LITERAL.

    Derived from the sources on disk (`main.jsx` holds the client today; the loop over
    the directory keeps this from silently checking nothing if that changes). A site
    whose path is not a literal carries no path to check here — those are pinned by
    `test_the_unverifiable_call_sites_are_the_declared_ones` instead.
    """
    calls: list[_Call] = []
    for source in sorted(list(SRC.glob("*.jsx")) + list(SRC.glob("*.js"))):
        text = source.read_text()
        for name, offset in _find_calls(text):
            kind, segments, end = _first_argument(text, offset)
            if kind == "dynamic":
                continue
            # `authAction` ignores a call-site `method:` — it POSTs by construction — so
            # the verb to compare is the one its own body sends.
            method = _auth_action_method() if name == "authAction" else _http_method(text, end)
            line = text.count("\n", 0, offset) + 1
            call = _Call(name, segments, method, line, kind)
            if _is_api_call(call):
                calls.append(call)
    return calls


def _unverifiable_sites() -> set[tuple[str, str]]:
    """(seam, first-argument text) for every call site whose path is not a literal."""
    found: set[tuple[str, str]] = set()
    for source in sorted(list(SRC.glob("*.jsx")) + list(SRC.glob("*.js"))):
        text = source.read_text()
        for name, offset in _find_calls(text):
            kind, segments, _ = _first_argument(text, offset)
            if kind == "dynamic":
                found.add((name, segments[0]))
    return found


def _addressed(call: _Call) -> str:
    """The request path this call builds.

    `api(p)` reaches `${API_BASE}p`; `authAction(p)` reaches the site root `p`; a
    `fetch` template that starts at `${API_BASE}` addresses everything after it.
    """
    if call.name == "authAction":
        return call.path
    if call.name == "api":
        return API_BASE + call.path
    # a `fetch` whose argument starts at `${API_BASE}` addresses the same base; the
    # prefix is restored so a `/api/v1/…` path is checked as the browser sends it.
    return API_BASE + HOLE.join(call.segments[1:]).replace(HOLE, "{}")


# ── the runtime half: FastAPI routes ────────────────────────────────────────────────
def _api_routes() -> dict[str, set[str]]:
    """`{template: methods}` from the object that actually serves requests."""
    table: dict[str, set[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            table.setdefault(route.path, set()).update(route.methods or ())
    return table


def _template_pattern(template: str) -> re.Pattern[str]:
    """A route template as a regex: `{param}` is one segment, `{param:path}` the rest."""
    parts = []
    for segment in template.strip("/").split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            parts.append(".+" if ":path" in segment else "[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("^/" + "/".join(parts) + "$")


def _upstream_candidates(path: str) -> list[str]:
    """The upstream paths a client path could address once the proxy has rebuilt it.

    A trailing hole yields TWO readings — the hole as one segment, and the hole as a
    query string (the path without it) — because the call site cannot distinguish them.
    This is the declared ambiguity in the module docstring; `TRAILING_HOLE_SITES` names
    the sites that needed the second reading.
    """
    rest = path[len(API_BASE) :] if path.startswith(API_BASE) else path
    rest = rest.split("?")[0].rstrip("/") or "/"
    holes = rest.count("{}")
    if holes and rest.endswith("{}"):
        return [rest, re.sub(r"/?\{\}$", "", rest) or "/"]
    return [rest]


def _matches_route(candidate: str, methods: set[str], table: dict[str, set[str]]) -> bool:
    """Whether some live route's template matches `candidate` AND accepts `methods`."""
    for template, allowed in table.items():
        if methods <= allowed and _template_pattern(template).match(candidate):
            return True
    return False


def _proxied(call: _Call) -> bool:
    """Whether this call goes through the BFF proxy (`/api/v1/…`), and so must have a
    live upstream route as well as the proxy file."""
    return _addressed(call).startswith(API_BASE + "/v1/")


# ── the runtime half: Pages Functions ───────────────────────────────────────────────
def _pages_exports(route: str) -> set[str]:
    """The methods the Pages Function at `route` accepts, derived from the file that
    serves it: `onRequest` accepts everything, `onRequest<Verb>` accepts one."""
    file = _pages_file(route)
    if file is None:
        return set()
    names = set(_ON_REQUEST.findall(file.read_text()))
    if "onRequest" in names:
        return {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    return {n[len("onRequest") :].upper() for n in names}


def _pages_file(route: str):
    """The Pages Function file serving `route`, or None: `/api/session` →
    `functions/api/session.ts`, `/auth/…` → `functions/auth/….ts`, and anything under a
    `[[path]]` catch-all → that file."""
    clean = route.split("?")[0].rstrip("/") or "/"
    stem = FUNCTIONS / clean.strip("/")
    for candidate in (stem.with_suffix(".ts"), stem / "index.ts"):
        if candidate.is_file():
            return candidate
    parts = clean.strip("/").split("/")
    for cut in range(len(parts), 0, -1):
        catch_all = FUNCTIONS.joinpath(*parts[:cut], "[[path]].ts")
        if catch_all.is_file():
            return catch_all
    return None


# ── the checks ─────────────────────────────────────────────────────────────────────
def _calls_without_holes() -> list[_Call]:
    return [c for c in _client_calls() if "{}" not in _addressed(c)]


def test_the_client_call_surface_is_found() -> None:
    """Non-vacuity: the extractor really is reading the client. Counts are floors, not
    pins, so legitimate client growth does not red this — but a broken extractor does."""
    calls = _client_calls()
    proxied = [c for c in calls if _proxied(c)]
    pages = [c for c in calls if not _proxied(c)]
    assert len(proxied) >= 40, f"only {len(proxied)} proxied call sites — the extractor is broken"
    assert len(pages) >= 5, f"only {len(pages)} Pages call sites — the extractor is broken"
    # The #4144 regression, pinned by name: the Backups read that 404'd must stay a
    # candidate of this file forever. Its call is `api(`/v1/backups${q}`)`, so the query
    # hole is part of the site — the literal path is what must resolve.
    assert any(_addressed(c).startswith("/api/v1/backups") for c in calls), sorted(
        _addressed(c) for c in calls
    )
    assert any(_addressed(c) == "/api/session" for c in calls)


def test_the_route_table_derivation_is_not_vacuous() -> None:
    table = _api_routes()
    assert len(table) >= 100, f"{len(table)} routes — the derivation is broken, not the app"
    assert {"GET", "POST"} <= table.get("/v1/backups", set()), table.get("/v1/backups")
    assert {"GET"} <= table.get("/v1/sessions/{session_id}", set()), sorted(table)


def test_every_hole_free_v1_path_has_a_proxy_and_a_live_route() -> None:
    """The core assertion, and the #4144 class: a path the client requests literally
    must be rebuilt by the proxy into a route the app serves, with a method the route
    accepts."""
    assert PROXY.is_file(), f"{PROXY} is gone — every /api/v1 call 404s"
    table = _api_routes()
    failures = []
    for call in _calls_without_holes():
        if not _proxied(call):
            continue
        candidate = _upstream_candidates(_addressed(call))[0]
        if not _matches_route(candidate, {call.method}, table):
            failures.append(f"{call.method} {_addressed(call)} (upstream {candidate}) — {call}")
    assert not failures, "client paths with no live route:\n  " + "\n  ".join(failures)


def test_every_hole_bearing_v1_path_matches_a_route_shape() -> None:
    """A hole is one path segment (a trailing hole may also be a query string, the
    declared ambiguity). Literal segments must still line up with a real template, so a
    typo after a hole is not absorbed."""
    table = _api_routes()
    failures, elastic = [], set()
    for call in _client_calls():
        if "{}" not in _addressed(call) or not _proxied(call):
            continue
        variants = _upstream_candidates(_addressed(call))
        hit = next((v for v in variants if _matches_route(v, {call.method}, table)), None)
        if hit is None:
            failures.append(f"{call.method} {_addressed(call)} — {call}")
        elif hit != variants[0]:
            elastic.add((call.name, variants[1].split("{")[0].rstrip("/"), call.method))
    assert not failures, "hole-bearing client paths with no matching route shape:\n  " + "\n  ".join(
        failures
    )
    assert elastic <= TRAILING_HOLE_SITES, (
        "these sites needed the elastic (query) reading of a trailing hole and are not "
        f"declared: {sorted(elastic - TRAILING_HOLE_SITES)}"
    )


def test_every_pages_path_is_served_with_the_method_the_client_uses() -> None:
    """The Pages half: the function file must exist (Pages routing IS the file tree) and
    must export the handler for the method the client sends — the dropped guard's
    method-blindness, closed by reading the exports."""
    failures = []
    for call in _client_calls():
        if _proxied(call):
            continue
        path = _addressed(call)
        if "{}" in path:
            continue
        exports = _pages_exports(path)
        if not exports:
            failures.append(f"{call.method} {path} — no Pages Function serves it ({call})")
        elif call.method not in exports:
            failures.append(
                f"{call.method} {path} — the function exports {sorted(exports)} ({call})"
            )
    assert not failures, "Pages paths not served as the client calls them:\n  " + "\n  ".join(
        failures
    )


def test_the_unverifiable_call_sites_are_the_declared_ones() -> None:
    """A call site whose first argument is not a literal cannot be checked here — so it
    is pinned instead of skipped. A new one reds this file and asks for a human decision
    (extend the seam, or check it in a browser lane), which is the behaviour the dropped
    guard's cycles kept discovering after the fact."""
    found = _unverifiable_sites()
    assert found <= UNVERIFIABLE_SITES, (
        f"new call sites whose path cannot be read statically: {sorted(found - UNVERIFIABLE_SITES)} "
        "— declare them here with the reason, or check them in a browser lane (#4446)"
    )
