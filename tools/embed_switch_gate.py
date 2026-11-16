#!/usr/bin/env python3
"""Pre-merge gate: every shipped deployment channel STATES both embedding switches (#12926).

**What this refuses, and why a pre-merge check is the right place.** ``#11445`` decided the
guarantee for "what earns an embedding" is the **shipped configuration**, not a code default —
and left the two code defaults alone on purpose. ``#12926`` measured the consequence: nothing
refused a shipped config that omits them, so the guarantee was a convention. The two switches
read **opposite** ways when unset (``tortoise/embed_gate.py`` is OFF unless enabled,
``tortoise/value_gate.py`` is ON unless disabled — both record this and that changing either is
a reopen of ``#11445``), so "unset" is not neutral: it silently turns half the rule off, with
nothing crashing. Per the two-tier configuration rule, that middle case (optional to run, unsafe
to default) is enforced **earlier** than runtime — at the earliest gate that can see the
artefact, which is the pull request — never by crashing a service over a storage-hygiene knob.

**Resolve, do not read.** A text scan of a manifest answers a different question from "what will
the deployed process see": compose models anchors and merge keys, flow mappings, ``env_file:``
expansion, ``${…}`` interpolation and per-service scoping; ``fly config show`` parses the Fly
manifest. So each channel is read through **its own grammar owner** and the verdict is taken
from the resolved model:

| channel | resolver |
|---|---|
| compose (per **service** — one service arming does not arm its neighbour) | ``docker compose -f <file> --env-file /dev/null config --format json`` |
| Fly manifest | ``fly config show --local --config <file>`` when ``flyctl`` is available, else ``tomllib`` on the manifest's ``[env]`` table |
| image build file (``Dockerfile*``) | a strict ``ENV`` reader (no external resolver exists) |
| entrypoint script (``entrypoint*.sh``) | a strict ``export``/assignment reader (no external resolver exists) |

⚠️ **``fly config show`` is not credential-free, measured** (flyctl v0.5.10): ``fly config show
--local --config fly.toml`` exits 1 with *"no access token available"* even for a purely local
parse; a dummy token gets the JSON. A required check therefore cannot depend on it, so the Fly
arm uses it **when it is present** and otherwise reads the same ``[env]`` table with
``tomllib`` — the TOML grammar owner — and **says which resolver answered**. Secrets can shadow
``[env]`` on Fly and are invisible to every offline reader, including ``fly config show``: a
declared bound of this gate, not something it pretends to measure.

**Discovery, never a list.** The channel FILES are discovered from the tree (globs below), so a
second compose file or a second Fly manifest is walked the hour it lands. A discovered file this
gate cannot resolve is **not skipped**: it is a FAILURE that names the file and the reason —
"could not read a channel" must never read as "no problem". A compose file outside the base
names (an override/patch, whose merge order this gate does not model — the ``#12070`` item 8
false-PASS direction) is refused by name rather than half-read.

**Three verdicts, and the wording matters.** A channel is ``STATED``, ``DOES NOT STATE <switch>``
(the file was read fine and simply omits it) or ``COULD NOT READ`` (the file/tool genuinely could
not be read). ``#12549`` defect 1 was exactly the collapse of that third state into the wrong
one — a file the walk had read was reported *"unreadable"*, sending a reader to chase a
permissions problem that did not exist — so the two messages are separate strings and a test
pins them.

**What it does NOT establish.** That the deployed process carries the value (a Fly secret
shadows ``[env]``; an operator can override the environment any way they like), that the value
is what *should* be shipped (presence, not policy — ``TORTOISE_*=0`` is a documented choice),
or anything about a **hand deploy**: the owner's ruling on ``#12926`` is *"a hand deploy is
their problem, so let's not worry about it"*. ``.env.example`` is a template nothing deploys,
so it is not a channel of this gate; the read-only instrument
(``tools/dod_5090_probe.py``) is what scores it.

Usage::

    uv run python tools/embed_switch_gate.py            # 0 pass, 1 refuse
    uv run python tools/embed_switch_gate.py --json     # the same verdict as data
"""

