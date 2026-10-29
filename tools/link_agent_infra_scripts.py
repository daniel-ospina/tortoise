#!/usr/bin/env python3
"""Materialise the repo-root ``scripts`` symlink from the machine's agent-infra checkout.

``scripts/`` is a MACHINE-LOCAL pointer: the agent-infra scripts (``admin-merge.sh``,
``record-review.sh``, ``check-no-sigpipe-grep.sh``, …) live in a checkout whose path differs
per machine. Only ``$AGENT_INFRA_PATH`` knows where that is, and **a git symlink blob cannot
contain a variable** — so the pointer can never be both tracked and correct (#9475).

What #9475 removed is the tracked half: the blob pinned
``/Users/danielospina/Documents/GitHub/agent-infra/scripts`` at commit time, so on every other
machine ``scripts/`` dangled and every documented ``scripts/<tool>.sh`` invocation died with
``No such file or directory``. ``scripts`` is now gitignored, and THIS tool writes the pointer
from the environment at setup time — the same thing ``agent-infra init``/``update`` does, kept
in-repo because that CLI is not installed on every lane (#9475).

Usage
-----
    uv run python tools/link_agent_infra_scripts.py            # create/repair the link
    uv run python tools/link_agent_infra_scripts.py --check    # assert it, change nothing
    uv run python tools/link_agent_infra_scripts.py --pin-check  # assert the CHECKOUT is the
                                                                # tree this box is pinned to

Exit codes
----------
    0  the link resolves to ``$AGENT_INFRA_PATH/scripts`` (``--check``: and it already did)
    1  the link is missing, dangling, or points somewhere else (``--check``: a failure)
    2  config error — no agent-infra checkout with a ``scripts/`` dir could be found, or an
       explicit ``--infra`` was passed and has none (it is AUTHORITATIVE, never a first try)

``--pin-check`` answers a DIFFERENT question — is the checkout behind this pointer the tree this
box is PINNED to (``$HARNESS_PIN`` / ``~/.pi/agent/state/HARNESS-PIN``)? It never touches the
link: 0 = the checkout is at the pin or carries it, **or** this machine records no pin (said out
loud, never silence); 1 = drift (behind / diverged / the pin commit is absent from the checkout);
2 = cannot verify (the pin file is empty or unreadable, or the checkout is not a git repo).

The two assertion modes answer DIFFERENT questions and are refused together (exit 2, via an
argparse ``mutually_exclusive_group``). Before that guard, ``--check --pin-check`` answered the
pin question first and returned, so it exited **0** on a box whose pin was clean while the
pointer assertion never ran — with a dangling pointer that is a green exit over a broken setup,
which is the fail-open-in-an-assertion-mode family this tool exists to close. The combination is
reachable by construction: ``AGENTS.md``'s setup block lists the two commands on adjacent lines
(PR #10250 review, P2).

``--check`` deliberately keeps the pointer-only contract above: a CORRECT link into a stale
checkout is exit 0. The staleness is then reported as a ``!`` note on stderr on every NON-QUIET
run, because the defect this closes (#10114) is a setup step that reads green over a tree the box
does not run. ``--quiet`` keeps meaning what its help says (PR #10250 review, P3): both callers
that pass it — ``.husky/pre-commit`` and ``tools/agent_infra_drift_check.py`` — key on it meaning
silent, so the drift note lives under the same guard as the state line rather than beside it.
Visibility is not traded away for that: every non-quiet run prints the note, and ``--pin-check``
turns the same fact into a verdict.

Exit 2 is deliberately distinct from 1: "the pointer is wrong" and "there is nothing on this
machine to point AT" are different repairs, and collapsing them is how a stale checkout gets
mistaken for a missing link. That distinction is load-bearing here, because this issue's own
symptom had BOTH causes at once — a dangling blob AND an ``$AGENT_INFRA_PATH`` checkout 1341
commits behind ``origin/main``, so ``scripts/admin-merge.sh`` was absent from the local
agent-infra too. See ``diagnose()``.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/link_agent_infra_scripts.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/link_agent_infra_scripts.py`"
    )

import argparse
import contextlib
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LINK_NAME = "scripts"

#: The harness pin record — the SAME file agent-infra's ``install.sh`` writes and
#: ``harness-up``/``multi-repo-controller.sh`` read (``HARNESS_PIN`` there, ``$HARNESS_PIN`` here).
#: It names the commit the box's controller RUNS, which is not necessarily the commit
#: ``$AGENT_INFRA_PATH`` happens to be at (#10114 measured 23 commits of daylight between them).
PIN_ENV = "HARNESS_PIN"
#: The base ``harness-up`` substitutes for ``$HOME`` on a cell (``agent-infra/scripts/harness-up:548``,
#: ``:682``). Honoured BEFORE ``Path.home()`` so this tool judges the record the controller reads.
CELL_HOME_ENV = "CELL_HOME"
#: LINE 1 of this record is the full sha and line 2 is an ISO timestamp — that is the shape the
#: fleet's own writer produces (agent-infra ``scripts/cloud-cell-bringup.sh``), so a reader that
#: needs the COMMIT must take line 1, exactly as every fleet reader does (``head -1``).
PIN_RELATIVE = Path(".pi") / "agent" / "state" / "HARNESS-PIN"
#: A ``git`` that hangs must not hang a setup step. Every call here is local and offline.
GIT_TIMEOUT = 20

#: A pin record must LOOK like a commit id. A corrupted or truncated pin is "cannot verify",
#: never "your checkout is stale" — the second verdict sends an operator to
#: `git checkout <garbage>`, which is a worse failure than the one being reported.
PIN_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
#: ``pin_state`` verdicts. The drift three are the ones that make ``--pin-check`` exit 1; the
#: cannot-verify two are exit 2; ``ok`` and ``no-pin`` are the only passes, and ``no-pin`` is
#: always STATED — an unanswered question is not a clean one.
PIN_OK = "ok"
PIN_NO_PIN = "no-pin"
PIN_UNREADABLE = "unreadable"
PIN_NOT_A_REPO = "not-a-repo"
PIN_ABSENT = "pin-absent"
PIN_BEHIND = "behind"
PIN_DIVERGED = "diverged"
PIN_DRIFT = frozenset({PIN_ABSENT, PIN_BEHIND, PIN_DIVERGED})
PIN_CANNOT_VERIFY = frozenset({PIN_UNREADABLE, PIN_NOT_A_REPO})

# Tools this repo names in its own docs and hooks. Only used to make a *stale checkout* legible
# (`diagnose` asks agent-infra whether it has them); never to gate the link itself — the link
# points at a directory, and a directory is either there or not.
DOCUMENTED_TOOLS = ("admin-merge.sh", "record-review.sh", "ci-failure-set.sh")


class ConfigError(RuntimeError):
    """No usable agent-infra checkout — exit 2, never confused with a dangling link."""


def resolve_infra(explicit: str | None) -> Path:
    """The agent-infra checkout to point at, ABSOLUTE, in precedence order.

    ``--infra`` is AUTHORITATIVE (not "try this one first"): if it is passed and has no
    ``scripts/`` directory, that is a config error naming the path — silently falling through to
    ``$AGENT_INFRA_PATH`` would make ``--check --infra <X>`` exit 0 without ever consulting ``X``,
    a false green in the assertion mode a lane or gate calls (PR #9541 review). Only when nothing
    was passed explicitly do we fall back to ``$AGENT_INFRA_PATH`` and then ``~/agent-infra``. The
    last fallback is not a guess: agent-infra's own ``link-skills.sh`` uses the identical
    ``${AGENT_INFRA_PATH:-$HOME/agent-infra}`` default, so a lane that relies on the fallback
    gets the same answer from both tools instead of two different ones.

    The returned path is ``resolve()``d because it is used as a SYMLINK TARGET: ``os.symlink``
    stores the string verbatim and a link's target is interpreted relative to the LINK's directory,
    not to the CWD — so a relative ``--infra`` validated here (against the CWD) would be written as
    a relative target under ``--repo`` and dangle, while ``materialise`` reported success (PR #9541
    review P1: the #9475 symptom reinstated by its own fix, reported as a healthy link).
    """
    if explicit:
        cand = Path(explicit).expanduser()
        if not (cand / LINK_NAME).is_dir():
            raise ConfigError(
                f"--infra {cand} has no {LINK_NAME}/ directory, so there is nothing to point at. "
                "--infra is AUTHORITATIVE when passed (it is not a fallback, and this tool will "
                "NOT silently use another checkout instead): point it at a clone of "
                "daniel-ospina/agent-infra, or pass nothing and set AGENT_INFRA_PATH."
            )
        return cand.resolve()

    candidates: list[Path] = []
    if os.environ.get("AGENT_INFRA_PATH"):
        candidates.append(Path(os.environ["AGENT_INFRA_PATH"]).expanduser())
    candidates.append(Path.home() / "agent-infra")

    tried: list[str] = []
    for cand in candidates:
        src = cand / LINK_NAME
        tried.append(str(cand))
        if src.is_dir():
            return cand.resolve()
    raise ConfigError(
        "no agent-infra checkout with a scripts/ directory. Looked at: "
        + ", ".join(tried)
        + ". Set AGENT_INFRA_PATH to a clone of daniel-ospina/agent-infra, or pass --infra."
    )


def default_pin_path() -> Path:
    """Where this machine records its harness pin. A function, not a constant.

    The base is ``${CELL_HOME:-$HOME}``, NOT ``Path.home()`` alone. That is the fleet's own
    resolution, and it is load-bearing: ``harness-up`` — the reader this record exists to agree
    with — computes ``PIN_SHA_FILE="${HARNESS_PIN:-${CELL_HOME}/.pi/agent/state/HARNESS-PIN}"``
    (``agent-infra/scripts/harness-up:682``) after ``CELL_HOME="${CELL_HOME:-${HOME:-/tmp/cell-home}}"``
    (``:548``), and ``cloud-cell-bringup.sh`` WRITES the record under ``$CELL_HOME``. On a cell the
    two bases disagree, so honouring only ``HOME`` made this tool answer about a DIFFERENT pin
    record than the controller runs — measured on the box this was written on, which carries both
    records with different shas (``$HOME`` → ``dcb54375…``, ``$CELL_HOME`` → ``9a3cf06e…``). The
    failure it produced was the one this module exists to prevent: ``HOME`` pointed at a directory
    with no record read as ``no-pin`` → **exit 0**, a false PASS over a box that does record a
    pin, while the same record named via ``--pin`` correctly said ``pin-absent`` → exit 1.

    And it is a function, not a constant, because both bases are read at CALL time: every test
    here redirects ``HOME``/``CELL_HOME`` (and CI has neither), so a module-level constant would
    freeze the pin path of whichever machine imported the module first — the same class of bug,
    one layer down. ``TestDefaultPinPathIsCallTime`` loads this module IN PROCESS and moves the
    bases, which is the only shape that can distinguish a function from a frozen constant.
    """
    base = os.environ.get(CELL_HOME_ENV) or Path.home()
    return Path(base) / PIN_RELATIVE


def resolve_pin(explicit: str | None) -> Path | None:
    """The harness pin file to compare against, or ``None`` when nothing records one.

    ``--pin`` is AUTHORITATIVE (passed but missing is a caller error, reported by ``pin_state``
    as unreadable — never silently replaced by the default). Otherwise ``$HARNESS_PIN``, then
    the box-wide record ``${CELL_HOME:-$HOME}/.pi/agent/state/HARNESS-PIN``. ``None`` means this machine records
    no pin at all: a GitHub runner, a fresh dev box, or a clone of agent-infra that was never
    installed as a harness. That is an ANSWER ("unrecorded"), not a pass and not a failure.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(PIN_ENV)
    if env:
        return Path(env).expanduser()
    default = default_pin_path()
    # ⛔ NOT ``default.exists()`` (PR #10250 review ROUND 6 ISSUE 2, P2 — both directions
    # MEASURED). ``Path.exists()`` re-raises every ``OSError`` except ``ENOENT``/``ENOTDIR``/
    # ``ELOOP``, so a ``chmod 000`` parent escaped as a ``PermissionError`` TRACEBACK and the
    # interpreter exited **1** — the DRIFT class, whose remedy sends an operator to
    # ``git fetch && git checkout`` for a filesystem permission problem. And because it returned
    # ``False`` for ELOOP, a record that IS there as a self-referential or dangling link was
    # reported as ``no-pin`` → exit **0**, a false PASS claiming the box "records no pin" over a
    # record it merely could not examine.
    #
    # ``lstat`` asks about the ENTRY, not what it resolves to, and this is the same predicate
    # ``link_state`` already uses (``os.path.lexists``). Only a provably ABSENT entry — same
    # absence ``Path.exists()`` itself defines, ``ENOENT``/``ENOTDIR`` — is ``None``. Everything
    # else is returned, because readability is ``pin_state``'s job and it already answers
    # ``PIN_UNREADABLE`` → 2: an unexaminable record is never an absent one (the rule
    # ``harness-up`` states as its own verdict: "cannot-verify is never an absent pin").
    try:
        os.lstat(default)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return default
    return default


def _pin_sha_line(pin: Path) -> str:
    """The commit id a pin record NAMES — line 1 of it, never the whole file.

    ⛔ The whole-file read is what PR #10250 review ROUND 6 ISSUE 1 (P2, measured) found: the
    fleet's own writer emits TWO lines —
    ``printf '%s\\n%s\\n' "$got" "$(date -u +%FT%TZ)"`` (agent-infra
    ``scripts/cloud-cell-bringup.sh:815``) — and its own suite pins that as the format CONTRACT
    (``cloud-cell-bringup.test.sh:1160-1167``: *"HARNESS-PIN line 1 is the FULL sha, so 'status'
    can read it with head -1"* / *"line 2 is the timestamp"*). ``str.strip()`` removes only
    LEADING/TRAILING whitespace, so the INTERIOR newline survived and the anchored
    ``PIN_SHA_RE`` could never match: a perfectly valid record was named as corrupt, ``--pin-check``
    could never return ok/behind/diverged on any box that script provisioned, and the ``--check``
    drift note degraded to ``pin: UNKNOWN`` on exactly the boxes #10114 is about.

    Every fleet reader takes line 1 the same way (``harness-up``, ``harness-apply-pass.sh``,
    ``cloud-cell-supervisor.sh``), so this is ONE reader shared by ``pin_state`` and
    ``pin_remedy`` — the remedy read the whole file too, and would have printed
    ``git checkout <sha>\\n<timestamp>`` once the verdict was fixed alone.
    """
    return pin.read_text(encoding="utf-8").split("\n", 1)[0].strip()


def _git(infra: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run ``git -C <infra> …``; ``None`` when git itself could not be run."""
    try:
        return subprocess.run(
            ["git", "-C", str(infra), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def pin_state(infra: Path, pin: Path | None) -> tuple[str, str]:
    """Classify ``infra`` against the harness pin. Returns ``(state, detail)``.

    The decision is ANCESTRY, never a commit count (``git merge-base --is-ancestor``, the
    primitive this repo already binds itself to in ``.github/scripts/check-deploy-staleness.sh``:
    "NEVER a commit count for the DECISION"). Counts appear only as the STATED distance.

    Why this exists (#10114): three times in one session a stale copy of a harness script
    silently disabled the PR-repair fleet — ``pr-doctor.py`` (Oct 20, no ``research_section``),
    then ``objective.py`` twice — and each time the only signal was an ``rc`` in a log nobody was
    reading. ``$AGENT_INFRA_PATH`` pointing at an older tree than the pin is the same defect one
    layer up, and on the box this was measured on it is live: the checkout is 23 commits behind
    the pin, and every setup step reads green over it.
    """
    if pin is None:
        return PIN_NO_PIN, (
            f"no harness pin recorded (no {default_pin_path()}, no ${PIN_ENV}, no --pin) — "
            "cannot say whether this checkout is the tree this box runs"
        )
    try:
        sha = _pin_sha_line(pin)
    except (OSError, UnicodeDecodeError) as exc:
        # ``UnicodeDecodeError`` is a ``ValueError``, NOT an ``OSError`` (Python's exception
        # hierarchy; verified, not assumed). A pin holding raw bytes therefore escaped a bare
        # ``except OSError``, escaped ``main``'s ``ConfigError`` handler, and the interpreter
        # exited **1** — the DRIFT class, whose repair sends an operator to
        # `git fetch && git checkout`. A pin this tool cannot DECODE is not a stale checkout, it
        # is the "cannot verify" state this module already has, so it lands there (exit 2).
        # The encoding is pinned to utf-8 rather than left to the locale: the harness writes a
        # hex sha, and a lane's locale must not decide what the box's pin file says.
        return PIN_UNREADABLE, f"the pin record {pin} cannot be read ({exc}) — cannot verify"
    if not sha:
        # ⛔ An EMPTY pin is not "no pin". The file exists and says nothing, so the box cannot
        # name the commit it runs; reading that as "unpinned" would be the false pass this whole
        # module is against.
        return PIN_UNREADABLE, (
            f"the pin record {pin} is EMPTY — it names no commit, and an empty pin is not "
            "'no pin'; refusing to call this verified"
        )
    if not PIN_SHA_RE.match(sha):
        return PIN_UNREADABLE, (
            f"the pin record {pin} holds {sha[:40]!r}, which is not a commit id — a corrupted "
            "pin cannot say which tree this box runs; refusing to call this verified"
        )

    # ⛔ ``git -C <dir>`` ANSWERS ABOUT AN ENCLOSING REPOSITORY (PR #10250 review ROUND 6
    # ISSUE 3, P3 — measured, both directions). git's own manual: *"most of the options and
    # operation modes require you to run this command inside a git repository **or a working
    # tree that is under the control of a git repository**"* — so a plain directory with no
    # ``.git`` of its own, nested anywhere inside a checkout (a vendored copy, exactly the shape
    # this docstring warns about), answered ``rev-parse --verify HEAD`` with the ENCLOSING
    # repo's HEAD. The verdict was then a false ``ok`` when that HEAD happened to carry the pin,
    # or a drift verdict whose printed remedy (``cd <dir> && git fetch``) cannot run at all.
    # ``--show-toplevel`` is the question that was actually being asked: is THIS directory the
    # root of the repository git is answering about?
    toplevel = _git(infra, "rev-parse", "--show-toplevel")
    root = toplevel.stdout.strip() if toplevel is not None and toplevel.returncode == 0 else ""
    if not root or os.path.realpath(root) != os.path.realpath(infra):
        # ``realpath`` on both sides: ``infra`` was already ``resolve()``d, and a pinned tree is
        # materialised as a LINKED WORKTREE (``~/harness/<sha12>``), where ``--show-toplevel``
        # legitimately names the worktree root — the same directory, not the main checkout.
        enclosing = f" (git answers for {root}, the ENCLOSING repository)" if root else ""
        return PIN_NOT_A_REPO, (
            f"{infra} is not a git checkout{enclosing}, so it cannot be compared with the pin "
            f"{sha[:12]} — cannot verify"
        )

    head = _git(infra, "rev-parse", "--verify", "HEAD")
    if head is None or head.returncode != 0:
        return PIN_NOT_A_REPO, (
            f"{infra} is not a git checkout (or git cannot read it), so it cannot be compared "
            f"with the pin {sha[:12]} — cannot verify"
        )
    head_sha = head.stdout.strip()

    present = _git(infra, "cat-file", "-e", f"{sha}^{{commit}}")
    if present is None or present.returncode != 0:
        return PIN_ABSENT, (
            f"{infra} does not contain the pinned commit {sha[:12]} — the checkout predates the "
            "pin, so anything resolved through it is older than what this box runs"
        )

    if head_sha == sha:
        return PIN_OK, f"{infra} is AT the pin {sha[:12]}"
    contains = _git(infra, "merge-base", "--is-ancestor", sha, head_sha)
    if contains is not None and contains.returncode == 0:
        return PIN_OK, f"{infra} ({head_sha[:12]}) carries the pin {sha[:12]}"
    behind = _git(infra, "merge-base", "--is-ancestor", head_sha, sha)
    if behind is not None and behind.returncode == 0:
        distance = _git(infra, "rev-list", "--count", f"{head_sha}..{sha}")
        count = (
            distance.stdout.strip() if distance is not None and distance.returncode == 0 else "?"
        )
        return PIN_BEHIND, (
            f"{infra} ({head_sha[:12]}) is {count} commits BEHIND the pin {sha[:12]} — every "
            "file resolved through it is older than what this box runs"
        )
    return PIN_DIVERGED, (
        f"{infra} ({head_sha[:12]}) and the pin {sha[:12]} have DIVERGED — neither is an "
        "ancestor of the other, so it is not the tree this box runs"
    )


def pin_remedy(infra: Path, pin: Path | None) -> str:
    """The command that actually fixes drift, so the finding is actionable on its face."""
    sha = "<pin>"
    if pin is not None:
        # Same widened tuple as ``pin_state``: an undecodable pin must degrade to the ``<pin>``
        # placeholder, never raise out of a function whose only job is to print a remedy hint.
        with contextlib.suppress(OSError, UnicodeDecodeError):
            sha = _pin_sha_line(pin) or "<pin>"
    return f"cd {infra} && git fetch origin && git checkout {sha}"


def link_state(dest: Path, src: Path) -> tuple[str, str]:
    """Classify ``dest`` against ``src``. Returns ``(state, detail)``.

    States: ``ok``, ``missing``, ``dangling``, ``wrong-target``, ``real-dir``, ``real-file``.
    ``real-dir`` is called out separately because a materialised *copy* of agent-infra's scripts
    would silently drift from it — the failure mode vendoring always has — while an empty
    directory is what a checkout looks like when someone created it by hand.
    """
    if not os.path.lexists(dest):
        return "missing", f"{dest} does not exist"
    if dest.is_symlink():
        target = os.readlink(dest)
        resolved = Path(os.path.realpath(dest))
        if not dest.exists():  # lexists said yes, exists said no -> dangling
            return "dangling", f"{dest} -> {target} (target missing)"
        if resolved != Path(os.path.realpath(src)):
            return "wrong-target", f"{dest} -> {target} (expected {src})"
        return "ok", f"{dest} -> {target}"
    if dest.is_dir():
        return "real-dir", f"{dest} is a real directory, not a link to {src}"
    return "real-file", f"{dest} is a real file, not a link to {src}"


def diagnose(infra: Path, pin: Path | None = None) -> list[str]:
    """Explain *why* a documented tool is still missing, beyond the link itself.

    The link can be perfect and ``scripts/admin-merge.sh`` still absent: the pointer resolves to
    whatever that checkout happens to contain. On this lane's own machine the local agent-infra
    was 1341 commits behind ``origin/main`` and contained 16 of the 182 scripts upstream has,
    including none of the three this repo's docs name. A tool that reports "link ok" and leaves
    that unspoken reproduces the issue it was written to close, one layer down.

    The PIN half is the same lesson one layer up (#10114): a checkout can carry every documented
    tool and still not be the tree this box RUNS. Measured on a live box, ``$AGENT_INFRA_PATH``
    was 23 commits behind ``HARNESS-PIN`` and this function said nothing, because presence of the
    three tools was the only question it asked. Presence is not provenance.
    """
    notes: list[str] = []
    missing = [t for t in DOCUMENTED_TOOLS if not (infra / LINK_NAME / t).exists()]
    if missing:
        notes.append(
            f"{infra} has no {', '.join(LINK_NAME + '/' + t for t in missing)} — the link is "
            "correct and the CHECKOUT is stale. Update it: cd "
            f"{infra} && git fetch origin && git checkout origin/main"
        )
        if (infra / ".git").exists():
            notes.append(
                f"({infra} is a git checkout — compare it with origin/main before trusting any "
                "'not found' from this repo's docs.)"
            )

    state, detail = pin_state(infra, pin)
    if state in PIN_DRIFT:
        notes.append(
            f"{detail}. The link is correct and the CHECKOUT is not the tree this box runs — "
            f"run `{Path(__file__).name} --pin-check` to make that a verdict, or fix it: "
            f"{pin_remedy(infra, pin)}"
        )
    elif state in PIN_CANNOT_VERIFY:
        notes.append(f"pin: UNKNOWN — {detail}")
    return notes


def materialise(dest: Path, src: Path, *, dry_run: bool = False) -> str:
    """Point ``dest`` at ``src``, replacing a stale link. Returns the action taken."""
    state, detail = link_state(dest, src)
    if state == "ok":
        return f"ok: {detail}"
    if state in {"real-dir", "real-file"} and not (
        state == "real-dir" and not any(dest.iterdir())
    ):
        # Refuse to delete something with content in it. A non-empty real scripts/ is either a
        # vendored copy (a deliberate choice that must not be silently undone) or a hand-made
        # directory (same). An EMPTY directory is the residue of a failed setup and is replaced.
        raise ConfigError(
            f"{detail}. Refusing to replace it — move it aside, or delete it if it is empty."
        )
    if dry_run:
        return f"would-relink: {state} ({detail})"
    if os.path.lexists(dest):
        if dest.is_dir() and not dest.is_symlink():
            dest.rmdir()
        else:
            dest.unlink()
    os.symlink(src, dest)
    # Belt-and-braces, and NOT redundant: re-classify what was just written. A link that does not
    # resolve must be exit 2 (config error), never a printed `relinked:` with exit 0 — reporting a
    # pointer that is not usable is the one contract this tool exists to keep, and a future
    # symlink surprise (a relative target, a platform quirk) must not be able to read as success.
    written, detail = link_state(dest, src)
    if written != "ok":
        raise ConfigError(
            f"wrote {dest} -> {src}, but it does not resolve ({written}: {detail}). The pointer is "
            "NOT usable — refusing to report success."
        )
    return f"relinked: {dest} -> {src} (was {state})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialise the machine-local `scripts` symlink from $AGENT_INFRA_PATH (#9475)."
    )
    # ⛔ Two ASSERTION modes, so they are mutually exclusive rather than "the first one wins".
    # `main` answers the pin question first and RETURNS; without this group, `--check --pin-check`
    # therefore exited 0 on a clean pin while the pointer assertion never ran — a fail-open green
    # in the exact surface a lane reads first (`AGENTS.md` puts both commands on adjacent lines).
    # argparse refuses the combination with its own usage error and status 2, which is this tool's
    # config-error class: the illegal call is NAMED instead of half-answered.
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="assert, do not change anything")
    modes.add_argument(
        "--pin-check",
        action="store_true",
        help=(
            "assert the CHECKOUT is the tree this box is pinned to (HARNESS-PIN): 0 = at/contains "
            "it or no pin recorded, 1 = drift, 2 = cannot verify. Never touches the link. "
            "Not combinable with --check: they answer different questions, so run both"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="report the action, change nothing")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repo root (default: this checkout)")
    parser.add_argument("--infra", default=None, help="agent-infra checkout (default: $AGENT_INFRA_PATH); authoritative when passed")
    parser.add_argument(
        "--pin",
        default=None,
        help=(
            f"harness pin record (default: ${PIN_ENV}, then "
            f"${{{CELL_HOME_ENV}:-$HOME}}/{PIN_RELATIVE}); authoritative when "
            "passed"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "only exit status and errors — suppresses the state line AND the drift note (the "
            "note is a warning, and both callers that pass this flag key on it meaning silent); "
            "use --pin-check when drift must be a verdict"
        ),
    )
    args = parser.parse_args(argv)

    dest = Path(args.repo).resolve() / LINK_NAME
    try:
        infra = resolve_infra(args.infra)
        src = infra / LINK_NAME
        pin = resolve_pin(args.pin)

        if args.pin_check:
            # The one mode that answers the PIN question as a verdict. It always prints the
            # verdict — including `no-pin`, which is an answer ("nothing records a pin here"),
            # because silence is exactly what made the #10114 outages expensive.
            state, detail = pin_state(infra, pin)
            print(f"pin: {state} — {detail}")
            if state in PIN_DRIFT:
                print(f"  remedy: {pin_remedy(infra, pin)}", file=sys.stderr)
                return 1
            if state in PIN_CANNOT_VERIFY:
                return 2
            return 0

        if args.check:
            state, detail = link_state(dest, src)
            if not args.quiet:
                print(f"{state}: {detail}")
                for note in diagnose(infra, pin):
                    print(f"  ! {note}", file=sys.stderr)
            return 0 if state == "ok" else 1
        action = materialise(dest, src, dry_run=args.dry_run)
        if not args.quiet:
            print(action)
            print(f"  agent-infra: {infra}")
            for note in diagnose(infra, pin):
                print(f"  ! {note}", file=sys.stderr)
        return 0
    except ConfigError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
