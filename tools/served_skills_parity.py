#!/usr/bin/env python3
"""Served-skills parity — the MIT repo is the AUTHOR, this tree is its mirror (#4643).

WHAT THIS OWNS

`daniel-ospina/tortoise-skills-and-integrations` (MIT, public) is the
**authored source** of the three reusable integration skills —
`how-to-use-tortoise`, `tortoise-decide`, `tortoise-file-finding`. The bytes
customers actually receive are the dashboard's copy at
`website/apps/dashboard/public/skills/<name>/SKILL.md`, which vite copies into
`dist/` and Cloudflare Pages serves at `app.premiselabs.co/skills/<name>/SKILL.md`.
Two copies, one author: this tool is the gate that keeps the mirror honest and
the derivation (`--sync`) that produces it.

⛔ IT DOES NOT COVER `tortoise-onboarding` (served, but authored in
`tortoise/onboarding/SKILL.md` here — its own byte-identity gate lives in
`tests/test_onboarding_variants.py` / `deploy-pages.yml`) nor
`install-tortoise-skills.sh` (authored here since #3324 deliberately took its
source of truth OUT of the MIT repo). The ownership table is
`docs/license-notes.md` §7 — edit the right repo for the artifact.

TWO CHECKS, BECAUSE THEY FAIL DIFFERENTLY

* ``--check-local`` — the served copy equals the PINNED upstream bytes.
  Offline, deterministic, runs per-PR (`tests/test_served_skills_parity.py`).
  Catches the direction that needs no network: a served copy edited in place
  here, or an upstream file left un-mirrored after a hand edit of the pin.
* ``--check-upstream`` — the pin is HONEST and CURRENT: the pinned commit's
  bytes really hash to the pin, and upstream `main` has not moved past it.
  Needs the network; runs in `deploy-pages.yml` (so the stale mirror cannot
  ship behind a green deploy) and on a schedule (`skills-parity.yml`, so it is
  caught even when nothing else triggers a deploy).

⛔ `--check-upstream` is not decoration. A pin-only gate is VACUOUS: pin any
old commit and the "byte-identical" claim becomes true of history. The gate is
only meaningful because HEAD currency is asserted alongside it.

FAIL-CLOSED, EVERY TIME

Missing manifest, missing skill entry, missing/unreadable served copy, a
mismatched byte count, a network error after retries — each is a FAILURE with a
non-zero exit, never a skip. A parity gate that passes when it could not read a
side reports health it never observed; that is the exact defect class this
exists to close (#3673's "0 fail-open paths", #4643 targets 1 and 2).

USAGE

  uv run python tools/served_skills_parity.py --check            # local + upstream
  uv run python tools/served_skills_parity.py --check-local      # offline
  uv run python tools/served_skills_parity.py --check-upstream    # network
  uv run python tools/served_skills_parity.py --sync             # re-derive
  uv run python tools/served_skills_parity.py --sync --commit <sha>

``--sync`` is the DERIVATION: it fetches upstream (HEAD, or ``--commit``),
writes each served copy byte-for-byte and re-cuts
`config/served-skills-upstream.yml`. Run it after editing a skill upstream,
then commit the changed skills + the pin together.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# #5128 shape: refuse a <3.12 interpreter before the work, not a traceback.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/served_skills_parity.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/served_skills_parity.py`"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_REL = Path("config") / "served-skills-upstream.yml"
RAW_HOST = "https://raw.githubusercontent.com"

# A network blip must not red a deploy for a reason unrelated to the change,
# and a fail-closed gate that is FLAKY teaches people to re-run it. Retry, then
# fail — the same shape as deploy-pages.yml's bindings preflight.
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = 3.0


class ParityError(RuntimeError):
    """A fail-closed check failure (never raised for "nothing to check")."""


def _err(msg: str) -> None:
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else msg,
          file=sys.stderr)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ── the pin ────────────────────────────────────────────────────────────────

def load_manifest(path: Path) -> dict:
    """Parse the pin. Fail-closed: every shape error raises ``ParityError``.

    Deliberately NOT tolerant. A manifest this tool cannot read is a FAILURE —
    the failure mode a lenient parse would produce is a gate that compares
    nothing and exits 0.
    """
    import yaml  # pyyaml is a declared dependency of the package

    if not path.is_file():
        raise ParityError(f"the pin is missing: {path} — the parity gate has "
                          "nothing to compare, so it cannot report health")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - exercised via test fixture
        raise ParityError(f"the pin is not parseable YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ParityError(f"the pin must be a mapping, got {type(data).__name__}")
    upstream = data.get("upstream")
    skills = data.get("skills")
    if not isinstance(upstream, dict) or not isinstance(skills, dict) or not skills:
        raise ParityError(
            "the pin must carry `upstream:` (a mapping) and `skills:` (a "
            f"non-empty mapping); keys present: {sorted(data)}")
    for key in ("repo", "branch", "commit"):
        if not isinstance(upstream.get(key), str) or not upstream[key].strip():
            raise ParityError(f"the pin's `upstream.{key}` must be a non-empty string")
    for name, entry in skills.items():
        # `isinstance(str)` and not merely "truthy": a list/dict value is
        # non-empty, so a truthiness test would ACCEPT it and defer the failure
        # to a malformed raw URL later (`str(['a'])` is truthy) — a late, less
        # legible failure than refusing a pin this tool cannot use.
        if not isinstance(entry, dict):
            raise ParityError(f"the pin's skills.{name} must be a mapping")
        for key in ("upstream_path", "served_path", "sha256"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ParityError(
                    f"the pin's skills.{name}.{key} must be a non-empty string")
        bytes_value = entry.get("bytes")
        if bytes_value is None or isinstance(bytes_value, (dict, list)):
            raise ParityError(f"the pin's skills.{name}.bytes is missing")
        try:
            int(bytes_value)
        except (TypeError, ValueError) as exc:
            raise ParityError(
                f"the pin's skills.{name}.bytes is not an integer: "
                f"{bytes_value!r}") from exc
    return data


def dump_manifest(manifest: dict) -> str:
    """Render the pin deterministically (sorted skills, fixed key order)."""
    lines = [
        "version: 1",
        "# GENERATED by tools/served_skills_parity.py --sync (#4643).",
        "# The authored source of the three reusable integration skills is the",
        "# MIT repo named below; this tree holds the derived MIRROR. Do not edit",
        "# a served skill or this pin by hand — edit the skill upstream, then run",
        "#   uv run python tools/served_skills_parity.py --sync",
        "# and commit the mirrored skills together with this file.",
        "#",
        "# `commit` is a real upstream commit and every sha256 is the CONTENT of",
        "# that commit — `--check-upstream` re-fetches by commit and re-hashes, so",
        "# a fabricated pin fails rather than passing quietly.",
        "#",
        "# Ownership of the artifacts NOT covered here (tortoise-onboarding, the",
        "# installer): docs/license-notes.md §7.",
        "upstream:",
        f"  repo: {manifest['upstream']['repo']}",
        f"  branch: {manifest['upstream']['branch']}",
        f"  commit: {manifest['upstream']['commit']}",
        "skills:",
    ]
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        lines.append(f"  {name}:")
        lines.append(f"    upstream_path: {entry['upstream_path']}")
        lines.append(f"    served_path: {entry['served_path']}")
        lines.append(f"    sha256: {entry['sha256']}")
        lines.append(f"    bytes: {int(entry['bytes'])}")
    return "\n".join(lines) + "\n"


# ── local: served copy vs pin ──────────────────────────────────────────────

def check_local(manifest: dict, root: Path) -> list[str]:
    """Problems where the served mirror disagrees with the pin. Never raises."""
    problems: list[str] = []
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        served = root / entry["served_path"]
        if not served.is_file():
            problems.append(
                f"{name}: the served mirror is MISSING at {entry['served_path']} "
                f"— the installer downloads this path; a 404 breaks the install "
                f"flow outright (#4643 target 1)")
            continue
        payload = served.read_bytes()
        digest = sha256_bytes(payload)
        if digest != entry["sha256"]:
            problems.append(
                f"{name}: the served mirror DRIFTED from the pin —\n"
                f"      {entry['served_path']}\n"
                f"      pin     sha256 {entry['sha256']} ({int(entry['bytes'])} bytes)\n"
                f"      mirror  sha256 {digest} ({len(payload)} bytes)\n"
                f"      the mirror is DERIVED — restore it with "
                f"`uv run python tools/served_skills_parity.py --sync`")
        elif len(payload) != int(entry["bytes"]):
            # Unreachable unless sha256 collides; kept because "the pin's byte
            # count is decorative" would otherwise be an untested claim.
            problems.append(
                f"{name}: byte count disagrees with the pin "
                f"({len(payload)} != {int(entry['bytes'])})")
    return problems


# ── upstream: the pin's honesty and currency ───────────────────────────────

def _retry(fn, what: str, attempts: int, backoff: float):
    """Run ``fn`` with retries; the LAST error is raised (fail-closed)."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # reported, then re-raised below (fail-closed)
            last = exc
            if attempt < attempts:
                _err(f"{what}: attempt {attempt}/{attempts} failed ({exc}) — retrying")
                time.sleep(backoff)
    raise ParityError(f"{what}: failed after {attempts} attempts ({last})")