from __future__ import annotations

import sys

# #5128: the interpreter guard, FIRST after the docstring and `__future__` and BEFORE every other
# import. `tomllib` below is 3.11+, and the documented invocation is a bare `python3
# tools/embed_switch_gate.py` — on an older ambient interpreter that dies with an UNATTRIBUTED
# `ModuleNotFoundError`, which reads as "the tool is broken" rather than "your interpreter is too
# old". Same shape as every other entry point in this directory (D9, `tools/longmem_eval/run.py`),
# and pinned by `tests/test_entry_point_python_guard.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/embed_switch_gate.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/embed_switch_gate.py`"
    )

import argparse
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tortoise import embed_gate, value_gate  # noqa: E402  (after the path insert)

#: The switch names, taken from the code that reads them — never re-spelled here. A rename in
#: either module moves this gate with it, and a second spelling is exactly the drift this check
#: exists to prevent. Ordered as the epic states them: the DISCARD question, then the EMBED one.
SWITCHES: tuple[str, ...] = (value_gate.ENV_FLAG, embed_gate.ENV_FLAG)

#: Compose's OWN default file names — the ones `docker compose config` treats as a project base.
#: A file of this family with any other name is a PATCH (`docker-compose.override.yml`,
#: `docker-compose.prod.yml`): compose merges it over a base, LAST, so an override that disarms
#: an armed base wins while a per-file reading sees the base's arm. Modelling that needs merge
#: order, so such a file is REFUSED BY NAME instead of read (`#12070` item 8).
COMPOSE_BASE_NAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)
COMPOSE_FAMILY_GLOB = "*compose*.y*ml"
FLY_MANIFEST_GLOB = "fly*.toml"
IMAGE_BUILD_GLOB = "Dockerfile*"
ENTRYPOINT_GLOB = "entrypoint*.sh"

#: The image `ENV` / shell assignment we accept. Anything else that MENTIONS a switch is a
#: failure ("mentions it but the assignment shape was not read"), never a silent pass.
_ENV_ASSIGNMENT = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>\"[^\"]*\"|'[^']*'|\S*)")
_ENV_INSTRUCTION = re.compile(r"(?i)^\s*ENV\s+(?P<body>.+)$")
_SHELL_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
_SHELL_EXPORT_NAME = re.compile(r"^\s*export\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*$")

#: A value that is NOTHING BUT a variable interpolation, with its optional fallback captured.
#: ``${X:-1}`` states a value (the fallback); ``${X:-}``, ``${X}`` and ``$X`` do not — they state
#: whatever the caller's environment holds, which can be empty, and the gate would then read a
#: channel as armed while the process resolves OFF (review P3). Neither reader resolves
#: interpolation — there is no environment to resolve it against that would be the SHIPPED value —
#: so a pure interpolation with no non-empty literal fallback is refused by name.
_PURE_INTERPOLATION = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-+])(?P<fallback>[^}]*))?\}$"
    r"|^\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _states_a_literal(value: str) -> bool:
    """Whether ``value`` states a value that does not depend on an unresolved interpolation."""
    text = value.strip().strip("\"'").strip()
    match = _PURE_INTERPOLATION.match(text)
    if not match:
        return True  # a literal (a partial interpolation like `a-${X}` is literal enough)
    fallback = (match.group("fallback") or "").strip().strip("\"'").strip()
    return bool(fallback)

#: Env keys kept when the resolver shell is scrubbed. Deliberately an ALLOWLIST: a lane's
#: exported `TORTOISE_EMBED_GATE` is not a shipped default, and `${…:-1}` inside a channel would
#: otherwise resolve off the runner's environment instead of the file (`#10996`'s doctrine, the
#: same reason `measure()` scrubs the process env).
_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "DOCKER_CONFIG",
    "DOCKER_HOST",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "XDG_RUNTIME_DIR",
)


