"""The wrangler pin is one artefact, ASSERTED against what actually resolved (#8816).

The pin used to be a string repeated twelve times across nine `run:` blocks, and the guard that
"checked" it read that text. The old guard's text scan could not see a spec written as
`wran\\gler@4`, `"wrangler"@4` or `wrangler$X@4`, cannot see a bare `wrangler`,
and never looked at `deploy-pages.yml` at all — where `wrangler@4` floated in
production. Two measurements decided the replacement (bash 5, node 22):

  * `wrangler --version` prints the bare resolved version on stdout;
  * a DECLARED float fails that comparison whatever the cache holds and whatever
    lane resolves it: the comparison's right-hand side is the full `X.Y.Z` the
    pin must be, and `wrangler --version` never prints a bare `4`. What the
    assertion cannot see is a float SPELLING on the command line — an `npx` spec
    (`npx wrangler@4`) can be served the pinned tarball from a warm cache, and
    that prints the pin itself, which the comparison accepts (measured here:
    `npx --yes "wrangler@4" --version` printed 4.127.0). In the three e2e jobs a
    float spelling is not excused that way either — `npm install -g` resolves the
    range against the REGISTRY, so `wrangler@4` becomes 4.151.1 and the
    comparison reds. Either way the caveat is about a SPELLING the text arms must
    refuse, not about the assertion being blind to a float DECLARATION.

So the version now lives in ONE file, `config/wrangler-version`, and the lane
ASSERTS the version it got against it — a comparison between an observation and a
declaration, which is exact whenever the pin differs from what a float resolves
to. The boot steps then run bare `wrangler`, so there is no spec at the call site
to float, and the deploy steps assert before they upload.

What is left for THIS file is the shape that keeps those properties true:

  1. one artefact, full `X.Y.Z`, and no second copy of the value in any workflow;
  2. no workflow declares the version in `env:` (the spelling that would shadow
     nothing and let a consuming step act on a different version than the one
     verified) and no inline assignment other than the file read;
  3. every wrangler spec is the asserted `@${WRANGLER_VERSION:?...}` form;
  4. every e2e job has an unconditional install-and-VERIFY step that runs BEFORE
     its boot and keeps its three-attempt registry retry (a single npm
     `ECONNRESET` reded the required `legal-e2e` check on 2026-10-15, and the
     last attempt is the hard one now);
  5. boot steps carry no spec and keep their two-attempt retry;
  6. every deploy step asserts before it uploads;
  7. every job that installs wrangler pins node 22 — `npm install` only WARNS
     (`EBADENGINE`) on an engines mismatch, so a node-20 job would install
     "successfully" and fail later, inside the readiness loop. An install inside a
     local COMPOSITE ACTION is that install of every job that `uses:` it, since
     the action's steps run inside the consuming job, under its `setup-node`.

A spec-LESS invocation is a second class, and it has no `wrangler@` token for the
spec arm to read: `npx --yes wrangler pages deploy` resolves to the registry's
latest without writing a spec down anywhere. Two arms cover it — one restricted
to the deploy paths, where the assertion validates the declared spec rather than
the spec on the upload line, and one over EVERY step: a bare `wrangler` is legal
only in the five declared boot steps and in the verify step's own quoted
`"$(wrangler --version …)"` probe (which is what proves the binary the boots then
run).

What this file replaced is gone: the pre-warm's own probe install, its reset of the
shared `_npx` prefix, and its version read out of script text. The install and the
assertion that remain are the lane's own, and the arms below REQUIRE them. The
division of labour is: the TEXT arms stop a float spelling being WRITTEN here, and
the assertion is the runtime check on the binary the lane actually runs — neither
alone is the guard.

Where the text arms stop is stated rather than hidden. Within a line the spec scan
resolves the two things bash does to a word that a reader would not see — quote
removal and backslash escaping — and nothing else; it does not join a word split
across a line continuation or a YAML fold. So these are NOT seen, by design: a spec
that reaches npm through an alias, a function, a wrapper script or a file written
first and executed later; a spec assembled at run time (`$(printf …)`); and a
version passed to a third-party ACTION rather than a shell (`uses:
cloudflare/wrangler-action` with `with: wranglerVersion:`), which no `run:` scan
reads. A NEW step can always be written with `wrangler@4` — the spec and
invocation arms refuse that in every workflow file, every composite action and the
command docs, the executed harnesses in `tests/test_pages_bindings.py` prove the
deploy step's assertion, and the arms below EXECUTE the manual deploy script
rather than grepping its path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
VERSION_FILE = REPO_ROOT / "config" / "wrangler-version"
VERSION_SOURCE = "config/wrangler-version"

PREWARM_JOBS = frozenset({"welcome-e2e", "legal-e2e", "dashboard-e2e"})
VERIFY_STEP = "Install and verify the wrangler toolchain (#8816)"
BOOT_STEP_PREFIX = "Start wrangler pages dev preview"
DEPLOY_STEP_PREFIX = "Deploy to Cloudflare Pages"

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")

#: The only legal spelling of a wrangler spec: the declared variable, with `:?`
#: so an empty declaration aborts instead of expanding to `wrangler@` (= latest).
LEGAL_SPEC = re.compile(r"wrangler@\$\{WRANGLER_VERSION:\?")

BARE_INVOCATION = re.compile(r"(?<![\w@$./-])wrangler(?![\w@])")

#: Pseudo job name for a step that lives in a composite action, which has no
#: `jobs:` — arms that read a JOB's config must skip it rather than look it up.
COMPOSITE = "<composite>"

#: A quoted LONE command word — `npx --yes "wrangler" pages deploy`. bash runs it as
#: the binary, but ``_strip_quoted`` deletes whole quoted SEGMENTS (so a diagnostic
#: is not an offence), and deleting the segment hid this spelling — one the module
#: header names explicitly. A message is never a LONE ``wrangler`` token, so matching
#: the quoted token itself cannot resurrect the diagnostic false positive.
QUOTED_WRANGLER = re.compile(r"""(?<![\w@$-])["']wrangler["'](?![\w@])""")

#: The only legal way the variable may be SET.
DECLARATION = 'WRANGLER_VERSION="$(cat config/wrangler-version)"'

#: Tokens that may legitimately accompany `${WRANGLER_VERSION…}` on a line: the
#: `:?` guard, an equality comparison, or a diagnostic. Anything else — notably
#: an inline `WRANGLER_VERSION="4.151.1"` — is an offender.
LEGAL_TOKENS = (
    "${WRANGLER_VERSION:?",
    "${WRANGLER_VERSION}",
    "$WRANGLER_VERSION",
    '= "$WRANGLER_VERSION"',
    '!= "$WRANGLER_VERSION"',
    "declared ${WRANGLER_VERSION}",
    "wrangler ${WRANGLER_VERSION}",
)



#: `if:` values that are false by construction: the boolean, the integer 0, and
#: their `${{ }}` spellings. `str(gate)` alone cannot tell `0` from `"0"`.
_CONSTANT_FALSE = re.compile(
    r"^(?:\$\{\{\s*)?(?:false|0(?:\.0*)?)(?:\s*\}\})?$", re.IGNORECASE
)


def _is_constant_false(gate: object) -> bool:
    if isinstance(gate, bool):
        return not gate
    if isinstance(gate, (int, float)):
        return not gate
    return bool(_CONSTANT_FALSE.match(str(gate).strip()))


def _fenced_blocks(text: str) -> list[list[tuple[int, str]]]:
    """Every ``` fence as its lines `(file line number, line)` — the parts a
    reader would PASTE. Grouped per FENCE (not flattened) so a fence can be
    judged on its own, and numbered with the FILE's line, because joining the
    fences and numbering the result points a reader at the wrong line."""
    blocks, inside, current = [], False, []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            if inside:
                blocks.append(current)
                current = []
            inside = not inside
            continue
        if inside:
            current.append((lineno, line))
    if current:
        blocks.append(current)
    return blocks

def _workflow_paths() -> list:
    """Every file that can carry a `run:` — workflows AND composite actions.

    A composite action is invoked from a workflow with `uses:`, so a spec inside
    one reaches the lane exactly like a step's own: scanning only
    `.github/workflows/` leaves the four actions under `.github/actions/`
    unguarded.
    """
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    actions = REPO_ROOT / ".github" / "actions"
    if actions.is_dir():
        # Recursive: `uses: ./.github/actions/a/b` is legal, so a one-level glob
        # leaves the nested ones in no population at all (measured: a literal
        # `wrangler@4` there survived every arm).
        paths += sorted(p for p in actions.rglob("action.y*ml") if p.is_file())
    assert paths, "no workflows found — the repo root probe is wrong"
    return paths


#: Documents that show a copy-pasteable wrangler command. They are guidance, and
#: guidance that floats teaches the drift this file exists to stop.
COMMAND_DOCS = ("website/README.md",)


def _load(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _declared_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _all_steps(jobs: dict) -> list[tuple[str, dict]]:
    return [
        (name, step)
        for name, job in jobs.items()
        for step in (job.get("steps") or [])
    ]


def _document_steps(doc: dict) -> list[tuple[str, dict]]:
    """Steps from a WORKFLOW **or** a COMPOSITE ACTION.

    A composite action declares `runs: steps:`, never `jobs:` — so reading only
    `jobs:` yielded ZERO steps for every `.github/actions/*/action.yml`, and a
    spec-less invocation inside one was invisible to every step arm while the
    file was still in `_workflow_paths()` (the arm looked like it covered them).
    """
    steps = _all_steps(doc.get("jobs") or {})
    runs = doc.get("runs") or {}
    if isinstance(runs, dict) and runs.get("using") == "composite":
        steps += [(COMPOSITE, step) for step in (runs.get("steps") or [])]
    return steps


def _run(step: dict) -> str:
    return step.get("run") or ""


def _steps_named(name: str) -> list[tuple[str, dict]]:
    out = []
    for path in _workflow_paths():
        for job_name, step in _document_steps(_load(path)):
            if (step.get("name") or "") == name:
                out.append((job_name, step))
    return out


def _composite_action_docs() -> dict[str, dict]:
    """`<dir relative to the repo root>` → document, for every LOCAL composite action.

    Keyed by DIRECTORY, because that is what a `uses: ./.github/actions/<dir>`
    names. A workflow document is not in here: it has `jobs:`, so the jobs loop
    already reaches it.
    """
    out: dict[str, dict] = {}
    for path in _workflow_paths():
        if path.parent == WORKFLOWS:
            continue
        doc = _load(path) or {}
        runs = doc.get("runs") or {}
        if isinstance(runs, dict) and runs.get("using") == "composite":
            out[path.parent.relative_to(REPO_ROOT).as_posix()] = doc
    return out


def _steps_with_local_actions(
    steps: list[dict], actions: dict[str, dict], seen: tuple[str, ...] = ()
) -> list[dict]:
    """`steps` PLUS the steps of every LOCAL composite action they `uses:`.

    A composite action's steps run INSIDE the consuming job, so a job that
    `uses: ./.github/actions/x` runs x's commands: an `npm install -g` in there
    installs wrangler for that job, under that job's `setup-node`. Transitive
    (`uses` may itself `uses` a local action) and cycle-safe via `seen`.

    This is the resolution the node arm lacked: it iterated `jobs:` only, and a
    composite document has none, so a wrangler install inside one was in NO
    population the arm examined (#8874 review, ISSUE 1).
    """
    out = list(steps)
    for step in steps:
        uses = str(step.get("uses") or "").strip()
        if not uses.startswith("./"):
            continue
        key = uses[2:].rstrip("/")
        if key in seen or key not in actions:
            continue
        nested = list((actions[key].get("runs") or {}).get("steps") or [])
        out += _steps_with_local_actions(nested, actions, (*seen, key))
    return out


def _installs_wrangler(steps: list[dict]) -> bool:
    """Does any step's CODE name the wrangler command — with a spec or without one?"""
    return any(
        "wrangler@" in _run(step)
        or "npm install -g wrangler" in _run(step)
        or _bare_invocations(_run(step))
        for step in steps
    )


def _node22_offenders(
    docs: list[tuple[str, dict]], actions: dict[str, dict]
) -> tuple[int, list[str]]:
    """`(wrangler-installing jobs examined, offenders)` for the given documents.

    `docs` is `[(label, workflow document)]`: the arm passes every document in
    `_workflow_paths()`, the self-test passes synthetic ones, so the arm's
    verdict can be PROVED to red on the shape it names rather than asserted.
    """
    offenders: list[str] = []
    checked = 0
    for label, doc in docs:
        for job_name, job in ((doc or {}).get("jobs") or {}).items():
            # Includes the steps of any local composite action this job uses.
            steps = _steps_with_local_actions(job.get("steps") or [], actions)
            if not _installs_wrangler(steps):
                continue
            checked += 1
            node = None
            for step in steps:
                uses = step.get("uses") or ""
                if uses.startswith("actions/setup-node"):
                    node = str((step.get("with") or {}).get("node-version", ""))
            # No `setup-node` at all is an offender, not a crash: the job would
            # run whatever node the image ships, and `npm install` only WARNS on
            # an engines mismatch.
            if "22" not in (node or ""):
                offenders.append(f"{label}:{job_name} node-version={node!r}")
    return checked, offenders


def _deploy_steps() -> list[tuple[str, dict]]:
    return [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "deploy-pages.yml")["jobs"])
        if (step.get("name") or "").startswith(DEPLOY_STEP_PREFIX)
    ]


