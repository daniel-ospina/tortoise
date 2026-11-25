#!/usr/bin/env python3
"""Read-only Fly secret DECLARATION drift report — the early-warning half (#14574).

The blocking half already works: ``.github/scripts/check-fly-secret-drift.py``
is a fail-closed PRE-DEPLOY gate, and it is correct. Measured 2026-11-21 →
2026-11-24, though, that correctness cost ~2 days: ``TORTOISE_CAPTURE_IN_FLIGHT``
was hand-set on ``tortoise-y4mjjq`` with no declaration, so every ``deploy-hosted``
run that reached the gate was refused — and **nothing told a human until a deploy
was attempted**. Deploys are hand-dispatched, so that window was two days and
75+ commits landed behind it. The next undeclared name,
``TORTOISE_RECORD_VECTOR_LEG``, cost another full cycle; the discovery is
*serial*, one name per attempt.

This tool is the SCHEDULE for that enumeration (#14574). It answers one question
per run — *is every name the deploy's secret store can hold declared?* — and it
NEVER blocks, never mutates, and never relaxes the gate.

⛔ THIS IS A REPORTER, NOT A GATE. The gate stays fail-closed and unchanged
(#4126, #14537, #14563). Nothing here is an exemption list, a bypass, or a new
route to "the deploy may proceed": a run of this tool cannot influence a deploy
in any way. It reads, diffs, and prints.

⛔ IT CANNOT MUTATE ANYTHING, AND THAT IS A PROPERTY OF THE SHAPE, NOT A PROMISE:
   * it never invokes ``gh`` at all — filing the alert is the WORKFLOW's step,
     through the repo's existing file-don't-duplicate substrate
     (``.github/scripts/auto-file-issue.sh``), so this process has no credential
     to do anything with;
   * its ONLY external command is the checker's own
     ``read_fly_secret_names`` — default ``flyctl secrets list --app <app>
     --json``, a READ. There is no code path here that builds a
     ``secrets set``/``secrets unset``/``deploy`` argument vector. ⛔ The command
     override (``FLY_SECRETS_CMD``) IS an env seam — it is the CHECKER's, and a
     caller cannot un-invent it — so this file's read-only property does NOT rest
     on a default: the WORKFLOW pins ``FLY_SECRETS_CMD`` to the read explicitly
     (spelled out in that step, not inherited), and
     ``tests/…::test_the_workflow_pins_the_only_command_it_runs`` asserts the argv
     the shipped file actually hands this tool — a stub on ``PATH`` records it — so
     a later edit that re-points the command reds a test instead of quietly
     widening the job's only credential.
   * ``tests/test_fly_secret_declaration_report.py`` pins both halves (no ``gh``,
     no mutating subcommand) so a later edit cannot quietly widen it.

⛔ NO SECRET VALUE IS READ, PRINTED, STORED OR LOGGED. ``flyctl secrets list
--json`` returns names, digests and status — never values — and this tool reads
ONLY the ``name`` field (through the checker's own hardened reader, which refuses
a payload with no usable string name rather than guessing). A digest is not
printed either: the report says WHICH NAMES are undeclared, which is the whole
finding.

WHAT IT COMPARES (two lists, two directions — the same two the gate uses)
-------------------------------------------------------------------------
``live``    every name ``flyctl secrets list`` returns for the app. An UNDECLARED
            live name is the #4126 defect and the thing that blocks the next
            deploy.
``staged``  every name ``deploy-hosted.yml``'s payload CAN assign — read by the
            checker's own ``extract_propagation_blocks`` + ``payload_partition``
            (EXECUTION of the payload under a stubbed ``flyctl``, not a regex).
            An UNDECLARED staged name is the same violation one step earlier:
            the run that first creates it passes (the gate runs BEFORE the
            propagation step) and every run after would hard-fail on it.
            The sample is the ALL-GITHUB-SECRETS-PRESENT one plus the
            NO-GITHUB-SECRET one, and — when ``GH_SECRETS_PRESENT`` is set — the
            run's own, which is the third population the gate's UNDECLARED check
            unions in.
            ⛔ THE RESIDUAL, STATED RATHER THAN PAPERED OVER: only the DEPLOY can
            state ``GH_SECRETS_PRESENT`` (#4259), so on a SCHEDULED tick the run
            sample collapses onto the all-present one — and a name the payload
            assigns only under a guard NEITHER synthetic sample reproduces
            (``A present AND B absent``) is therefore deploy-time discovery, not an
            hourly finding. Everything both synthetic samples can see — the
            ``-z``-guarded direction included, which is the #4126 shape — is the
            same set the gate's UNDECLARED check builds, so the two agree rather
            than argue about a name's declaration status.
            `test_the_reporter_and_the_gate_agree_on_an_undeclared_name` pins that
            agreement; `test_the_run_sample_widens_the_staged_set_when_it_is_provided`
            pins the seam AND the residual, so neither can be assumed away.

⛔ THE REUSE IS DELIBERATE: the manifest grammar (``read_manifest``, including
``fly-only:<issue-ref>`` validation), the Fly payload shape
(``read_fly_secret_names``, which refuses an empty or malformed list instead of
reading it as a clean fleet) and the payload classification all belong to the
gate. A second implementation would be a second owner of one contract, and the
two readers would disagree eventually — which is the failure mode this whole
registry exists to catch.

Exit codes (the gate's own three, for the gate's own reason)
  0 — CLEAN: every live name and every stageable name is declared. A silent
      green run is the CORRECT output; nothing is filed.
  1 — DRIFT: the remainder is non-empty. The workflow files one issue per name.
  2 — UNREADABLE: the state could not be determined — the Fly read failed, timed
      out, or answered a shape this tool refuses; the manifest is unparsable; the
      payload could not be classified. ⛔ NEVER 0, and never rendered as "clean":
      a read that could not happen is not an OK reading, it is the state the
      `spawn-gate` `UNREADABLE` verdict exists for. The workflow surfaces this by
      failing the run with the reason — an hour of "unknown" must never look like
      an hour of "declared".

Env seams (the gate's — all optional, all exercised by the hermetic suite)
  FLY_SECRETS_FILE          fixture holding the ``--json`` secret list
  FLY_SECRETS_CMD           secret-list command override (default: the read)
  FLY_MANAGED_SECRETS_FILE  manifest path override
  DEPLOY_WORKFLOW           deploy workflow path override
  FLY_APP                   app name (default: fly.toml ``app``)
  FLY_TOML                  fly.toml path override
  FLY_MITIGATION_LEDGER     mitigation ledger the payload filter reads (#11122).
                            NOT test-only: the sync step drops ledgered names, so
                            the "can this be staged" answer is wrong without it.
  GH_SECRETS_PRESENT        the run's OWN present-secret sample — the gate's own
                            seam (#4259), and the third population
                            ``staged_names`` unions in. Unset on a SCHEDULED tick,
                            which is the residual the module docstring states;
                            read only to WIDEN the reading, never to narrow it.
                            (This file reads it; the gate is what defines it.)
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/fly_secret_declaration_report.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/fly_secret_declaration_report.py`"
    )

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-fly-secret-drift.py"
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"
DEFAULT_TOML = REPO_ROOT / "fly.toml"

#: The declaration shapes an operator may copy, with the one-line consequence of
#: each. Kept as data (not prose re-typed per issue) so the issue body and any
#: future renderer cannot drift apart.
SOURCES = (
    ("gh-secret:<GH_NAME>", "the deploy propagates it from the GitHub Actions secret <GH_NAME>"),
    ("workflow", "the deploy sets it from non-secret context (a literal or $GITHUB_SHA)"),
    ("fly-toml-env", "it is NON-SECRET config whose home is fly.toml's [env] table"),
    (
        "fly-only:<issue-ref>",
        "only with a recorded ruling on that issue — never as a synonym for `unmanaged`",
    ),
)

#: The issue title is the DEDUPE KEY (`auto-file-issue.sh` searches open issues by
#: exact title, authored by the Actions app) and, simultaneously, the discriminator
#: the clean-run sweep uses to decide which open issues this workflow OWNS. Both
#: roles are why it lives HERE, in one place, and not in the workflow's shell:
#: a title grammar spelled in two languages drifts, and the drift files duplicates
#: on one side while closing nothing on the other. ⛔ It embeds the VARIABLE and
#: nothing that varies per occurrence — no run id, no timestamp, no count (the
#: #2706 duplicate-spam defect).
TITLE_PREFIX = "Fly secret "
TITLE_SUFFIX = (
    " is undeclared — the deploy gate refuses every run until it is declared (#4126)"
)
#: A permissive name class (`.+?`) because `read_fly_secret_names` accepts ANY
#: non-empty name: a name this grammar could not round-trip would be reported by
#: `file` and then never resolved by the sweep, i.e. an issue that outlives its own
#: fix. ⛔ `\S+` is NOT permissive enough and the earlier spelling was self-refuting:
#: a WHITESPACE-BEARING name (`A B`) is accepted by `read_fly_secret_names`, filed
#: under a title `\S+` cannot re-read, and so never resolved — the very
#: "ledger that can only grow" defect (#8841) this comment claimed to prevent.
#:
#: ⛔ WHAT MAKES A SUFFIX-BEARING NAME ROUND-TRIP IS THE `$` ANCHOR, NOT THE
#: NON-GREEDINESS — an earlier revision of this comment claimed the opposite and a
#: review measured it false: with the anchor kept, replacing `(.+?)` by `(.+)` leaves
#: every row in the suite green (49 passed), because a hard `$` forces the group's
#: extent either way.
#:
#: ⛔ DO NOT CLAIM THE `.+?` SPELLING IS SAFER — two revisions of this comment tried,
#: and both were measured false. With `$` present the spellings capture IDENTICALLY (a
#: hard literal `$` forces the group's extent from either end, and the greedy spelling
#: leaves the suite green). With `$` REMOVED the non-greedy spelling is strictly WORSE:
#: it truncates a suffix-bearing name (`"BEFORE <suffix> AFTER"` reads back as
#: `"BEFORE "`) and it FOLDS distinct names onto one read-back (`"A"` and
#: `"A<suffix> B"` both read back as `"A"`), while `(.+)` round-trips both exactly. So
#: the non-greedy spelling is retained only because it is what shipped; it carries no
#: protection of its own, and it is not the safer choice. The anchor does the work — and
#: if the anchor is ever loosened,
#: `tests/…::test_a_name_that_contains_the_title_suffix_still_round_trips` reds on the
#: truncation rather than letting the loss pass unnoticed.
_TITLE_RE = re.compile(
    r"^" + re.escape(TITLE_PREFIX) + r"(.+?)" + re.escape(TITLE_SUFFIX) + r"$"
)


def issue_title(name: str) -> str:
    """The stable title (= dedupe key) for an undeclared NAME."""
    return f"{TITLE_PREFIX}{name}{TITLE_SUFFIX}"


def _gate() -> ModuleType:
    """The provenance gate, imported by path (its filename is not an identifier).

    Imported, not copied: see the module docstring's "THE REUSE IS DELIBERATE".
    """
    spec = importlib.util.spec_from_file_location("_fly_secret_gate", GATE_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover — a broken checkout
        raise ValueError(f"cannot load the provenance gate at {GATE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def staged_names(gate: ModuleType, workflow_text: str) -> set[str]:
    """Every Fly variable ``deploy-hosted.yml``'s payload CAN assign.

    ``assigned | unconditional | live``: the all-secrets-present sample (what a
    fully configured repo stages), plus the no-secrets sample (what a
    ``-z``-guarded line stages while its secret is absent), plus the RUN sample —
    the gate's own third population, and the only one that can see a guard NEITHER
    synthetic sample reproduces. A name in ANY of them is a name the deploy can
    create on Fly, so the declaration must exist before it does.
    ⛔ The run sample is present only when ``GH_SECRETS_PRESENT`` is set; the
    module docstring's residual paragraph states what that leaves unseen on a
    scheduled tick. A reader who assumes the run sample is ignored has just deleted
    the seam — keep this line in step with the ``return`` below.

    ⛔ The payload is EXECUTED (``payload_partition`` runs it under a stubbed
    ``flyctl``), so no shell spelling — ``&&``, ``test``, ``[[``, ``case``, a
    multi-line ``if``, an accumulator rename — can hide an assignment. Raises
    ValueError when the workflow cannot be classified; the caller turns that into
    exit 2, never into an empty (i.e. clean-looking) set.
    """
    blocks = gate.extract_propagation_blocks(workflow_text)
    # Every `secrets.X` the payload reads, so the all-present sample really is
    # all-present; an omitted name would flip a guard and misreport a stageable
    # name as unstageable (the gate models this the same way, #4259).
    referenced: set[str] = set()
    for script, step_env in blocks:
        referenced |= gate.workflow_secret_refs(script)
        for value in (step_env or {}).values():
            referenced |= gate.workflow_secret_refs(str(value))
    # The RUN sample — the gate's own seam, and the ONLY population that can see a
    # guard neither synthetic sample reproduces. `GH_SECRETS_PRESENT` is written by
    # the DEPLOY, so the scheduled reader normally has no value and the run sample
    # then collapses onto `referenced` (the docstring above states that residual
    # instead of claiming the two populations are identical).
    present = (
        gate.read_gh_secret_presence()
        if os.environ.get("GH_SECRETS_PRESENT") is not None
        else referenced
    )
    assigned, unconditional, live, _sources = gate.payload_partition(blocks, present)
    # ⛔ THE GATE'S CONDITION, VERBATIM — `not assigned`, with no extra `and`. A
    # payload whose assignments are ALL `-z`-guarded (the shape `payload_partition`'s
    # own docstring names) leaves `assigned` empty while `unconditional` is not, so
    # the gate refuses the deploy (exit 2) and a guard written
    # `not assigned and not unconditional` reported CLEAN for that same tree — a green
    # reading for a repo whose every deploy is refused, i.e. exactly the window
    # #14574 exists to close (reproduced against the real gate by the #14585 review;
    # `test_an_all_guarded_payload_is_exit_2_exactly_as_the_gate_refuses_it` pins both
    # verdicts). `unconditional` stays an additive term of the returned set
    # (`assigned | unconditional | live`) — never a licence to skip the refusal.
    if not assigned:
        raise ValueError(
            "the deploy payload assigns nothing when every GitHub secret is present "
            "— the deploy's own gate refuses this state (exit 2), so a payload this "
            "tool could not read is not a payload with no secrets"
        )
    return set(assigned) | set(unconditional) | set(live)


def report(
    *,
    manifest_path: Path,
    workflow_path: Path,
    toml_path: Path,
) -> dict[str, object]:
    """The whole reading, or raise (the caller maps every failure to exit 2)."""
    gate = _gate()
    declared = gate.read_manifest(manifest_path)
    app = gate.resolve_app(toml_path)
    live = set(gate.read_fly_secret_names(app))
    staged = staged_names(gate, workflow_path.read_text())

    remainder: list[dict[str, object]] = []
    for name in sorted(live | staged):
        if name in declared:
            continue
        classes = [
            label
            for label, member in (("live", live), ("staged", staged))
            if name in member
        ]
        remainder.append(
            {
                "name": name,
                "classes": classes,
                # The FIX, carried as data so the issue body (and any future
                # renderer) cannot drift from the shapes `read_manifest` accepts.
                "fix": declaration_help(name),
            }
        )
    return {
        "verdict": "DRIFT" if remainder else "CLEAN",
        "app": app,
        "manifest": manifest_path.name,
        "deploy_workflow": workflow_path.name,
        "live_count": len(live),
        "declared_count": len(declared),
        "staged_count": len(staged),
        "remainder": remainder,
    }


def _render(reading: dict[str, object]) -> None:
    """Human output. ONE line when clean — a silent green run is the output."""
    remainder = reading["remainder"]
    counts = (
        f"{reading['live_count']} live + {reading['staged_count']} stageable name(s), "
        f"{reading['declared_count']} declared in {reading['manifest']}"
    )
    if not remainder:
        print(f"fly-secret-declaration-report: CLEAN — {counts} (#14574)")
        return
    assert isinstance(remainder, list)
    print(f"fly-secret-declaration-report: DRIFT — {counts} (#14574)")
    for entry in remainder:
        name = entry["name"]
        where = " and ".join(
            "on Fly" if c == "live" else "staged by the deploy" for c in entry["classes"]
        )
        print(f"UNDECLARED — {name} is {where}, and declared nowhere in {reading['manifest']}")


def declaration_help(name: str) -> str:
    """The one-line fix, as text an issue body can carry verbatim."""
    lines = [f"Declare `{name}` in `.github/scripts/fly-managed-secrets.txt` — one line:"]
    lines.append("")
    lines.append("```")
    for source, _why in SOURCES:
        lines.append(f"{name:<32}{source}")
    lines.append("```")
    lines.append("")
    for source, why in SOURCES:
        lines.append(f"- `{source}` — {why}.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report Fly secrets that have no declaration in fly-managed-secrets.txt "
            "(read-only; #14574). Exit 0 clean, 1 drift, 2 could-not-read."
        )
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        metavar="PATH",
        help="also write the machine-readable reading to PATH ('-' for stdout)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="manifest path (default: $FLY_MANAGED_SECRETS_FILE or the shipped one)",
    )
    parser.add_argument(
        "--workflow",
        default=None,
        help="deploy workflow path (default: $DEPLOY_WORKFLOW or deploy-hosted.yml)",
    )
    parser.add_argument(
        "--explain",
        metavar="NAME",
        default=None,
        help=(
            "print the copy-paste declaration line(s) for NAME and exit (no reading is "
            "taken, so no Fly credential is needed) — the workflow renders the issue "
            "body's fix section through this, so the text has one owner"
        ),
    )
    parser.add_argument(
        "--toml", default=None, help="fly.toml path (default: $FLY_TOML or ./fly.toml)"
    )

    parser.add_argument(
        "--title",
        metavar="NAME",
        default=None,
        help="print the stable issue title for NAME and exit (the dedupe key)",
    )
    parser.add_argument(
        "--filter-owned-titles",
        action="store_true",
        help=(
            "read issue TITLES on stdin and print the SECRET NAME of each one this "
            "workflow owns (the exact grammar `--title` produces), so a clean-run "
            "sweep can only ever resolve its own issues"
        ),
    )
    args = parser.parse_args(argv)

    if args.title:
        print(issue_title(args.title))
        return 0

    if args.filter_owned_titles:
        # A title is ours IFF it is exactly what `--title` would produce. Anything
        # else — a sibling monitor's finding, a stranger's look-alike on this PUBLIC
        # repo, a look-alike that merely CONTAINS our wording — is ignored.
        owned = 0
        for line in sys.stdin:
            match = _TITLE_RE.match(line.strip())
            if match:
                owned += 1
                print(match.group(1))
        print(f"{owned} open issue(s) belong to fly-secret-declaration-drift", file=sys.stderr)
        return 0

    if args.explain:
        # No reading: this is the FIX text, not a measurement. Kept in the same module
        # because the four shapes it names are `read_manifest`'s, and a second copy of
        # that grammar is exactly what this tool refuses to create.
        print(declaration_help(args.explain))
        return 0

    manifest_path = Path(
        args.manifest or os.environ.get("FLY_MANAGED_SECRETS_FILE") or DEFAULT_MANIFEST
    )
    workflow_path = Path(
        args.workflow or os.environ.get("DEPLOY_WORKFLOW") or DEFAULT_WORKFLOW
    )
    toml_path = Path(args.toml or os.environ.get("FLY_TOML") or DEFAULT_TOML)

    try:
        reading = report(
            manifest_path=manifest_path,
            workflow_path=workflow_path,
            toml_path=toml_path,
        )
    except Exception as exc:  # noqa: BLE001, RUF100 — EVERY failure is "could not read"
        # ⛔ A bare exception would exit 1, i.e. "drift found" — a verdict this run
        # never reached, and one the workflow would render as a named variable.
        # Everything that is not a completed reading is UNREADABLE (exit 2).
        print(
            "::error::fly-secret-declaration-report: UNREADABLE — the secret inventory "
            f"could not be determined ({type(exc).__name__}: {exc}). This is NOT a clean "
            "run: a read that did not happen is not a reading (#14574).",
            file=sys.stderr,
        )
        if args.json_path:
            payload = {"verdict": "UNREADABLE", "why": f"{type(exc).__name__}: {exc}"}
            _write_json(args.json_path, payload)
        return 2

    _render(reading)
    if args.json_path:
        _write_json(args.json_path, reading)
    return 0 if reading["verdict"] == "CLEAN" else 1


def _write_json(path: str, payload: dict[str, object]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path == "-":
        sys.stdout.write(text)
        return
    Path(path).write_text(text)


if __name__ == "__main__":
    sys.exit(main())
