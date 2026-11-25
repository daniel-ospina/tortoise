"""Answer ONE question: is the Tortoise MCP loadable from THIS agent, and if not,
why? (#14607)

WHY THIS MODULE EXISTS. A ``core`` MCP server was configured, did not start, and
reported that fact nowhere except a status string in a catalog listing — so an
agent working inside this repo fell back to hand-rolled HTTP and direct Cypher and
measured the product through doors the product does not own (#14607). Worse, the
one message that DID surface named the wrong layer: ``-32001 Unauthorized: invalid
or missing Bearer token`` is what a server can say when a client sends
``Authorization: Bearer `` (empty), but the fault was that the harness config had
already lost the credential — ``"Authorization": "Bearer ${TORTOISE_API_KEY}"``
expands an environment variable the harness read once at process start, and an
unset variable expands to ``""`` (#10605's class, fixed in ``mcp_bridge`` and never
applied to the config).

So the deliverable is a VERDICT, not a log: one line, one class from a CLOSED set,
each class naming the layer the reader must fix. The classes are deliberately not
collapsible — the four ways a `core` server goes silent have four different
remedies, and reporting an unreachable endpoint as "your token is invalid" is the
same defect in the opposite direction.

DESIGN CONSTRAINTS (stated, because each one is a way this could silently rot):

* **Offline by default.** The static classification reads files and environment
  only. A network probe is opt-in (``live=True``) and its failure is reported as
  its own class, never folded into a credential class.
* **No credential is ever printed, hashed, or length-leaked.** A value is only
  ever tested for blankness.
* **The class names the layer; the line names the fix.** A class without a fix is
  a dead end, and a dead end is what this issue is about.
* **It reads the config the HARNESS reads.** The paths per harness are declared in
  `HARNESS_CONFIG_PATHS` and are the ones this repo verified; a harness whose file
  moves is a new class (``entry-absent``) that says which files were read, so the
  message can never be a lie about a file nobody looked at.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: The server name the harness must declare. One name, everywhere.
SERVER_NAME = "tortoise"

# ── The CLOSED class set ──────────────────────────────────────────────────
#: Loadable — a LIVE call succeeded. The only green state, and it is reachable
#: only by proving it: an unprobed entry is not OK, because "the file looks fine"
#: is exactly the reading that let a `core` server sit down for weeks (#14607).
OK = "ok"
#: The entry resolves and would start, but no live call was made. An ABSTENTION,
#: rendered and exited as one — never as OK. A verifier that greens an unprobed
#: config is a false PASS, which is the defect class this module exists to remove.
RESOLVES_UNPROBED = "resolves-unprobed"
#: No `tortoise` entry in ANY config file this harness reads.
ENTRY_ABSENT = "entry-absent"
#: The entry interpolates a `${VAR}` that is unset/blank. For an
#: `Authorization` header this is the #14607/#10605 empty-bearer capture; for
#: `command`/`cwd` it is a spawn that cannot happen.
ENV_UNSET = "env-unset"
#: A stdio entry's `command` does not exist (absolute path or a path with `/`).
COMMAND_MISSING = "command-missing"
#: A stdio entry's `cwd` does not exist.
CWD_MISSING = "cwd-missing"
#: A stdio entry's interpreter exists but cannot `import tortoise`, so the spawn
#: succeeds and the MODULE fails — a distinct layer from a missing command, and
#: the reason the written entry names a portable `python3` rather than one
#: machine's virtualenv path.
PYTHON_LACKS_TORTOISE = "python-lacks-tortoise"
#: An http entry with no credential in any source → the request would carry
#: nothing (or an empty bearer) and the server can only answer `-32001`.
NO_CREDENTIAL = "no-credential"
#: The endpoint could not be reached at all (DNS, connect, TLS, timeout). A
#: TRANSPORT failure — never an auth verdict.
UNREACHABLE = "endpoint-unreachable"
#: The endpoint answered and refused the credential (401/403 / -32001).
AUTH_REJECTED = "auth-rejected"
#: The endpoint answered with something else (5xx, a malformed MCP reply).
SERVER_ERROR = "server-error"
#: The probe could not run (no httpx/transport layer, internal fault). An
#: abstention, never a pass.
PROBE_UNKNOWN = "probe-unknown"

CLASSES = (
    OK, RESOLVES_UNPROBED, ENTRY_ABSENT, ENV_UNSET, COMMAND_MISSING, CWD_MISSING,
    PYTHON_LACKS_TORTOISE, NO_CREDENTIAL,
    UNREACHABLE, AUTH_REJECTED, SERVER_ERROR, PROBE_UNKNOWN,
)

#: Classes that mean "the MCP is not usable from this agent".
DOWN_CLASSES = frozenset(CLASSES) - {OK}

#: Classes that are an ABSTENTION ("could not decide") rather than a failure. Each
#: exits 3 and is never rendered as a pass.
ABSTENTION_CLASSES = frozenset({RESOLVES_UNPROBED, PROBE_UNKNOWN})

#: The NAME at the head of a `${…}` expression. The default (everything after a
#: `:-`) is consumed by a brace-balance SCAN, not by a regex group — see
#: `_expand`. A form neither path recognises is reported as a literal, which
#: keeps the verifier honest rather than silently treating an unexpanded
#: expression as a value.
_INTERP_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: The harness config files each harness actually reads, in PRECEDENCE ORDER
#: (first match wins per server name, which is how both pi and Claude Code
#: resolve a project entry over a user entry). `~` is expanded at use.
HARNESS_CONFIG_PATHS: dict[str, tuple[str, ...]] = {
    # pi: project `.mcp.json` overrides the user-level catalog.
    "pi": (".mcp.json", "~/.pi/agent/.mcp.json"),
    # Claude Code: project `.mcp.json`; user scope lives in `~/.claude.json`
    # (which also carries other state, so only `mcpServers` is read).
    "claude": (".mcp.json", "~/.claude.json", "~/.claude/settings.json"),
    "claude-code": (".mcp.json", "~/.claude.json", "~/.claude/settings.json"),
    # Codex CLI: `$CODEX_HOME/config.toml` is TOML; this verifier does not parse
    # it (stated, not implied) — see `_TOML_HARNESSES`.
    "codex": ("~/.codex/config.toml",),
    "cursor": (".cursor/mcp.json", "~/.cursor/mcp.json"),
}

#: Harnesses whose config is TOML. Declared so the verdict can say "this verifier
#: cannot read that format" instead of reporting `entry-absent` about a file it
#: never parsed — an absence claim about an unread file is exactly the kind of
#: false negative this module exists to remove.
_TOML_HARNESSES = frozenset({"codex"})

#: The credential stores, in the product's own documented order (#1708 D5).
CREDENTIAL_ENV_NAMES = ("TORTOISE_API_KEY", "TORTOISE_MCP_API_KEY")
CREDENTIAL_FILES = ("./.tortoise", "~/.tortoise/credentials.json")


@dataclass
class Verdict:
    """One line, one class, one fix."""

    status: str
    detail: str
    fix: str = ""
    source: str = ""          # the config file the entry came from
    transport: str = ""       # "http" | "stdio" | ""
    configs_read: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def exit_code(self) -> int:
        """0 proven loadable · 1 down (named class) · 3 abstention.

        The abstention arm is deliberately NOT 0: a script that gates on this exit
        code must not read "I could not check" as "it is fine".
        """
        if self.ok:
            return 0
        return 3 if self.status in ABSTENTION_CLASSES else 1

    def line(self) -> str:
        """THE agent-facing line. Never empty, never names a credential."""
        if self.ok:
            head = "Tortoise MCP: OK"
        elif self.status in ABSTENTION_CLASSES:
            head = f"Tortoise MCP: UNVERIFIED [{self.status}]"
        else:
            head = f"Tortoise MCP: DOWN [{self.status}]"
        where = f" ({self.source}, {self.transport})" if self.source else ""
        tail = f" — {self.fix}" if self.fix and not self.ok else ""
        return f"{head}{where}: {self.detail}{tail}"


# ── Static classification ─────────────────────────────────────────────────

def _matching_brace(text: str, open_at: int) -> int:
    """Index of the `}` matching the `{` at `open_at`, or -1 when unterminated."""
    depth = 0
    for j in range(open_at + 1, len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                return j
            depth -= 1
    return -1


def _split_default(body: str) -> tuple[str, str | None] | None:
    """`NAME` + optional `:-DEFAULT`, or None for a form this verifier refuses."""
    m = _INTERP_NAME_RE.match(body)
    if not m:
        return None
    rest = body[m.end():]
    if not rest:
        return m.group(0), None
    if rest.startswith(":-"):
        return m.group(0), rest[2:]
    return None


def _expand(expr: str) -> tuple[str, list[str]]:
    """Expand `${VAR}`/`${VAR:-default}`; also return the names that were UNSET.

    The unset list is the payload, not the expansion: an entry that interpolates
    a variable the harness never exported is the defect, whether the expression is
    a credential, a command path or a working directory.

    ⛔ The default is scanned with BRACE BALANCE, never with a `[^}]*` group. The
    nested form is what this repo's own configs use —
    `${TORTOISE_HOME:-${HOME}/Documents/GitHub/tortoise}` — and a group that stops
    at the first `}` both LOSES the unset name and substitutes a mangled value:
    measured on that expression with `TORTOISE_HOME` unset, it returned
    `('${HOME/Documents/GitHub/tortoise}', [])`. The empty unset list means
    `env-unset` can never fire, and the mangled path then classified as
    `command-missing` — sending the reader to replace the interpreter when the
    fault was the unset variable. That is the defect class this module exists to
    remove, so the scanner replaces the group rather than keeping the form
    unsupported. A nested brace is not exotic input: `${VAR:-default}` is
    vendor-documented, and `_walk_strings` reads this repo's own configs.
    """
    unset: list[str] = []
    out: list[str] = []
    i = 0
    while i < len(expr):
        start = expr.find("${", i)
        if start < 0:
            out.append(expr[i:])
            break
        out.append(expr[i:start])
        end = _matching_brace(expr, start + 1)
        if end < 0:
            # Unterminated: emit the rest LITERALLY. An unexpanded expression is
            # an honest value; inventing one is not.
            out.append(expr[start:])
            break
        parsed = _split_default(expr[start + 2:end])
        if parsed is None:
            # A form this verifier does not expand stays literal (`${A:+b}`,
            # a bare `${}`) — and its bytes are preserved for the reader.
            out.append(expr[start:end + 1])
        else:
            out.append(_substitute(*parsed, unset=unset))
        i = end + 1
    return "".join(out), unset


def _substitute(name: str, default: str | None, *, unset: list[str]) -> str:
    """One `${…}`: the value, else the default, else "" — recording what was lost."""
    value = os.environ.get(name)
    if value:
        return value
    # `${A:-${B}}` nests; expand the default too, and record what it lost.
    if default is not None:
        expanded, inner_unset = _expand(default)
        for lost in inner_unset:
            if lost not in unset:
                unset.append(lost)
        if expanded:
            return expanded
    if name not in unset:
        unset.append(name)
    return ""


def _walk_strings(node, path: str = "") -> list[tuple[str, str]]:
    """Every (json-path, string) leaf of an entry — for interpolation scanning.

    `_comment` values are SKIPPED: they are prose about the entry, not values a
    client sends, so a `_comment` that quotes an expression (as this repo's own
    entries do) must not be classified as an unset variable. That false class
    would send a reader to fix a variable nothing reads.
    """
    out: list[tuple[str, str]] = []
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            if str(k).startswith("_"):
                continue
            out.extend(_walk_strings(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_strings(v, f"{path}[{i}]"))
    return out


def _read_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_entry(harness: str, *, project_dir: Path | None = None
               ) -> tuple[str | None, dict | None, list[str], str]:
    """Find the `tortoise` entry the harness would load.

    Returns ``(source_path, entry, files_read, status)``. ``status`` is ``"ok"``
    for a found entry, else the class that explains the miss (``entry-absent`` or
    ``probe-unknown`` for a format this verifier does not parse).
    """
    project_dir = Path(project_dir or Path.cwd())
    rels = HARNESS_CONFIG_PATHS.get(harness)
    if rels is None:
        return None, None, [], PROBE_UNKNOWN
    if harness in _TOML_HARNESSES:
        # Read the TOML only to say honestly what was looked at; the entry is
        # NOT parsed (see `_TOML_HARNESSES`).
        read = [str(Path(p.replace("~", str(Path.home())))
                    if p.startswith("~") else project_dir / p)
                for p in rels]
        read = [p for p in read if Path(p).exists()]
        return None, None, read, PROBE_UNKNOWN
    read: list[str] = []
    for rel in rels:
        path = Path(rel.replace("~", str(Path.home()))) \
            if rel.startswith("~") else project_dir / rel
        if not path.exists():
            continue
        read.append(str(path))
        data = _read_json(path)
        if not data:
            continue
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        entry = servers.get(SERVER_NAME)
        if isinstance(entry, dict):
            return str(path), entry, read, OK
    return None, None, read, ENTRY_ABSENT


def credentials_present() -> list[str]:
    """Which credential sources resolve to a NON-BLANK value (names only)."""
    found: list[str] = []
    for name in CREDENTIAL_ENV_NAMES:
        if (os.environ.get(name) or "").strip():
            found.append(f"env:{name}")
    for rel in CREDENTIAL_FILES:
        path = Path(rel.replace("~", str(Path.home()))) \
            if rel.startswith("~") else Path.cwd() / rel.lstrip("./")
        data = _read_json(path)
        if not data:
            continue
        key = data.get("api_key") or data.get("apiKey") or data.get("key")
        if isinstance(key, str) and key.strip():
            found.append(f"file:{path}")
    return found


def _missing_module(command: str, module: str) -> str:
    """Why `<command> -m <module>` would fail to import — "" when it would work.

    A bounded subprocess (`-c "import <module>"`) rather than an import in THIS
    process: the question is about the interpreter the config names, not about the
    one running the verifier, and those differ exactly when this matters.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [command, "-c", f"import {module.split('.')[0]}"],
            capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the interpreter could not be run ({type(exc).__name__})"
    if proc.returncode == 0:
        return ""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    last = tail[-1] if tail else f"exit {proc.returncode}"
    return last[:200]