def remote_head(repo: str, branch: str, attempts: int = DEFAULT_ATTEMPTS,
                backoff: float = DEFAULT_BACKOFF) -> str:
    """`git ls-remote` HEAD of ``branch`` — no token, no API rate limit.

    An EMPTY result is a failure, not "no such ref": ls-remote exits 0 with no
    output on a typo'd branch, and reading that as currency would be a
    fail-open path. `GIT_TERMINAL_PROMPT=0` keeps a private/renamed repo from
    hanging a deploy on a credential prompt.
    """
    def _once() -> str:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
                   GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        proc = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{repo}", f"refs/heads/{branch}"],
            capture_output=True, text=True, env=env, timeout=60)
        if proc.returncode != 0:
            raise ParityError(
                f"git ls-remote {repo} {branch} exited {proc.returncode}: "
                f"{proc.stderr.strip()[:300]}")
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if len(lines) != 1:
            raise ParityError(
                f"git ls-remote {repo} {branch} returned {len(lines)} refs, "
                "expected exactly 1 — refuse to read an ambiguous answer")
        return lines[0].split()[0]

    return _retry(_once, f"reading {repo}@{branch}", attempts, backoff)


def fetch_raw(repo: str, commit: str, path: str, attempts: int = DEFAULT_ATTEMPTS,
              backoff: float = DEFAULT_BACKOFF) -> bytes:
    """The bytes at an IMMUTABLE per-commit raw URL (fail-closed on 404/5xx)."""
    url = f"{RAW_HOST}/{repo}/{commit}/{path}"

    def _once() -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "tortoise-parity-gate"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 200:
                raise ParityError(f"GET {url} answered HTTP {resp.status}")
            return resp.read()

    return _retry(_once, f"fetching {url}", attempts, backoff)