# ------------------------------------------------- shell text, read honestly


def _cut_comment(line: str) -> str:
    """`line` with a COMMENT TAIL cut off — quoting honoured, so `"a#b"` is data.

    A `#` starts a comment only at the start of a word and outside quotes, which
    is shell's own rule, and a word starts after a blank OR a metacharacter — so a
    `;#` tail is a comment too. This file's subject is prose ABOUT the code: a
    comment that NAMES a retry loop on a step that has none must never satisfy the
    check, and `ci.yml`'s boot steps carry exactly that kind of prose.
    """
    quote = ""
    at_word_start = True
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = ""
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(line):
                i += 1
        elif ch in "'\"":
            quote = ch
            at_word_start = False
        elif ch == "\\" and i + 1 < len(line):
            at_word_start = False
            i += 1
        elif ch == "#" and at_word_start:
            return line[:i]
        elif ch in " \t;&|()<>":
            at_word_start = True
        else:
            at_word_start = False
        i += 1
    return line



def _strip_quoted(line: str) -> str:
    """`line` with quoted SEGMENTS removed — a diagnostic is not a command.

    `::error::resolved wrangler ${got} != …` NAMES wrangler in a message, and a
    `npx --yes "wrangler@${WRANGLER_VERSION:?}"` spec is judged by the spec arm
    (which keeps quotes, or a literal spec inside one would vanish).
    """
    out = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = ""
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(line):
                i += 1
        elif ch in "'\"":
            quote = ch
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _retry_loop_bodies(run: str, header: str) -> list[str]:
    """The bodies of each `header` … `done` loop found in comment-stripped CODE."""
    bodies: list[str] = []
    current: list[str] | None = None
    for raw in str(run or "").splitlines():
        line = _cut_comment(raw)
        if current is None:
            if header in line:
                current = []
            continue
        if line.strip() == "done":
            bodies.append("\n".join(current))
            current = None
        else:
            current.append(line)
    return bodies


