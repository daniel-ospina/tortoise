"""The recorded status vocabulary — roadmap §7 item 9 (ADOPTED 2026-09-17).

ONE vocabulary at the client boundary, published as a term set. Each term names
exactly one condition, and no two of them may be collapsed into each other:

    available      the store was reached AND returned content
    empty          the store was reached AND returned nothing
    degraded       the store is CONFIGURED but could NOT be reached — "off by
                   outage" (a network/HTTP failure). Never reported as empty.
    unconfigured   NO store / endpoint / provider key is configured — "off by
                   policy", a SET-UP gap. Never an outage, never an empty store.

⛔ **THE VOCABULARY IS RECORDED — CONSUME IT, DO NOT MINT.** These four terms
are the adopted contract, not a local invention:

* **roadmap §7 item 9, ADOPTED 2026-09-17** —
  ``premise-labs/product/archive/2026-09-13-tortoise-beta-roadmap.md:286`` adopts "a
  distinct exit code at the client boundary plus one status vocabulary
  (``available | empty | degraded | unconfigured``)". The beta plan's objective 3
  cites that contract and does not restate it.
* **its origin is the O-A2 finding** (the research brief behind §7.9): today a
  missing key reports ``status:"skip"`` and an HTTP error reports
  ``status:"degraded"`` with the same outcome, so "off by policy" and "off by
  outage" are not separable. The four terms exist to separate them —
  ``unconfigured`` is *off by policy*, ``degraded`` is *off by outage*.

Do **not** add a fifth term, rename one, or add a synonym. A condition the four
do not cover is a **contract change**: raise it, do not name it locally — a term
coined in parallel is how one contract becomes two.

**The load-bearing property:** ``empty`` (the store answered and had nothing) is
never reported as ``degraded`` or ``unconfigured`` (the store did not answer),
and neither failure is ever reported as a successful empty result. ``degraded``
(an outage) and ``unconfigured`` (a set-up gap) are likewise never reported as
each other — a broken memory must not look empty, and a set-up gap must not
blame the service.

**Home — ONE declaration, imported by both client surfaces.** This module is the
single home of the four terms, and it is deliberately in the ENGINE package so
that both clients that speak the vocabulary can import it rather than each
declaring their own copy:

* the thin network client — ``client/tortoise_client/cli.py`` (the
  ``tortoise-client status`` probe);
* the S9 skill-wiring client — ``tortoise/tortoise_client.py``.

The client wheel stages this file as a SHARED module (``client/build_client.sh``
copies it, like ``tortoise/mcp_client.py``), so ``import tortoise.status_vocabulary``
works in a client-only install too.

**One RAISED divergence, not a silent alignment.** The read-path half of the
contract exists as an UNMERGED branch (``tortoise/read_status.py`` on
``feat/3892-read-path-status`` / PR #4040), so nothing in this module imports it
and nothing here assumes it has landed. That read-path half declares the same
four terms but maps two of them differently:

* **this vocabulary** — ``degraded`` = the store is configured but could not be
  reached (off by outage); ``unconfigured`` = no store / endpoint / key was ever
  declared (off by policy).
* **the pending read path** — ``unconfigured`` = the read could not reach a
  store at all, *"no store / endpoint configured, or unreachable"*;
  ``degraded`` = the store was reached but a leg did not run.

So the same word would name two different conditions across the two surfaces,
and the one condition the owner decision #3832 / D5 exists to separate —
*never configured* vs *configured but down* — is collapsed again on the read
path. **That is raised, not papered over**: it is a decision for the lanes and
the owner decision it touches, so this module keeps the boundary's mapping and
this note is the flag. When the read path lands it should consume this module's
terms and resolve its own condition mapping explicitly, rather than redeclaring
the words.
"""

from __future__ import annotations

#: The four recorded terms — the whole vocabulary, in no implied order.
STATUS_AVAILABLE = "available"
STATUS_EMPTY = "empty"
STATUS_DEGRADED = "degraded"
STATUS_UNCONFIGURED = "unconfigured"

#: The published term set. Exactly these four, and nothing else.
CLIENT_STATUS_TERMS: tuple[str, ...] = (
    STATUS_AVAILABLE,
    STATUS_EMPTY,
    STATUS_DEGRADED,
    STATUS_UNCONFIGURED,
)

