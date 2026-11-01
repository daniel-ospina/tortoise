"""#8218 — SCOPE §9 class 4(i): a rendered client identity must be BACKED.

The target is a COUNT and the count must be 0:

    0 rendered client identities that are not backed by an allowlist entry or by
    metadata from an origin the client demonstrably controls.

So these tests do not assert a string against one fixture. They **enumerate the
render path's identity sources** — every writer of `oauth_clients`, found by AST
over `tortoise/oauth.py`, not by hand — and then, for each of them, render the
REAL page (the inline <script>, executed under node against a DOM built from the
served markup) and read `#client-line`. An identity is counted as RENDERED when
the sentence presents a name as the client's; the invariant is that the number
so counted that is unbacked is 0.

Red before this change: `POST /register {"client_name": "Claude"}` produced
`#client-line` = "Claude wants to access your Tortoise MCP surface." for a client
that is not Claude.

Two things must stay true together, and they pull in opposite directions:

* an UNBACKED client renders NO name (the phishing payload is the name);
* a first-party client with an allowlist entry still renders AS ITSELF — so the
  fix cannot be "delete the name from every screen", which is the naive fix that
  a string-match test would happily certify.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.test_oauth_consent_page_step4 import (  # noqa: F401 — helper reuse
    APP_ORIGIN,
    REDIRECT,
    _authorize_params,
    _pkce,
    _register_client,
    _run_page,
)

# The `api_client` / `supabase_cp` fixtures live in the step-4 module (it owns the
# AS client's construction). Registered as a plugin rather than imported by name:
# importing a fixture function makes ruff read every test's `api_client`
# PARAMETER as an F811 redefinition of the import (the same reason that module
# defines its own fixtures instead of importing `test_oauth_mcp`'s).
from tortoise.client_identity import (
    ATTESTED_CLIENTS_ENV,
    BACKED_BY_ALLOWLIST,
    BACKED_BY_CIMD_ORIGIN,
    SELF_DECLARED,
    consent_client_line,
    resolve_client_identity,
)

pytest_plugins = ["tests.test_oauth_consent_page_step4"]

REPO_ROOT = Path(__file__).resolve().parents[1]
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"

#: Names an attacker would try, including this product's own and its largest
#: upstream's. The population matters: a fix that special-cases one forbidden
#: word is not a fix, so the assertion runs over all of them and counts.
_ATTACKER_NAMES = [
    "Claude",
    "Anthropic",
    "Claude by Anthropic",
    "Tortoise",
    "Tortoise MCP",
    "OpenAI",
    "GitHub",
    "  Claude  ",           # survives a strip() and must still not render
    "Claude\u200b",        # zero-width: a name-equality denylist would miss it
]


def _identity_clause(line: str) -> str:
    """The SUBJECT of the sentence — the part that names the client.

    The rest of the line is product copy ("… your Tortoise MCP surface"), so a
    substring scan over the whole string counts the product's own name as a
    rendered client identity. The measurement has to be the clause that
    presents the client, or the invariant cannot be counted.
    """
    return line.split(" wants to access", 1)[0]


def _page_and_line(api_client, tmp_path, *, client: dict | None = None,
                   client_id: str | None = None,
                   state: str = "st-1") -> tuple[str, str]:
    """Render the page for a client and read `#client-line`.

    Goes through the REAL `/oauth/authorize` (so the handler's decision is what
    is measured) and runs the page's OWN script (so the measurement is the
    printed sentence, not a Python-side string this test also wrote).

    ``client_id`` renders for an ALREADY-registered client, which is what an
    allowlist assertion needs: re-registering would mint a new `ct_` id and the
    test would then be measuring a client the operator never attested.
    """
    tc, _ = api_client
    if client_id is None:
        params = _authorize_params(tc, state=state, client=client)
    else:
        _verifier, challenge = _pkce()
        params = {
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": "mcp",
            "resource": "",
        }
    params["attempt_key"] = "ak-8218"
    r = tc.get("/oauth/authorize", params=params)
    assert r.status_code == 200, r.text
    raw = r.request.url.query
    raw = raw.decode() if isinstance(raw, bytes) else raw
    out = _run_page(r.text, {"search": "?" + raw,
                             "responses": [_SOLE_ORG, {"status": 500, "body": {}}]},
                    tmp_path)
    return r.text, out["afterLoad"]["clientLine"]


#: The chooser's own response shape (the page asks for the org list right after
#: the mint); copied from the step-4 suite's fixture constant.
_SOLE_ORG = {"status": 200, "body": {"orgs": [{"id": "team-free-001",
                                               "name": "Free", "role": "owner"}],
                                     "active_org_id": "team-free-001"}}


# ═══════════════════════════════════════════════════════════════════════════
# 1. The reproduction — the user-facing defect, on the rendered page
# ═══════════════════════════════════════════════════════════════════════════

def test_reproduction_a_DCR_client_naming_itself_Claude_is_not_rendered_as_Claude(
        api_client, tmp_path, monkeypatch):
    """#8218's reproduction, at head: `POST /register {"client_name": "Claude"}`
    must NOT produce a consent screen presenting "Claude" as the client.

    Pre-fix the rendered line was, verbatim:

        "Claude wants to access your Tortoise MCP surface."

    Registration site: `tortoise/oauth.py::register_client` (stores whatever
    `client_name` the caller sent). Render site: `tortoise/oauth.py::consent_page_html`
    → the `#client-line` assignment in `_CONSENT_HTML`.
    """
    monkeypatch.delenv(ATTESTED_CLIENTS_ENV, raising=False)
    _html, line = _page_and_line(api_client, tmp_path,
                                 client={"client_name": "Claude"})
    assert "Claude" not in _identity_clause(line), (
        f"the consent screen presents a self-asserted client name as the "
        f"client's identity: {_identity_clause(line)!r}")
    assert line.startswith("An unverified app ("), (
        "an unbacked client must be labelled as unverified: " + repr(line))


# ═══════════════════════════════════════════════════════════════════════════
# 2. The invariant, measured as a COUNT over the identity SOURCES
# ═══════════════════════════════════════════════════════════════════════════

def _functions_touching(needle: str) -> dict[str, list[str]]:
    """Every function in the PACKAGE that mentions ``needle``, by module.

    Total on purpose (review round 2, findings 1+2). The earlier shape matched a
    single `ast.Call` carrying the table, a `method` kwarg AND `"POST"` in one
    node — which missed `TABLE = "oauth_clients"`, helper indirection, lowercase
    verbs, and every PATCH/PUT. Worse, it only enumerated WRITERS, so a SECOND
    RENDER SITE (a page, email or notification printing a registry name) reddend
    nothing. Enumerating by mention instead means any new function that reads or
    writes either the table or the column fails this suite until someone
    classifies it — the failure mode the invariant exists for.
    """
    found: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "tortoise").rglob("*.py")):
        source = path.read_text()
        # Text pre-filter, then parse. A function that mentions `needle` must
        # live in a file that mentions it, so this cannot change the result -
        # it only stops the pass parsing the ~260 modules that cannot match.
        # It matters now that the file also runs on `core` (config/ci-surfaces:
        # enumeration half), where this guard fires for every package edit.
        if needle not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if needle in ast.dump(node):
                found.setdefault(path.name, []).append(node.name)
    return {k: sorted(set(v)) for k, v in found.items()}


#: CLASSIFIED. Every function that names the `oauth_clients` table. The two
#: WRITERS are the identity sources; the rest are readers/counters that never
#: render a name. A new entry here is a decision, not a formality.
_CLASSIFIED_TABLE_TOUCHERS = {
    "hosted_api.py": ["_cp_offload", "_read_count", "oauth_clients_count"],
    "oauth.py": ["_client_row", "_persist_cimd_client", "register_client",
                 "resolve_client"],
}

#: CLASSIFIED. Every function that names `client_name`. `client_identity.py` IS
#: the renderer, and `consent_page_html`/`oauth_authorize` must reach a name only
#: THROUGH it — so a new consumer outside this set is a second identity source
#: until proven otherwise.
_CLASSIFIED_NAME_CONSUMERS = {
    "cimd.py": ["validate_document"],
    "client_identity.py": ["_cimd_host", "consent_client_line",
                           "resolve_client_identity"],
    "hosted_api.py": ["oauth_authorize"],
    "oauth.py": ["_client_row", "_persist_cimd_client", "consent_page_html",
                 "register_client"],
}


def test_the_identity_sources_are_exactly_the_known_writers():
    """A new table toucher — writer OR reader — must be classified here first.

    Two writers exist and each is classified: DCR (`register_client`,
    self-asserted until an operator attests it) and CIMD (`_persist_cimd_client`,
    the host of an origin the client demonstrably controls).
    """
    assert _functions_touching("oauth_clients") == _CLASSIFIED_TABLE_TOUCHERS, (
        "a function that names the `oauth_clients` table was added or removed — "
        "classify it against SCOPE §9 class 4(i) before landing it")


def test_every_consumer_of_the_column_is_classified():
    """The RENDER-SIDE half (review round 2, finding 1): a second render site — a
    page, an email, a notification — that prints a registry `client_name`
    without going through `client_identity` is a second identity source, and it
    used to redden nothing."""
    assert _functions_touching("client_name") == _CLASSIFIED_NAME_CONSUMERS, (
        "a function that reads `client_name` is outside the classified set — it "
        "must route through `client_identity.resolve_client_identity` or be "
        "classified here with a reason")


def test_the_invariant_counts_ZERO_unbacked_rendered_identities(
        api_client, tmp_path, monkeypatch):
    """The target, as a COUNT: 0 rendered identities with nothing behind them.

    For every attacker name in the population, the client is registered through
    the real DCR endpoint, RENDERED through the real page, and the printed
    sentence is inspected. A rendered identity counts as unbacked when the
    sentence presents a name — and the count MUST be zero.
    """
    monkeypatch.delenv(ATTESTED_CLIENTS_ENV, raising=False)
    unbacked_rendered = []
    for name in _ATTACKER_NAMES:
        _html, line = _page_and_line(api_client, tmp_path,
                                     client={"client_name": name},
                                     state="st-" + str(abs(hash(name)) % 10**6))
        presented = name.strip().strip("\u200b").strip()
        if presented and presented in _identity_clause(line):
            unbacked_rendered.append((name, line))
    assert unbacked_rendered == [], (
        f"{len(unbacked_rendered)} rendered client identit"
        f"{'y is' if len(unbacked_rendered) == 1 else 'ies are'} not backed by an "
        f"allowlist entry or by metadata from an origin the client controls: "
        f"{unbacked_rendered}")
    assert len(unbacked_rendered) == 0


def test_the_page_is_not_where_the_decision_is_made():
    """The identity rule has ONE home (a Python function), not a second copy in
    the inline script. A page that reads a raw `client_name` is a second identity
    source, and two sources that can disagree is the class #3524 deleted."""
    from tortoise.oauth import _CONSENT_HTML

    assert "PARAMS.client_name" not in _CONSENT_HTML, (
        "the page reads a client-supplied name again — the decision belongs to "
        "`client_identity.resolve_client_identity`")
    assert "PARAMS.client_line" in _CONSENT_HTML