@dataclass
class Channel:
    """One deployment channel's verdict. ``stated`` holds a value per switch it states."""

    channel: str
    kind: str
    resolver: str
    stated: dict[str, str] = field(default_factory=dict)
    #: Why this channel could not be read at all — never used for "read fine, omits a switch".
    unreadable: str = ""
    #: Shapes that mention a switch but were not read as an assignment to it.
    ambiguous: tuple[str, ...] = ()
    #: A channel-shaped entry that is NOT a deployment channel, with the reason (reported only).
    noted: str = ""
    #: True when the governor refuses the file on principle (a compose patch) rather than
    #: failing to read it — a different fact, so a different word in the report.
    refused: bool = False

    def switch_problem(self, switch: str) -> str:
        """The refusal for ``switch`` on this channel, or ``""`` when it is stated.

        Three shapes, and the ORDER is the point: a readable assignment WINS over a prose
        mention (a file that states the switch in an `ENV` line and also names it in a log
        message is fine), an empty value is a placeholder rather than an arm, and a mention with
        no readable assignment is a **failure** — a shape this reader does not model must never
        read as "not stated", which would be indistinguishable from a genuine omission.
        """
        if self.unreadable:
            return ""  # reported once, as the channel-level failure
        if switch in self.stated:
            if self.stated[switch].strip():
                return ""
            return (
                f"{self.channel} states {switch} as an EMPTY value ({self.resolver}) — an empty "
                "placeholder arms nothing"
            )
        if switch in self.ambiguous:
            return (
                f"{self.channel} mentions {switch} but does not state a value this gate can read "
                f"({self.resolver}) — state it as a literal assignment (a ${{…}} with no non-empty "
                "fallback can resolve empty)"
            )
        return f"{self.channel} does not state {switch} ({self.resolver})"

    @property
    def ok(self) -> bool:
        if self.noted:
            return True
        return not self.unreadable and not any(self.switch_problem(s) for s in SWITCHES)

    def problems(self) -> list[str]:
        if self.noted:
            return []
        if self.unreadable:
            head = "REFUSED" if self.refused else "COULD NOT BE READ"
            return [f"{self.channel} {head} ({self.resolver}): {self.unreadable}"]
        return [p for p in (self.switch_problem(s) for s in SWITCHES) if p]

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "kind": self.kind,
            "resolver": self.resolver,
            "stated": dict(self.stated),
            "unreadable": self.unreadable,
            "refused": self.refused,
            "noted": self.noted,
            "problems": self.problems(),
            "ok": self.ok,
        }


# ── channel readers ────────────────────────────────────────────────────────────────────────────


def _scrubbed_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}


def _logical_lines(text: str) -> list[str]:
    """``text`` as logical lines: backslash continuations joined, full-line comments dropped.

    Comment lines are dropped BEFORE joining because a comment inside a continuation is not
    part of the value (Docker and the shell both strip it) — leaving it in would make an
    innocent ``ENV A=1 \\`` + ``# note`` + ``B=2`` read as one unparseable blob.
    """
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        if raw.strip().startswith("#"):
            continue
        if raw.rstrip().endswith("\\"):
            current += raw.rstrip()[:-1] + " "
            continue
        lines.append(current + raw)
        current = ""
    if current:
        lines.append(current)
    return lines