def check_upstream(manifest: dict, root: Path, attempts: int = DEFAULT_ATTEMPTS,
                   backoff: float = DEFAULT_BACKOFF) -> list[str]:
    """The pin is honest AND current, and the mirror matches it.

    Three assertions, in the order that makes each failure legible:
      1. upstream `branch` HEAD == the pinned commit (CURRENCY — without this
         the whole gate is vacuous, see the module docstring);
      2. the pinned commit's bytes hash to the pinned sha256 (HONESTY — a
         hand-fabricated pin fails here instead of passing quietly);
      3. the served mirror equals those bytes (the end-to-end claim, re-stated
         so this check alone is sufficient at a deploy).
    """
    problems: list[str] = []
    upstream = manifest["upstream"]
    repo, branch, pinned = upstream["repo"], upstream["branch"], upstream["commit"]

    head = remote_head(repo, branch, attempts, backoff)
    if head != pinned:
        problems.append(
            f"the MIRROR IS STALE — {repo}@{branch} is at {head[:12]} but the "
            f"pin records {pinned[:12]}.\n"
            f"      The MIT repo is the AUTHOR: a skill changed there and the "
            f"served copy here was never re-derived.\n"
            f"      Fix: `uv run python tools/served_skills_parity.py --sync` "
            f"and commit the mirrored skills + the pin. (#4643 target 2 — this "
            f"is the drift that must not ship behind a green deploy.)")
        # Do NOT return early: report an honest/dirty pin too, in one run.
        return problems + _check_pinned_bytes(manifest, root, attempts, backoff)

    problems.extend(_check_pinned_bytes(manifest, root, attempts, backoff))
    return problems


def _check_pinned_bytes(manifest: dict, root: Path, attempts: int,
                        backoff: float) -> list[str]:
    problems: list[str] = []
    upstream = manifest["upstream"]
    repo, pinned = upstream["repo"], upstream["commit"]
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        payload = fetch_raw(repo, pinned, entry["upstream_path"], attempts, backoff)
        digest = sha256_bytes(payload)
        if digest != entry["sha256"]:
            problems.append(
                f"{name}: the PIN IS NOT HONEST — {entry['upstream_path']} at "
                f"{pinned[:12]} hashes to {digest}, the pin claims "
                f"{entry['sha256']}. Re-cut it: "
                f"`uv run python tools/served_skills_parity.py --sync`")
            continue
        served = root / entry["served_path"]
        if not served.is_file():
            problems.append(
                f"{name}: the served mirror is MISSING at {entry['served_path']}")
            continue
        if served.read_bytes() != payload:
            problems.append(
                f"{name}: the served mirror differs from {repo}@"
                f"{pinned[:12]}:{entry['upstream_path']} — re-derive with "
                f"`--sync`")
    return problems


# ── derive ─────────────────────────────────────────────────────────────────