# ═══════════════════════════════════════════════════════════════════════════
# 3. Discrimination — a legitimate first-party client still renders AS ITSELF
# ═══════════════════════════════════════════════════════════════════════════

def test_a_legitimate_first_party_client_is_still_shown_as_itself(
        api_client, tmp_path, monkeypatch):
    """THE case a naive fix fails. An operator allowlist entry must render the
    canonical name, so the fix is not "delete the name from every screen"."""
    tc, _ = api_client
    client_id = _register_client(tc, client_name="Claude")["client_id"]
    monkeypatch.setenv(ATTESTED_CLIENTS_ENV,
                       f'{{"{client_id}": "Claude"}}')
    _html, line = _page_and_line(api_client, tmp_path, client_id=client_id)
    assert line.startswith("Claude ("), (
        "an allowlisted first-party client must be shown as itself, not "
        f"flattened into 'unverified': {line!r}")
    assert client_id in line, (        "the client_id rides along as the handle even when a name is backed")


def test_the_allowlist_is_keyed_by_client_id_not_by_the_claimed_name(
        api_client, tmp_path, monkeypatch):
    """A DIFFERENT client claiming the allowlisted name stays unverified — so the
    allowlist admits an identity, not a string."""
    tc, _ = api_client
    attested = _register_client(tc, client_name="Claude")["client_id"]
    impostor = _register_client(tc, client_name="Claude")["client_id"]
    assert impostor != attested
    monkeypatch.setenv(ATTESTED_CLIENTS_ENV, f'{{"{attested}": "Claude"}}')
    _html, line = _page_and_line(api_client, tmp_path, client_id=impostor)
    assert line.startswith("An unverified app ("), (
        "a different client claiming the allowlisted NAME must stay unverified — "
        f"the allowlist admits an identity, not a string: {line!r}")


