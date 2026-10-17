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
  bytes really hash to the pin, and the bytes at upstream `main` HEAD are still
  those bytes. Needs the network.

  Two runners execute it: the `deploy-dashboard` job in `deploy-pages.yml`
  (before the build stages `public/`, so a stale mirror cannot deploy) and the
  daily `.github/workflows/skills-parity.yml` (`schedule` +
  `workflow_dispatch`), so drift is caught without waiting for a deploy.

⛔ `--check-upstream` is not decoration. A pin-only gate is VACUOUS: pin any
old commit and the "byte-identical" claim becomes true of history. The gate is
only meaningful because the upstream-HEAD bytes are compared alongside it.

⛔ THE PIN'S SCOPE IS PART OF THE CLAIM. Both byte arms iterate the pin's own
keys, so on their own a NEW upstream `skills/<name>/SKILL.md` would be neither
mirrored nor reported — the mirror would silently serve an incomplete set of a
repo that had grown. `--check-upstream` therefore also enumerates upstream
`skills/` (at the pinned commit AND at `branch` HEAD) and requires set equality
with the upstream directories the pin BINDS (the parent of each entry's
`upstream_path` — the served name need not match it) plus the pin's declared
`out_of_scope:` entries. An addition that is not ours to serve is recorded there
deliberately, not left invisible — and the declaration is checked at HEAD, so a
skill that appeared upstream after the pin can be declared without a pin re-cut.

⛔ DRIFT AND "COULD NOT VERIFY" ARE DIFFERENT ANSWERS. Both exit non-zero
(fail-closed), but they are reported separately: a network error, an
unreadable listing or a rate-limited API is NOT evidence that the mirror
drifted, and calling it drift would auto-file a false "the mirror drifted"
alarm — or, worse, teach people to re-run the gate. `--json` carries `problems`
(drift) and `cannot_verify` (a side was unreadable) as SEPARATE fields.

WHY CURRENCY COMPARES BYTES, NOT THE COMMIT POINTER

The currency arm compares the PINNED sha256 against the bytes at upstream HEAD.
It deliberately does NOT require `HEAD == pinned`: the MIT repo also takes
commits that change no skill we serve (a README edit, CI config, an edit to a
skill already recorded under `out_of_scope:`). Keying on the
pointer reds the dashboard deploy and auto-files a "the mirror drifted" P2 for a
commit that changed none of the three files — a false alarm on the one signal
humans are asked to trust, and a wrong diagnosis ("a skill changed there") for a
cause that did not happen. A skill edit still reds (the bytes at HEAD differ from
the pin); an unrelated commit does not. ⛔ An upstream ADDITION is a different
event and IS red, by the scope arm above — this paragraph bounds what the
CURRENCY arm reports, never what the gate does.

FAIL-CLOSED, EVERY TIME

Missing manifest, missing skill entry, missing/unreadable served copy, a
byte count that disagrees with the pin, a network error after retries — each is
a FAILURE with a non-zero exit, never a skip. A parity gate that passes when it
could not read a side reports health it never observed; that is the exact
defect class this exists to close (#3673's "0 fail-open paths", #4643 targets
1 and 2).

OUTPUT CONTRACT

``--json`` writes ONE JSON document to stdout and nothing else — every human
line goes to stderr on that path, so `json.loads(stdout)` works on a GREEN run
as well as a red one (the direction a monitor actually reads).

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

``--sync --commit <sha>`` is for pinning a specific upstream commit. It is not
silently equivalent to HEAD: the currency arm compares the pinned bytes against
upstream HEAD, so a sync to a commit whose skill bytes differ from HEAD prints
that consequence and the resulting pin reds `--check-upstream` until upstream
moves back (or `--sync` is re-run at HEAD). See ``sync()``.
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter before the work, not a traceback.
# ⛔ POSITION IS THE RULE: `import sys` + this guard are the FIRST statements
# after the docstring and `__future__`, BEFORE every other import — a
# module-level 3.12-only import would raise before a later guard could run
# (`tests/test_entry_point_python_guard.py` enforces exactly this shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/served_skills_parity.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/served_skills_parity.py`"
    )

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_REL = Path("config") / "served-skills-upstream.yml"
RAW_HOST = "https://raw.githubusercontent.com"
# A directory listing has no raw form, so the one API call in this tool lives
# here. Unauthenticated it is 60 requests/hour per IP — enough for a daily run
# and a deploy, and both runners pass a token anyway (5000/hour).
API_HOST = "https://api.github.com"
# The upstream repo's skill directory; the pin's `upstream_path` values live
# beneath it. Enumerated so an ADDITION upstream is visible (see the docstring).
UPSTREAM_SKILLS_DIR = "skills"