def _header_carries_credential(entry: dict) -> bool:
    headers = entry.get("headers")
    if not isinstance(headers, dict):
        return False
    for name, value in headers.items():
        if str(name).lower() != "authorization" or not isinstance(value, str):
            continue
        token = value[7:].strip() if value.lower().startswith("bearer ") \
            else value.strip()
        if token:
            return True
    return False


def classify(harness: str, *, project_dir: Path | None = None
             ) -> Verdict:
    """Static classification — files and environment only, no network.

    The order is fixed and each step is a distinct layer: the config (does the
    entry exist) → interpolation (can it even be built) → spawnability (can the
    command start) → credential (would the request carry one).
    """
    source, entry, read, status = read_entry(harness, project_dir=project_dir)
    if entry is None:
        if status == PROBE_UNKNOWN:
            return Verdict(
                status=PROBE_UNKNOWN,
                detail=(f"cannot read this harness's config format (read: "
                        f"{', '.join(read) or 'no config files present'})"),
                fix=("verify by hand, or run the harness's own MCP listing; "
                     "this verifier parses JSON configs only"),
                configs_read=read)
        return Verdict(
            status=ENTRY_ABSENT,
            detail=(f"no `{SERVER_NAME}` server declared in any config this "
                    f"harness reads (read: {', '.join(read) or 'none present'})"),
            fix=(f"register it: `tortoise onboard --harness {harness}`"),
            configs_read=read)

    leaves = _walk_strings(entry)
    unset: list[str] = []
    for _path, value in leaves:
        _, missing = _expand(value)
        unset.extend(m for m in missing if m not in unset)
    transport = "http" if (entry.get("type") in ("http", "streamable-http")
                           or entry.get("url")) else "stdio"
    if unset:
        names = ", ".join(f"${n}" for n in unset)
        blank_credential = any(
            "author" in p.lower() for p, _v in leaves) or \
            not credentials_present()
        detail = (f"the entry interpolates {names}, which the harness "
                  f"environment does not set — an unset variable expands to an "
                  f"empty string")
        if blank_credential:
            detail += ("; the request would carry `Authorization: Bearer ` "
                       "(empty), which a server can only answer `-32001 invalid "
                       "or missing Bearer token` — the message names the "
                       "credential's VALUE while the fault is the CAPTURE")
        return Verdict(
            status=ENV_UNSET, detail=detail,
            fix=("replace the entry with the credential-free bridge — no "
                 "`${VAR}` in the config at all: "
                 f"`tortoise onboard --harness {harness}`"),
            source=source, transport=transport, configs_read=read)

    if transport == "stdio":
        # ⛔ EXPAND before the existence checks. A command or cwd may interpolate
        # (`${TORTOISE_HOME:-${HOME}/…}/.venv/bin/python3`), and checking the RAW
        # string asked whether a path containing `${…}` exists — always False —
        # then reported an expression as "the command that does not exist", which
        # sends the reader to replace an interpreter that was never the problem.
        # Anything still UNSET was already returned as `env-unset` above, so a
        # remaining expansion is a value the entry can actually be built with.
        raw_cmd = entry.get("command") or ""
        cmd = _expand(raw_cmd)[0] if raw_cmd else raw_cmd
        if cmd and ("/" in cmd) and not Path(cmd).exists():
            return Verdict(
                status=COMMAND_MISSING,
                detail=f"the entry's command `{cmd}` does not exist on this machine",
                fix=("point the entry at a real interpreter, or re-register it: "
                     f"`tortoise onboard --harness {harness}`"),
                source=source, transport=transport, configs_read=read)
        if not cmd:
            return Verdict(
                status=COMMAND_MISSING,
                detail="the entry declares neither a `url` (http) nor a "
                       "`command` (stdio), so it cannot start",
                fix=f"re-register it: `tortoise onboard --harness {harness}`",
                source=source, transport=transport, configs_read=read)
        if not shutil.which(cmd) and "/" not in cmd:
            return Verdict(
                status=COMMAND_MISSING,
                detail=f"the entry's command `{cmd}` is not on PATH",
                fix=f"re-register it: `tortoise onboard --harness {harness}`",
                source=source, transport=transport, configs_read=read)
        raw_cwd = entry.get("cwd")
        cwd = _expand(raw_cwd)[0] if isinstance(raw_cwd, str) and raw_cwd else raw_cwd
        if isinstance(cwd, str) and cwd and not Path(cwd).exists():
            return Verdict(
                status=CWD_MISSING,
                detail=f"the entry's cwd `{cwd}` does not exist, so the "
                       f"command cannot be spawned there",
                fix=f"re-register it: `tortoise onboard --harness {harness}`",
                source=source, transport=transport, configs_read=read)
        args = entry.get("args")
        if isinstance(args, list) and "-m" in args:
            module = args[args.index("-m") + 1:] if len(args) > args.index("-m") + 1 else []
            module_name = module[0] if module and isinstance(module[0], str) else ""
            if module_name.startswith("tortoise"):
                missing = _missing_module(cmd, module_name)
                if missing:
                    return Verdict(
                        status=PYTHON_LACKS_TORTOISE,
                        detail=(f"`{cmd} -m {module_name}` cannot run: {missing} "
                                f"— the command exists, so a spawn SUCCEEDS and "
                                f"the import fails inside the child"),
                        fix=(f"install tortoise for `{cmd}`, or pin this box's "
                             f"interpreter: `tortoise onboard --harness "
                             f"{harness}` from the environment that has it"),
                        source=source, transport=transport, configs_read=read)
        return Verdict(
            status=RESOLVES_UNPROBED,
            detail="stdio entry resolves and would start (NOT probed — run with "
                   "`--live` to prove the endpoint answers)",
            fix="prove it: `tortoise mcp-status --live`",
            source=source, transport=transport, configs_read=read)

    # http
    if not _header_carries_credential(entry) and not credentials_present():
        return Verdict(
            status=NO_CREDENTIAL,
            detail=("the entry carries no credential and no credential resolves "
                    "in the environment or either store — the request goes out "
                    "unauthenticated"),
            fix=("mint or supply a key (`tortoise signup`, or "
                 "`tortoise init --api-key tt_…`), then re-verify"),
            source=source, transport=transport, configs_read=read)
    return Verdict(
        status=RESOLVES_UNPROBED,
        detail="http entry resolves and carries a credential (NOT probed — run "
               "with `--live` to prove the endpoint accepts it)",
        fix="prove it: `tortoise mcp-status --live`",
        source=source, transport=transport, configs_read=read)