def _bare_invocations(code: str) -> list[str]:
    """Spec-less wrangler invocations — `npx wrangler`, `wrangler pages deploy`."""
    offences = []
    for lineno, line in enumerate(str(code or "").splitlines(), 1):
        uncommented = _cut_comment(line)
        if BARE_INVOCATION.search(_strip_quoted(uncommented)) or QUOTED_WRANGLER.search(
            uncommented
        ):
            offences.append(f"line {lineno}: {line.strip()}")
    return offences


# ---------------------------------------------------------------- 1. one artefact


def test_the_version_lives_in_one_artefact_and_is_a_full_version() -> None:
    assert VERSION_FILE.is_file(), f"{VERSION_SOURCE} is missing — the pin has no home"
    declared = _declared_version()
    assert EXACT_VERSION.fullmatch(declared), (
        f"{VERSION_SOURCE} holds {declared!r}, which is not a full X.Y.Z — a partial "
        "version resolves to whatever npm serves, which is the drift this replaces"
    )
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        assert declared not in text, (
            f"{path.name} restates the pinned version verbatim ({declared}); it must read "
            f"{VERSION_SOURCE} instead, or a bump edits one place and drifts in another"
        )


def test_no_workflow_declares_the_version_as_an_env_key() -> None:
    """`env: {WRANGLER_VERSION: 4.151.1}` is the spelling a text scan cannot see."""

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "env" and isinstance(value, dict):
                    assert "WRANGLER_VERSION" not in value, (
                        f"{where}: `env` sets WRANGLER_VERSION={value['WRANGLER_VERSION']!r}. "
                        "It must be read from "
                        f"{VERSION_SOURCE} in the step that uses it — an env declaration "
                        "lets a consuming step act on a different version than the one "
                        "the verify step asserted"
                    )
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]")

    for path in _workflow_paths():
        walk(_load(path), path.name)


#: The only legal VALUE of the variable, spelled out: what `DECLARATION` assigns.
_DECLARED_VALUE = '"$(cat config/wrangler-version)"'

#: An assignment of the variable, found by OCCURRENCE — not only at a segment's
#: start. bash lets an assignment follow a keyword (`then`, `do`, `else`) or a
#: prefix (`readonly`, `env`), and a segment-initial rule is blind to both: the
#: line's legal mention then carries the whole line through the token fallthrough.
_ASSIGNMENT = re.compile(
    r"(?:^|[\s;&|({])(?:export\s+|declare\s+|readonly\s+|local\s+|env\s+)?"
    r"WRANGLER_VERSION\s*="
)
_SEGMENT = re.compile(r"\|\||&&|[;|]")


def _mention_offences(text: str) -> list[str]:
    """Lines whose use of `WRANGLER_VERSION` is neither the declaration nor a read.

    Judged per SEGMENT, not per line or per token: a line may assign a literal and
    still name the variable elsewhere (`V="4.1"  # see $V`), and it may also hold
    the declaration AND a second assignment. An assignment is found ANYWHERE in a
    segment (however it is prefixed or introduced), and its value must be EXACTLY
    the declared one to the end of that segment — a prefix test would accept
    `"$(cat config/wrangler-version)"-beta`, which is a different version.
    """
    offenders = []
    for lineno, line in enumerate(text.splitlines(), 1):
        code = _cut_comment(line)
        if "WRANGLER_VERSION" not in code:
            continue
        assignment_bad = False
        for segment in _SEGMENT.split(code):
            segment = segment.strip()
            for match in _ASSIGNMENT.finditer(segment):
                if segment[match.end():].strip() != _DECLARED_VALUE:
                    assignment_bad = True
                    break
            if assignment_bad:
                break
        if assignment_bad:
            offenders.append(f"{lineno}: {code.strip()}")
            continue
        if DECLARATION in code:
            continue
        if any(token in code for token in LEGAL_TOKENS):
            continue
        offenders.append(f"{lineno}: {code.strip()}")
    return offenders