# A network blip must not red a deploy for a reason unrelated to the change,
# and a fail-closed gate that is FLAKY teaches people to re-run it. Retry, then
# fail — the same shape as deploy-pages.yml's bindings preflight.
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = 3.0


class ParityError(RuntimeError):
    """A fail-closed check failure (never raised for "nothing to check")."""


class PinDefect(ParityError):
    """The PIN itself is unusable — a DETERMINISTIC defect, not a read failure.

    Reported with the drift (`problems`), never as "could not verify": the file
    is missing or malformed in THIS tree, so no re-run and no network condition
    can change the answer. Filing it under "re-run once the network/credential is
    sane" was advice that can never work, and it hid a pin defect behind a
    transient-looking heading (review finding, P2).
    """


def _err(msg: str) -> None:
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else msg,
          file=sys.stderr)


def _note(msg: str) -> None:
    """A non-fatal observation. stderr only — never stdout (the --json contract)."""
    print(f"::notice::{msg}" if os.environ.get("GITHUB_ACTIONS") else msg,
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
    out_of_scope = data.get("out_of_scope")
    if out_of_scope is not None:
        # Optional, and deliberately EXPLICIT: the upstream arm requires set
        # equality between upstream `skills/` and the upstream directories the
        # pin's `upstream_path`s bind, so the only
        # way to say "that upstream skill is not ours to serve" is to write it
        # down here. A malformed entry must fail closed — a silently ignored
        # name would make the declaration a no-op that reads as an exclusion.
        if not isinstance(out_of_scope, list):
            raise ParityError("the pin's `out_of_scope:` must be a list of "
                              f"skill names, got {type(out_of_scope).__name__}")
        for name in out_of_scope:
            if not isinstance(name, str) or not name.strip():
                raise ParityError(
                    f"the pin's `out_of_scope:` entries must be non-empty "
                    f"strings, got {name!r}")
            if name in skills:
                raise ParityError(
                    f"the pin lists {name!r} as BOTH pinned and out_of_scope — "
                    "one skill cannot be in and out of the served set")
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
        "# a fabricated pin fails rather than passing quietly. The same sha256 is",
        "# re-computed over the bytes at upstream HEAD, so the pin is checked",
        "# against what the author repo has NOW; an upstream commit that leaves",
        "# these three files byte-identical is not a drift.",
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
    out_of_scope = manifest.get("out_of_scope") or []
    if out_of_scope:
        # Only emitted when non-empty, so a pin that declares nothing keeps the
        # shape it had (and `test_4643_the_pin_is_exactly_what_the_generator_
        # writes` stays a byte-identity assertion rather than a reformat).
        lines.append("# Upstream skills/ directories that are deliberately NOT served here.")
        lines.append("# The upstream arm requires set equality against these, so an")
        lines.append("# upstream addition must be pinned or recorded here.")
        lines.append("out_of_scope:")
        for name in sorted(out_of_scope):
            lines.append(f"  - {name}")
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
            # REACHABLE, and not by collision: `bytes` is independent pin
            # metadata, so a hand-edited `bytes:` beside a correct `sha256:`
            # lands here (the payload matches the digest, the count does not).
            # Kept — and covered by
            # `test_4643_the_pins_byte_count_is_checked_not_decorative` — because
            # "the pin's byte count is decorative" would otherwise be an
            # untested claim.
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


def list_upstream_skills(repo: str, commit: str,
                         attempts: int = DEFAULT_ATTEMPTS,
                         backoff: float = DEFAULT_BACKOFF) -> list[str]:
    """The skill DIRECTORY names under upstream `skills/` at ``commit``.

    The one thing the byte arms cannot see: they iterate the PIN's keys, so a
    new upstream `skills/<name>/SKILL.md` would never be fetched, never be
    mirrored and never be reported — the pin would quietly describe a smaller
    set than the repo it claims to mirror. Enumerated here so the pin's SCOPE
    is a checked claim, not an assumption.

    Fail-closed: a non-200, a non-list payload, a malformed entry or an EMPTY
    listing raises ``ParityError`` (which the CLI reports as *cannot verify*,
    never as agreement). An empty answer is refused for the same reason
    `remote_head` refuses one: GitHub serves `[]` for a moved/renamed path, and
    reading that as "upstream has no skills" would silently satisfy a
    set-equality test.

    A `GITHUB_TOKEN`/`GH_TOKEN` in the environment is used when present (the
    runners pass one): unauthenticated the API allows 60 requests/hour per IP.
    """
    url = f"{API_HOST}/repos/{repo}/contents/{UPSTREAM_SKILLS_DIR}?ref={commit}"

    def _once() -> list[str]:
        headers = {"User-Agent": "tortoise-parity-gate",
                   "Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 200:
                raise ParityError(f"GET {url} answered HTTP {resp.status}")
            payload = json.loads(resp.read())
        if not isinstance(payload, list):
            raise ParityError(
                f"GET {url} did not return a directory listing "
                f"(got {type(payload).__name__}) — refuse to guess the scope")
        names: list[str] = []
        for entry in payload:
            if not isinstance(entry, dict):
                raise ParityError(f"GET {url} returned a malformed entry: {entry!r}")
            if entry.get("type") != "dir":
                # A file under `skills/` (the licence, say) is not a skill.
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ParityError(
                    f"GET {url} returned a directory with no usable name: {entry!r}")
            names.append(name)
        if not names:
            raise ParityError(
                f"{repo}@{commit[:12]} listed NO skill directories under "
                f"{UPSTREAM_SKILLS_DIR}/ — refuse to read an empty listing as "
                "agreement (a moved path answers `[]`, not an error)")
        return sorted(names)

    return _retry(_once, f"listing {repo}/{UPSTREAM_SKILLS_DIR}@{commit[:12]}",
                  attempts, backoff)


def _upstream_dirs(manifest: dict) -> dict[str, list[str]]:
    """Upstream DIRECTORY name → the pin keys that bind to it.

    The pin carries the served name and the upstream binding as SEPARATE fields
    (`skills.<key>` vs `<key>.upstream_path`), and nothing requires them to be
    equal — a served name may be shortened. Comparing the pin's KEYS against the
    upstream listing therefore reds a perfectly honest pin whose served name
    differs from the directory, and the remedy it prints (`--sync`, which re-cuts
    the same keys) cannot clear it: the binding is `upstream_path`.
    """
    dirs: dict[str, list[str]] = {}
    for key, entry in manifest["skills"].items():
        parts = PurePosixPath(str(entry["upstream_path"])).parts
        # `skills/<dir>/SKILL.md` → `<dir>`; a bare `<dir>/SKILL.md` → `<dir>`.
        name = parts[-2] if len(parts) >= 2 else parts[0]
        dirs.setdefault(name, []).append(key)
    return dirs


def _check_scope(manifest: dict, head: str, attempts: int,
                 backoff: float) -> list[str]:
    """The pin describes the RIGHT SET of upstream skills — at both commits.

    Two comparisons, because they fail differently:

    * at the PINNED commit — the pin must not name a skill its own commit does
      not contain (that is a dishonest pin, and the byte arm would report it as
      a 404, i.e. as "cannot verify" rather than as the pin defect it is);
    * at `branch` HEAD — an upstream ADDITION is the drift the byte arms cannot
      see, and a REMOVAL is a pinned skill that no longer exists upstream.

    Each directory may be excluded explicitly via the pin's `out_of_scope:`
    list, so "that one is not ours to serve" is a recorded decision rather than
    silence. The declaration is validated at HEAD ONLY: the only case that needs
    it is a skill added upstream AFTER the pin, which is by definition absent
    from the pinned commit, so validating it against the pin rejected the exact
    declaration the "upstream added" message asks for (see the ⛔ below).
    """
    upstream = manifest["upstream"]
    repo, branch, pinned = upstream["repo"], upstream["branch"], upstream["commit"]
    pinned_dirs = _upstream_dirs(manifest)
    declared_out = set(manifest.get("out_of_scope") or [])
    expected = set(pinned_dirs) | declared_out
    problems: list[str] = []

    at_pin = set(list_upstream_skills(repo, pinned, attempts, backoff))
    for name in sorted(set(pinned_dirs) - at_pin):
        pinned_as = ", ".join(sorted(pinned_dirs[name]))
        problems.append(
            f"the pin NAMES a skill its own commit does not contain: "
            f"'{UPSTREAM_SKILLS_DIR}/{name}' (pinned as {pinned_as}) is absent "
            f"from {repo}@{pinned[:12]} — the pin is not honest about its scope. "
            f"Re-cut it: `uv run python tools/served_skills_parity.py --sync`")
    # ⛔ `out_of_scope:` is deliberately NOT validated against the PINNED commit.
    # The only case that needs the declaration is an upstream skill added AFTER
    # the pin — so a name it must carry is BY DEFINITION absent from the pinned
    # commit. Rejecting `declared_out - at_pin` told the operator to "drop the
    # stale declaration", i.e. to undo exactly what the "upstream added" message
    # asks for; the two messages prescribed each other and the loop had no exit
    # (review finding, P2). It is validated at HEAD below, where the drift is.
    if problems:
        # The pin misdescribes its OWN commit. Comparing that against HEAD too
        # would add a second, noisier finding for the same underlying defect.
        return problems

    at_head = at_pin if head == pinned else set(
        list_upstream_skills(repo, head, attempts, backoff))
    # A directory in neither the pin nor `out_of_scope:` is red EITHER WAY, but
    # the two cases must not be called the same thing: one really was added, the
    # other has been sitting upstream since before the pin and was simply never
    # declared. Reporting the second as "upstream added" sends the operator
    # looking for an upstream commit that does not exist (review finding, P2).
    undeclared = at_head - expected
    since_pin = {name for name in undeclared if name in at_pin}
    added = undeclared - since_pin
    for name in sorted(since_pin):
        problems.append(
            f"upstream has '{UPSTREAM_SKILLS_DIR}/{name}' at {repo}@{branch} "
            f"HEAD ({head[:12]}) AND at the pinned commit, but the pin neither "
            f"serves nor declares it — this is not a new addition, it is an "
            f"undeclared directory. Pin it (add it to the pin's `skills:` with "
            f"its `upstream_path`/`served_path`) or record `{name}` under "
            f"`out_of_scope:` if it is deliberately not served.")
    for name in sorted(added):
        problems.append(
            f"upstream added '{UPSTREAM_SKILLS_DIR}/{name}' at {repo}@{branch} "
            f"HEAD ({head[:12]}) — pin it or record it as out of scope.\n"
            f"      The pin's byte arms iterate the pin's OWN keys, so this "
            f"skill is invisible to them: it is not mirrored here, and nothing "
            f"else reports it. Add it to the pin's `skills:` (with its "
            f"`upstream_path`/`served_path`) and run `--sync`, or list "
            f"`{name}` under `out_of_scope:` if it is deliberately not served — "
            f"the declaration is checked at HEAD, so THAT path needs no pin "
            f"re-cut.")
    for name in sorted(set(pinned_dirs) - at_head):
        problems.append(
            f"upstream removed '{UPSTREAM_SKILLS_DIR}/{name}' at {repo}@{branch} "
            f"HEAD ({head[:12]}) — the pinned skill no longer exists upstream, so "
            f"the mirror is STALE. Re-derive with "
            f"`uv run python tools/served_skills_parity.py --sync`, or drop it "
            f"from the pin if it is no longer served.")
    for name in sorted(declared_out - at_head):
        # The declaration is checked at HEAD too: a skill somebody once excluded
        # and that upstream has since deleted must be dropped from the pin, or
        # the `out_of_scope:` list slowly widens the expected set with names
        # nothing can ever match.
        problems.append(
            f"the pin's `out_of_scope:` names '{UPSTREAM_SKILLS_DIR}/{name}', "
            f"which {repo}@{branch} HEAD ({head[:12]}) does not contain — drop "
            "the stale declaration")
    return problems


def check_upstream(manifest: dict, root: Path, attempts: int = DEFAULT_ATTEMPTS,
                   backoff: float = DEFAULT_BACKOFF,
                   problems: list[str] | None = None,
                   cannot_verify: list[str] | None = None) -> list[str]:
    """The pin is honest AND current AND the right scope, and the mirror matches it.

    Four assertions, in the order that makes each failure legible:
      0. upstream `skills/` contains EXACTLY the upstream directories the pin's
         `upstream_path`s bind (plus the pin's
         declared `out_of_scope:`) — at the pinned commit and at `branch` HEAD,
         so an upstream ADDITION or REMOVAL is reported rather than invisible
         (both byte assertions iterate the pin's own keys, see `_check_scope`);
      1. the pinned commit's bytes hash to the pinned sha256 (HONESTY — a
         hand-fabricated pin fails here instead of passing quietly);
      2. the bytes at upstream `branch` HEAD hash to the SAME sha256
         (CURRENCY — without this the whole gate is vacuous, see the module
         docstring). Compared by BYTES, not by the commit pointer: an upstream
         commit that cannot change what we serve must not red this;
      3. the served mirror equals those bytes (the end-to-end claim, re-stated
         so this check alone is sufficient at a deploy).

    A side that cannot be READ (network, rate limit, 5xx) is recorded in
    ``cannot_verify`` — the CLI reports that as *cannot verify*, distinctly from
    the drift list. ⛔ It is NOT accumulated in a local list dropped on the way
    out: a later read failure used to raise out of this function, so a drift
    already OBSERVED was discarded and the run reported "could not verify — this
    is NOT a drift" with an empty drift section in the uploaded report (review
    finding, P2). Callers that pass no lists get this function's own findings.
    """
    problems = [] if problems is None else problems
    cannot_verify = [] if cannot_verify is None else cannot_verify
    upstream = manifest["upstream"]
    repo, branch, pinned = upstream["repo"], upstream["branch"], upstream["commit"]

    try:
        head = remote_head(repo, branch, attempts, backoff)
    except ParityError as exc:
        cannot_verify.append(
            f"upstream HEAD of {repo}@{branch}: {exc} — scope, honesty and "
            "currency are all decided against HEAD, so this run proves nothing "
            "about the bytes")
        return problems
    # SCOPE FIRST, and on a scope problem STOP: a skill the pin names but
    # upstream does not have would make the byte arms fetch a 404, which the CLI
    # reports as "cannot verify" — misclassifying a real, actionable drift as a
    # network problem. Report the scope defect instead; it is the more
    # fundamental answer, and it is what the operator must act on first.
    try:
        scope = _check_scope(manifest, head, attempts, backoff)
    except ParityError as exc:
        # An unreadable LISTING is not a scope defect — keep it in the other
        # bucket and let the byte arms report whatever they CAN read.
        cannot_verify.append(
            f"the upstream `{UPSTREAM_SKILLS_DIR}/` listing: {exc} — the pin's "
            "scope is unproven for this run")
        scope = []
    problems.extend(scope)
    if scope:
        return problems
    # Honesty + mirror next: a pin that is not what its own commit contains is
    # the more fundamental failure, and it is reported even when upstream moved.
    problems.extend(_check_pinned_bytes(manifest, root, attempts, backoff,
                                        cannot_verify))
    if head == pinned:
        return problems

    # Currency. The pointer moved — but only a byte difference is a drift.
    drifted = 0
    unread = 0
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        try:
            payload = fetch_raw(repo, head, entry["upstream_path"], attempts,
                                backoff)
        except ParityError as exc:
            unread += 1
            cannot_verify.append(
                f"{name}: {exc} — the bytes at {repo}@{branch} HEAD "
                f"({head[:12]}) could not be read, so currency is unproven for "
                "this skill")
            continue
        digest = sha256_bytes(payload)
        if digest == entry["sha256"]:
            continue
        drifted += 1
        problems.append(
            f"{name}: the MIRROR IS STALE — the bytes of {entry['upstream_path']} "
            f"at {repo}@{branch} HEAD ({head[:12]}) hash to {digest}; the pin "
            f"records {entry['sha256']} (pinned commit {pinned[:12]}).\n"
            f"      The MIT repo is the AUTHOR: this skill changed there and the "
            f"served copy here was never re-derived.\n"
            f"      Fix: `uv run python tools/served_skills_parity.py --sync` "
            f"and commit the mirrored skills + the pin. (#4643 target 2 — this "
            f"is the drift that must not ship behind a green deploy.)")
    if not drifted and not unread:
        # Green, but say WHY it is green — otherwise a reader sees the pin's
        # commit differ from HEAD and has to re-derive that it is harmless.
        _note(f"upstream {repo}@{branch} moved to {head[:12]} (pin: pinned "
              f"{pinned[:12]}) but the pinned skill bytes are unchanged — "
              f"nothing to re-derive; `--sync` would only bump the pointer.")
    return problems


def _check_pinned_bytes(manifest: dict, root: Path, attempts: int,
                        backoff: float,
                        cannot_verify: list[str] | None = None) -> list[str]:
    problems: list[str] = []
    cannot_verify = [] if cannot_verify is None else cannot_verify
    upstream = manifest["upstream"]
    repo, pinned = upstream["repo"], upstream["commit"]
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        try:
            payload = fetch_raw(repo, pinned, entry["upstream_path"], attempts,
                                backoff)
        except ParityError as exc:
            # One unreadable file must not hide the others' verdicts.
            cannot_verify.append(
                f"{name}: {exc} — the PINNED bytes could not be read, so the "
                "honesty, byte-count and mirror arms are unproven for this "
                "skill")
            continue
        digest = sha256_bytes(payload)
        if digest != entry["sha256"]:
            problems.append(
                f"{name}: the PIN IS NOT HONEST — {entry['upstream_path']} at "
                f"{pinned[:12]} hashes to {digest}, the pin claims "
                f"{entry['sha256']}. Re-cut it: "
                f"`uv run python tools/served_skills_parity.py --sync`")
            continue
        # sha256 equality implies byte equality against the CONTENT, but the
        # pin's `bytes:` field is independent metadata: a hand-edited count
        # beside a correct digest is invisible unless it is compared here.
        if len(payload) != int(entry["bytes"]):
            problems.append(
                f"{name}: the pin's byte count is WRONG — {entry['upstream_path']} "
                f"at {pinned[:12]} is {len(payload)} bytes, the pin records "
                f"{int(entry['bytes'])}. Re-cut it: "
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

class SyncResult(NamedTuple):
    """What ``sync`` did, and where it pinned — the OPERATOR-facing facts.

    ``target`` is returned rather than left in the manifest: ``sync`` re-loads
    the pin into its own dict, so a caller printing the outer manifest reports
    the PREVIOUS commit — the one fact `--sync` exists to report.
    """

    changed: list[str]
    target: str
    previous: str
    at_head: bool | None  # None = could not be read (the note is still printed)


def sync(root: Path, manifest_path: Path, commit: str | None = None,
         attempts: int = DEFAULT_ATTEMPTS, backoff: float = DEFAULT_BACKOFF,
         dry_run: bool = False) -> SyncResult:
    """Re-derive the mirror from upstream. Returns what changed and the target.

    ``commit`` pins a specific upstream commit instead of HEAD. That is a
    deliberate capability, and it has a stated consequence: the currency arm of
    ``check_upstream`` compares the pinned bytes against upstream HEAD, so a
    sync to a commit whose skill bytes differ from HEAD yields a pin that reds
    until upstream matches it. ``at_head`` reports whether that applies, so the
    caller can say so instead of leaving the operator to discover it from a red
    gate (see the ``--commit`` help).
    """
    manifest = load_manifest(manifest_path)
    upstream = manifest["upstream"]
    repo, branch = upstream["repo"], upstream["branch"]
    previous = upstream["commit"]
    target = commit or remote_head(repo, branch, attempts, backoff)
    at_head: bool | None = True
    if commit is not None:
        try:
            at_head = commit == remote_head(repo, branch, attempts, backoff)
        except ParityError:
            # Unreadable HEAD must not turn an explicit --commit into a failure:
            # the pin is written either way, and the note below still applies.
            at_head = None
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
    return SyncResult(changed=changed, target=target, previous=previous,
                      at_head=at_head)


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
                        help="--sync from this upstream commit (default: branch "
                             "HEAD). The currency arm compares the pinned bytes "
                             "against upstream HEAD, so a pin that is not HEAD "
                             "reds `--check-upstream` until the bytes at HEAD "
                             "equal it — use it to pin a commit whose skill "
                             "bytes match HEAD, not to silence a drift.")
    parser.add_argument("--dry-run", action="store_true",
                        help="--sync: report the changes without writing")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF)
    parser.add_argument("--json", action="store_true",
                        help="machine-readable result: ONE JSON document on "
                             "stdout, every human line on stderr")
    parser.add_argument("--report", default=None,
                        help="write a markdown report (for the auto-file monitor)")
    args = parser.parse_args(argv)

    # ⛔ `--sync --check` used to exit 0 having run NO gate: the `if args.sync:`
    # branch returned before `do_local`/`do_upstream` were consulted, so a
    # caller who asked for both got a green exit and no check — a fail-open path
    # in a tool whose whole purpose is fail-closed. Derive and check are
    # different commands; refuse the combination instead of silently picking one.
    if args.sync:
        requested = [flag for flag, on in (("--check", args.check),
                                           ("--check-local", args.check_local),
                                           ("--check-upstream", args.check_upstream))
                     if on]
        if requested:
            parser.error(
                f"--sync cannot be combined with {' / '.join(requested)}: --sync "
                "RE-DERIVES the mirror and writes the pin, so a check in the same "
                "invocation would grade bytes it just overwrote. Run --sync, "
                "commit, then --check.")
        # Output-mode flags are refused too, for a DIFFERENT reason: this branch
        # returns before the document/report is written, so accepting them prints
        # human lines (or nothing) and exits 0 — `json.loads(stdout)` raises, and
        # a monitor asking for a report is handed nothing. A flag accepted and
        # silently ignored is worse than a refusal (review finding, P2).
        ignored = [flag for flag, on in (("--json", args.json),
                                         ("--report", args.report is not None))
                   if on]
        if ignored:
            parser.error(
                f"--sync cannot be combined with {' / '.join(ignored)}: this "
                "branch returns BEFORE the document/report is written, so the "
                "flag would be accepted, print nothing machine-readable, and "
                "exit 0. Run --sync, then the check that writes the report.")

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
    cannot_verify: list[str] = []
    try:
        try:
            manifest = load_manifest(manifest_path)
        except ParityError as exc:
            # ⛔ A pin this tool cannot read is a DEFECT IN THIS TREE, not a read
            # failure: it is deterministic, so "re-run once the network/credential
            # is sane" can never clear it, and the transient-looking heading hid
            # an actionable defect (review finding, P2).
            raise PinDefect(
                f"the pin is UNUSABLE — {manifest_path}: {exc}. This is a defect "
                "in this tree (fix the pin, or re-derive it with `--sync`), not "
                "a network problem: a re-run cannot change the answer") from exc
        if args.sync:
            result = sync(root, manifest_path, args.commit, args.attempts,
                          args.backoff, args.dry_run)
            verb = "would re-derive" if args.dry_run else "re-derived"
            # The TARGET, not the pre-sync pin: `sync` re-loads the manifest into
            # its own dict, so the outer copy still holds the old commit — which
            # is the one fact an operator runs --sync to learn.
            print(f"{verb} {len(result.changed)} served skill(s) from "
                  f"{manifest['upstream']['repo']}@{result.target[:12]}")
            if result.previous != result.target:
                print(f"  pin moved {result.previous[:12]} -> {result.target[:12]}")
            for line in result.changed:
                print(f"  ~ {line}")
            if result.at_head is False:
                _note(
                    f"{result.target[:12]} is NOT upstream HEAD: the currency arm "
                    "compares the pinned bytes against upstream HEAD, so "
                    "`--check-upstream` reds until the bytes at HEAD equal this "
                    "pin (or upstream rewinds).")
            elif result.at_head is None:
                _note("upstream HEAD could not be read, so whether this commit is "
                      "HEAD is unverified — the currency arm will decide.")
            return 0
        if do_local:
            problems.extend(check_local(manifest, root))
        if do_upstream:
            # ACCUMULATED INTO, not returned-and-replaced: a later fetch that
            # exhausts its retries used to raise out of `check_upstream`, so a
            # drift already observed was thrown away and the uploaded report said
            # `### Drift (0)` on a run that HAD found one (review finding, P2).
            check_upstream(manifest, root, args.attempts, args.backoff,
                           problems, cannot_verify)
    except PinDefect as exc:
        problems.append(str(exc))
    except ParityError as exc:
        # ⛔ NOT a drift. A network error, a 5xx, a rate-limited listing or an
        # unreadable pin is *evidence of nothing* about the bytes; reporting it
        # in `problems` makes a monitor auto-file "the served mirror drifted"
        # for a cause that did not happen, and trains humans to re-run the gate.
        # It still FAILS (fail-closed) — silence would report health it never
        # observed.
        cannot_verify.append(str(exc))

    ok = not problems and not cannot_verify
    if args.json:
        print(json.dumps({
            "ok": ok,
            "checked_local": bool(do_local),
            "checked_upstream": bool(do_upstream),
            # Two fields on purpose: `problems` is DRIFT (bytes disagree),
            # `cannot_verify` is "a side was unreadable". A consumer that treats
            # them alike re-introduces the false-alarm class this split removes.
            "problems": problems,
            "cannot_verify": cannot_verify,
        }, indent=2))

    if problems:
        _err(f"served-skills parity FAILED with {len(problems)} problem(s) — DRIFT:")
        for problem in problems:
            _err(f"  - {problem}")
    if cannot_verify:
        _err("served-skills parity could NOT VERIFY — this is NOT a drift; a side "
             f"could not be read ({len(cannot_verify)}):")
        for problem in cannot_verify:
            _err(f"  ! {problem}")
        _err("  the gate fails closed: an unreadable side is never reported as "
             "agreement. Re-run once the network/credential is sane.")
    if not ok and args.report:
        _report(args.report,
                ["## served-skills parity failed", ""]
                + [f"### Drift ({len(problems)})", ""]
                + [f"- {p}" for p in problems]
                + [f"### Could not verify ({len(cannot_verify)}) — not a drift", ""]
                + [f"- {p}" for p in cannot_verify])
    if not ok:
        return 1

    scope = " + ".join(filter(None, [
        "served↔pin" if do_local else "",
        "pin↔upstream" if do_upstream else ""]))
    # `--json` OWNS stdout: the human summary goes to stderr on that path so
    # `json.loads(stdout)` works on the GREEN run too — the direction a monitor
    # actually reads, and the one that used to break (JSON, then summary lines,
    # on the same stream).
    out = sys.stderr if args.json else sys.stdout
    print(f"served-skills parity OK ({scope}) for "
          f"{len(manifest['skills'])} skill(s) from "
          f"{manifest['upstream']['repo']}@{manifest['upstream']['commit'][:12]}",
          file=out)
    for name in sorted(manifest["skills"]):
        entry = manifest["skills"][name]
        print(f"  ✓ {name}  {entry['sha256'][:12]}  {entry['served_path']}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