# ════════════════════════════════════════════════════════════════════════
# 4. The served PARAMS flag a stale page reads
# ════════════════════════════════════════════════════════════════════════

def test_the_page_carries_the_verification_flag_for_a_future_consumer(
        api_client, tmp_path, monkeypatch):
    """`consent_page_html` sets `PARAMS.client_verified` beside `client_line`
    and NOTHING in the current inline script reads it — a dead field unless its
    contract is pinned, so it is pinned here rather than deleted (review P3
    note).

    It is a FORWARD-compatibility field, and the earlier wording here claimed
    backwards compatibility, which cannot hold: a page served by a PRE-#8218
    deploy reads only `PARAMS.client_name` / `PARAMS.client_id`, so it can never
    read this one. What it buys is a future page (or any consumer of the same
    markup) taking the verdict directly instead of re-deriving it from the
    sentence — and it must never claim verification the resolver did not grant.

    Asserts BOTH arms in one test on purpose: a flag hard-wired to "1" would
    pass a one-sided assertion while flattening every client into "verified".
    """
    tc, _ = api_client
    monkeypatch.delenv(ATTESTED_CLIENTS_ENV, raising=False)
    unbacked_html, _ = _page_and_line(api_client, tmp_path,
                                      client={"client_name": "Claude"})
    client_id = _register_client(tc, client_name="Claude")["client_id"]
    monkeypatch.setenv(ATTESTED_CLIENTS_ENV, f'{{"{client_id}": "Claude"}}')
    backed_html, _ = _page_and_line(api_client, tmp_path, client_id=client_id)

    def _params(html: str) -> dict:
        payload = html.split("const PARAMS = ", 1)[1].split(";\n", 1)[0]
        return json.loads(payload.encode().decode("unicode_escape"))

    assert _params(unbacked_html)["client_verified"] == "", (
        "an unbacked client carried a truthy verification flag to the page")
    assert _params(backed_html)["client_verified"] == "1", (
        "an allowlisted client must carry the verification flag a future consumer "
        "takes instead of re-deriving the verdict")