#: term -> the condition it names. The published mapping, machine-readable so a
#: reader (or another lane) consumes the set instead of re-deriving it.
CONDITIONS: dict[str, str] = {
    STATUS_AVAILABLE: "the store was reached and returned content",
    STATUS_EMPTY: "the store was reached and returned nothing",
    STATUS_DEGRADED: "the store is configured but could not be reached (off by outage)",
    STATUS_UNCONFIGURED: "no store / endpoint / provider key is configured (off by policy)",
}

#: The pre-#3805 client-boundary words a FLAT table lookup can translate — kept
#: so the supersede is checkable and so a driver that still emits one is
#: translated rather than treated as an unknown state.
#:
#: ⛔ ``tortoise_unavailable`` is deliberately NOT in this table, and must not be
#: added. ``tortoise/mcp_client.py`` reports it for *any* failure to answer and
#: has no notion of a missing endpoint (it falls back to its default URL), so it
#: covers both "configured, but down" and "never pointed at a memory". A single
#: value here would make the documented migration path collapse the exact
#: never-configured-vs-down split this contract exists to keep — an unset
#: endpoint would read as ``degraded`` (exit 3) instead of ``unconfigured``
#: (exit 4). Read that word through ``resolve(word, configured=...)`` only; it is
#: named in ``LEGACY_WORDS_NEEDING_CONFIGURATION`` so the full superseded set
#: stays discoverable.
LEGACY_WORDS: dict[str, str] = {
    "ok": STATUS_AVAILABLE,
    "not_configured": STATUS_UNCONFIGURED,
}

#: The superseded words the table above CANNOT translate, because their term
#: depends on the configuration fact (was an endpoint ever declared?). Together
#: with ``LEGACY_WORDS`` this is the whole pre-#3805 word set; these resolve only
#: through ``resolve(word, configured=...)``.
LEGACY_WORDS_NEEDING_CONFIGURATION: tuple[str, ...] = ("tortoise_unavailable",)


def classify(*, configured: bool, reached: bool, hits: int | None = None) -> str:
    """Map the client boundary's observable facts onto the recorded terms.

    ``configured``  a store / endpoint / provider key was declared.
    ``reached``     the store answered.
    ``hits``        rows a READ returned; ``None`` when no read was made (a
                    connectivity probe), which is ``available`` — the store
                    answered and nothing was asked about its content.

    The order is the contract, not an implementation detail: configuration is
    checked before reachability, and reachability before content. A store that
    was never reached therefore cannot come back as ``empty``, and a set-up gap
    (``unconfigured``) cannot come back as an outage (``degraded``).
    """
    if not configured:
        return STATUS_UNCONFIGURED
    if not reached:
        return STATUS_DEGRADED
    if hits is None or hits > 0:
        return STATUS_AVAILABLE
    return STATUS_EMPTY


def resolve(word: str | None, *, configured: bool) -> str:
    """Resolve a driver's status word onto the recorded term set.

    A word already in the set passes through. A legacy word that is unambiguous
    on its own (``ok`` / ``not_configured``) is translated from
    ``LEGACY_WORDS``. The one word **cannot** be translated without the
    configuration fact: ``tortoise_unavailable`` — ``tortoise/mcp_client.py``
    reports it for *any* failure to answer (it has no notion of a missing
    endpoint and falls back to its default URL), so it covers both "configured,
    but down" and "never pointed at a memory". That collapse is exactly what the
    client boundary removes here, which is why ``configured`` is a parameter —
    and why the word is kept OUT of ``LEGACY_WORDS``, so a caller cannot even
    be tempted to resolve it from the flat table.

    Anything unrecognised degrades to ``degraded``: fail loud, never silently
    report success for a state we do not know.
    """
    if word in CLIENT_STATUS_TERMS:
        return word
    if word in LEGACY_WORDS_NEEDING_CONFIGURATION:
        return classify(configured=configured, reached=False)
    legacy = LEGACY_WORDS.get(word or "")
    if legacy is not None:
        return legacy
    return STATUS_DEGRADED