# ── The live probe (opt-in) ────────────────────────────────────────────────

def _classify_probe_failure(exc: BaseException) -> str:
    """Map a probe exception onto the closed set.

    Credential classes are NOT inferred from a status code string: they come from
    `mcp_client.describe_failure`, which is the one owner of that read (it strips
    a URL before matching `401`, so an endpoint at `…/401/mcp` cannot be read as
    an auth rejection — the exact inversion this function would otherwise re-add).
    """
    try:
        from . import mcp_client
    except Exception:
        return PROBE_UNKNOWN
    text = str(exc)
    try:
        described = mcp_client.describe_failure(exc)
    except Exception:
        described = text
    lowered = f"{described} {text}".lower()
    if getattr(mcp_client, "MCPKeyEmptyError", None) and \
            isinstance(exc, mcp_client.MCPKeyEmptyError):
        return NO_CREDENTIAL
    if "mcp-key-empty" in lowered or "mcp_key_empty" in lowered:
        return NO_CREDENTIAL
    if any(tok in lowered for tok in (
            "name or service not known", "nodename nor servname",
            "temporary failure in name resolution", "could not resolve",
            "connection refused", "connect error", "timed out", "timeout",
            "ssl", "network is unreachable", "no route to host",
            "all connection attempts failed", "unavailable")):
        return UNREACHABLE
    if any(tok in lowered for tok in (
            "401", "403", "unauthorized", "forbidden", "invalid or missing bearer",
            "token is invalid", "revoked")):
        return AUTH_REJECTED
    return SERVER_ERROR


