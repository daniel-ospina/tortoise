#!/usr/bin/env python3
"""container_reaper — the ACTUATOR for the per-lane FalkorDB container sweep.

WHY THIS EXISTS (and why it is not a fifth reaper)
--------------------------------------------------
`tools/test_lane.py` already DECIDES, correctly, which ``fdb-lane-*`` containers
are out of service: three ownership proofs plus an age backstop (#8180, #10693,
#10712). What it has never had is a CALLER. ``reap_orphans()`` is invoked from
``start()`` and from nowhere else, so **no scheduler on any box could call it at
all** — and the measured consequence is the leak the owner named:

  * cell-1 — **76** containers, **63** more than an hour old, load **285.53** on
    8 cores (101 ``redis-server``, 78 ``containerd-shim``, 76 ``docker-proxy``);
    reaping 63 took load **285.53 → 40.89**.
  * cell-4 — **94**; cell-3 — **19**. **189 stranded containers over 3 boxes**,
    every one of them reaped **by hand**.
  * this cell (`cmux`, measured 2026-11-03): the only scheduled sweep is
    ``eldato-disk-reaper.timer`` (15 min), whose classes are
    ``uv / venv / worktree / duplicate-install``. A case-insensitive search for
    ``docker`` or ``container`` across ``disk-reaper.sh`` and its delegates
    returns **0**. ``tools/run_reaper.py`` cancels GitHub Actions runs;
    ``orphan-reap.py`` reaps processes; ``install-reaper-schedule.sh`` schedules
    the *embedded* (redislite) reaper and its Linux arm needs a ``crontab`` this
    box does not have.

So the defect was never the decision. It was that *"an actuator that cannot
deliver is a defect, not a status"* — and this module is the actuator: the entry
point a timer calls, with a dry run that is the default.

THE DECISION IS NOT RE-IMPLEMENTED HERE
---------------------------------------
This module enumerates, reports and invokes. It owns no ownership rule of its
own: the verdict for every container comes from ``test_lane.reap_orphans()``,
the same call ``start()`` makes, so the timer and the lane can never disagree
about what is an orphan.

The dry run is not a second implementation either. It runs the REAL sweep with
the removal syscall intercepted at ``test_lane._docker`` — the tool's single
docker chokepoint — so a dry run can differ from ``--apply`` in exactly one way:
the ``rm`` does not happen. A dry run that decided for itself could agree with
``--apply`` today and diverge on the next change to the proofs; this cannot.

WHAT MAY BE REMOVED, AND WHAT MAY NEVER BE
------------------------------------------
Delegated, and stated here only so an operator reading the timer's output knows
what the default buys:

* **removed** — a container whose owning worktree is GONE (proof 1: the one
  removal #8180 authorises unconditionally), plus whatever the operator has
  explicitly armed with ``TORTOISE_TEST_LANE_REAP_LANE_GONE`` /
  ``TORTOISE_TEST_LANE_REAP_OWNER_INDETERMINATE`` (proofs 2 and 3; withheld by
  default because #8180's ruling does not settle them).
* **never removed** — a container whose owning lane holds a live process, and
  this worktree's own container. **Age is never the proof**: a four-hour-old
  container with a live owner is KEPT, and a test pins exactly that.
* **never attempted** — ``docker system prune -a`` (it eats images other lanes
  need), and any volume (a non-test graph may live in one). This tool issues
  ``docker rm -f`` on a name it has already established it owns, and nothing
  else.

USAGE
-----
    uv run python tools/container_reaper.py                # DRY RUN (default)
    uv run python tools/container_reaper.py --apply         # remove
    uv run python tools/container_reaper.py --json          # machine-readable
    uv run python tools/container_reaper.py --json --apply  # what the timer runs

Exit codes
----------
  0  complete (dry run or apply): every candidate was DECIDED, and nothing that
     needed removing is still there (a deliberately kept container — proof 2/3
     withheld, or one whose client list shows a live client — is a decision)
  2  INCOMPLETE — at least one candidate could NOT be decided: ``docker ps`` or
     ``git worktree list`` could not be read (a failed read is never "no
     containers"), a container's ``CLIENT LIST`` was unreadable, or the
     directory of its owning worktree could not be examined. Removals that DID
     complete in the same sweep are still counted and reported — see the
     summary line, which is a count of what this run actually did
  3  usage error (argparse itself exits 2 before `main`, so a malformed command
     line is indistinguishable from INCOMPLETE — it is reported, never guessed at)
  4  STALE COPY — ``TORTOISE_CONTAINER_REAPER_SHA256`` (this file) or
     ``TORTOISE_TEST_LANE_POLICY_SHA256`` (``tools/test_lane.py``, the decision
     half) does not match, so a scheduler that pinned an old copy is refused
     rather than allowed to sweep with superseded policy
  5  PARTIAL — the sweep decided, but at least one ``docker rm`` FAILED, so a
     container that needed to go is still on the host

The staleness check is the timer's own defence, and it is a HARD refusal: the
installer writes the source hash into the unit, so a unit whose ``ExecStart``
still points at a moved or outdated worktree fails loudly instead of sweeping.
It is inert (and the check is skipped) when the variable is unset, so an
interactive run is never blocked by it.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape), and this tool runs on cells and
# dev boxes alike.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/container_reaper.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/container_reaper.py`"
    )

import argparse
import contextlib
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# ⛔ Imported through the PACKAGE, not as a sibling module. `tools/` is a package
# (it has `__init__.py`) and every test reaches the tool as
# `from tools import test_lane`; a bare `import test_lane` here would create a
# SECOND module object, so a test that stubs `tools.test_lane._docker` would
# still leave this module calling the real docker — a seam that reads as stubbed
# and is not. The repo root is therefore put on the path first.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import test_lane as tl  # the sibling package module

EXIT_OK = 0
EXIT_INCOMPLETE = 2      # a candidate was NOT DECIDED (a read failed) — not "nothing to do"
EXIT_USAGE = 3
EXIT_STALE = 4
EXIT_PARTIAL = 5         # a removal was attempted and FAILED

#: `reap_orphans()` fail-closes by RETURNING a line, not by raising — so a sweep
#: that decided nothing is indistinguishable from a clean one unless the caller
#: reads it. Measured: on a cell where `git worktree list` cannot run, the sweep
#: returns exactly `reap: skipped — ...; NOTHING was removed (fail-closed)` and the
#: actuator reported `ok: true, removed: 0`, exit 0, every 15 minutes — a green
#: light on the leak this tool exists to close.
_SWEEP_SKIPPED = "reap: skipped"

#: What a scheduler pins; see "Exit codes" 4.
#:
#: ⛔ TWO hashes, because the dangerous half is not this file. `reap_orphans()`
#: — the three ownership proofs, the age backstop, `_live_lane_paths` — lives in
#: `tools/test_lane.py`, which this module IMPORTS. Pinning only this file would
#: invert the risk: editing the safe enumerator would disable the timer, while
#: editing the file that decides what gets DELETED would be invisible
#: (#10806 review P1-3).
STALE_ENV = "TORTOISE_CONTAINER_REAPER_SHA256"
POLICY_ENV = "TORTOISE_TEST_LANE_POLICY_SHA256"

#: One `docker ps` for the whole population: name, state, creation, and the two
#: ownership labels #10712 writes at creation. Reading the labels HERE (rather
#: than per container with `docker inspect`) keeps the sweep to two docker calls
#: (this one and, only when there is a candidate, the `ps -s` size read below —
#: three in total; see the note there) on a box that is already overloaded — and
#: the labels are read defensively, so this module needs no edit when that PR
#: lands.
CENSUS_FORMAT = (
    "{{.Names}}\t{{.State}}\t{{.CreatedAt}}"
    '\t{{.Label "' + getattr(tl, "LABEL_CREATED", "tortoise.test-lane.created") + '"}}'
    '\t{{.Label "' + getattr(tl, "LABEL_OWNER", "tortoise.test-lane.worktree") + '"}}'
)

#: Docker's own `ps` rendering of a creation stamp — `2026-11-03 22:28:01 +0000
#: UTC`, NOT ISO-8601. Parsed tolerantly because it is display text: an
#: unparseable stamp makes the AGE "unknown", never the container removable
#: (the decision does not use it; only the report does).
_PS_CREATED = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\s*([+-]\d{4}))?")

#: `docker ps -s` size token. Docker prints SI units; the writable layer is what
#: is actually reclaimed, and the `(virtual N)` half is the SHARED image, which a
#: single container's removal does not free — reporting them as one number would
#: overstate the win by ~40x (measured: 5.78MB writable vs 252MB virtual).
_SIZE_TOKEN = re.compile(r"^\s*([0-9.]+)\s*([kKMGTP]?B)")
_SI = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
       "PB": 1000**5}


def _ps_created_epoch(text: str) -> float | None:
    """Seconds since the epoch for Docker's ``CreatedAt`` text, or ``None``.

    ``None`` is the honest answer for text this parser does not recognise — the
    report then says ``age=unknown`` rather than inventing one.
    """
    m = _PS_CREATED.match((text or "").strip())
    if not m:
        return None
    day, clock, offset = m.groups()
    try:
        stamp = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if offset:
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = int(offset[1:3]), int(offset[3:5])
        # Docker prints the CREATION time in the daemon's zone; shifting into UTC
        # keeps an age comparable across a mixed fleet.
        delta = sign * (hours * 3600 + minutes * 60)
        return stamp.replace(tzinfo=UTC).timestamp() - delta
    return stamp.replace(tzinfo=UTC).timestamp()


def _ps_size_bytes(text: str) -> int | None:
    """The WRITABLE layer this container holds, in bytes, or ``None``.

    Deliberately reads the token BEFORE ``(virtual …)``: the virtual number is
    the shared image plus the layer, so quoting it as "freed" would overstate a
    single removal by the whole image.
    """
    m = _SIZE_TOKEN.match(text or "")
    if not m:
        return None
    try:
        return int(float(m.group(1)) * _SI.get(m.group(2).upper(), 1))
    except (ValueError, KeyError):
        return None


def _human_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.2f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1000
    return f"{value:.2f}TB"


def _human_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 0:
        # A clock skew between the daemon and this host. Say so instead of
        # printing a negative age that reads as a bug in the reaper.
        return "clock-skew"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


@contextlib.contextmanager
def _removal_intercepted(record: list[str]):
    """Run the real sweep with every ``docker rm`` turned into a RECORD.

    The interception sits on ``test_lane._docker`` — the single chokepoint every
    docker call in that module goes through — so the sweep's CONTROL FLOW is
    unchanged (it sees a success and moves on) while nothing is destroyed. That
    is what makes the dry run provably the same decision as ``--apply``.

    A ``rm`` that is NOT ``-f`` is refused loudly: the sweep only ever issues
    ``rm -f``, so anything else means this interception has drifted away from
    the call sites it exists to catch, and a dry run that silently allowed it
    would be the one failure mode that matters.
    """
    original = tl._docker

    #: Verbs that CANNOT change host state. Anything else is refused during a dry
    #: run — a deny-list, not an allow-list: the previous `rm`-only intercept
    #: passed every other verb through, so a future `kill`, `volume rm`,
    #: `container prune` or `system prune` in the sweep would have EXECUTED during
    #: what this file calls a dry run (measured: `stop()` already issues `kill`).
    #: Verbatim verbs that cannot change host state. Deliberately NARROW: `image`
    #: and `images` are NOT here (`docker image prune`, `docker image rm` are
    #: writes, and a reviewer executed both during a dry run), and neither are
    #: `stats`/`top`/`diff`/`history`, which the sweep never issues — an unused
    #: entry in this set is pure surface for a future mistake.
    _READ_VERBS = frozenset({
        "ps", "inspect", "port", "logs", "version", "info", "events",
    })

    def intercept(*args, **kwargs):
        verb = args[0] if args else ""
        if verb in _READ_VERBS:
            return original(*args, **kwargs)
        # `exec` runs an ARBITRARY command in the container, so it is allowed only
        # as the one probe the sweep makes: `redis-cli CLIENT LIST` (the client
        # liveness read). `docker exec <c> redis-cli FLUSHALL` executed during a
        # dry run before this check existed.
        if verb == "exec":
            rest = [a for a in args[2:]]
            if rest[:3] == ["redis-cli", "CLIENT", "LIST"] and len(rest) == 3:
                return original(*args, **kwargs)
            raise SystemExit(
                f"container_reaper: refusing the dry run — the sweep issued "
                f"`docker {' '.join(args)}`, and only the `redis-cli CLIENT "
                f"LIST` probe is allowed to exec. A dry run that cannot prove "
                f"it would not change the container is not a dry run.")
        if verb == "rm" and "-f" in args:
            # The name is the last token that is not a flag — `rm -f --volumes
            # <name>` would otherwise record `--volumes` and lose the removal.
            name = next((a for a in reversed(args[1:]) if not a.startswith("-")),
                        "")
            record.append(name)
            return subprocess.CompletedProcess(
                ["docker", *args], 0, name + "\n", "")
        raise SystemExit(
            f"container_reaper: refusing the dry run — the sweep issued "
            f"`docker {' '.join(args)}`, which is neither a read verb nor the "
            f"`rm -f` this interception covers. A dry run that cannot prove it "
            f"would not change the host is not a dry run.")

    tl._docker = intercept
    try:
        yield record
    finally:
        tl._docker = original


def _census() -> list[dict] | None:
    """Every ``fdb-lane-*`` container, plus its writable-layer size.

    ``None`` means docker could not be read — which authorises NOTHING. Absence
    of evidence is never evidence of absence, and this tool deletes.
    """
    listed = tl._docker("ps", "-a", "--filter", f"name={tl.NAME_PREFIX}",
                        "--format", CENSUS_FORMAT)
    if listed.returncode != 0:
        return None
    rows: list[dict] = []
    for line in (listed.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].strip():
            continue
        name = parts[0].strip()
        if not tl.is_managed(name):
            continue
        state = parts[1].strip().lower() or "unknown"
        created_text = parts[2] if len(parts) > 2 else ""
        label_created = parts[3].strip() if len(parts) > 3 else ""
        label_owner = parts[4].strip() if len(parts) > 4 else ""
        rows.append({
            "name": name,
            "state": state,
            "created": _ps_created_epoch(created_text),
            "label_created": label_created,
            "label_owner": label_owner,
        })
    # `ps -s` walks every container's writable layer — the most expensive docker
    # read in the sweep (the catalogue comment above says "two docker calls"; it
    # is THREE, and this is the one to skip). With no candidate at all there is
    # nothing to size, so the idle tick stays cheap.
    sizes: dict[str, int | None] = {}
    sized = (tl._docker("ps", "-a", "-s", "--filter", f"name={tl.NAME_PREFIX}",
                        "--format", "{{.Names}}\t{{.Size}}")
             if rows else None)
    if sized is not None and sized.returncode == 0:
        for line in (sized.stdout or "").splitlines():
            name, _, size = line.partition("\t")
            if name.strip():
                sizes[name.strip()] = _ps_size_bytes(size)
    # A failed SIZE read is not a failed census: the container is still a
    # candidate, it just reports `bytes=unknown`. Only the NAME/STATE read is
    # load-bearing, because only that read names what may be removed.
    for row in rows:
        row["bytes"] = sizes.get(row["name"])
    return rows


def _age_seconds(row: dict, now: float) -> float | None:
    """Age from the CREATION LABEL when it is there, else Docker's own stamp.

    The label is preferred because it is the container's own record and survives
    a daemon whose `CreatedAt` is reported in a different zone (#10712 writes
    it); Docker's stamp is the fallback so this reports correctly on a container
    created before that label existed.
    """
    stamped = row.get("label_created")
    if stamped:
        try:
            return now - datetime.fromisoformat(stamped).timestamp()
        except ValueError:
            pass
    created = row.get("created")
    return None if created is None else now - created


def _owner_report(row: dict, slug_map: dict[str, str] | None,
                  live_paths: set[str] | None) -> str:
    """``<path> (live|gone|idle|unresolvable)`` for one container.

    Reported from the SAME two PREDICATES the reaper decides with
    (``_worktree_slugs`` / ``_live_lane_paths``), so the line an operator reads
    and the verdict beneath it cannot tell different stories **about the same
    readings**. It is not the same two READINGS: ``reap()`` takes its own census
    reads for this column and then calls ``reap_orphans()``, which reads both
    surfaces again — a worktree removed or a process exiting in between can make
    the two disagree for one sweep. Stated rather than papered over, because this
    is the evidence line of a destructive tool (#10813 review P3).
    """
    owner = row.get("label_owner") or None
    if not owner and slug_map is not None:
        owner = slug_map.get(row["name"][len(tl.NAME_PREFIX):])
    if not owner:
        return "unresolvable"
    where = owner
    if slug_map is None:
        return f"{where} (ownership record unreadable)"
    try:
        exists = Path(owner).is_dir()
    except OSError:
        return f"{where} (unexaminable)"
    if not exists:
        return f"{where} (gone)"
    if live_paths is None:
        return f"{where} (liveness unreadable)"
    # The SAME predicate the sweep decides with — `_holds(owner_path, held_path)`
    # — not a slug set. A slug set counts every ANCESTOR of every live path, so a
    # process in `<main>/.worktrees/x` makes the MAIN checkout read "(live)" while
    # `_holds` (correctly) says that different lane holds nothing of main's: the
    # report would print "(live)" beside a line saying it is about to be removed.
    # On a destructive tool the report line IS the evidence.
    if any(tl._holds(p, owner) for p in live_paths):
        return f"{where} (live)"
    return f"{where} (idle)"


def _verdicts(lines: list[str], *, dry_run: bool) -> dict[str, str]:
    """Map each container to the verdict the SWEEP reached about it.

    Lifted from ``reap_orphans``' own report lines, so this module never forms a
    second opinion. A name absent from the lines was not a candidate and is
    reported as ``kept (not a candidate)`` rather than guessed at.
    """
    out: dict[str, str] = {}
    for line in lines:
        if not line.startswith("reap: "):
            continue
        for name in re.findall(r"fdb-lane-[0-9a-z]+", line):
            text = line[len("reap: "):]
            if text.startswith("removed"):
                text = ("would remove" + text[len("removed"):]) if dry_run \
                    else text
            out[name] = text
    return out


def reap(*, apply: bool, as_json: bool = False) -> int:
    """One sweep. Dry run unless ``apply``. Returns the process exit code."""
    rows = _census()
    if rows is None:
        msg = ("container_reaper: `docker ps` could not be read, so NOTHING "
               "was decided or removed (a failed read is not 'no containers')")
        if as_json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_INCOMPLETE

    slug_map = tl._worktree_slugs()
    live_paths = tl._live_lane_paths()
    recorded: list[str] = []
    if apply:
        lines = tl.reap_orphans()
    else:
        with _removal_intercepted(recorded) as rec:
            lines = tl.reap_orphans()
        recorded = rec

    # A sweep that could not decide is NOT a clean sweep (#10806 review P1).
    # ⛔ `unproven` is appended BELOW, after the disagreement check — folding it
    # in HERE (its first version) read the list while it was still empty, so the
    # one guard whose entire job is "refuse a green light" was dead code that
    # reported `ok: true` (#10813 review P1, caught by a fresh reviewer).
    skipped = [ln for ln in lines if ln.startswith(_SWEEP_SKIPPED)]
    # The sweep's OTHER fail-closed shape: a container it COULD have removed but
    # could not decide about (`kept … its owning worktree … is gone, but the
    # container reports nothing about its clients (unreadable)`). Narrowed to the
    # armed population on purpose — `is gone` in the line — because a peer
    # container whose probe fails is a report, not an undecided tick, and making
    # every such container red a 15-minute timer is the over-block this project
    # classifies B (#10813 review P1-2).
    # Two arms of proof 1 — the only population the unit removes — can keep a
    # container because a surface they NEED was unreadable: the container's client
    # list, or the owning worktree's directory (`EACCES` is reachable: a worktree
    # under another user's CELL_HOME is unexaminable for this unit's user, and the
    # doc cites cross-clone lanes on one host). Both are "kept because we could not
    # decide", so both are INCOMPLETE — the third shape of the same class
    # (#10813 review). Neither predicate can fire on a proof-2 report, so the
    # timer is not reddened for a peer whose probe merely failed.
    undecided = [ln for ln in lines
                 if ln.startswith("reap: kept")
                 and ("is gone" in ln or "could not be examined" in ln)
                 and ("unreadable" in ln or "fail-closed" in ln)]
    skipped = skipped + undecided
    failed = [ln for ln in lines if "FAILED to remove" in ln]
    verdicts = _verdicts(lines, dry_run=not apply)
    # WHAT ACTUALLY HAPPENED, from the sweep's own verdict lines — NOT from the
    # dry-run interception list, which is empty by construction under `--apply`
    # (measured: the first real run printed "removed 0" beside a line that said
    # "removed fdb-lane-…", a summary contradicting its own body).
    acted = [n for n, v in verdicts.items()
             if v.startswith("would remove" if not apply else "removed")]
    if not apply and set(acted) != set(recorded):
        # The interception and the verdicts are two views of one run; if they
        # ever disagree, the dry run is no longer proof of what `--apply` does.
        # Not a warning: the verdicts and the interception are two views of ONE
        # run, so a disagreement means this dry run is not evidence of what
        # `--apply` would do. Reporting that as exit 0 would be the same green
        # light this module exists to refuse (#10806 review).
        skipped.append(
            "the intercepted removals and the sweep's verdicts disagree "
            f"({sorted(recorded)} vs {sorted(acted)}) — this dry run is NOT "
            "proof of what --apply would do")
    now = datetime.now(UTC).timestamp()
    total_bytes = 0
    rendered: list[str] = []
    for row in rows:
        age = _age_seconds(row, now)
        verdict = verdicts.get(
            row["name"],
            # A swept population is empty when the sweep FAIL-CLOSED, so a default
            # verdict here would report a decision nobody took — and the
            # per-container line is what an operator greps (#10813 review P3).
            #
            # Keyed on the WHOLE-SWEEP refusal (`_SWEEP_SKIPPED`), NOT on
            # `skipped`: that list also carries per-container `undecided` keeps,
            # and on those the sweep DID run and DID decide about every other
            # row — saying "the sweep did not run" there was false in the same
            # way the summary line was (#10813 review).
            "NOT DECIDED (the sweep did not run)"
            if any(ln.startswith(_SWEEP_SKIPPED) for ln in skipped)
            else "kept (not a candidate)")
        freed = row["bytes"] if verdict.lower().startswith(
            ("removed", "would remove")) else None
        if freed:
            total_bytes += freed
        rendered.append(
            f"{row['name']}  age={_human_age(age)}  "
            f"owner={_owner_report(row, slug_map, live_paths)}  "
            f"state={row['state']}  bytes={_human_bytes(row['bytes'])}  "
            f"{verdict}")

    # Everything the sweep said that is NOT already on a container line — the
    # fail-closed reason lives here, and dropping it under `--json` (the form the
    # timer runs) is what made the green light invisible in the journal.
    notes = [ln for ln in lines if not re.search(r"fdb-lane-[0-9a-z]+", ln)]
    notes += undecided        # named containers, but the REASON must reach the log
    if as_json:
        print(json.dumps({
            "ok": not skipped and not failed,
            "apply": apply,
            "decided": not skipped,
            "containers": len(rows),
            "would_remove" if not apply else "removed": len(acted),
            "failed": len(failed),
            "freed_bytes": total_bytes,
            "lines": rendered,
            "notes": notes,
        }, indent=2))
        return _exit_for(skipped, failed, acted, apply=apply)

    for line in rendered:
        print(line)
    # The sweep's own lines are printed too, but only the ones NOT already
    # rendered above (a bare `skipped — …` / fail-closed line has no container),
    # so an operator never has to read the same fact twice.
    for line in notes:
        print(line)
    verb = "removed" if apply else "would remove"
    print(f"container_reaper: {len(rows)} container(s) seen; {verb} "
          f"{len(acted)}; {_human_bytes(total_bytes)} of writable layer "
          f"{'reclaimed' if apply else 'reclaimable'}"
          + ("" if apply else " — DRY RUN, pass --apply to act"))
    return _exit_for(skipped, failed, acted, apply=apply)


def _exit_for(skipped: list[str], failed: list[str],
              acted: list[str] = (), *, apply: bool = True) -> int:
    """The verdict: a failed removal beats an undecided sweep, which beats OK.

    Both were measured reporting `ok: true` / exit 0 before this existed — the
    timer's journal said the sweep was clean while nothing had been decided or
    something that needed removing was still up.

    ⛔ `acted` is not decoration: the `undecided` list is built per CONTAINER, so
    one sweep can remove an orphan and be unable to decide about another. The
    summary must then say what it DID, never "NOTHING was decided or removed" —
    a summary contradicting its own body is the exact defect class this module
    exists to refuse (see the `acted` comment above). Exit 2 stands either way:
    the tick is still INCOMPLETE.

    ⛔ …and it must not say the removal COMPLETED when this is the DEFAULT mode
    (`--apply` absent), where nothing is removed by construction. `acted` is
    derived from "would remove" verdicts under a dry run, so an un-mode-aware
    sentence printed "1 removal(s) DID complete in this sweep" beside a body that
    said `would remove` and a host where no `rm` was issued — a false
    operator-facing claim from the tool's documented default path, i.e. the very
    summary-contradicts-its-body class this returns exit 2 to refuse (#10813
    review P2). `apply` defaults to True only so a caller that can only ever
    reach this with a real sweep (nothing does today) keeps its meaning.
    """
    if failed:
        print("container_reaper: PARTIAL — at least one removal was attempted "
              "and FAILED; the container is still on the host", file=sys.stderr)
        return EXIT_PARTIAL
    if skipped:
        if not acted:
            did = "NOTHING was removed"
        elif apply:
            did = f"{len(acted)} removal(s) DID complete in this sweep"
        else:
            did = (f"{len(acted)} removal(s) WOULD complete in this sweep "
                   f"(DRY RUN — nothing was removed)")
        print("container_reaper: INCOMPLETE — at least one container could not "
              "be decided (see its reason above); " + did +
              ". This is exit 2, never a clean sweep.", file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_OK


def _stale_copy_refusal() -> str | None:
    """Each hash a scheduler pinned, compared against the file it names.

    Inert when unset, so an interactive run is never blocked. This is the
    deployed-vs-source check: the installer writes the hash of every file it
    pinned into the unit, and a unit still pointing at an older copy — or at a
    checkout whose `test_lane.py` has moved on — fails HERE rather than sweeping
    with superseded policy.
    """
    import os as _os
    checks = (
        # (env var, the file whose hash it pins, the name the message uses)
        (STALE_ENV, Path(__file__), "this actuator"),
        (POLICY_ENV, Path(tl.__file__), "the DECISION half (tools/test_lane.py)"),
    )
    for env, path, what in checks:
        want = _os.environ.get(env, "").strip()
        if not want:
            continue
        try:
            got = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            return (f"container_reaper: STALE COPY — {env} is pinned but "
                    f"{path} cannot be read ({exc}). Refusing to sweep.")
        if got == want:
            continue
        return (f"container_reaper: STALE COPY — {env} pins {want[:12]}… but "
                f"{what} is {got[:12]}… ({path}). Refusing to sweep with "
                f"superseded policy; re-run the installer "
                f"(tools/install-container-reaper-timer.sh).")
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="container_reaper.py",
        description="Reap test-lane FalkorDB containers that are PROVABLY out "
                    "of service (dry run by default; #8180/#5084).",
    )
    # A leading `reap` is accepted so the spelling matches
    # `tools/test_lane.py reap` if that verb is ever added; it is the same run.
    p.add_argument("command", nargs="?", default="reap", choices=["reap"],
                   help="the only command (default: reap)")
    p.add_argument("--apply", action="store_true",
                   help="actually remove. Without it this is a DRY RUN — the "
                        "real sweep runs with `docker rm` intercepted")
    p.add_argument("--json", action="store_true",
                   help="machine-readable summary (for a scheduler's log)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stale = _stale_copy_refusal()
    if stale:
        print(stale, file=sys.stderr)
        return EXIT_STALE
    try:
        return reap(apply=args.apply, as_json=args.json)
    except SystemExit as exc:        # the interception's own refusal
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
