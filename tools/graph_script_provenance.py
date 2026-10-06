"""A frozen ``graph-scripts/`` one-shot must carry a DECLARED provenance marker
(#4830).

WHY THIS FILE EXISTS
--------------------
``graph-scripts/`` records "this script is a historical one-shot, do not run it"
in **four mutually independent spellings** across **24 files**:

==================================================  =====  ==========================
spelling                                             files  where
==================================================  =====  ==========================
``# Historical — uses embedded tortoise.db. …``         20  line 2
``HISTORICAL ONE-SHOT — queries the removed …``          2  module docstring, line 4
``# ARCHIVED (2026-08-05) — superseded by …``            1  line 2
``⚠️ SUPERSEDED — THE OPTION SET BELOW IS …``            1  module docstring, line 1
==================================================  =====  ==========================

Nothing reconciled them, and **no test asserted one exists** — so nothing could
ask "does every frozen one-shot carry a marker?", because "a marker" was four
different strings. A marker is not a mechanism; this module is the mechanism.

WHAT IT DECLARES (the contract)
-------------------------------
``MARKER_SPELLINGS``
    The accepted provenance spellings, **declared here and nowhere else**, as
    they appear in the files. ``declared_spelling()`` normalises (leading
    ``#``/``>``/quote decoration is not part of the spelling) and answers
    whether a header line is one of them.
``UNMARKED_SCRIPTS``
    The declared residue: tracked ``graph-scripts/`` files that carry **no**
    marker today. This is the *retrofit's remaining scope*, named so it is
    visible and countable, and a file that is neither marked nor listed here
    REDS. The list can therefore only SHRINK as the retrofit lands — a ratchet,
    not a skip.
``PRICE_BEARING_SCRIPTS``
    The declared set of scripts that assert a **price of this product**; each
    must carry a marker AND name ``CANONICAL_PRICE_SOURCE``.
``CANONICAL_PRICE_SOURCE``
    ``product/pricing.json`` — the file ``tortoise/pricing.py`` names canonical
    (``"status": "current"``, ``"owner_confirmed": "2026-08-07"``).

FAIL-CLOSED — AN UNRECOGNISED SPELLING IS REJECTED, NOT COUNTED
--------------------------------------------------------------
``MARKER_CANDIDATE_RE`` is deliberately **broader** than the declared set: any
header line that *opens* with a provenance stem (``HISTORICAL``, ``ARCHIVED``,
``SUPERSEDED``, ``DEPRECATED``, ``FROZEN``, ``OBSOLETE``, after comment/quote
decoration and an optional ⚠️/🚨) is a candidate. A candidate that matches no
declared spelling is reported as ``unmatched`` and reds the check. That is what
stops the set from silently growing a fifth.

The search region is the module **header** — leading comments plus the module
docstring, up to the first real statement — because that is where a provenance
marker belongs. Scanning the whole file instead flagged two body identifiers
(``superseded = […]`` in ``audit_graph.py`` / ``audit_graph_deep.py``) as
markers, which is exactly the noise that trains a guard away.

WHAT IS DELIBERATELY *NOT* HERE
-------------------------------
* **No normalisation of the 24 files.** Unifying the spellings is the issue's
  own separate (expensive) sweep; this module declares the contract the sweep
  will normalise *towards*, without doing the churn.
* **No machine-readable provenance field** (``frozen | superseded | archived``
  + ``canonical_source``) per script, also proposed by #4830. The two
  assertions above do not need it, and adding it is a 24-file data migration —
  deferred with the retrofit. The lexical price trigger below is the proxy that
  item 2 would replace.

Exit codes (``--check``)
------------------------
    0  the declared contract holds
    1  at least one violation — every one is printed, never summarised away
    2  usage or environment error (``graph-scripts/`` missing)
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first, with an unattributed error.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/graph_script_provenance.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/graph_script_provenance.py`"
    )

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPH_SCRIPTS = REPO_ROOT / "graph-scripts"

#: The canonical price source: named canonical by ``tortoise/pricing.py``
#: (``"status": "current"``, ``"owner_confirmed": "2026-08-07"``).
CANONICAL_PRICE_SOURCE = "product/pricing.json"

#: THE contract (#4830). One declaration, consumed by the test that checks it.
#: Spelled exactly as the files carry them; comparison is decoration-insensitive
#: (see ``_normalise``), so the leading ``#``/``"""`` is not part of the identity.
MARKER_SPELLINGS: tuple[str, ...] = (
    "# Historical — uses embedded tortoise.db. Do not run against production Docker.",
    "HISTORICAL ONE-SHOT — queries the removed context field (see #49);",
    "# ARCHIVED (2026-08-05) — superseded by the generic `tortoise decide` command.",
    '"""⚠️ SUPERSEDED — THE OPTION SET BELOW IS NOT OUR PRICE LIST. Do not use this to file pricing.',
)