_FIX_BY_CLASS = {
    UNREACHABLE: ("the endpoint cannot be reached from this machine — a "
                  "TRANSPORT failure, not a credential one; check egress/DNS for "
                  "the configured host, or point the entry at a reachable target"),
    AUTH_REJECTED: ("the endpoint answered and REFUSED the credential — the key "
                    "is present but invalid, revoked, or for another "
                    "organization; re-mint it and re-verify"),
    SERVER_ERROR: ("the endpoint answered with an error that is neither transport "
                   "nor auth; read its body and check server health"),
    PROBE_UNKNOWN: ("the probe could not run; this is an abstention, not a pass"),
}


def declared_endpoint(harness: str, *, project_dir: Path | None = None
                      ) -> str | None:
    """The endpoint the harness ENTRY names, when it declares one.

    The probe must grade the endpoint the harness will actually contact, not the
    one this shell resolves. `tortoise onboard --harness pi --url
    http://localhost:8000/mcp` writes the target into the entry's
    `env.TORTOISE_MCP_URL`, while `mcp_client.mcp_url()` reads the CALLING
    shell's variable or the credential store — so grading that instead was wrong
    in BOTH directions: `OK` while the entry points at a dead local endpoint (a
    FALSE PASS inside the verifier, the class this module exists to remove), and
    `endpoint-unreachable` / `auth-rejected` about a host the harness never
    contacts.

    Returns None when the entry declares no endpoint (a bare stdio entry), where
    the client's own resolution IS the right answer.
    """
    _source, entry, _read, _status = read_entry(harness, project_dir=project_dir)
    if not entry:
        return None
    candidates: list[str] = []
    env = entry.get("env")
    if isinstance(env, dict):
        value = env.get("TORTOISE_MCP_URL")
        if isinstance(value, str):
            candidates.append(value)
    value = entry.get("url")
    if isinstance(value, str):
        candidates.append(value)
    for raw in candidates:
        expanded, _unset = _expand(raw)
        if expanded.strip():
            return expanded.strip()
    return None