# ═══════════════════════════════════════════════════════════════════════════
# 5. The CIMD half — metadata from an origin the client controls
# ═══════════════════════════════════════════════════════════════════════════

def test_a_CIMD_client_renders_its_HOST_and_never_its_document_name():
    """#2847/#8218: the client_id URL is the origin, and the row's name is the
    HOST (`tortoise/cimd.py` drops the document's `client_name` on purpose).
    The rendered identity is therefore traceable to an origin the client
    controls — and a row whose name is NOT the host is not backed."""
    cimd_row = {"id": "https://claude.ai/oauth/mcp-client",
                "client_name": "claude.ai"}
    identity = resolve_client_identity(cimd_row)
    assert identity.backed_by == BACKED_BY_CIMD_ORIGIN
    assert identity.display_name == "claude.ai"
    assert "claude.ai" in consent_client_line(identity)

    tampered = {"id": "https://claude.ai/oauth/mcp-client",
                "client_name": "Claude"}
    tampered_identity = resolve_client_identity(tampered)
    assert tampered_identity.backed_by == SELF_DECLARED, (
        "a stored name that is not the client_id host did not come from the "
        "metadata fetch and must not be rendered")


def test_a_cimd_row_with_no_stored_name_is_NOT_backed():
    """Review round 2, finding 3 — the `claimed is None` arm FAILED OPEN.

    `oauth_clients.client_name` is `NOT NULL` and both writers always set it, so
    a row with no name did not come from the metadata fetch. Treating it as a
    CIMD origin rendered whatever host the id string claimed, with nothing
    proving the fetch happened.
    """
    for row in ({"id": "https://claude.ai/oauth/mcp-client"},
                {"id": "https://claude.ai/oauth/mcp-client", "client_name": ""}):
        identity = resolve_client_identity(row)
        assert identity.backed_by == SELF_DECLARED, identity
        # The handle is the full client_id URL, so the host appears there by
        # design — what must not happen is the host being presented as the
        # client's NAME (the sentence subject before "wants to access").
        assert identity.display_name is None
        assert consent_client_line(identity).startswith("An unverified app (")