#: Declared markerless residue — the retrofit's remaining scope, measured at
#: #4830 (24 marked + 33 unmarked = the 57 tracked ``graph-scripts/*.py``).
#: NOT a claim that these are live drivers: it says only that they carry no
#: marker TODAY. A new script must pick a side (mark it, or declare it here),
#: and the retrofit run removes entries; neither direction is silent.
UNMARKED_SCRIPTS: frozenset[str] = frozenset(
    {
        "1714_dedup_observation.py",
        "2146_e2e_live_orphan_cleanup.py",
        "2146_falkordb_graph_cleanup.py",
        "2199_baseline_source_rename.py",
        "2500_backfill_terminal_ep_vacuity.py",
        "4220_blog_residue_cleanup.py",
        "a3_pilot.py",
        "audit_beta_gate.py",
        "audit_ids.py",
        "backfill_embeddings.py",
        "backfill_invite_ghost_members.py",
        "backfill_is_episodic.py",
        "backfill_is_operator.py",
        "backfill_onboarding_state.py",
        "backfill_pack_installs.py",
        "backfill_references.py",
        "baseline_scan.py",
        "clear_max_sessions_4010.py",
        "connectivity_gate.py",
        "context_removal_audit.py",
        "decide.py",
        "e2e_live_reconcile.py",
        "graph-diagnostics.py",
        "merge_endometriosis.py",
        "migrate_direction.py",
        "migrate_instantiates_to_about.py",
        "parity_sample.py",
        "pre_migration_snapshot.py",
        "profile_395_local_ep.py",
        "rdb_snapshot_restore.py",
        "remove_context_migration.py",
        "repair_false_onboarding_completion.py",
        "smoke_test.py",
    }
)

#: Scripts that assert a price **of this product**. #4830's sharpest gap: both
#: ``bp_approach_cycle3``/``cycle4`` assert non-canonical tiers as ours and
#: already carry a marker that says nothing about pricing — stale-as-ours with a
#: marker on it, and nothing red. ``bp_approach_cycle1`` asserts the same
#: superseded ``$20/month`` / ``$100`` boundaries, so it is in the set too: a
#: trigger narrowed to the two named files would have to depend on an incidental
#: "Our premium" phrase and would leave cycle1's explicit boundary unguarded.
#: The check also asserts this set EQUALS the lexically detected one, so a new
#: price-bearing script cannot be silently omitted.
PRICE_BEARING_SCRIPTS: frozenset[str] = frozenset(
    {
        "bp_approach_cycle1.py",
        "bp_approach_cycle3.py",
        "bp_approach_cycle4.py",
    }
)

_PROVENANCE_STEM = r"(?:HISTORICAL|ARCHIVED|SUPERSEDED|DEPRECATED|FROZEN|OBSOLETE)"

#: A header line that CLAIMS provenance — deliberately broader than the declared
#: set, so an unrecognised spelling is FOUND and then rejected (fail-closed).
#: Leading comment/quote decoration and an optional ⚠️/🚨 are not part of the
#: claim. Anchored to the line start, so body identifiers named ``superseded``
#: never match.
MARKER_CANDIDATE_RE = re.compile(
    rf"^[\s#>'\"]*(?:\U0001f6a8|\u26a0\ufe0f|\u26a0)?\s*{_PROVENANCE_STEM}\b",
    re.IGNORECASE,
)

