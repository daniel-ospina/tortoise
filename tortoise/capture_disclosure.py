"""#3517 — the capture disclosure, rendered from runtime structured data.

**The disclosure is a product SETTING's transparency, not a consent ritual.**
Epic #1714 decision B2 settled that recording is default-ON, legal basis
contract + notice (T&C / privacy policy), with a reachable off-switch — so
there is no consent gate, no consent record, and no approval bound to the
install. The non-LLM template survives for **factual accuracy**: the user must
read what is recorded, where it goes and where the off-switch is, and a wrong
disclosure is a UX bug (epic #1714 R7, decision 1).

Three consequences, each asserted by a test:

1. **The agent never authors this prose.** ``tortoise session disclosure``
   prints it; ``tortoise/onboarding/SKILL.md`` tells the agent to print that
   output verbatim. The sentences live in ONE place
   (``tortoise/config/capture_disclosure.json``), which the dashboard imports at
   build time — so the agent-prompt render and the dashboard render cannot
   drift. ``tests/test_capture_disclosure_template.py`` reds if a sentence is
   copied anywhere else.
2. **Every placeholder is filled from runtime structured data** — the harness,
   the hosted/self-hosted branch, the organization's live ``session_recording``
   flag, the resolved API host, and (once it exists) the install probe. Nothing
   here is model-authored and nothing is guessed: an unknown field renders as an
   honest absence, never as an invention.
3. **The disclosure is a pure READ of onboarding state.** It neither consumes
   nor writes the ``capture-disclosed`` FLOW step. That step is written exactly
   once by the CAPTURE path (``_capture_session_impl`` →
   ``write_completed_step(..., "capture-disclosed")``, first-write-wins) and is
   what makes the first-capture announcement fire exactly once per org. A render
   that wrote or skipped it would silently change the flow — see
   :func:`step_accounting` and
   ``tests/test_onboarding_session_recording_parity.py::TestDisclosureStepAccounting``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

#: The shipped single source of truth. Packaged in the wheel beside
#: ``config/routing.yaml`` (pyproject ``[tool.setuptools.package-data]``) —
#: a wheel install resolves it from the package, never from the repo root.
SOURCE_PATH = Path(__file__).resolve().parent / "config" / "capture_disclosure.json"

#: The FLOW step this module is forbidden to touch. Named here so the
#: accounting is declared in ONE place next to the code it constrains.
CAPTURE_DISCLOSED_STEP = "capture-disclosed"


@lru_cache(maxsize=1)
def source() -> dict:
    """The parsed single source of truth (cached — it is read-only data)."""
    return json.loads(SOURCE_PATH.read_text(encoding="utf-8"))


def step_accounting() -> dict:
    """What this module does to the ``capture-disclosed`` FLOW step.

    Declared here rather than in prose so the claim is executable: the render
    is a read of onboarding state, so it consumes nothing and writes nothing.
    The step's single writer is the capture path.
    """
    return {
        "step": CAPTURE_DISCLOSED_STEP,
        "consumes": False,
        "writes": False,
        "reader": "renders from onboarding state; never mutates it",
        "writer": (
            "tortoise/hosted_api.py::_capture_session_impl — "
            "write_completed_step(proj, org_id, 'capture-disclosed', ...), "
            "first-write-wins, on the first 2xx capture of an org"
        ),
    }


def _host(api_host: str | None) -> str:
    """The server the user's sessions reach, or an honest absence.

    Only the HOST is ever rendered — the key is never read here, so a
    disclosure can never leak the credential it was resolved with.
    """
    return api_host or "the server you are connected to"


def host_of(api_url: str | None) -> str | None:
    """``https://user:pass@api.example.com:8443/mcp/`` → ``api.example.com:8443``.

    A disclosure names WHERE sessions go, so it must never carry userinfo, a
    path or a query — only the authority the resolver derived. An unparseable
    or empty URL returns ``None`` so the caller renders the honest absence
    rather than a guess.
    """
    if not api_url:
        return None
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(api_url if "//" in api_url else f"//{api_url}")
    except ValueError:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1]  # drop any userinfo
    return netloc or None


def facts(*, harness: str, hosted: bool = True,
          api_host: str | None = None,
          recording: bool | None = None) -> dict:
    """Build the template's substitution table from runtime structured data.

    ``recording`` is the organization's LIVE ``session_recording`` flag as read
    back from ``tortoise_onboarding_state`` / ``GET /v1/onboarding/state``.
    ``None`` means unknown (the read did not happen, e.g. self-hosted) — never a
    fabricated default.
    """
    src = source()
    variant = "_hosted" if hosted else "_self_hosted"
    off_switch = src["destinations"]["off_switch"][
        "hosted_label" if hosted else "self_hosted_label"]
    facts = {
        "harness": harness,
        "hosted": hosted,
        "recording": recording,
        "api_host": _host(api_host),
        "off_switch": off_switch,
        "view_delete": src["destinations"]["view_delete"][
            "hosted_label" if hosted else "self_hosted_label"],
        "off_switch_effect": src["recording"]["off_switch_effect"],
        "recorded": src["facts"][f"recorded{variant}"],
        "beacon": src["facts"][f"beacon{variant}"],
        "destination": src["facts"][f"destination{variant}"].format(
            api_host=_host(api_host)),
    }
    # The state sentence is selected by the LIVE flag, not assumed: an org whose
    # recording is off must never be told it is on. `None` (the read did not
    # happen) is its own honest wording — not the "on" default in disguise.
    state_kind = {True: "state_on", False: "state_off"}.get(
        recording, "state_unknown")
    facts["state"] = src["facts"][state_kind].format(**facts)
    return facts


def render(kind: str, **fact_kwargs) -> str:
    """Render one of the templates from structured facts.

    Mechanical by construction: a missing placeholder is a ``KeyError``, not a
    silent blank in a sentence the user reads.
    """
    template = source()["templates"][kind]
    return template.format(**facts(**fact_kwargs))


def render_install_disclosure(**kw) -> str:
    """The disclosure printed when the user answers YES to the install ask."""
    return render("install_disclosure", **kw)


def render_installed_confirmation(**kw) -> str:
    """The short confirmation — it MUST name the off-switch (epic #1714 §A(a))."""
    return render("installed_confirmation", **kw)


def render_declined_confirmation(**kw) -> str:
    """The NO branch: nothing installed, and where the setting stays visible."""
    return render("declined_confirmation", **kw)


def render_first_capture_line(**kw) -> str:
    """The once-per-org in-conversation line (§W6 trigger reads ``first_capture``)."""
    return render("first_capture_line", **kw)


# ── The false-promise grep (#3517 owns the pattern set and the file set) ─────
#
# The withdrawn contract is the pre-#1927 SERVER-SIDE **403 consent gate**: the
# capture data plane refusing an un-opted-in organization with a 403 and treating
# the flag itself as the consent. #1927 replaced it with a default-ON setting and
# a quiet **409** opt-out, and B2 withdrew the consent record entirely. A doc that
# still promises the 403 contract tells an agent — or a reader — to expect a
# refusal that cannot happen, which is exactly the "no promise without a
# mechanism" failure epic #1714 R7 forbids.
#
# The patterns are deliberately NARROW. "consent-gated paths" is LIVE and correct
# (client transmission authorization, #3615) and must not be flagged.
FALSE_PROMISE_PATTERNS = {
    "withdrawn-403-consent-gate": (
        r"403[^\n]{0,80}?consent|consent[^\n]{0,80}?403|the flag IS the consent"
    ),
    "withdrawn-consent-key": (
        r"capture_opt_in|session_recording_opted_in_at|consent_version"
    ),
}

#: Files that state the LIVE contract: a hit here is a defect, no exceptions.
LIVE_CONTRACT_FILES = (
    "tortoise/onboarding/SKILL.md",
    "website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md",
)

#: Historical research. It is allowed to quote the withdrawn contract — but ONLY
#: under this marker, so a reader meeting the old contract is told it is dead.
#: The glob (not a hand-list) is deliberate: a new research brief is covered the
#: day it lands.
RESEARCH_GLOB = "docs/research/1714-*.md"
WITHDRAWAL_MARKER = "WITHDRAWN CONTRACT"


def false_promise_hits(text: str) -> list[tuple[int, str, str]]:
    """``(line_number, pattern_name, matching_line)`` for every hit, in order."""
    hits: dict[int, tuple[int, str, str]] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in FALSE_PROMISE_PATTERNS.items():
            # `search`, not `match`: the withdrawn contract is usually named
            # mid-line, and a start-anchored scan would miss every mention.
            if re.search(pattern, line, re.IGNORECASE):
                hits.setdefault(lineno, (lineno, name, line.strip()))
    return [hits[k] for k in sorted(hits)]


def audit_false_promises(repo_root: Path) -> list[str]:
    """Every un-retracted withdrawn-contract hit, as human-readable findings.

    Empty list for a clean tree. Discovery, so a new file is covered the day it
    lands rather than the day someone remembers to list it:

    * the two LIVE contract copies must be clean — a hit there is a defect;
    * every ``docs/research/1714-*.md`` brief may quote the withdrawn contract,
      but only BELOW a :data:`WITHDRAWAL_MARKER` banner — the banner has to
      precede the first hit, so a reader meets the retraction first.
    """
    findings: list[str] = []
    for rel in LIVE_CONTRACT_FILES:
        path = repo_root / rel
        if not path.is_file():
            findings.append(f"{rel}: missing (the live contract must exist)")
            continue
        for lineno, name, line in false_promise_hits(
                path.read_text(encoding="utf-8")):
            findings.append(
                f"{rel}:{lineno}: {name} — the withdrawn 403 consent contract "
                f"appears in a LIVE contract file: {line!r}")
    for path in sorted(repo_root.glob(RESEARCH_GLOB)):
        text = path.read_text(encoding="utf-8")
        hits = false_promise_hits(text)
        if not hits:
            continue
        banner = text.find(WITHDRAWAL_MARKER)
        first_lineno, name, line = hits[0]
        banner_lineno = (text[:banner].count("\n") + 1) if banner >= 0 else None
        if banner_lineno is None or banner_lineno > first_lineno:
            findings.append(
                f"{path.relative_to(repo_root)}:{first_lineno}: {name} — quotes "
                f"the withdrawn 403 consent contract with no "
                f"{WITHDRAWAL_MARKER!r} banner above it: {line!r}")
    return findings