def _dockerfile_env(text: str) -> tuple[dict[str, str], set[str]]:
    """``ENV`` assignments in an image build file: ``{key: value}`` (last wins) + ambiguous keys.

    Both Dockerfile forms are handled — ``ENV K=V K2=V2`` and the legacy ``ENV K V``. A line that
    names a switch in a shape neither form matches is reported as AMBIGUOUS rather than ignored,
    so a spelling this reader does not model fails the gate instead of passing it.
    """
    stated: dict[str, str] = {}
    ambiguous: set[str] = set()
    for line in _logical_lines(text):
        match = _ENV_INSTRUCTION.match(line)
        if not match:
            continue
        body = match.group("body").strip()
        pairs = _ENV_ASSIGNMENT.findall(body)
        if pairs and _ENV_ASSIGNMENT.match(body):
            for key, value in pairs:
                if key in SWITCHES and not _states_a_literal(value):
                    ambiguous.add(key)  # an interpolation that can resolve empty (review P3)
                    continue
                stated[key] = value.strip("\"'")
            continue
        parts = body.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parts[0]):
            if parts[0] in SWITCHES and not _states_a_literal(parts[1]):
                ambiguous.add(parts[0])
                continue
            stated[parts[0]] = parts[1].strip().strip("\"'")
            continue
        ambiguous.update(_mentioned_switches(body))
    return stated, ambiguous


def _shell_assignments(text: str) -> tuple[dict[str, str], set[str]]:
    """Assignments an entrypoint script exports (last wins) + switch names seen ambiguously.

    Only top-level-shaped assignments are read; ``export K`` with no value states nothing (it
    exports whatever the caller had, which is the absence this gate exists to refuse) and is
    therefore not recorded as a value.
    """
    stated: dict[str, str] = {}
    ambiguous: set[str] = set()
    for line in _logical_lines(text):
        match = _SHELL_ASSIGNMENT.match(line)
        if match:
            key, value = match.group("key"), match.group("value")
            if key in SWITCHES and not _states_a_literal(value):
                ambiguous.add(key)  # `${X:-}` / `$X` — can resolve empty (review P3)
                continue
            stated[key] = value.strip().strip("\"'")
            continue
        if _SHELL_EXPORT_NAME.match(line):
            continue  # `export NAME` states no value; not an arm
        ambiguous.update(_mentioned_switches(line))
    return stated, ambiguous


def _mentioned_switches(text: str) -> set[str]:
    return {s for s in SWITCHES if re.search(rf"\b{re.escape(s)}\b", text)}


def _read_text(path: Path) -> tuple[str, str]:
    """``(text, unreadable_reason)`` — an IO failure is a reason, never an exception."""
    try:
        return path.read_text(encoding="utf-8", errors="replace"), ""
    except OSError as exc:  # pragma: no cover - a permissions/ENOENT window
        return "", f"{type(exc).__name__}: {exc}"


def _image_repo(reference: object) -> str:
    """An image reference without its tag/digest — ``ghcr.io/o/x:1.2`` -> ``ghcr.io/o/x``.

    Used to ask "is this the SAME image this repo builds?", which is the only offline evidence a
    PULLED service can offer that it runs Tortoise (review round 4, P2).
    """
    text = str(reference or "").strip()
    if not text:
        return ""
    text = text.split("@", 1)[0]
    head, sep, tail = text.rpartition(":")
    return head if sep and "/" not in tail else text


def _resolve_compose(path: Path, root: Path) -> tuple[dict | None, str]:
    """`docker compose config --format json` for one file as ``(model, reason)``."""
    if not shutil.which("docker"):
        return None, (
            "the `docker` CLI is not on PATH, so the compose project cannot be resolved — a text "
            "scan is a different question (see this module's docstring)"
        )
    argv = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-f",
        str(path),
        "config",
        "--format",
        "json",
    ]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(root),
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"the resolver could not run: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, (
            f"`docker compose config` exited {proc.returncode}: "
            f"{detail[-1] if detail else '(no output)'}"
        )
    try:
        model = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"`docker compose config` did not emit JSON: {exc}"
    if not (model.get("services") or {}):
        return None, "the resolver produced no services, so there is no environment to read"
    return model, ""