#: The lexical price trigger: a currency amount and a product tier or first-person
#: attribution on the SAME line. A proxy for "asserts a price of this product" —
#: it deliberately does not fire on third-party costs (``Neo4j $65/mo``) that name
#: no tier of ours. #4830 item 2 (a machine-readable field) is what removes the
#: need to parse prose; deferred with the retrofit.
PRICE_LINE_RE = re.compile(r"(?i)^(?=.*\$\d)(?=.*\b(?:freemium|premium|our|ours)\b)")

_DECLARED: tuple[tuple[str, str], ...]


def _normalise(line: str) -> str:
    """The spelling, stripped of comment/quote decoration — not of its words."""
    stripped = line.strip()
    return re.sub(r"^[#>'\"]+", "", stripped).strip()


_DECLARED = tuple((raw, _normalise(raw)) for raw in MARKER_SPELLINGS)


def header_lines(source: str) -> list[tuple[int, str]]:
    """``(lineno, line)`` for the module header: leading comments + the module
    docstring, up to the first real statement.

    A provenance marker belongs in the header; scoping to it is what lets
    ``MARKER_CANDIDATE_RE`` be broad without flagging body identifiers. Written
    as a text state machine rather than ``ast`` so a corpus file that does not
    compile under this interpreter cannot break the guard.
    """
    out: list[tuple[int, str]] = []
    in_docstring = False
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if in_docstring:
            out.append((lineno, line))
            if stripped.endswith('"""') or stripped.endswith("'''"):
                in_docstring = False
            continue
        if not stripped or stripped.startswith("#"):
            out.append((lineno, line))
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            out.append((lineno, line))
            if not (len(stripped) > 3 and stripped.endswith(quote)):
                in_docstring = True
            continue
        break
    return out


def declared_spelling(line: str) -> str | None:
    """The declared spelling this header line carries, or ``None``.

    ``None`` is a REJECTION, never a pass: it is how an unrecognised spelling is
    refused rather than counted.
    """
    normalised = _normalise(line)
    for raw, declared in _DECLARED:
        if declared and normalised.startswith(declared):
            return raw
    return None


def names_a_price(source: str) -> bool:
    """True when the source asserts a price of this product (lexical proxy)."""
    return any(PRICE_LINE_RE.match(line) for line in source.splitlines())


def names_canonical_source(source: str) -> bool:
    """True when the CANONICAL SOURCE is named in the header (the marker block),
    not merely mentioned somewhere in the body."""
    return any(
        CANONICAL_PRICE_SOURCE in line for _, line in header_lines(source)
    )


@dataclass(frozen=True)
class Scan:
    """Raw facts about a ``graph-scripts/`` directory — no declarations applied."""

    corpus: tuple[str, ...]
    #: filename -> the declared spelling it carries
    marked: dict[str, str]
    #: (file, lineno, text) for a candidate marker matching NO declared spelling
    unmatched: tuple[tuple[str, int, str], ...]

    @property
    def unmarked(self) -> tuple[str, ...]:
        return tuple(f for f in self.corpus if f not in self.marked)


def scan(root: Path = GRAPH_SCRIPTS) -> Scan:
    """Read every ``*.py`` in *root* and report its marker state."""
    corpus: list[str] = []
    marked: dict[str, str] = {}
    unmatched: list[tuple[str, int, str]] = []
    for path in sorted(root.glob("*.py")):
        corpus.append(path.name)
        source = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in header_lines(source):
            if not MARKER_CANDIDATE_RE.match(line):
                continue
            spelling = declared_spelling(line)
            if spelling is None:
                unmatched.append((path.name, lineno, line.strip()))
            else:
                marked.setdefault(path.name, spelling)
    return Scan(tuple(corpus), marked, tuple(unmatched))