def test_every_version_mention_is_a_read_or_an_asserted_spec() -> None:
    offenders = [
        f"{path.name}:{hit}"
        for path in _workflow_paths()
        for hit in _mention_offences(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "WRANGLER_VERSION used outside the declared shapes:\n" + "\n".join(
        offenders
    )


def test_every_wrangler_spec_is_the_asserted_form() -> None:
    """Every `wrangler@…` here must be the asserted form.

    The scan is a WORD-LEVEL model, and deliberately bounded: it cuts comments
    and resolves the two things bash does to a word that a reader would not see —
    it removes quotes and resolves backslash escapes — then looks for a
    `wrangler@` spec. Nothing else is modelled. A message that must NAME a bad
    spelling therefore belongs in a comment (`_cut_comment` removes it before this
    scan), which is the live style; no step in this repo writes `wrangler@…` into
    a message, and the earlier attempts to exempt one built a shell parser whose
    defects cost more than the exemption was worth (measured: with no exemption at
    all, every arm still passes).
    """
    offenders = set()
    for path in _workflow_paths():
        for where, token in _floating_specs(path.read_text(encoding="utf-8")):
            offenders.add(f"{path.name}:{where}: {token}")
    assert not offenders, (
        "wrangler spec is not the asserted variable form (a literal spec cannot be "
        "verified against what resolved):\n" + "\n".join(sorted(offenders))
    )


def _floating_specs(text: str) -> list[tuple[int, str]]:
    """`(line, token)` for every `wrangler@…` in CODE that is not the asserted form.

    CODE means: comments removed, then the two things bash does to a word that a
    reader would not see — quotes removed and backslash escapes resolved. A
    message is NOT exempt: a step that must name a bad spelling writes it in a
    comment, which this removes before the scan.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        # `_cut_comment`, not a whole-line strip: a TRAILING comment naming a
        # spec (`… --version  # was wrangler@4`) is prose too, and reading it as
        # code makes the arm fail on a correct workflow.
        code = _cut_comment(line)
        # The word bash actually runs. A legal quoted spec still matches
        # LEGAL_SPEC in that form.
        for variant in (code, re.sub(r"[\"']|\\(.)", r"\1", code)):
            for match in re.finditer(r"wrangler@\S*", variant):
                token = match.group(0)
                if LEGAL_SPEC.match(token):
                    continue
                # A bare `wrangler@` followed by prose punctuation is a mention,
                # not a spec — anything shaped like a spec (digit, letter, `^`,
                # `~`, `$`, quote, backslash, `{`) is an offender.
                if len(token) > len("wrangler@") and token[len("wrangler@")] in "`).,;:":
                    continue
                found.append((lineno, token))
    return found


def test_a_spec_may_be_named_in_a_comment_but_not_in_a_message() -> None:
    """The exemption that kept re-staling is replaced by a rule.

    Three review cycles built a shell parser to tell a message from a command, and
    each one opened a spelling it did not see (`echo …; npx wrangler@4`,
    `| /bin/sh`, a spec staged in a variable). With no exemption at all every arm
    still passes on the real tree, so the rule is now unconditional: a step that
    must name a float writes it in a COMMENT, which the scan removes.
    """
    assert _floating_specs("        run: echo \"refusing wrangler@4\""), \
        "a message naming a float must be refused — the rule is stated, not inferred"
    assert not _floating_specs("  # was: npx --yes wrangler@4 pages deploy dist"), \
        "a COMMENT naming a float must pass — that is the sanctioned way to name it"
    assert not _floating_specs(
        '  run: npx --yes "wrangler@${WRANGLER_VERSION:?}" pages deploy dist'
    ), "the asserted form must pass, quoted or not"
    # Every capability the docstring advertises, pinned: removing any one of them
    # (a one-token edit) must red THIS arm, not sit green behind a claim.
    assert _floating_specs('  run: npx --yes "wrangler"@4 pages deploy dist'), \
        "a quote-split spec is the word bash builds — quote removal is advertised"
    assert _floating_specs(r"  run: npx --yes wran\gler@4 pages deploy"), \
        "a backslash-split spec is the word bash runs — resolving it is advertised"
    assert not _floating_specs(
        '  run: npx --yes "wrangler@${WRANGLER_VERSION:?}" --version  # was wrangler@4'
    ), "a TRAILING comment naming a spec is prose — cutting it is advertised"

    # The mention rule's own self-tests: this arm carried the false negative that
    # `V="4.1"  # see $V` slipped through, and nothing but a transcript pinned it.
    declared = 'WRANGLER_VERSION="$(cat config/wrangler-version)"'
    assert _mention_offences(declared) == [], "the declaration must pass"
    assert _mention_offences("  " + declared) == [], "an indented declaration must pass"
    assert _mention_offences("echo pinned; " + declared) == [], \
        "the declaration after a separator must pass"
    # The false negative this commit exists to fix: a SECOND assignment on the
    # same line, which the old `DECLARATION in line` shortcut waved through.
    assert _mention_offences(declared + '; WRANGLER_VERSION="4.151.1"'), \
        "a second assignment beside the declaration must red"
    assert _mention_offences(declared + ' || WRANGLER_VERSION="4.151.1"'), \
        "a literal reached through `||` must red"
    assert _mention_offences('WRANGLER_VERSION="4.151.1"  # see $WRANGLER_VERSION'), \
        "a literal behind a comment mention must red"
    assert _mention_offences('export WRANGLER_VERSION="4.151.1"'), \
        "`export` does not make a literal legal"
    assert _mention_offences('WRANGLER_VERSION="$(cat /tmp/elsewhere)"'), \
        "only the ONE artefact is a legal source — a second file is a second home"
    assert _mention_offences(
        'WRANGLER_VERSION="$(cat /tmp/elsewhere)"; echo "$WRANGLER_VERSION"'
    ), "a second source must red EVEN when the line also reads the variable legally"
    assert _mention_offences('echo "pinned: ${WRANGLER_VERSION}"') == [], \
        "a plain read must pass"
    assert _mention_offences('echo "WRANGLER_VERSION=$WRANGLER_VERSION"') == [], \
        "printing the NAME with the value assigns nothing"

    # The P3 this round exists to close: an assignment is judged by OCCURRENCE, so
    # one introduced by a keyword or a prefix is seen. Measured pre-fix: each of
    # these five lines was GREEN across all 25 arms, because `^`-anchored matching
    # skipped the assignment and the line's legal `$WRANGLER_VERSION` token then
    # carried the whole line through the fallthrough.
    for spelling in (
        'if [ -z "$WRANGLER_VERSION" ]; then WRANGLER_VERSION="4.151.1"; fi',
        'env WRANGLER_VERSION="4.151.1" true; echo "$WRANGLER_VERSION"',
        'if [ -n "$WRANGLER_VERSION" ]; then :; else WRANGLER_VERSION="4.151.1"; fi',
        'for x in 1; do WRANGLER_VERSION="4.151.1"; done; echo "$WRANGLER_VERSION"',
        'readonly WRANGLER_VERSION="4.151.1"; echo "$WRANGLER_VERSION"',
    ):
        assert _mention_offences(spelling), (
            "an assignment behind a keyword or prefix still gives the pin a second "
            f"home and must red: {spelling}"
        )
    # The value is compared WHOLE, not by prefix: a suffix is a different version.
    assert _mention_offences(declared + "-beta"), \
        "a suffix after the declared value is a different version — compare the value whole"
    # ... and the same keyword shapes with the LEGAL value must still pass, or the
    # rule would forbid the declaration's own spelling wherever it is introduced.
    assert _mention_offences(
        'if [ -n "$WRANGLER_VERSION" ]; then :; else ' + declared + '; fi'
    ) == [], "the legal declaration introduced after `else` must pass"


def test_each_workflow_reads_the_version_at_least_once() -> None:
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        if "wrangler@" not in text:
            continue
        assert DECLARATION in text, (
            f"{path.name} uses a wrangler spec but never reads {VERSION_SOURCE}"
        )


# ------------------------------------------------- 2. the lane asserts, per job


def test_the_three_website_e2e_jobs_install_and_verify() -> None:
    found = _steps_named(VERIFY_STEP)
    jobs = {job for job, _step in found}
    assert jobs >= PREWARM_JOBS, (
        f"jobs without a {VERIFY_STEP!r} step: {sorted(PREWARM_JOBS - jobs)} — the pin is "
        "unverified there, so any spec that resolves elsewhere runs unnoticed"
    )
    for job, step in found:
        run = _run(step)
        assert DECLARATION in run, f"{job}: the verify step does not read {VERSION_SOURCE}"
        assert "npm install -g" in run, f"{job}: the verify step does not install"
        assert "--version" in run, f"{job}: the verify step never asks what resolved"
        assert '"$WRANGLER_VERSION"' in run, (
            f"{job}: the verify step installs/comares something other than the declared value"
        )
        assert _retry_loop_bodies(run, "for attempt in 1 2 3;"), (
            f"{job}: the install lost its three-attempt retry loop — one npm-registry "
            "ECONNRESET (2026-10-15) reded the required legal-e2e check when the fetch "
            "had none, and the last attempt is HARD now"
        )
        # The loop must END in a hard failure, or "three attempts" is prose: a
        # `done` that falls through leaves the pin unverified on a persistent
        # registry failure.
        assert re.search(r"after 3 attempts", run), (
            f"{job}: the install loop has no hard failure after the last attempt — "
            "a persistent registry failure would leave the pin unverified"
        )
        for body in _retry_loop_bodies(run, "for attempt in 1 2 3;"):
            assert "npm install -g" in body, (
                f"{job}: the retry does not wrap the install, so the install itself is "
                "still a single unprotected fetch"
            )
            assert re.search(r"sleep \$\(\(attempt \* 10\)\)", body), (
                f"{job}: the retry carries no backoff — all three attempts would burn "
                "inside the same second and fail together"
            )


def test_the_retry_check_ignores_a_commented_out_loop() -> None:
    """Prose about the loop must not satisfy the check — and a 2-attempt loop is not it."""
    header = "for attempt in 1 2 3;"
    assert _retry_loop_bodies(f"{header} do\n  npm install -g x\ndone", header)
    assert not _retry_loop_bodies(
        "# old body used `for attempt in 1 2 3;`\nnpm install -g x", header
    )
    assert not _retry_loop_bodies("npm install -g x  # for attempt in 1 2 3;", header)
    assert not _retry_loop_bodies("for attempt in 1 2; do\nnpm install -g x\ndone", header)
    assert not _retry_loop_bodies("npm install -g x", header)


def test_the_verify_step_precedes_every_boot_in_the_same_job() -> None:
    """The boot must run the binary the verify step proved — asserted as an ORDER.

    ``test_the_three_website_e2e_jobs_install_and_verify`` proves a verify step
    EXISTS in each job, and ``test_boot_steps_carry_no_spec_and_keep_their_retry``
    proves the boot carries no spec. Neither asserts that the verify runs FIRST:
    with the order unasserted, moving the verify after the boot leaves the boot
    running whatever global ``wrangler`` happens to be on PATH, unverified, while
    every test in this file stays green.
    """
    verify_at: dict[str, list[int]] = {}
    boot_at: dict[str, list[int]] = {}
    steps = _all_steps(_load(WORKFLOWS / "ci.yml").get("jobs") or {})
    for idx, (job, step) in enumerate(steps):
        name = step.get("name") or ""
        if name == VERIFY_STEP:
            verify_at.setdefault(job, []).append(idx)
        elif name.startswith(BOOT_STEP_PREFIX):
            boot_at.setdefault(job, []).append(idx)

    for job in sorted(PREWARM_JOBS):
        verifies = verify_at.get(job) or []
        assert verifies, f"{job}: no {VERIFY_STEP!r} step at all"
        for boot in boot_at.get(job) or []:
            assert any(v < boot for v in verifies), (
                f"{job}: the boot at step {boot} runs BEFORE every verify step "
                f"{verifies} — the boot would use whatever wrangler is on PATH, "
                "which nothing asserted"
            )


def test_the_verify_step_is_unconditional() -> None:
    for job, step in _steps_named(VERIFY_STEP):
        assert "if" not in step, (
            f"{job}: the install-and-verify step is gated by `if: {step['if']}` — a gate "
            "that skips it leaves the pin unverified while the run is green"
        )
        if job == COMPOSITE:
            # Checked above for its own `if:`; a composite action has no job-level
            # gate to read (`jobs[job]` would KeyError).
            continue
        job_cfg = _load(WORKFLOWS / "ci.yml")["jobs"][job]
        gate = job_cfg.get("if")
        if gate is not None:
            assert not _is_constant_false(gate), (
                f"{job}: job-level `if: {gate!r}` is constant-false — the whole job, "
                "including the verify, can never run"
            )


def test_the_ci_yml_wrangler_wiring_is_exercised_by_an_ungated_job() -> None:
    """`ci.yml` is in no `changes` gate, so one job must run it on EVERY PR.

    `ci.yml` carries three of the five consumers and every assertion, and it is
    deliberately absent from the `website`/`dashboard_e2e` gates (a workflow-only
    change would otherwise run two full e2e suites). What makes that safe is
    this: an UNGATED job installs, verifies and boots from the same wiring, so
    the lane is executed on every PR that can change it.
    """
    ci_jobs = _load(WORKFLOWS / "ci.yml")["jobs"]
    verified = {job for job, _step in _steps_named(VERIFY_STEP)} & set(ci_jobs)
    assert verified, f"no job in ci.yml carries {VERIFY_STEP!r} — the probe is wrong"
    ungated = {job for job in verified if ci_jobs[job].get("if") is None}
    assert ungated, (
        "every job carrying the wrangler wiring is gated by `changes`, and ci.yml is in "
        f"no gate — a ci.yml-only wiring change would run none of them: gated={sorted(verified)}"
    )


#: The comparison as the CONDITION of a test — `if [` / `elif [` on the same
#: comment-cut line. A bare mention is not an assertion: it cannot fail a job.
_EQUALITY_TEST = re.compile(r'\b(?:if|elif)\s+\[\s*"\$got"\s*=\s*"\$WRANGLER_VERSION"\s*\]')

#: `exit 1` standing alone as a COMMAND — see `_equality_offences`.
_EXIT_1 = re.compile(r"^\s*exit\s+1\s*$")


def _equality_offences(run: str) -> list[str]:
    """Why `run` does not ASSERT the resolved version — judged on comment-cut CODE.

    Two conditions, both of which a MESSAGE fails:

    * the comparison must be the condition of a test (`if`/`elif`), not a string
      that mentions it — `echo '[ "$got" = "$WRANGLER_VERSION" ]'` names the
      comparison in full;
    * the failure must be an `exit 1` command, not the words `exit 1` inside one.

    Cutting comments (the fix this PR ships) is not enough on its own: the arm
    then accepted a step that merely PRINTS the comparison, which is the same
    defect one level down — the class the sibling spec arm now refuses by rule.
    """
    code = [_cut_comment(line) for line in str(run or "").splitlines()]
    if not any(_EQUALITY_TEST.search(line) for line in code):
        return ['no `if [ "$got" = "$WRANGLER_VERSION" ]` TEST — the resolved version is not asserted']
    if not any(
        _EXIT_1.match(segment)
        for line in code
        for segment in _SEGMENT.split(_strip_quoted(line))
    ):
        return ["a mismatch would not fail the step: `exit 1` is in no command"]
    return []


def test_the_version_check_is_an_equality_against_the_declared_value() -> None:
    """The assertion must compare, not merely print — a print cannot fail a job."""

    for job, step in _steps_named(VERIFY_STEP):
        offences = _equality_offences(_run(step))
        assert not offences, f"{job}: " + "; ".join(offences)


def test_the_equality_arm_refuses_a_message_that_merely_prints_the_comparison() -> None:
    """Non-vacuity for the arm above: a print of the comparison must NOT satisfy it.

    Measured on the pre-fix body: `echo '[ "$got" = "$WRANGLER_VERSION" ]'`
    matched the bare `re.search`, and any other line carrying `exit 1` — even one
    inside another message — satisfied the second assertion.
    """
    real = (
        'if [ "$got" = "$WRANGLER_VERSION" ]; then\n'
        '  echo "wrangler ${got} installed and verified"\n'
        "  exit 0\n"
        "fi\n"
        'echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"\n'
        "exit 1\n"
    )
    assert _equality_offences(real) == [], "the shipped shape must pass"
    assert _equality_offences(
        'echo \'[ "$got" = "$WRANGLER_VERSION" ]\'\n'
        'echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"\n'
        "exit 0\n"
    ), "a step that only PRINTS the comparison cannot fail a job"
    assert _equality_offences(
        'if [ "$got" = "$WRANGLER_VERSION" ]; then\n  exit 0\nfi\n'
        'echo "a mismatch would exit 1 here"\n'
    ), "`exit 1` inside a message fails nothing"
    assert _equality_offences(
        '# if [ "$got" = "$WRANGLER_VERSION" ]; then\nexit 1\n'
    ), "a COMMENTED-OUT comparison asserts nothing"


# ------------------------------------------------------ 3. boot and deploy shape


def test_boot_steps_carry_no_spec_and_keep_their_retry() -> None:
    boots = [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "ci.yml")["jobs"])
        if (step.get("name") or "").startswith(BOOT_STEP_PREFIX)
    ]
    assert len(boots) == 5, f"expected 5 boot steps, found {len(boots)}"
    for job, step in boots:
        run = _run(step)
        assert "wrangler pages dev" in run, f"{job}: boot does not start wrangler"
        assert "wrangler@" not in run, (
            f"{job}: the boot carries a spec — it must run the binary the verify step "
            "asserted, so that a spec has nowhere to float"
        )
        assert re.search(r"for attempt in 1 2;", run), (
            f"{job}: the boot lost its two-attempt retry"
        )


def test_each_deploy_step_asserts_before_it_uploads() -> None:
    deploys = [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "deploy-pages.yml")["jobs"])
        if (step.get("name") or "").startswith(DEPLOY_STEP_PREFIX)
    ]
    assert len(deploys) == 2, f"expected 2 deploy steps, found {len(deploys)}"
    for job, step in deploys:
        run = _run(step)
        assert DECLARATION in run, f"{job}: the deploy step does not read {VERSION_SOURCE}"
        assert "--version" in run, f"{job}: the deploy step never asserts what resolved"
        assert run.index("--version") < run.index("pages deploy"), (
            f"{job}: `pages deploy` runs before the version is asserted — the upload "
            "would go out on an unverified spec"
        )
        assert '"$WRANGLER_VERSION"' in run, (
            f"{job}: the assertion compares something other than the value being used"
        )


# ------------------------------------------- 4. no spec-LESS invocation on a deploy path


def test_every_deploy_invocation_is_the_declared_spec() -> None:
    """A spec-LESS invocation ships an UNDECLARED version past the assertion.

    The runtime assertion validates the DECLARED spec, never the spec on the
    upload line — so a deploy step copied as `npx --yes wrangler pages deploy`
    would deploy an unverified version while every `wrangler@` arm stayed green
    (a bare invocation has no `@` to match). Bare `wrangler` is legal only in the
    boot steps, where the install-and-verify step already proved the binary.
    """
    deploys = _deploy_steps()
    assert len(deploys) == 2, f"expected 2 deploy steps, found {len(deploys)}"
    offenders = [
        f"{job}: {hit}" for job, step in deploys for hit in _bare_invocations(_run(step))
    ]
    script = REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh"
    offenders += [f"deploy.sh: {hit}" for hit in _bare_invocations(script.read_text())]
    assert not offenders, (
        "a deploy path invokes wrangler without the declared spec, so the version it "
        "uploads with is not the one that was asserted:\n" + "\n".join(offenders)
    )


def test_the_bare_invocation_scan_ignores_messages_and_comments() -> None:
    for code in (
        'npx --yes wrangler pages deploy "$STAGE"',
        "npx wrangler --version",
        "wrangler pages deploy dist",
        # A QUOTED lone command word is still a command: `_strip_quoted` removes
        # quoted segments, so before #8852's review this spelling — named in the
        # module header — passed the scan untouched.
        'npx --yes "wrangler" pages deploy dist',
        "npx --yes 'wrangler' pages deploy dist",
    ):
        assert _bare_invocations(code), code
    for code in (
        'got="$(npx --yes "wrangler@${WRANGLER_VERSION:?}" --version)"',
        'echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"',
        "# npx --yes wrangler pages deploy (the old float)",
    ):
        assert not _bare_invocations(code), code


def test_no_step_invokes_wrangler_without_a_spec_outside_the_declared_boots() -> None:
    """The deploy-path arm above is keyed on the TWO deploy steps; this one is not.

    A spec-LESS invocation has no `wrangler@` token and, in a step that is not
    named `Deploy to Cloudflare Pages…`, no arm read it either — measured on this
    file's predecessor: a one-off `npx --yes wrangler pages deploy dist` inserted
    into `welcome-e2e`, and a whole new job booting a bare `npx wrangler`, BOTH
    survived every arm. A bare `wrangler` is therefore legal in exactly one place:
    the five declared boot steps, which run the binary the verify step proved
    (`test_boot_steps_carry_no_spec_and_keep_their_retry` pins that there are
    exactly five and that each carries no spec), and in the verify step's own
    DOUBLE-QUOTED `"$(wrangler --version …)"` probe — the same call unquoted is an
    offender (measured). Everything else — a new step, a new job, a deploy line —
    is an offender.

    The scan is quote- and comment-aware (`_strip_quoted` / `_cut_comment`), so a
    diagnostic that NAMES wrangler, or a commented-out old command, is not an
    offence; the count assertion keeps the arm from passing vacuously.
    """
    offenders = []
    boots = 0
    for path in _workflow_paths():
        for job, step in _document_steps(_load(path)):
            name = step.get("name") or ""
            hits = _bare_invocations(_run(step))
            if name.startswith(BOOT_STEP_PREFIX):
                assert hits, (
                    f"{path.name}:{job}: {name!r} is a declared boot but invokes no "
                    "wrangler — the probe is wrong"
                )
                boots += len(hits)
                continue
            offenders += [f"{path.name}:{job}: {name!r}: {hit}" for hit in hits]
    assert boots == 5, f"expected 5 spec-free boot invocations, found {boots}"
    assert not offenders, (
        "wrangler invoked WITHOUT a spec outside the five declared boots — a bare "
        "invocation resolves to whatever npm serves, which is the float this file "
        "exists to kill:\n" + "\n".join(offenders)
    )


# ------------------------------------------------------------- 5. node floor, derived


def test_every_job_that_installs_wrangler_pins_node_22() -> None:
    """Derived, not a list: a new wrangler job joins the arm automatically.

    A wrangler install that lives in a COMPOSITE ACTION joins it too: its steps
    run inside the consuming job, so `_steps_with_local_actions` resolves them
    into that job and that job's `setup-node` is what covers them. This arm used
    to iterate `jobs:` alone — a composite document has none, so such an install
    was in no population it examined, while the sentence above promised
    derivation from the tree (#8874 review, ISSUE 1).
    """

    checked, offenders = _node22_offenders(
        [(path.name, _load(path)) for path in _workflow_paths()],
        _composite_action_docs(),
    )
    assert checked >= 4, f"only {checked} wrangler-installing jobs found — the probe is wrong"
    assert not offenders, (
        "a job installs wrangler without node 22; `npm install` only WARNS on an engines "
        "mismatch, so it would 'succeed' and break later in the readiness loop:\n"
        + "\n".join(offenders)
    )


def test_a_composite_action_that_installs_wrangler_reaches_the_node_arm() -> None:
    """Non-vacuity for the arm above: a composite install must reach its job.

    Synthetic documents, never the tree. On the pre-fix body — which read
    `jobs[job]['steps']` and never resolved a `uses:` — this composite action was
    in no population at all: `checked` was 0 and no offender was produced, so a
    job pinning node 20 while installing a floating wrangler through an action
    passed the arm.
    """
    action = {
        "runs": {
            "using": "composite",
            "steps": [{"run": 'npm install -g "wrangler@${WRANGLER_VERSION:?}"'}],
        }
    }
    job_pinning_20 = {
        "jobs": {
            "e2e": {
                "steps": [
                    {"uses": "actions/setup-node@v4", "with": {"node-version": "20"}},
                    {"uses": "./.github/actions/wrangler-toolchain"},
                ]
            }
        }
    }
    actions = {".github/actions/wrangler-toolchain": action}
    checked, offenders = _node22_offenders([("ci.yml", job_pinning_20)], actions)
    assert checked == 1, (
        "a composite action's install did not reach the consuming job — the arm would "
        "report nothing about a job that installs wrangler through an action"
    )
    assert offenders == ["ci.yml:e2e node-version='20'"], offenders

    # The same job pinning node 22 is clean, and an action NOTHING uses is not an
    # offender (nothing runs its install, so no node version can be required of it).
    job_pinning_22 = {
        "jobs": {
            "e2e": {
                "steps": [
                    {"uses": "actions/setup-node@v4", "with": {"node-version": "22"}},
                    {"uses": "./.github/actions/wrangler-toolchain"},
                ]
            }
        }
    }
    assert _node22_offenders([("ci.yml", job_pinning_22)], actions) == (1, [])
    assert _node22_offenders([("ci.yml", {"jobs": {"x": {"steps": []}}})], actions) == (0, [])

    # Transitive: an action that `uses` another local action inherits its steps.
    wrapping = {
        "runs": {
            "using": "composite",
            "steps": [{"uses": "./.github/actions/wrangler-toolchain"}],
        }
    }
    nested = dict(actions, **{".github/actions/wrap": wrapping})
    job_using_wrapper = {
        "jobs": {
            "e2e": {
                "steps": [
                    {"uses": "actions/setup-node@v4", "with": {"node-version": "20"}},
                    {"uses": "./.github/actions/wrap"},
                ]
            }
        }
    }
    assert _node22_offenders([("ci.yml", job_using_wrapper)], nested)[1], (
        "a wrangler install one `uses:` level deeper is still that job's install"
    )


# ------------------------------------------------ 6. the manual deploy script


def test_the_manual_deploy_script_pins_the_same_version() -> None:
    """`website/apps/dashboard/deploy.sh` is the documented manual deploy.

    It is outside the workflows, so no other arm reaches it — and a bare
    `npx wrangler pages deploy` there ships whatever npm serves, with nothing
    asserting what that was. It reads the same artefact.
    """

    script = REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh"
    assert script.is_file(), f"{script} is missing"
    text = script.read_text(encoding="utf-8")
    code = "\n".join(_cut_comment(ln) for ln in text.splitlines())
    # The read must go through an ABSOLUTE script dir resolved BEFORE any `cd`.
    # This arm used to pin the literal `cat "$(dirname "$0")/…config/wrangler-version"`,
    # which pinned the BUG: after `cd "$(dirname "$0")"`, a RELATIVE `$0` makes that
    # path double-apply the prefix, so the documented
    # `bash website/apps/dashboard/deploy.sh` aborted with "No such file or
    # directory" — and no arm could see it, because the arm asserted the broken
    # text verbatim (#8816 review).
    assert 'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"' in code, (
        "the manual deploy does not resolve an ABSOLUTE script dir — a relative `$0` "
        "re-derived after `cd` points at a path that does not exist"
    )
    assert 'cat "$SCRIPT_DIR/../../../config/wrangler-version"' in code, (
        "the manual deploy does not read config/wrangler-version through SCRIPT_DIR — "
        "it would deploy an unasserted wrangler"
    )
    assert 'cat "$(dirname "$0")/../../../config/wrangler-version"' not in code, (
        "the relative-`$0` read is back: after `cd`, it double-applies the prefix and "
        "the documented manual deploy aborts"
    )
    assert "--version" in code, "the manual deploy never asserts what resolved"
    declared = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert declared not in code, (
        f"the manual deploy restates the pinned version ({declared}); it must read "
        f"{VERSION_SOURCE}, or a bump edits one place and drifts in another"
    )
    # The SAME word model as the workflow arm: a quote- or backslash-split spec
    # here is the word bash runs, and a second regex would not see it (measured:
    # `npx --yes "wrangler"@4 pages deploy` in this script passed every arm).
    for _where, token in _floating_specs(code):
        raise AssertionError(
            f"manual deploy spec {token!r} is not the asserted variable form"
        )


#: Stub `npm` for the executed arms: `npm ci` is skipped by a pre-existing
#: node_modules and `npm run build` is not the subject.
STUB_NPM = "#!/bin/sh\nexit 0\n"

#: Stub `npx`: answers the `--version` probe with the declared value and records
#: its argv, so the harness can prove WHICH spec the deploy would use.
STUB_NPX_MANUAL = """#!/bin/bash
printf '%s\\n' "$@" > "$STUB_DIR/npx_args"
for arg in "$@"; do
  if [ "$arg" = "--version" ]; then
    cat "$STUB_DIR/config/wrangler-version"
    break
  fi
done
exit 0
"""


def _run_manual_deploy(tmp_path: Path, *, from_repo_root: bool):
    """Execute the SHIPPED deploy.sh from a scratch tree, stubs on PATH.

    The tree mirrors the checkout layout (`website/apps/dashboard/deploy.sh` +
    `config/wrangler-version`), so the script's own path arithmetic is under test
    rather than a string in a file.
    """
    dashboard = tmp_path / "website" / "apps" / "dashboard"
    dashboard.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh",
                 dashboard / "deploy.sh")
    (dashboard / "node_modules").mkdir()  # skips `npm ci`
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wrangler-version").write_text(
        VERSION_FILE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("npm", STUB_NPM), ("npx", STUB_NPX_MANUAL)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
    }
    if from_repo_root:
        # Exactly what docs/infra-runbook.md documents.
        cwd, argv = tmp_path, "./website/apps/dashboard/deploy.sh"
    else:
        cwd, argv = dashboard, "./deploy.sh"
    return subprocess.run(
        ["bash", argv], cwd=str(cwd), capture_output=True, text=True, env=env
    )


@pytest.mark.parametrize("from_repo_root", [True, False])
def test_the_manual_deploy_resolves_the_version_from_either_invocation(
    tmp_path, from_repo_root: bool
) -> None:
    """EXECUTED, because a text pin cannot see a path that does not resolve.

    The text arm above pins the shape. This one runs the shipped script the way
    the runbook documents (`./website/apps/dashboard/deploy.sh` from the repo
    root) AND from its own directory, and requires the deploy to carry the
    declared version. On the pre-fix script (re-derived `$(dirname "$0")` after
    the `cd`) the repo-root invocation aborts inside `cat` under `set -e` with
    `…/website/apps/dashboard/../../../config/wrangler-version: No such file or
    directory` — one of these two arms would have failed and the other passed,
    which is exactly the blind spot the old single text pin had.
    """
    r = _run_manual_deploy(tmp_path, from_repo_root=from_repo_root)
    out = r.stdout + r.stderr
    where = "the repo root" if from_repo_root else "its own directory"
    assert r.returncode == 0, f"deploy.sh failed when invoked from {where}:\n{out}"
    args = (tmp_path / "npx_args").read_text(encoding="utf-8").splitlines()
    declared = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert args[:2] == ["--yes", f"wrangler@{declared}"], (
        f"the manual deploy ran {args!r} — it must carry the version it read"
    )


# ------------------------------------------- 6. the documentation that teaches it


def test_no_document_shows_a_floating_wrangler_spec() -> None:
    """A README example is a command someone will paste.

    `website/README.md` recommended `npx --yes wrangler@4 pages deploy` while the
    workflows pinned 4.127.0 — the exact drift this change removes, stated as
    copy-pasteable guidance in a fence. Every spec in these files must be the asserted form, and
    the version must come from the one artefact.
    """

    for rel in COMMAND_DOCS:
        path = REPO_ROOT / rel
        assert path.is_file(), f"{rel} is missing"
        text = path.read_text(encoding="utf-8")
        # Only FENCED code, not prose: a doc may legitimately name the bad
        # spelling while explaining why it is bad.
        blocks = [
            [(n, line) for n, line in block if not line.lstrip().startswith("#")]
            for block in _fenced_blocks(text)
        ]
        code = "\n".join(line for block in blocks for _n, line in block)
        # Same word model as the workflow arm — quote- and backslash-split specs
        # are the words bash builds, and a separate regex missed them (measured).
        offenders = [token for _where, token in _floating_specs(code)]
        assert not offenders, (
            f"{rel} still shows a copy-pasteable wrangler spec that is not the "
            f"asserted form ({offenders}) — it would ship whatever npm serves"
        )
        # Per LINE, so the reported number is the file's.
        bare = [
            f"line {lineno}: {hit.split(': ', 1)[-1]}"
            for block in _fenced_blocks(text)
            for lineno, line in block
            for hit in _bare_invocations(line)
        ]
        assert not bare, (
            f"{rel} shows a spec-less wrangler command ({bare}) — a paste of it "
            "runs whatever npm serves, with nothing asserting what that was"
        )
        # Per FENCE, not file-wide: a fence that shows a wrangler spec must also
        # show where the version comes from, or it teaches a literal (`V=4`) while
        # another fence satisfies a whole-file substring check.
        for block in blocks:
            shown = "\n".join(line for _n, line in block)
            # EVERY fence: a reader copies a literal even from a fence that shows
            # no spec, and the next command they type uses it.
            assert not re.search(r"WRANGLER_VERSION=[\"']?\d", shown), (
                f"{rel}: a fence assigns WRANGLER_VERSION a literal — it must read "
                "config/wrangler-version, or a bump drifts here"
            )
            if "wrangler@" not in shown:
                continue
            assert "config/wrangler-version" in shown, (
                f"{rel}: a fence shows a wrangler spec without reading "
                "config/wrangler-version — the version has one home"
            )


#: `cd` into the REPOSITORY ROOT — correct for reading (`cat
#: config/wrangler-version`), wrong for uploading, because wrangler takes
#: `functions/` from the cwd.
_ROOT_CD = re.compile(
    r"^cd\s+[\"']?\$\(\s*git\s+rev-parse\s+--show-toplevel\s*\)[\"']?\s*$"
)

#: An UPLOAD — the two wrangler subcommands that read `functions/` off the cwd.
_UPLOAD = re.compile(r"\bpages\s+(?:deploy|dev)\b")


def test_no_document_uploads_from_the_repository_root() -> None:
    """A fence that uploads must `cd` into the upload root first.

    `wrangler` resolves `functions/` against the process cwd — never against the
    directory it uploads — so a fence that reads the version from the repo root
    and then uploads from there ships a site with NO Functions: dead auth on a
    green deploy (#3620). Reading the version from the root is right; uploading
    from it is not.

    What earned this arm: the troubleshooting fence THIS BRANCH added in
    `dc46eb705` had exactly that shape (`cd "$(git rev-parse --show-toplevel)"`
    and the upload two lines later), and `20a717661` corrected it to serve
    `website/` from inside `website/`. `origin/main` never carried the fence at
    all — it had only the prose `npx wrangler pages deploy <dir> still works` —
    so the RED this arm demonstrates on that shape is measured on a MUTATED COPY
    of the corrected fence, not on a historical commit.

    Judged PER FENCE and in reading order, splitting on `&&`/`;` so
    `cd "$STAGE" && … pages deploy` counts as the `cd` it is. A comment naming
    the command does not count (`_cut_comment`, as elsewhere in this file), and
    `uploads_seen` keeps the arm from passing green having examined no upload at
    all.
    """

    uploads_seen = 0
    for rel in COMMAND_DOCS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        offenders = []
        for block in _fenced_blocks(text):
            cwd_is_upload_root = False
            for lineno, line in block:
                for segment in re.split(r"&&|;", _cut_comment(line)):
                    segment = segment.strip()
                    if segment.startswith("cd "):
                        cwd_is_upload_root = not _ROOT_CD.match(segment)
                        continue
                    if not _UPLOAD.search(segment):
                        continue
                    uploads_seen += 1
                    if not cwd_is_upload_root:
                        offenders.append(
                            f"{rel}:{lineno}: uploads with the repository root as "
                            f"the cwd ({segment}) — `functions/` is resolved from "
                            "the cwd, so this ships a site with no Functions"
                        )
        assert not offenders, "\n".join(offenders)
    assert uploads_seen, (
        f"no wrangler upload found in any fence under {COMMAND_DOCS} — the arm would "
        "pass green having examined nothing, so a fence that changed shape (or a "
        "wrong `COMMAND_DOCS`) would read as compliance"
    )