def _built_image_repos(models: list[dict]) -> set[str]:
    """Every image this repo BUILDS, per the compose projects — the positive "ours" signal.

    A service with ``build:`` AND ``image:`` names the repository its own build is published to;
    collecting the whole tree's set before classifying means a PULLED service that names one of
    them is recognised as ours wherever it appears.
    """
    repos: set[str] = set()
    for model in models:
        for service in (model.get("services") or {}).values():
            if service.get("build"):
                repo = _image_repo(service.get("image"))
                if repo:
                    repos.add(repo)
    return repos


def _is_tortoise_service(service: dict, built_repos: set[str]) -> tuple[bool, str]:
    """``(is_channel, why_not)`` for one resolved compose service.

    ⛔ NOT every service in a project is a channel — the switch is read by TORTOISE, and a
    Redis/FalkorDB sidecar never reads it. But "not built here" is NOT evidence of that: a service
    that pulls one of OUR OWN images (a pinned release, a staged tag) boots the same daemon and
    reads the same two codes-defaults, so it MUST state them (review round 4, P2 — the first rule
    let such a service through with the actively false report "it never reads either switch").

    Positive evidence of ours, in order: it is BUILT here; or its environment already carries a
    ``TORTOISE_*`` key (we configure it); or its image is one this tree BUILDS (same repository,
    tag ignored); or its image repository names Tortoise, which covers a fork/other-namespace
    publication. Everything else is a sidecar and is REPORTED as one.

    ⚠️ Declared bound: a Tortoise image pulled from a namespace that neither matches a build in
    this tree nor contains "tortoise" is indistinguishable offline from a third-party image, and
    is reported as skipped. That is what the report line says ("no evidence this service runs
    Tortoise") rather than asserting the process never reads the switches.
    """
    if service.get("build"):
        return True, ""
    environment = service.get("environment") or {}
    if any(str(key).startswith("TORTOISE_") for key in environment):
        return True, ""
    repo = _image_repo(service.get("image"))
    if repo and repo in built_repos:
        return True, ""
    if "tortoise" in repo.lower():
        return True, ""
    return False, (
        "no evidence this service runs Tortoise — it is not built here, its image is not one this "
        f"tree builds, and it carries no TORTOISE_* config (image `{service.get('image') or '(none)'}`)"
    )


def _compose_channels(path: Path, root: Path, built_repos: set[str]) -> list[Channel]:
    """One ``Channel`` per SERVICE of a compose base file, read through `docker compose config`."""
    label = path.name
    model, reason = _resolve_compose(path, root)
    if model is None:
        return [
            Channel(channel=label, kind="compose", resolver="docker compose config", unreadable=reason)
        ]
    channels: list[Channel] = []
    for name in sorted(model.get("services") or {}):
        service = (model.get("services") or {})[name]
        environment = service.get("environment") or {}
        # compose renders both `K: v` and `- K=v` into one mapping; a value-less entry is
        # `null`, which states NO value (the host's environment is substituted) — recorded as
        # the empty string so `switch_problem` refuses it as an empty placeholder.
        stated = {
            key: ("" if value is None else str(value))
            for key, value in environment.items()
            if key in SWITCHES
        }
        is_channel, why_not = _is_tortoise_service(service, built_repos)
        channels.append(
            Channel(
                channel=f"{label}: {name}",
                kind="compose",
                resolver="docker compose config --format json",
                stated=stated if is_channel else {},
                noted=why_not,
            )
        )
    return channels