def violations(root: Path = GRAPH_SCRIPTS) -> list[str]:
    """Every way the declared contract fails to hold for *root*.

    Returns ALL violations — a guard that reports only the first sends the next
    reader round the loop once per file.
    """
    result = scan(root)
    problems: list[str] = []

    # (a) fail-closed: every provenance marker present is a DECLARED spelling.
    for name, lineno, text in result.unmatched:
        problems.append(
            f"UNRECOGNISED MARKER {name}:{lineno}: {text!r} is not in "
            f"MARKER_SPELLINGS — a marker spelling nobody declared is refused, "
            f"not counted (#4830)."
        )

    # The corpus is a DECLARED PARTITION: marked ∪ UNMARKED_SCRIPTS == corpus,
    # disjoint. Both directions, so the ratchet cannot grow and a stale
    # declaration cannot outlive its file.
    corpus = set(result.corpus)
    for name in result.corpus:
        if name not in result.marked and name not in UNMARKED_SCRIPTS:
            problems.append(
                f"UNCLASSIFIED {name}: carries no declared marker and is not in "
                f"UNMARKED_SCRIPTS — mark it, or declare it as markerless "
                f"residue (#4830)."
            )
    for name in sorted(UNMARKED_SCRIPTS):
        if name in result.marked:
            problems.append(
                f"STALE DECLARATION {name}: declared markerless but carries "
                f"{result.marked[name]!r} — remove it from UNMARKED_SCRIPTS so "
                f"the residue stays counted (#4830)."
            )
        elif name not in corpus:
            problems.append(
                f"STALE DECLARATION {name}: in UNMARKED_SCRIPTS but not in the "
                f"corpus — drop it (#4830)."
            )

    # The price-bearing declaration must EQUAL what the trigger detects, so a
    # new price-naming script cannot be quietly left out of (b).
    detected = {
        path.name
        for path in sorted(root.glob("*.py"))
        if names_a_price(path.read_text(encoding="utf-8", errors="replace"))
    }
    for name in sorted(detected - set(PRICE_BEARING_SCRIPTS)):
        problems.append(
            f"UNDECLARED PRICE SCRIPT {name}: names a price of this product but "
            f"is not in PRICE_BEARING_SCRIPTS — declare it so (b) covers it "
            f"(#4830)."
        )
    for name in sorted(set(PRICE_BEARING_SCRIPTS) - detected):
        problems.append(
            f"STALE PRICE DECLARATION {name}: declared price-bearing but no "
            f"product price is detected — re-derive the declaration (#4830)."
        )

    # (b) a price-bearing script carries a marker AND names the canonical source.
    for name in sorted(PRICE_BEARING_SCRIPTS):
        if name not in corpus:
            problems.append(
                f"MISSING PRICE SCRIPT {name}: declared price-bearing but not "
                f"in the corpus (#4830)."
            )
            continue
        path = root / name
        source = path.read_text(encoding="utf-8", errors="replace")
        if name not in result.marked:
            problems.append(
                f"PRICE SCRIPT WITHOUT MARKER {name}: names a price of this "
                f"product and carries no declared provenance marker (#4830)."
            )
        if not names_canonical_source(source):
            problems.append(
                f"PRICE SCRIPT WITHOUT CANONICAL SOURCE {name}: names a price of "
                f"this product and does not name {CANONICAL_PRICE_SOURCE} — a "
                f"stale tier list reads as ours with nothing to correct it "
                f"(#4830)."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report every violation of the declared marker contract",
    )
    parser.add_argument(
        "--graph-scripts",
        default=str(GRAPH_SCRIPTS),
        help="the directory to check (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    root = Path(args.graph_scripts)
    if not root.is_dir():
        print(f"graph-script-provenance: no such directory: {root}", file=sys.stderr)
        return 2
    problems = violations(root)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        print(
            f"\n{len(problems)} violation(s) of the declared graph-scripts "
            f"provenance contract (#4830)."
        )
        return 1
    checked = scan(root)
    print(
        f"OK — {len(checked.marked)} marked, {len(checked.unmarked)} declared "
        f"markerless, {len(PRICE_BEARING_SCRIPTS)} price-bearing "
        f"({CANONICAL_PRICE_SOURCE})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