def sync(root: Path, manifest_path: Path, commit: str | None = None,
         attempts: int = DEFAULT_ATTEMPTS, backoff: float = DEFAULT_BACKOFF,
         dry_run: bool = False) -> list[str]:
    """Re-derive the mirror from upstream. Returns the changes made."""
    manifest = load_manifest(manifest_path)
    upstream = manifest["upstream"]
    repo, branch = upstream["repo"], upstream["branch"]
    target = commit or remote_head(repo, branch, attempts, backoff)
    changed: list[str] = []

    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        payload = fetch_raw(repo, target, entry["upstream_path"], attempts, backoff)
        digest = sha256_bytes(payload)
        served = root / entry["served_path"]
        if not served.is_file() or served.read_bytes() != payload:
            changed.append(f"{entry['served_path']} ({len(payload)} bytes, {digest[:12]})")
            if not dry_run:
                served.parent.mkdir(parents=True, exist_ok=True)
                served.write_bytes(payload)
        entry["sha256"] = digest
        entry["bytes"] = len(payload)

    manifest["upstream"]["commit"] = target
    if not dry_run:
        manifest_path.write_text(dump_manifest(manifest), encoding="utf-8")
    return changed


# ── report + CLI ───────────────────────────────────────────────────────────

def _report(path: str, lines: list[str]) -> None:
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate/derive the served-skill mirror (#4643).",
        # ⛔ allow_abbrev=False: with abbreviations on, `--check` is AMBIGUOUS
        # (`--check-local` / `--check-upstream`) and argparse exits 2 — a gate
        # whose documented invocation refuses to run is a gate nobody runs.
        allow_abbrev=False)
    parser.add_argument("--root", default=str(REPO_ROOT),
                        help="repo root (default: this checkout)")
    parser.add_argument("--manifest", default=None,
                        help=f"pin path (default: <root>/{MANIFEST_REL})")
    parser.add_argument("--check", action="store_true",
                        help="both arms below (offline + network)")
    parser.add_argument("--check-local", action="store_true",
                        help="offline: the served mirror equals the pin")
    parser.add_argument("--check-upstream", action="store_true",
                        help="network: the pin is honest and current")
    parser.add_argument("--sync", action="store_true",
                        help="re-derive the mirror from upstream")
    parser.add_argument("--commit", default=None,
                        help="--sync from this upstream commit (default: branch HEAD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="--sync: report the changes without writing")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF)
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    parser.add_argument("--report", default=None,
                        help="write a markdown report (for the auto-file monitor)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    manifest_path = Path(args.manifest) if args.manifest else root / MANIFEST_REL

    # Bare `--check` is both arms; a bare invocation (no arm named) is ALSO
    # both, because a caller who typed the script name and nothing else asked
    # for the whole contract, and defaulting to the offline half would report
    # green on a gate that never looked upstream.
    explicit = args.check or args.check_local or args.check_upstream
    do_local = args.check or args.check_local or not (explicit or args.sync)
    do_upstream = args.check or args.check_upstream or not (explicit or args.sync)

    problems: list[str] = []
    try:
        manifest = load_manifest(manifest_path)
        if args.sync:
            changed = sync(root, manifest_path, args.commit, args.attempts,
                           args.backoff, args.dry_run)
            verb = "would re-derive" if args.dry_run else "re-derived"
            print(f"{verb} {len(changed)} served skill(s) from "
                  f"{manifest['upstream']['repo']}@{manifest['upstream']['commit'][:12]}")
            for line in changed:
                print(f"  ~ {line}")
            return 0
        if do_local:
            problems.extend(check_local(manifest, root))
        if do_upstream:
            problems.extend(check_upstream(manifest, root, args.attempts, args.backoff))
    except ParityError as exc:
        problems.append(f"FAIL-CLOSED: {exc}")

    if args.json:
        import json
        print(json.dumps({
            "ok": not problems,
            "checked_local": bool(do_local),
            "checked_upstream": bool(do_upstream),
            "problems": problems,
        }, indent=2))

    if problems:
        _err(f"served-skills parity FAILED with {len(problems)} problem(s):")
        for problem in problems:
            _err(f"  - {problem}")
        if args.report:
            _report(args.report, ["## served-skills parity failed", ""] + [
                f"- {p}" for p in problems])
        return 1

    scope = " + ".join(filter(None, [
        "served↔pin" if do_local else "",
        "pin↔upstream" if do_upstream else ""]))
    print(f"served-skills parity OK ({scope}) for "
          f"{len(manifest['skills'])} skill(s) from "
          f"{manifest['upstream']['repo']}@{manifest['upstream']['commit'][:12]}")
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        print(f"  ✓ {name}  {entry['sha256'][:12]}  {entry['served_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