def _fly_channel(path: Path, root: Path) -> Channel:
    """A Fly manifest's runtime ``[env]``, via `fly config show --local` or `tomllib`."""
    label = _label(path, root)
    text, unreadable = _read_text(path)
    if unreadable:
        return Channel(label, "fly", "tomllib [env]", unreadable=unreadable)
    # Prefer the channel's own resolver when it is installed. It is NOT required: `fly config
    # show` refuses without an access token even for `--local` (measured, see the docstring), so
    # a required check cannot depend on it — the fallback reads the same `[env]` table with the
    # format's grammar owner and the output names which one answered.
    if shutil.which("flyctl"):
        resolved = _fly_config_show(path)
        if resolved is not None:
            stated, ambiguous = resolved
            return Channel(
                label,
                "fly",
                "fly config show --local",
                stated=stated,
                ambiguous=tuple(sorted(ambiguous)),
            )
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return Channel(label, "fly", "tomllib [env]", unreadable=f"TOML decode: {exc}")
    env = document.get("env")
    if not isinstance(env, dict):
        return Channel(
            label,
            "fly",
            "tomllib [env]",
            unreadable="the manifest has no `[env]` table, so there is no environment to read",
        )
    stated, ambiguous = _env_table_values(env)
    return Channel(label, "fly", "tomllib [env]", stated=stated, ambiguous=tuple(ambiguous))


def _env_table_values(env: dict) -> tuple[dict[str, str], set[str]]:
    """A resolved env TABLE's switch values, plus the keys whose value is not a scalar.

    ⛔ The first version computed `ambiguous = {s for s in SWITCHES if s in env and s not in
    stated}`, which is DEAD by construction — `stated` is built from the same keys (review P3).
    The shape it was reaching for is a value that is not a scalar at all (a TOML table, a JSON
    object/array): that states no string the gate can read, so it is reported rather than
    silently recorded as its `str()` repr.
    """
    stated: dict[str, str] = {}
    ambiguous: set[str] = set()
    for key, value in env.items():
        if key not in SWITCHES:
            continue
        if isinstance(value, bool):
            stated[key] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            stated[key] = str(value)
        else:
            ambiguous.add(key)
    return stated, ambiguous