def probe(*, project_dir: Path | None = None, url: str | None = None) -> Verdict:
    """Make ONE live tool call through the product's own MCP client.

    Uses `mcp_client`'s per-request credential resolution, so a key added after
    the harness started is visible here — and a missing key is the client's
    NAMED refusal rather than an empty-bearer replay.

    `url` is the endpoint the ENTRY names (see `declared_endpoint`); when it is
    absent the client's own resolution stands. The verdict's `detail` names which
    endpoint was graded, because "the endpoint answers" is only meaningful about
    a NAMED host.
    """
    try:
        from . import mcp_client
        from .mcp_client import _BearerProvider, _client
    except Exception as exc:
        return Verdict(
            status=PROBE_UNKNOWN,
            detail=f"the MCP client could not be imported ({type(exc).__name__})",
            fix=_FIX_BY_CLASS[PROBE_UNKNOWN])

    target = (url or "").strip()

    async def _call() -> list:
        resolved = target or mcp_client.mcp_url()
        provider = _BearerProvider(resolved)
        async with _client(provider) as client:
            tools = await client.list_tools()
            return list(tools or [])

    graded = target or "the client-resolved endpoint"
    try:
        tools = mcp_client._run(_call())
    except Exception as exc:
        status = _classify_probe_failure(exc)
        return Verdict(status=status, detail=f"{_first_line(exc)} (graded {graded})",
                       fix=_FIX_BY_CLASS.get(status, ""),
                       transport="http")
    return Verdict(
        status=OK,
        detail=f"live call succeeded — {len(tools)} tools registered (graded {graded})",
        transport="http")