def test_a_cimd_looking_client_id_registered_by_DCR_is_not_backed():
    """The CIMD backing is derived from the client_id, so a non-https, a
    userinfo-carrying or a NON-ASCII (homograph) value cannot claim it."""
    for client_id in ("http://claude.ai/cb", "https://user@claude.ai/cb",
                      "not-a-url", "ct_123",
                      # \u0441 = Cyrillic es: visually `claude.ai`, different host.
                      "https://\u0441laude.ai/oauth/client"):
        assert resolve_client_identity(
            {"id": client_id, "client_name": "claude.ai"}).backed_by != BACKED_BY_CIMD_ORIGIN


def test_userinfo_is_refused_exactly_as_cimd_refuses_it():
    """`_cimd_host` must never be LOOSER than the gate the persister passed.

    `cimd.validate_client_id_url` refuses userinfo with `is not None`, so an
    EMPTY userinfo (`https://@claude.ai/x`) is refused upstream. `_cimd_host`
    tested the VALUES for truthiness, which accepted exactly that shape and
    rendered `claude.ai` as a BACKED name for a URL the CIMD gate would not have
    stored — the fail-open direction its own docstring forbids. The same lines
    also read `parsed.username`/`parsed.password` raw, which is what reddened
    `tests/test_from_uri_userinfo.py::test_no_raw_urlparse_userinfo_read_in_
    guarded_dirs` (#3039) on this branch's head.

    Both halves are pinned against `cimd`'s own predicate here, so a future edit
    cannot silently differ from the gate it is supposed to mirror.
    """
    from tortoise.cimd import CimdError, validate_client_id_url

    for client_id in (
        "https://claude.ai@evil.example/oauth/client",   # non-empty userinfo
        "https://@claude.ai/oauth/client",               # EMPTY userinfo
        "https://:pw@claude.ai/oauth/client",            # empty user, password
    ):
        # The upstream gate refuses all three...
        with pytest.raises(CimdError):
            validate_client_id_url(client_id)
        # ...so the render predicate must refuse them too.
        identity = resolve_client_identity(
            {"id": client_id, "client_name": "claude.ai"})
        assert identity.backed_by == SELF_DECLARED, client_id
        assert identity.display_name is None, client_id


# ═══════════════════════════════════════════════════════════════════════════
# 6. Fail-closed: a broken allowlist attests NOBODY
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw", [
    "not json", "[1,2,3]", '"a string"', '{"ct_x": ""}', '{"ct_x": 3}',
    '{"": "Claude"}',
])
def test_a_malformed_allowlist_attests_nobody(raw, monkeypatch):
    monkeypatch.setenv(ATTESTED_CLIENTS_ENV, raw)
    assert resolve_client_identity(
        {"id": "ct_x", "client_name": "Claude"}).backed is False


def test_an_empty_allowlist_is_the_default(monkeypatch):
    monkeypatch.delenv(ATTESTED_CLIENTS_ENV, raising=False)
    assert resolve_client_identity(
        {"id": "ct_x", "client_name": "Claude"}).backed_by == SELF_DECLARED


def test_the_unbacked_sentence_withholds_the_name_and_keeps_the_handle():
    line = consent_client_line(resolve_client_identity(
        {"id": "ct_abc123", "client_name": "Claude"}))
    assert "Claude" not in line
    assert "ct_abc123" in line
    assert "unverified" in line.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 7. The allowlist is read per call (an operator change needs no restart)
# ═══════════════════════════════════════════════════════════════════════════

def test_the_allowlist_is_read_at_call_time(monkeypatch):
    row = {"id": "ct_abc", "client_name": "Claude"}
    monkeypatch.delenv(ATTESTED_CLIENTS_ENV, raising=False)
    assert resolve_client_identity(row).backed is False
    monkeypatch.setenv(ATTESTED_CLIENTS_ENV, '{"ct_abc": "Claude"}')
    assert resolve_client_identity(row).backed_by == BACKED_BY_ALLOWLIST