def _fly_config_show(path: Path) -> tuple[dict[str, str], set[str]] | None:
    """`fly config show --local` as ``(stated, ambiguous)``, or ``None`` when it cannot answer."""
    try:
        proc = subprocess.run(
            ["flyctl", "config", "show", "--local", "--config", str(path)],
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None  # e.g. no access token, even for `--local` — the tomllib arm answers
    try:
        model = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    env = model.get("env")
    if not isinstance(env, dict):
        return None
    return _env_table_values(env)


def _label(path: Path, root: Path) -> str:
    """The channel label: the repo-relative path when it is under the root, else the file name."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - defensive
        return path.name


# ── discovery ────────────────────────────────────────────────────────────────────────────────


def _root_files(root: Path, pattern: str) -> list[Path]:
    """Files at the repo ROOT matching ``pattern``, sorted — the discovery step, not a list.

    Root-scoped on purpose and named as a bound: a nested compose file
    (`apps/<x>/docker-compose.yml`) or a nested image file is another application's deployment,
    not a channel a Tortoise process is deployed through. A `fly.<app>.toml` or a second
    `docker-compose.yml` at the root is discovered the hour it lands (`#12022`'s lesson: two
    real channels were missed by a fixed path list).
    """
    return sorted(p for p in root.glob(pattern) if p.is_file())


def discover(root: Path) -> list[Channel]:
    """Read every deployment channel under ``root``; refuse (never skip) what cannot be read."""
    channels: list[Channel] = []

    compose_files = _root_files(root, COMPOSE_FAMILY_GLOB)
    # Pass 1: resolve every base project, so the tree's OWN built image repositories are known
    # before any single service is classified (a pulled service may name an image another compose
    # file builds).
    models: list[dict] = []
    for path in compose_files:
        if path.name in COMPOSE_BASE_NAMES:
            model, _reason = _resolve_compose(path, root)
            if model is not None:
                models.append(model)
    built_repos = _built_image_repos(models)
    for path in compose_files:
        if path.name not in COMPOSE_BASE_NAMES:
            channels.append(
                Channel(
                    channel=path.name,
                    kind="compose",
                    resolver="(refused: compose patch/override)",
                    refused=True,
                    unreadable=(
                        "this is a compose PATCH, not a project base: compose merges it over a "
                        "base LAST, so an override that disarms an armed base wins while reading "
                        "it alone would show the base's arm. This gate models no merge order, so "
                        "it refuses the file by name instead of half-reading it (#12070 item 8) — "
                        "teach it the merge order, or state the switches in the base"
                    ),
                )
            )
            continue
        channels.extend(_compose_channels(path, root, built_repos))

    for path in _root_files(root, FLY_MANIFEST_GLOB):
        channels.append(_fly_channel(path, root))

    for path in _root_files(root, IMAGE_BUILD_GLOB):
        label = _label(path, root)
        text, unreadable = _read_text(path)
        if unreadable:
            channels.append(Channel(label, "dockerfile", "Dockerfile ENV", unreadable=unreadable))
            continue
        stated, ambiguous = _dockerfile_env(text)
        channels.append(
            Channel(
                label,
                "dockerfile",
                "Dockerfile ENV",
                stated=stated,
                ambiguous=tuple(sorted(ambiguous)),
            )
        )

    for path in _root_files(root, ENTRYPOINT_GLOB):
        label = _label(path, root)
        text, unreadable = _read_text(path)
        if unreadable:
            channels.append(Channel(label, "entrypoint", "shell assignments", unreadable=unreadable))
            continue
        stated, ambiguous = _shell_assignments(text)
        channels.append(
            Channel(
                label,
                "entrypoint",
                "entrypoint assignments",
                stated=stated,
                ambiguous=tuple(sorted(ambiguous)),
            )
        )

    return channels


def evaluate(root: Path = REPO_ROOT) -> tuple[list[Channel], list[str]]:
    """The channels and the failure lines. An EMPTY population is a failure, never a pass."""
    channels = discover(root)
    failures: list[str] = []
    # ⛔ "Nothing compared" is not a pass — in EITHER shape: an empty population (nothing was
    # discovered) and a population whose every member is `noted` (discovered shapes, none of which
    # is a deployment channel — e.g. a compose file left with only sidecar services, whose
    # Tortoise service lost every `TORTOISE_*` key and so stopped being recognised as one).
    checked = [channel for channel in channels if not channel.noted]
    if not channels:
        failures.append(
            f"no deployment channel was discovered under {root} — nothing was compared, and "
            "'nothing compared' is not a pass"
        )
    elif not checked:
        failures.append(
            "every discovered manifest was skipped as a non-channel, so no deployment channel was "
            "checked — 'nothing compared' is not a pass"
        )
    for channel in channels:
        failures.extend(channel.problems())
    return channels, failures


def render(channels: list[Channel], failures: list[str]) -> str:
    lines = [f"embedding switches: {', '.join(SWITCHES)}"]
    for channel in channels:
        if channel.noted:
            lines.append(f"  {'skipped':<8} {channel.channel:<28} [{channel.noted}]")
            continue
        cells = []
        for switch in SWITCHES:
            if channel.unreadable:
                cells.append(f"{switch}=?")
            elif switch in channel.stated:
                cells.append(f"{switch}={channel.stated[switch] or '(empty)'}")
            else:
                cells.append(f"{switch}=MISSING")
        verdict = "ok" if channel.ok else "REFUSED"
        lines.append(f"  {verdict:<8} {channel.channel:<28} {' '.join(cells)}  [{channel.kind}: {channel.resolver}]")
    if failures:
        lines.append("")
        lines.append("refused (#12926 — a resolved deployment config must STATE both switches):")
        lines.extend(f"  - {failure}" for failure in failures)
    else:
        lines.append("")
        checked = sum(1 for channel in channels if not channel.noted)
        lines.append(
            f"OK — every discovered deployment channel states both switches ({checked} checked)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repository root to walk (default: this checkout)")
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"embed_switch_gate: --repo is not a directory: {root}", file=sys.stderr)
        return 2
    channels, failures = evaluate(root)
    if args.json:
        print(json.dumps({"channels": [c.as_dict() for c in channels], "failures": failures}, indent=2))
    else:
        print(render(channels, failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
