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

Exit codes
----------
    0  the link resolves to ``$AGENT_INFRA_PATH/scripts`` (``--check``: and it already did)
    1  the link is missing, dangling, or points somewhere else (``--check``: a failure)
    2  config error — no agent-infra checkout with a ``scripts/`` dir could be found

Exit 2 is deliberately distinct from 1: "the pointer is wrong" and "there is nothing on this
machine to point AT" are different repairs, and collapsing them is how a stale checkout gets
mistaken for a missing link. That distinction is load-bearing here, because this issue's own
symptom had BOTH causes at once — a dangling blob AND an ``$AGENT_INFRA_PATH`` checkout 1341
commits behind ``origin/main``, so ``scripts/admin-merge.sh`` was absent from the local
agent-infra too. See ``diagnose()``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LINK_NAME = "scripts"

# Tools this repo names in its own docs and hooks. Only used to make a *stale checkout* legible
# (`diagnose` asks agent-infra whether it has them); never to gate the link itself — the link
# points at a directory, and a directory is either there or not.
DOCUMENTED_TOOLS = ("admin-merge.sh", "record-review.sh", "ci-failure-set.sh")


class ConfigError(RuntimeError):
    """No usable agent-infra checkout — exit 2, never confused with a dangling link."""


def resolve_infra(explicit: str | None) -> Path:
    """The agent-infra checkout to point at, in precedence order.

    ``--infra`` beats ``$AGENT_INFRA_PATH`` beats ``~/agent-infra``. The last fallback is not a
    guess: agent-infra's own ``link-skills.sh`` uses the identical
    ``${AGENT_INFRA_PATH:-$HOME/agent-infra}`` default, so a lane that relies on the fallback
    gets the same answer from both tools instead of two different ones.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("AGENT_INFRA_PATH"):
        candidates.append(Path(os.environ["AGENT_INFRA_PATH"]).expanduser())
    candidates.append(Path.home() / "agent-infra")

    tried: list[str] = []
    for cand in candidates:
        src = cand / LINK_NAME
        tried.append(str(cand))
        if src.is_dir():
            return cand
    raise ConfigError(
        "no agent-infra checkout with a scripts/ directory. Looked at: "
        + ", ".join(tried)
        + ". Set AGENT_INFRA_PATH to a clone of daniel-ospina/agent-infra, or pass --infra."
    )


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


def diagnose(infra: Path) -> list[str]:
    """Explain *why* a documented tool is still missing, beyond the link itself.

    The link can be perfect and ``scripts/admin-merge.sh`` still absent: the pointer resolves to
    whatever that checkout happens to contain. On this lane's own machine the local agent-infra
    was 1341 commits behind ``origin/main`` and contained 16 of the 182 scripts upstream has,
    including none of the three this repo's docs name. A tool that reports "link ok" and leaves
    that unspoken reproduces the issue it was written to close, one layer down.
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
    return f"relinked: {dest} -> {src} (was {state})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialise the machine-local `scripts` symlink from $AGENT_INFRA_PATH (#9475)."
    )
    parser.add_argument("--check", action="store_true", help="assert, do not change anything")
    parser.add_argument("--dry-run", action="store_true", help="report the action, change nothing")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repo root (default: this checkout)")
    parser.add_argument("--infra", default=None, help="agent-infra checkout (default: $AGENT_INFRA_PATH)")
    parser.add_argument("--quiet", action="store_true", help="only exit status and errors")
    args = parser.parse_args(argv)

    dest = Path(args.repo).resolve() / LINK_NAME
    try:
        infra = resolve_infra(args.infra)
        src = infra / LINK_NAME
        if args.check:
            state, detail = link_state(dest, src)
            if not args.quiet:
                print(f"{state}: {detail}")
                for note in diagnose(infra):
                    print(f"  ! {note}", file=sys.stderr)
            return 0 if state == "ok" else 1
        action = materialise(dest, src, dry_run=args.dry_run)
        if not args.quiet:
            print(action)
            print(f"  agent-infra: {infra}")
            for note in diagnose(infra):
                print(f"  ! {note}", file=sys.stderr)
        return 0
    except ConfigError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