def _first_line(exc: BaseException, limit: int = 240) -> str:
    """A failure's own FIRST line — bounded, and never the whole traceback."""
    text = str(exc).strip().splitlines()
    line = text[0] if text else type(exc).__name__
    return line if len(line) <= limit else line[:limit - 1] + "…"


# ── The entry point the CLI and the installer both use ─────────────────────

def check(harness: str = "pi", *, project_dir: Path | None = None,
          live: bool = False) -> Verdict:
    """Classify statically, then (when ``live``) probe.

    The static verdict SHORT-CIRCUITS a probe it already disproved: probing a
    config whose command does not exist, or whose credential is absent, spends a
    network round trip to learn what the files already said — and worse, its
    transport error would mask the static class with a less specific one. Only
    `resolves-unprobed` — "the entry would start" — is worth a round trip, and
    only a successful call upgrades the verdict to OK.
    """
    static = classify(harness, project_dir=project_dir)
    if not live or static.status not in (OK, RESOLVES_UNPROBED):
        return static
    probed = probe(project_dir=project_dir,
                   url=declared_endpoint(harness, project_dir=project_dir))
    # Keep the config provenance the static pass found: the probe does not read
    # the harness file, so dropping it would lose "which file did I load" — and
    # the probe always speaks HTTP to the endpoint even when the ENTRY is stdio
    # (the bridge is a stdio→HTTP shim), so its own transport label would
    # mis-describe what the harness loads.
    probed.source = static.source
    probed.transport = static.transport
    probed.configs_read = static.configs_read
    return probed
