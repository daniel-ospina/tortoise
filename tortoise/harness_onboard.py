"""The installer: register the MCP, prove it, install the seam, and write the two
sentences an agent actually reads (#14607).

WHY THIS IS A MODULE AND NOT A DOC PAGE. Registration alone leaves the agent to
remember, and a hand-maintained config file is wrong in at least four independent
ways (missing spawn path, unset `${VAR}`, no credential in any store, unreachable
endpoint) with no surface that says which. The comparables converge on one shape
— installer + hooks + skill, with the MCP as the DEEP surface (Hindsight:
`install <harness>` registers MCP at user scope, adds hooks, installs a companion
skill; Supermemory: one-command install, hooks search before the model sees the
prompt) — and the pieces of that shape already exist here
(`tortoise.install_hooks`, `tortoise.mcp_bridge`, `tortoise mcp-status`). What was
missing is the one command that puts them in the right state and VERIFIES it.

THE TWO AX RULES THIS FILE IS BUILT AROUND (#14263, owner rulings):

* ⛔ **never a silent append** — every write to a user's instruction file is
  delimiter-marked, idempotent, reversible (`--uninstall` removes exactly the
  marked block), and its diff is PRINTED. A user who cannot see what was done to
  their `AGENTS.md` cannot consent to it.
* ⛔ **never a silent fallback** — the health verdict is a live call. An install
  that could not verify says so and exits non-zero; it does not print a green line
  it did not earn.

CREDENTIALS: no key is ever read into this module's output, written to a config, or
printed. The bridge resolves it per request from the environment or the stores —
which is precisely why the entry written here carries none (`mcp_bridge`'s
docstring is #10605's post-mortem: a config that carries a credential is a CAPTURE
of an environment variable that the harness read once, at spawn).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import mcp_selfcheck

#: The hosted default the repo's own `.mcp.json` already documents. A caller that
#: self-hosts passes `--url`; the bridge takes over `api_url` from a stored
#: credential anyway (co-source, #2369 D1.1), so this value cannot aim a stored
#: key at a host of its choosing.
DEFAULT_MCP_URL = "https://api.premiselabs.co/mcp/"


def default_url() -> str:
    """The endpoint an install targets when the caller does not name one.

    The operator's own configuration WINS over the built-in hosted default. A box
    that sets `TORTOISE_MCP_URL` (a self-hosted or local endpoint) must not have
    the installer write the HOSTED default into its config: that is exactly how
    the entry and the environment came apart — `onboard` wrote the hosted default
    while `mcp_selfcheck`'s probe graded the shell's endpoint, so the verification
    reported a working install for a config that pointed somewhere else. The
    probe now grades the ENTRY (`mcp_selfcheck.declared_endpoint`), so the two
    must AGREE at write time rather than only at verify time.
    """
    configured = (os.environ.get("TORTOISE_MCP_URL") or "").strip()
    return configured or DEFAULT_MCP_URL

#: The interpreter a written entry names. `python3` (PATH), NOT
#: `sys.executable`: a config that is COMMITTED carries this value to every
#: machine that clones the repo, and an absolute path into the installer's own
#: virtualenv dangles everywhere else — #9475's lesson, one layer up. A box whose
#: `python3` cannot import tortoise is caught by `mcp_selfcheck`'s
#: `python-lacks-tortoise` class rather than by a silent spawn failure.
DEFAULT_PYTHON = "python3"

#: Which instruction file each harness reads. Verified per harness in the
#: `tortoise-onboarding` skill; `AGENTS.md` is the cross-harness convention and
#: the fallback for a harness with no dedicated file.
INSTRUCTION_FILES: dict[str, str] = {
    "pi": "AGENTS.md",
    "claude": "CLAUDE.md",
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
    "cursor": "AGENTS.md",
}

BEGIN_MARK = "<!-- tortoise:mcp-first:begin v1 -->"
END_MARK = "<!-- tortoise:mcp-first:end -->"

#: `--harness` values that are ALIASES of a canonical harness. `claude-code` is
#: advertised by the CLI and resolves the same config/instruction files as
#: `claude`, but `CAPTURE_SEAM` has no `claude-code` key — so looking the seam up
#: with the raw value refused a seam that is exactly the right artifact
#: ("no capture/recall seam for 'claude-code'"), installed no hooks, and still
#: exited 0. Normalising ONCE here keeps the alias from being a special case at
#: every lookup.
HARNESS_ALIASES: dict[str, str] = {"claude-code": "claude"}


def canonical_harness(harness: str) -> str:
    """The harness key the seam/version tables are keyed by."""
    return HARNESS_ALIASES.get(harness, harness)

#: THE TWO SENTENCES (#14607 part B), plus the one command that makes them
#: actionable. Wording is the issue's own; the fix line is the AX admission test
#: ("if the third part cannot be written, the surface must not refuse").
INSTRUCTIONS = f"""{BEGIN_MARK}
## Tortoise: reads go through the MCP

Reads go through the Tortoise MCP (`tortoise_*` tools). Never hand-roll HTTP to
`/v1/…` and never query the graph directly.

If the `tortoise_*` tools are absent, or the server reports not-loaded, that is a
**product defect**: stop, report it, and fix the MCP — do not silently work around
it. One command names the reason:

```bash
tortoise mcp-status
```
{END_MARK}
"""


@dataclass
class InstallResult:
    harness: str
    wrote: list[str] = field(default_factory=list)      # files actually written
    planned: list[str] = field(default_factory=list)    # files that would change
    diffs: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)   # things NOT done, and why
    actions: list[str] = field(default_factory=list)    # writes that LANDED (#10241)
    verdict: mcp_selfcheck.Verdict | None = None

    @property
    def ok(self) -> bool:
        return bool(self.verdict and self.verdict.ok)


# ── The credential-free entry ─────────────────────────────────────────────

def bridge_entry(*, url: str | None = None,
                 python: str | None = None) -> dict:
    """The harness entry for the bridge: no credential, no `${VAR}`, nothing stale.

    `python` defaults to `python3` on PATH, NOT to the installer's own
    interpreter: the entry is written into a config that may be COMMITTED, and an
    absolute path into one machine's virtualenv dangles on every other (#9475).
    A box whose `python3` cannot import tortoise is reported as its own class by
    `mcp_selfcheck`, so the portability choice never becomes a silent spawn
    failure.
    """
    return {
        "type": "stdio",
        "command": python or DEFAULT_PYTHON,
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": url or default_url()},
        "_comment": (
            "Registered by `tortoise onboard`. Deliberately carries NO "
            "credential: the bridge resolves the key on every request from "
            "TORTOISE_API_KEY / ./.tortoise / ~/.tortoise/credentials.json "
            "(#10605 — a credential in a config is a capture of an env var the "
            "harness read once, at spawn). Diagnose with `tortoise mcp-status`."
        ),
    }


# ── Config merge (idempotent, diffs shown, foreign entries preserved) ─────

def _config_path(harness: str, project_dir: Path, *,
                 scope: str = "project") -> Path | None:
    """The config file to WRITE, for the requested scope.

    Scope is explicit rather than inferred from whichever file happens to exist,
    because "write the one that is there" is how an installer silently edits a
    user's GLOBAL config while the command was run inside a project — a change
    that is neither reviewable in a diff nor scoped to the repository the user
    asked about.

    ⛔ THE PROMISE IS "WITHIN THE PROJECT", NOT "INSIDE `project_dir`". Inside a git
    work tree `project` resolves to the layer the HARNESS loads, which is the
    repo-root file — so an install run from `<repo>/tortoise` edits `<repo>/.mcp.json`
    and `<repo>/AGENTS.md` on purpose (a nested file would shadow it). Outside a
    work tree it never escapes `project_dir` (see `mcp_selfcheck.write_layer`):
    `cd ~/scratch && tortoise onboard --harness pi` used to write `~/AGENTS.md`
    while reporting project scope.
    """
    # ⛔ RESOLVE FIRST: the CLI's default is the RELATIVE `Path(".")`, and
    # `Path(".").parents == []`, so every walk below stayed in the cwd and the
    # installer wrote `<cwd>/.mcp.json` — a nested file that SHADOWS the repo root
    # for every session started below it. `read_entry` resolves, so installer and
    # verifier disagreed about which file is the repo layer.
    project_dir = Path(project_dir).resolve()
    rels = mcp_selfcheck.HARNESS_CONFIG_PATHS.get(harness)
    if not rels:
        return None
    project_rels = [r for r in rels if not r.startswith("~")]
    user_rels = [r for r in rels if r.startswith("~")]
    if scope == "user":
        for rel in user_rels:
            path = Path(rel.replace("~", str(Path.home())))
            if path.exists():
                return path
        return (Path(user_rels[0].replace("~", str(Path.home())))
                if user_rels else None)
    if not project_rels:
        # ⛔ A HARNESS WITH NO PROJECT LAYER HAS NO `project` SCOPE. There is no file
        # to name, and inventing `<repo>/.mcp.json` made the TOML refusal tell a
        # Codex user to paste a `[mcp_servers.tortoise]` table into a file Codex
        # never reads, while claiming it was "TOML" (`--scope` defaults to project).
        # `write_config` refuses TOML harnesses by their REAL path before this; `None`
        # is the honest answer for any other caller.
        return None
    for rel in project_rels:
        # ⛔ Resolve the project layer the SAME way `read_entry` does. Writing
        # `<project_dir>/<rel>` while the verifier walks UP to the git top-level
        # created a NEW nested `.mcp.json` that then SHADOWED the repo-root config
        # for every session started below it (hiding that file's other servers),
        # and the verification agreed because it read back the file the installer
        # had just made. Measured from `<repo>/tortoise`: wrote
        # `<repo>/tortoise/.mcp.json` while the root one already existed.
        found = mcp_selfcheck.write_layer(project_dir, rel)
        if found is not None and found.exists():
            return found
    root = mcp_selfcheck._repo_root(project_dir) or project_dir
    return root / (project_rels[0] if project_rels else ".mcp.json")


_JSON_WS = " \t\n\r"


def _read_text(path: Path) -> str:
    """Read `path` WITHOUT newline translation, so `\\r\\n` survives the round trip.

    ⛔ `Path.read_text` opens in universal-newline mode, so every `\\r\\n` became
    `\\n` BEFORE the merge and the diff were computed: a CRLF config then landed
    all-LF while the diff shown for approval was a one-member change with no `\\r`
    in it (measured: 6 `\\r` in, 0 out, one `-` line). That is the
    reformat-the-user-did-not-ask-for class this module exists to prevent, hidden by
    the very diff the installer shows to justify it.
    """
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_text(path: Path, text: str) -> None:
    """Write `text` with `newline=""`, so an existing `\\r\\n` is not translated out."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _newline_of(text: str) -> str:
    """The file's OWN line terminator: `\\r\\n` when it uses one, else `\\n`.

    Used for the lines this installer INSERTS, so a CRLF config does not end up
    with LF members (the same reformat, one line at a time).
    """
    return "\r\n" if "\r\n" in text else "\n"


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in _JSON_WS:
        i += 1
    return i


def _member_span(text: str, key: str, start: int = 0) -> tuple[int, int] | None:
    r"""`(value_start, value_end)` of `key` in the JSON OBJECT that begins at `start`.

    ⛔ NESTING-AWARE BY CONSTRUCTION, and that is the fix: the previous
    implementation was `re.search(rf'"{name}"\s*:\s*', text)`, which matched the
    FIRST occurrence **anywhere** in the document. A file whose `profiles` object
    held a `"tortoise"` key had that unrelated object overwritten with the server
    entry, the entry never reached `mcpServers`, and `write_config` reported the
    file as updated — two silent consequences reported as success, reachable on
    an advertised path (`--scope user` for `claude` targets `~/.claude.json`,
    whose `projects` map holds arbitrary per-project state).

    Each value is consumed by `json.JSONDecoder.raw_decode`, which skips a whole
    object/array, so a `"key"` inside a nested object can never be mistaken for
    this object's member. Returns None when the object has no such member, or
    when the text at `start` is not a JSON object.
    """
    decoder = json.JSONDecoder()
    i = _skip_ws(text, start)
    if i >= len(text) or text[i] != "{":
        return None
    i = _skip_ws(text, i + 1)
    while i < len(text) and text[i] == '"':
        try:
            name, i = decoder.raw_decode(text, i)
        except ValueError:
            return None
        if not isinstance(name, str):
            return None
        i = _skip_ws(text, i)
        if i >= len(text) or text[i] != ":":
            return None
        i = _skip_ws(text, i + 1)
        value_start = i
        try:
            _value, value_end = decoder.raw_decode(text, i)
        except ValueError:
            return None
        if name == key:
            return value_start, value_end
        i = _skip_ws(text, value_end)
        if i < len(text) and text[i] == ",":
            i = _skip_ws(text, i + 1)
            continue
        return None
    return None


def _splice_entry(text: str, name: str, entry: dict) -> str:
    """Replace ONE entry's value in the JSON TEXT, preserving every other byte.

    Why text surgery instead of `json.dumps(whole document)`: a round trip
    reformats what the installer was not asked to touch (this repo's own
    `.mcp.json` mixes `\\u2014`-escaped and literal em-dashes by hand), so a
    one-entry change would arrive as a whole-file rewrite — the opposite of the
    "merge, never overwrite" promise, and unreviewable in a diff.

    The entry is located by walking the document's STRUCTURE
    (`mcpServers` → `name`), never by text search, so an identically-named key in
    an unrelated object can never be the thing that gets rewritten.

    ⛔ BOTH shapes are spliced, because the FIRST INSTALL is the common path: when
    `name` is absent from an existing `mcpServers` object, the member is INSERTED
    into it (comma + the same indentation the file already uses) rather than the
    caller re-dumping the document — measured before this: a config holding another
    server came back with its `\\u2014` escapes expanded and its objects re-flowed,
    which is exactly the reformat this function exists to prevent. An EMPTY
    `mcpServers` object is spliced too, without a leading comma.

    LINE ENDINGS ARE THE FILE'S OWN: `\r\n` is never translated (the text comes
    from `_read_text`) and every line this function INSERTS uses the terminator the
    document already uses, so a CRLF config stays CRLF.

    Returns the edited text, or "" when there is no spliceable **TOP-LEVEL**
    `mcpServers` object: the key is absent, it exists only NESTED inside another
    object (`~/.claude.json`'s `projects.<path>.mcpServers` is the real shape), or
    its FIRST occurrence is not a JSON object (`json.loads` keeps the LAST duplicate,
    this walker reads the FIRST). ⛔ It does NOT police the round trip — `write_config`
    re-parses what will be written and refuses there; this function never sees the
    caller's intended document. ⛔ NO member/brace LAYOUT is refused — the closing
    brace sharing the last member's line is spliced like any other (an earlier cut
    of this docstring listed it as a bail-out, which sent that common hand-written
    shape through a whole-document dump). The caller then falls back to a full dump,
    and SAYS so.
    """
    servers = _member_span(text, "mcpServers")
    if servers is None:
        return ""
    if servers[0] >= len(text) or text[servers[0]] != "{":
        return ""
    span = _member_span(text, name, servers[0])
    if span is None:
        return _insert_member(text, name, entry, servers[0], servers[1])
    start, end = span
    nl = _newline_of(text)
    # Match the indentation of the value being replaced: everything after the
    # first line of the dumped entry shifts right by the original indent.
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    indent = indent[:len(indent) - len(indent.lstrip())]
    dumped = json.dumps(entry, indent=2, ensure_ascii=False)
    body = dumped.replace("\n", nl + indent)
    return text[:start] + body + text[end:]


def _insert_member(text: str, name: str, entry: dict,
                   obj_start: int, obj_end: int) -> str:
    """Add `name: entry` to the JSON object spanning `[obj_start, obj_end)`.

    Every byte outside that object is returned unchanged, and the member's
    indentation is taken from the file (the object's own line indent + two
    spaces) rather than hard-coded, so a 4-space or tab-preferring config keeps
    its own style. An object written INLINE (`{"other": …}`) gets an inline
    member, because inserting a multi-line value there would reformat the line it
    shares. Inserted lines use the document's OWN terminator (`_newline_of`).

    ⛔ This function NEVER returns "" — the caller has already proved the object is
    a JSON object, so every member/brace LAYOUT is spliced, including the closing
    brace sharing the last member's line (an earlier cut returned "" for that
    layout, which sent the common hand-written shape through a whole-document
    re-dump).
    """
    nl = _newline_of(text)
    close = obj_end - 1                       # the object's own `}`
    inner = text[obj_start + 1:close]
    one_line = "\n" not in inner
    dumped = json.dumps(entry, indent=2, ensure_ascii=False)
    if one_line:
        compact = json.dumps(entry, ensure_ascii=False)
        member = f'"{name}": {compact}'
        if not inner.strip():
            return text[:obj_start + 1] + member + text[close:]
        return text[:close].rstrip() + ", " + member + text[close:]
    # ── multi-line object: find the line holding the closing brace, insert ABOVE it
    line_start = text.rfind("\n", 0, close) + 1
    obj_line_start = text.rfind("\n", 0, obj_start) + 1
    base = text[obj_line_start:obj_start]
    base = base[:len(base) - len(base.lstrip())]
    # The file's OWN member indent wins when it already has a member (a 4-space or
    # tab-preferring config must not receive a 2-space member); the object's line
    # indent + two spaces is the fallback for an object that is empty.
    own = re.search(r'\n([ \t]*)"', text[obj_start:close])
    member_indent = own.group(1) if own else base + "  "
    body = dumped.replace("\n", nl + member_indent)
    line = f'{member_indent}"{name}": {body}'
    if not inner.strip():
        return text[:line_start] + line + nl + text[line_start:]
    if text[line_start:close].strip():
        # ⛔ The closing brace SHARES its line with the last member's value — the
        # common hand-written shape (`"other": {"a": 1}}`), and the shape an
        # earlier cut of this function REFUSED, which re-dumped the whole document
        # over a layout it simply did not anticipate. Insert AT the brace instead:
        # the comma lands after that value and the new member starts its own line.
        return text[:close] + "," + nl + line + nl + text[close:]
    return (text[:line_start].rstrip("\n").rstrip() + "," + nl + line + nl
            + text[line_start:])


def _toml_table(entry: dict) -> str:
    """The equivalent `[mcp_servers.tortoise]` table, for a harness that reads TOML.

    Offered instead of written: this installer does not own the TOML format, so
    the honest output is the table to paste — not a JSON document in a `.toml`
    file that the harness silently fails to parse.
    """
    args = ", ".join(json.dumps(str(a)) for a in entry.get("args") or [])
    lines = [
        "[mcp_servers.tortoise]",
        f"command = {json.dumps(str(entry.get('command', DEFAULT_PYTHON)))}",
        f"args = [{args}]",
    ]
    env = entry.get("env") or {}
    if isinstance(env, dict) and env:
        lines.append("")
        lines.append("[mcp_servers.tortoise.env]")
        for key, value in env.items():
            lines.append(f"{key} = {json.dumps(str(value))}")
    return "\n".join(lines)


def write_config(harness: str, project_dir: Path, *, url: str | None = None,
                 dry_run: bool = False, python: str | None = None,
                 scope: str = "project") -> InstallResult:
    """Merge the bridge entry into the harness config — never overwrite the file.

    Other servers, other keys and unknown fields are preserved verbatim: this is a
    merge, and a merge that reformats a user's unrelated config is a change they
    did not ask for.
    """
    out = InstallResult(harness=harness)
    entry = bridge_entry(url=url, python=python)
    if harness in mcp_selfcheck._TOML_HARNESSES:
        # ⛔ FORMAT IS CHECKED BEFORE ANY PATH IS RESOLVED, AND THE PATH NAMED IS THE
        # ONE THE HARNESS READS. This used to run after `_config_path`, so a Codex
        # install at the DEFAULT scope reported `<repo>/.mcp.json` — three false
        # statements in one refusal (that file is not TOML, it is not Codex's
        # config, and Codex never reads it) and the reader was told to paste a TOML
        # table into it. `--scope project` is not a Codex concept: `config.toml` is
        # the harness's single global file.
        rels = mcp_selfcheck.HARNESS_CONFIG_PATHS.get(harness, ())
        real = next((Path(r.replace("~", str(Path.home())))
                     for r in rels if r.startswith("~")), None)
        out.refusals.append(
            f"{harness}: this installer writes JSON configs only — {harness} reads "
            f"TOML ({real if real else 'its own TOML config'}), and a JSON document "
            f"there would leave {harness} unable to parse the file while the "
            f"install reported success. NOTHING was written"
            + (" — and `--scope project` is not a " + harness
               + " concept; it has one global config, not one per project"
               if real is not None else "")
            + f". Add this table to {real if real else 'that file'} by hand:\n"
            + _toml_table(entry))
        return out
    path = _config_path(harness, project_dir, scope=scope)
    if path is None:
        out.refusals.append(f"{harness}: no known config path — nothing written")
        return out
    if path.suffix.lower() == ".toml":
        # ⛔ FORMAT IS CHECKED BEFORE CONTENT. This path used to write a JSON
        # document into `~/.codex/config.toml`, which Codex cannot parse — so the
        # install did nothing while `wrote:` reported the file updated — and, in
        # the other direction, told an existing Codex user their valid TOML was
        # "not valid JSON". Writing the wrong format and calling it written is
        # the silent-fallback class this module exists to remove, so the answer
        # is a NAMED refusal plus the table to paste, never a write.
        out.refusals.append(
            f"{harness}: this installer writes JSON configs only — {path} is "
            f"TOML, and a JSON document there would leave {harness} unable to "
            f"parse the file while the install reported success. NOTHING was "
            f"written. Add this table to {path} by hand:\n"
            + _toml_table(entry))
        return out
    before = ""
    data: dict = {}
    if path.exists():
        try:
            before = _read_text(path)
            data = json.loads(before)
        except (OSError, ValueError) as exc:
            # A config we cannot parse is REFUSED, not clobbered — rewriting it
            # would delete servers we never read.
            out.refusals.append(
                f"{path}: not valid JSON ({type(exc).__name__}) — refusing to "
                f"rewrite it; fix the file (or move it aside) and re-run")
            return out
    if not isinstance(data, dict):
        out.refusals.append(f"{path}: expected a JSON object at the top level")
        return out
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        out.refusals.append(f"{path}: `mcpServers` is not an object")
        return out
    existing = servers.get(mcp_selfcheck.SERVER_NAME)
    if existing == entry:
        out.planned.append(str(path))
        return out                      # idempotent: byte-identical, no write
    servers[mcp_selfcheck.SERVER_NAME] = entry
    spliced = _splice_entry(before, mcp_selfcheck.SERVER_NAME, entry) \
        if before else ""
    if spliced:
        after = spliced
    else:
        # No spliceable TOP-LEVEL `mcpServers` object: the key is absent, it exists
        # only NESTED (the `~/.claude.json` shape, whose servers live under
        # `projects.<path>.mcpServers`), or its FIRST occurrence is not a JSON object.
        # Dump, and SAY that the whole file was written rather than implying a
        # one-entry merge. ⛔ A file that merely LACKS the entry is NOT this branch —
        # `_splice_entry` inserts into an existing object — and ⛔ neither is a LAYOUT:
        # every member/brace shape, including the closing brace sharing the last
        # member's line, is spliced. ⛔ The parenthetical says TOP-LEVEL and FIRST
        # because the walker starts at the document root and `json.loads` keeps the
        # LAST duplicate: neither "the file has none" nor a bare "`mcpServers` is not
        # an object" is true of every shape that lands here.
        nl = _newline_of(before)
        after = json.dumps(data, indent=2, ensure_ascii=False).replace("\n", nl) + nl
        if before:
            out.actions.append(
                f"{path}: no `mcpServers` object this installer can splice "
                f"(the file has no TOP-LEVEL `mcpServers` key, or its first one is "
                f"not an object) — the whole document was re-written")
    # ⛔ RE-PARSE WHAT WILL BE WRITTEN and refuse unless it is the document this
    # function intended. Splicing is byte surgery, so a duplicate key, a
    # structural surprise or a stale offset could otherwise land a file whose
    # parsed content differs from `data` — with `wrote:` still reporting success.
    try:
        rewritten = json.loads(after)
    except ValueError as exc:
        out.refusals.append(
            f"{path}: the merged document did not re-parse "
            f"({type(exc).__name__}) — NOTHING was written; the file is "
            f"unchanged. Re-run with the file moved aside to write a fresh one")
        return out
    if rewritten != data:
        out.refusals.append(
            f"{path}: the merged text does not round-trip to the intended "
            f"document (it would write something other than the entry this "
            f"installer built) — NOTHING was written; the file is unchanged. "
            f"Re-run with the file moved aside to write a fresh one")
        return out
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path.name}", tofile=f"b/{path.name}"))
    out.diffs.append(diff)
    out.planned.append(str(path))
    if dry_run:
        return out
    if before and not _git_tracks(path):
        # A backup ONLY for a file git is not already keeping: in a repository
        # the previous content is in the history this very diff is committed to,
        # and a `*.bak` left in a checkout is litter the next `git status` shows
        # to someone who never asked for it (measured: this installer left one).
        backup = path.with_suffix(path.suffix + ".bak-14607")
        try:
            # ⛔ A USER-scope config is 0600 because it carries key material
            # (`env`, prompt history). `write_text` creates at `0666 & ~umask`, so
            # a measured backup of a `0600 ~/.claude.json` landed at **0644** with
            # the secrets inside — the installer widening the permissions of a file
            # it was only reading. Copy the SOURCE mode, and create exclusively so
            # an existing backup is never silently overwritten.
            mode = stat.S_IMODE(path.stat().st_mode)
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(before)
            os.chmod(backup, mode)
            out.actions.append(
                f"{path}: previous content saved to {backup.name} (mode "
                f"{oct(mode)}) — delete it yourself once the install looks right")
        except FileExistsError:
            out.actions.append(
                f"{path}: a backup already exists at {backup.name} — left as is")
        except OSError as exc:
            # Refuse rather than write a backup we could not protect: a 0644 copy
            # of a 0600 config is worse than no backup at all.
            out.refusals.append(
                f"{path}: could not create a protected backup ({exc}) — the config "
                f"was NOT rewritten; back it up yourself and re-run")
            return out
    _write_text(path, after)
    out.wrote.append(str(path))
    return out


def _git_tracks(path: Path) -> bool:
    """Is `path` tracked by git? Unknown ⇒ False (write the backup, be safe)."""
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path.name)],
            cwd=str(path.parent), capture_output=True, text=True, timeout=10,
            check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# ── Instruction file (the two sentences), delimiter-marked + reversible ───

#: How far a CLOSING fence may be indented beyond its opener, in COLUMNS.
#: CommonMark: "the closing fence may be indented up to three spaces". Read by
#: `_fence_scan`'s LENIENT pass only — see the two-pass note there for why the
#: strict reading is tried first.
CLOSER_ALLOWANCE_COLUMNS = 3


def _indent_columns(line: str) -> int:
    """The line's leading whitespace width in COLUMNS, not characters.

    ⛔ NOT `len(line) - len(line.lstrip(" \t"))`. That counts a TAB as ONE, so a
    tab-indented opener (1 character, 4 columns) compared against a 4-space closer
    (4 characters, 4 columns) read as "deeper than its opener" and a legal
    CommonMark closer was refused. The tab stop is 4, which is CommonMark's own.
    """
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4 - (width % 4)
        else:
            break
    return width


def _fence_scan(text: str) -> tuple[list[tuple[int, int]], int | None]:
    """Fenced spans, plus the 1-based line an UNCLOSED fence opened on (or None).

    ⛔ A FENCED EXAMPLE OF THE BLOCK IS NOT THE BLOCK. The marker scan was a plain
    `text.count(...)` and `_strip_block` removed from the first BEGIN to the next
    END, so an instruction file that DOCUMENTS the block — a fenced example, which
    is how a repo explains what the installer appends — read as well-formed and was
    EDITED: measured, `--uninstall` left the fence's body empty and exited 0 with no
    refusal. The sibling installer (`install-tortoise-skills.sh`) skips every
    CommonMark fence for the count AND the rewrite; this is the same rule, so the
    two cannot disagree about what a marker means.

    ⛔ ANY INDENT IS A FENCE HERE, and that is deliberate. `indent <= 3` is what
    CommonMark requires for a fence at TOP level, but the same three backticks
    inside a list item are a fence at 4+ spaces — and an indented documentation
    example is exactly how a repo writes one down. Excluding it made the classifier
    disagree with CommonMark and with the user: measured, `--uninstall` DELETED the
    body of a 4-space-indented example and exited 0 with no refusal. The markers are
    literal text in both cases; erring toward "this is documentation, leave it" is
    the only direction that cannot destroy text.

    ⛔ AN UNCLOSED FENCE IS NOT "FENCED TO EOF". That reading hid every real marker
    below it, so `_marker_pair` saw (0, 0) — "nothing to do" — and
    `write_instructions` APPENDED one more copy of the block per run, inside the
    open fence, where `--uninstall` could then never remove it: measured, three runs
    produced three BEGINS and the removal still wrote the file while deleting
    nothing. The sibling installer learned the same lesson (`fenceopen` → *"An
    UNCLOSED fence would hide every real marker below it, and 'no markers' means
    APPEND — one more copy per run"*). So the tail is NOT silently treated as
    fenced: the caller REFUSES by name, which costs a clear error instead of an
    unreversible file. The returned line number is what the refusal names.
    """
    spans, open_line = _fence_scan_pass(text, allowance=0)
    # ⛔ STRICT IS PREFERRED, BUT ITS "CLOSING" IS NOT EVIDENCE THAT IT IS RIGHT.
    # Two independent ways strict goes wrong, and each is measured:
    #
    #   * it leaves a fence OPEN, because a closer indented +1/+2/+3 beyond a TOP-LEVEL
    #     opener is LEGAL (`_closer_ceiling`) and strict refuses it. Then both writers
    #     hard-refused a file they had handled correctly — `onboard` and `--uninstall`
    #     each printed `an unclosed code fence opened on line N` on a file whose only
    #     fault was a legal `+2` closer;
    #   * it CLOSES, wrongly: given a ```md fence whose legal closer is at `+2` and the
    #     installer-appended block below it, strict skips the legal closer and pairs the
    #     opener with the BLOCK's own closing fence, swallowing the block — `start
    #     markers: 0 / end markers: 1`, so `--uninstall` refused a file it had written.
    #     That is the asymmetry test below: the fence reader consults the LENIENT reading
    #     when the strict one leaves the marker pair MALFORMED.
    #
    # ⛔ WHAT MUST NOT MOVE: the nested-example decision. A documentation example whose
    # fence is indented +1..+4 inside an outer one leaves strict's counts SYMMETRIC
    # (`0 BEGIN / 0 END` — the markers are fenced, i.e. documentation), so strict is kept
    # and `--uninstall` never reads the example as the real block.
    #
    # ⛔ THE TRIGGER, STATED ACCURATELY (a review measured this and the earlier wording
    # denied it): it is NOT "strict left it malformed, full stop". The code below takes
    # the lenient reading when strict leaves the pair MALFORMED **or when the lenient
    # reading's own counts are BALANCED** — i.e. it prefers the SELF-CONSISTENT reading.
    # The nested-example decision survives because strict is symmetric there (`0/0`), so
    # the first branch already returned and lenient is never consulted; that is a
    # property of that fixture, not of the trigger.
    #
    # ⛔ KNOWN RESIDUAL, recorded rather than implied (2 of 200,000 randomly generated
    # documents, both containing U+2028 or `\v` inside fence lines): a document where
    # STRICT is asymmetric and LENIENT is symmetric flips from REFUSAL to WRITE. Measured
    # on this head against the pre-`2249288c654c` reader. It needs exotic line separators
    # inside a fence AND a marker pair that only one reading sees, so no realistic
    # document reached it in 2,646 + 2,500 structured and 133 writer-level round trips;
    # the honest discriminator would be a design decision, and the obvious candidate
    # (`lenient spans ⊆ strict spans`) does NOT separate the two cases. Preferring
    # REFUSAL whenever the two readings disagree is the conservative fix and is NOT taken
    # here because it would refuse the legal-`+2`-closer file this change exists to stop
    # refusing. Do not read the sentence above as "the lenient branch cannot lose text".
    if open_line is None and _marker_counts_balanced(text, spans):
        return spans, None
    lenient_spans, lenient_open = _fence_scan_pass(text, allowance=CLOSER_ALLOWANCE_COLUMNS)
    if lenient_open is None and (open_line is not None
                                 or _marker_counts_balanced(text, lenient_spans)):
        return lenient_spans, None
    if open_line is None:
        return spans, None
    return spans, open_line


def _marker_counts_balanced(text: str, spans: list[tuple[int, int]]) -> bool:
    """Do BEGIN and END markers occur equally often OUTSIDE ``spans``?

    The signals `_fence_scan` uses to decide whether the STRICT reading is trustworthy:
    a reading that leaves 0 BEGIN / 1 END has demonstrably paired the opener with the
    wrong fence (see `_fence_scan`). A reading that leaves 0/0 is the nested-example
    case and is deliberately NOT treated as evidence against strict.
    """
    begins = ends = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if not any(start <= offset < end for start, end in spans):
            if _is_marker_line(line, BEGIN_MARK):
                begins += 1
            elif _is_marker_line(line, END_MARK):
                ends += 1
        offset += len(line)
    return begins == ends


def _closer_ceiling(open_indent: int, allowance: int) -> int:
    """The deepest indent a CLOSING fence may carry — measured from its CONTAINER.

    ⛔ NOT `open_indent + allowance` for every opener, which was a text-destruction
    regression on the first attempt at the lenient reading. CommonMark: *"the closing
    code fence may be preceded by up to three spaces of indentation"* and *"this is not
    a closing fence, because it is indented 4 spaces"* — the three spaces are relative to
    the CONTAINING BLOCK, not to the opener. So a TOP-level fence (opener at 0..3) closes
    at most at column `allowance`, and only a deeper opener — a fence inside a list item,
    where the container itself is indented — gets `open_indent + allowance`.

    Measured cost of getting it wrong: an opener at 2 accepted a closer at 5, so a
    4-space CONTENT fence of a documentation example became the "closer", the example's
    markers fell OUTSIDE a fence, `_markers_well_formed` flipped to `(True, "")`, and
    `--uninstall` deleted the example's body with exit 0 and NO refusal — on a shape the
    pre-change code refused.
    """
    if open_indent <= allowance:
        return allowance
    return open_indent + allowance


def _fence_scan_pass(
    text: str, *, allowance: int,
) -> tuple[list[tuple[int, int]], int | None]:
    """One scan of `text`; `allowance` is how far a closer may out-indent its opener.

    Split out of `_fence_scan` only so the strict/lenient pair can share one loop —
    the two differ in that single comparison and nothing else, so they cannot drift.
    """
    spans: list[tuple[int, int]] = []
    open_char: str | None = None
    open_len = 0
    open_at = 0
    open_line = 0
    open_indent = 0
    offset = 0
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        indent = _indent_columns(line)
        stripped = line.lstrip(" \t")
        candidate = stripped[:1]
        run = 0
        while run < len(stripped) and stripped[run] == candidate:
            run += 1
        is_fence = candidate in ("`", "~") and run >= 3
        if open_char is None:
            if is_fence:
                open_char, open_len, open_at, open_line = candidate, run, offset, number
                open_indent = indent
        elif (is_fence and candidate == open_char and run >= open_len
              and indent <= _closer_ceiling(open_indent, allowance)
              # ⛔ A CLOSER CARRIES NO INFO STRING (CommonMark). Without this clause
              # ` ```bash ` closes a backtick fence, so every marker below it reads as
              # OUTSIDE the fence and `--uninstall` deletes the user's own text with
              # exit 0 — measured: a doc whose closing example line was ` ```bash `
              # lost `user's own line — never delete this` and reported no refusal.
              # The commonest real trigger is an example that shows a LANGUAGED fence
              # while being closed by an unlabelled one, which is exactly what
              # documentation about fenced blocks writes down.
              and not stripped[run:].strip()):
            spans.append((open_at, offset + len(line)))
            open_char = None
        offset += len(line)
    if open_char is not None:
        return spans, open_line
    return spans, None


def _fence_spans(text: str) -> list[tuple[int, int]]:
    """CLOSED fenced spans only — see `_fence_scan` for the unclosed case."""
    spans, _unclosed = _fence_scan(text)
    return spans


def _outside_fences(text: str) -> str:
    """`text` with every CLOSED fenced span blanked, preserving offsets and lines.

    Blanking (rather than deleting) keeps a character offset in the result equal to
    the offset in the original. An unclosed fence is NOT blanked to EOF — see
    `_fence_scan`; callers refuse that case instead of guessing.
    """
    spans = _fence_spans(text)
    if not spans:
        return text
    out = list(text)
    for start, end in spans:
        for index in range(start, min(end, len(out))):
            if out[index] != "\n":
                out[index] = " "
    return "".join(out)


def _is_marker_line(line: str, marker: str) -> bool:
    """Is this line the MARKER ITSELF, and nothing else?

    ⛔ NOT A SUBSTRING MATCH. `_strip_block` tested `marker in line`, so a prose
    line that merely MENTIONS a marker was removed as if it were the block, and a
    line carrying both markers set `inside` and never cleared it — measured:
    `"Our installer writes <BEGIN>" … "and <END> at the end."` lost both lines, and
    `"<BEGIN><END>"` on one line swallowed everything after it. The sibling
    installer matches the anchored form (nothing but whitespace/BOM around the
    marker); this does the same.
    """
    return line.strip().lstrip("\ufeff").strip() == marker


def _marker_pair(text: str) -> tuple[int, int]:
    """How many BEGIN and END markers a file carries OUTSIDE fenced examples.

    ⛔ THE PAIRING IS THE SAFETY PROPERTY. `_strip_block` removes from a BEGIN to
    the next END, so a file carrying a BEGIN with NO END makes that removal swallow
    everything the user wrote after it — measured: an instruction file whose block
    had been hand-trimmed took the append branch on install (BEGIN count 2), and
    `--uninstall` then left `'# House rules\n'`, deleting `My own later notes: never
    delete this.` Reversibility is advertised as "removes exactly the marked
    block"; a malformed pair must therefore be REFUSED, never guessed at.

    Fenced spans are excluded here and in `_strip_block` (`_fence_spans`), so a
    documented example of the block is neither counted nor removed.
    """
    scan = _outside_fences(text)
    return (sum(1 for line in scan.splitlines() if _is_marker_line(line, BEGIN_MARK)),
            sum(1 for line in scan.splitlines() if _is_marker_line(line, END_MARK)))


def _markers_well_formed(text: str) -> tuple[bool, str]:
    """True when the file carries exactly ONE BEGIN before exactly ONE END."""
    _spans, unclosed = _fence_scan(text)
    if unclosed is not None:
        # Refuse rather than guess: an open fence hides whatever follows it, and
        # "no markers" means APPEND — which would write the block where no later run
        # can remove it (see `_fence_scan`).
        return False, (f"an unclosed code fence opened on line {unclosed} — close it "
                       f"(or delete the stray fence) so the markers can be read")
    begins, ends = _marker_pair(text)
    if begins == 0 and ends == 0:
        return True, ""
    if begins != 1 or ends != 1:
        return False, (f"start markers: {begins} / end markers: {ends} — the "
                       f"marked block is malformed")
    scan = _outside_fences(text)
    first_begin = next(index for index, line in enumerate(scan.splitlines())
                       if _is_marker_line(line, BEGIN_MARK))
    first_end = next(index for index, line in enumerate(scan.splitlines())
                     if _is_marker_line(line, END_MARK))
    if first_begin > first_end:
        return False, "the END marker appears BEFORE the BEGIN marker"
    return True, ""


def _strip_block(text: str) -> str:
    """Remove the marked block, leaving the rest of the file untouched.

    Assumes the pair has been validated by `_markers_well_formed`; callers must
    refuse a malformed file rather than silently deleting the text between a
    dangling BEGIN and some later END. A fenced EXAMPLE of the block is left alone
    — it is documentation, not ours to remove — and a marker must be ALONE on its
    line (`_is_marker_line`) rather than mentioned in prose.

    ⛔ DECIDED BY OFFSET, NOT BY A PARALLEL LINE LIST. This used to walk `text`'s
    lines and compare each against the SAME INDEX of
    `_outside_fences(text).splitlines()` — two decompositions assumed to be the same
    length. `_outside_fences` blanks a fenced span character by character, keeping
    only `\n`, but `str.splitlines()` also breaks on `\v`, `\f`, `\x1c`-`\x1e`,
    `\x85`, `\u2028` and `\u2029`. So ONE such character INSIDE a fence removes a
    line from the blanked view, every index after it shifts by one, and the marker
    test then fires on the WRONG line: measured on the pre-fix code, `--uninstall`
    over a file whose fenced example carried a `\u2028` DELETED the user's own
    "never delete this" line, kept the block's body, and left the block's END
    behind — user text destroyed outside the block, and the residue makes every
    later run REFUSE. A fenced span is a character range, so the fence question is
    asked about the line's OFFSET and the two decompositions can never disagree.
    """
    spans = _fence_spans(text)
    out: list[str] = []
    inside = False
    offset = 0
    for line in text.splitlines(keepends=True):
        fenced = any(start <= offset < end for start, end in spans)
        if not fenced and _is_marker_line(line, BEGIN_MARK):
            inside = True
        elif not fenced and _is_marker_line(line, END_MARK):
            inside = False
        elif not inside:
            out.append(line)
        offset += len(line)
    # Collapse the blank run the removal may leave at the tail, with the document's
    # OWN terminator (a CRLF file must not come back with an LF tail).
    stripped = "".join(out)
    return stripped.rstrip("\r\n") + _newline_of(text) if stripped else ""


def _instruction_path(project_dir: Path, name: str) -> Path:
    """The instruction file to write, resolved the way `read_entry` resolves config.

    ⛔ Writing `<project_dir>/<name>` while the CONFIG is resolved up to the git
    top-level split the install in two: measured from `<repo>/tortoise`, the config
    landed at `<repo>/.mcp.json` (correct) but the two sentences created a NEW
    `<repo>/tortoise/AGENTS.md`, and `--uninstall` run from `<repo>` then reported
    "nothing to remove". The agent in that subdirectory reads the new file; the one
    at the root never sees the sentences.

    ⛔ But never an ANCESTOR outside the project: `_instruction_path` delegates to
    `write_layer`, not `_project_layer`, so outside a git work tree the file stays
    in `project_dir` (a write following the READ walk put the block in
    `/tmp/ancestor/AGENTS.md`, i.e. a reachable `~/AGENTS.md`).
    """
    project_dir = Path(project_dir).resolve()   # see `_config_path`
    return (mcp_selfcheck.write_layer(project_dir, name)
            or (mcp_selfcheck._repo_root(project_dir) or project_dir) / name)


def write_instructions(harness: str, project_dir: Path, *,
                       dry_run: bool = False) -> InstallResult:
    """Write the two sentences idempotently; show the diff; add nothing twice."""
    out = InstallResult(harness=harness)
    name = INSTRUCTION_FILES.get(harness)
    if name is None:
        out.refusals.append(
            f"{harness}: no instruction file known — the two sentences were NOT "
            f"written; add them to this harness's instruction file by hand")
        return out
    path = _instruction_path(project_dir, name)
    # ⛔ THE SAME READ, THE SAME HAZARD: universal newlines flattened a CRLF
    # instruction file to LF BEFORE the diff was computed, so installing two
    # sentences rewrote every line ending of the user's own AGENTS.md and the diff
    # showed only the appended block. `_read_text` keeps the file's bytes, and the
    # block this function appends uses the file's own terminator.
    before = _read_text(path) if path.exists() else ""
    nl = _newline_of(before)
    block = INSTRUCTIONS if nl == "\n" else INSTRUCTIONS.replace("\n", nl)
    ok, why = _markers_well_formed(before)
    if not ok:
        out.refusals.append(
            f"{path}: {why}. The two sentences were NOT written — finish or remove "
            f"the partial `{BEGIN_MARK}` / `{END_MARK}` pair by hand first, so the "
            f"block stays reversible")
        return out
    if _marker_pair(before) == (1, 1):
        # ⛔ `_marker_pair`, NOT a substring test. The well-formed check above already
        # guarantees the pair is (0, 0) or (1, 1) OUTSIDE fences, so the only question
        # left is whether OUR block exists — and `BEGIN_MARK in before` answers a
        # different one: a repo that DOCUMENTS the block in a fenced example carries
        # both strings without carrying the block (`_is_marker_line` is the rule, and
        # this is the same branch decision, one layer up).
        # HONESTLY: because the gate above admits only (0, 0) or (1, 1) and
        # `_strip_block` is a NO-OP on a (0, 0) file, this branch and the substring
        # test are OBSERVATIONALLY IDENTICAL today (measured: the mutant survives the
        # whole suite). It is kept as DEFENCE IN DEPTH — if `_strip_block` ever stopped
        # being a no-op on a (0, 0) file, the substring test would silently rewrite a
        # fenced example. The protection itself comes from `_strip_block`'s idempotence,
        # not from this predicate; do not read this line as the guard.
        # Idempotent: the block is STRIPPED and RE-APPENDED at the end of the file,
        # preserving everything else around it — ⛔ NOT "in place": the user's own
        # text keeps its bytes but not its position relative to the block (measured: a
        # block sitting mid-file comes back below the user's trailing lines).
        # ⛔ Edits the user made INSIDE the block are NOT preserved — the block is
        # ours and is rewritten whole. (The previous comment claimed otherwise; the
        # `v1` tag in `BEGIN_MARK` is the seam for changing what we own — bump it and
        # the old block is no longer matched, so both blocks show up in the diff
        # instead of one silently rewriting the other.)
        after_body = _strip_block(before)
        after = (after_body.rstrip("\r\n") + nl + nl + block
                 if after_body.strip() else block)
    else:
        after = (before.rstrip("\r\n") + nl + nl + block
                 if before.strip() else block)
    # ⛔ THE APPEND MUST NOT REPAIR THE FILE'S FENCES. Measured P0: a file that only
    # DOCUMENTS the block (` ``` `/`   ``` `/` ``` `/BEGIN/sentinel/` ``` `) reads
    # `_marker_pair == (0, 0)` — the BEGIN is fenced, i.e. documentation — so we take
    # the append branch. Appending our own fences RE-PAIRS the file, so the SAME file
    # now reads `(1, 1)` with the USER's documented BEGIN as the block's BEGIN. The
    # next run strips from that BEGIN to our END and the user's own prose is gone, with
    # no refusal and exit 0 (192 of the 41,619 `(0, 0)` documents an exhaustive 4-6 line
    # enumeration produced, all plain backticks — no exotic separator needed).
    # The installer must not change what a later run REMOVES; if it would, refuse.
    if _strip_block(after) != _strip_block(before):
        out.refusals.append(
            f"{path}: writing the block would change what a later run removes "
            f"(appending it re-pairs the file's fences) — the two sentences were "
            f"NOT written, because a later `--uninstall` would then take your own "
            f"text with it")
        return out
    if after == before:
        out.planned.append(str(path))
        return out
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))
    out.diffs.append(diff)
    out.planned.append(str(path))
    if dry_run:
        return out
    _write_text(path, after)
    out.wrote.append(str(path))
    return out


def remove_instructions(harness: str, project_dir: Path, *,
                       dry_run: bool = False) -> InstallResult:
    """The reversal: remove ONLY the marked block (never a user's own text)."""
    out = InstallResult(harness=harness)
    name = INSTRUCTION_FILES.get(harness)
    if name is None:
        out.refusals.append(
            f"{harness}: no instruction file known — nothing was removed")
        return out
    path = _instruction_path(project_dir, name)
    if not path.exists():
        out.planned.append(str(path))
        return out
    before = _read_text(path)
    ok, why = _markers_well_formed(before)
    if not ok:
        out.refusals.append(
            f"uninstall: {path}: {why} — REFUSING to remove, because a malformed "
            f"pair would take your own text with it. Remove the block by hand")
        return out
    # ⛔ `_marker_pair`, NOT `BEGIN_MARK not in before`. A marker inside a fenced
    # example is DOCUMENTATION, so counting it here took a documentation-only file
    # down the removal path: `_strip_block` then removed nothing, an empty diff was
    # recorded, the file was written back byte-identical, and `onboard` printed
    # NOTHING — because "nothing to remove" is only reported when `not diffs`.
    # Measured before this fix: `--uninstall` over a repo that documents the block
    # exited 0 in silence. The user could not tell it from a failed removal.
    if _marker_pair(before)[0] == 0:
        out.planned.append(str(path))    # nothing of ours is there
        return out
    after = _strip_block(before)
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))
    out.diffs.append(diff)
    out.planned.append(str(path))
    if not dry_run:
        _write_text(path, after)
        out.wrote.append(str(path))
    return out


# ── The one command ───────────────────────────────────────────────────────

def onboard(harness: str = "pi", *, project_dir: Path | None = None,
            url: str | None = None, dry_run: bool = False,
            verify_only: bool = False, live: bool = True,
            hooks: bool = True, instructions: bool = True,
            uninstall: bool = False, scope: str = "project") -> InstallResult:
    """Register → verify → hooks → skill → instructions → ONE health verdict.

    ORDER IS LOAD-BEARING: verify BEFORE writing the instructions, because writing
    "reads go through the MCP" into an agent's instructions while the MCP is down
    would instruct the agent to use a door that does not open — the exact silent
    fallback the sentences exist to end.
    """
    project_dir = Path(project_dir or Path.cwd()).resolve()
    out = InstallResult(harness=harness)

    if uninstall:
        # ⛔ `_absorb`, not `.refusals` alone. The reversal EDITS THE USER'S
        # `AGENTS.md`, so its diff is the consent artifact (#14263's first AX
        # rule) — forwarding only the refusals printed NOTHING while the file
        # changed and the command exited 0.
        removed = remove_instructions(harness, project_dir, dry_run=dry_run)
        _absorb(out, removed)
        if not removed.diffs and not removed.refusals:
            out.actions.append(
                f"{harness}: no tortoise block in "
                f"{INSTRUCTION_FILES.get(harness, 'the instruction file')} — "
                f"nothing to remove")
        return out

    if verify_only:
        out.verdict = mcp_selfcheck.check(
            harness, project_dir=project_dir, live=live)
        return out

    # 1 · the entry
    cfg = write_config(harness, project_dir, url=url, dry_run=dry_run, scope=scope)
    _absorb(out, cfg)

    # 2 · the seam (capture + per-turn recall hooks — what makes recall automatic
    #     rather than remembered)
    if hooks:
        try:
            from .capture_install import CAPTURE_SEAM, install_capture
        except Exception as exc:
            out.refusals.append(
                f"hooks: capture installer unavailable ({type(exc).__name__}); "
                f"recall/retain are NOT automatic — re-run after fixing that")
        else:
            if canonical_harness(harness) not in CAPTURE_SEAM:
                out.refusals.append(
                    f"hooks: no capture/recall seam for {harness!r} (known: "
                    f"{', '.join(sorted(CAPTURE_SEAM))}) — recall will not be "
                    f"automatic, and this installer does not invent one")
            else:
                res = install_capture(canonical_harness(harness), root=project_dir,
                                      dry_run=dry_run)
                # `error` and `actions` are BOTH rendered (#10241): a write that
                # landed before a failure has already changed what the harness
                # loads, so reporting only FAILED would hide it.
                out.actions.extend(res.actions)
                if res.error:
                    out.refusals.append(f"hooks: {res.error}")
                elif res.changed:
                    out.wrote.append(
                        f"hooks: {harness} capture + per-turn recall seam"
                        + (" (planned)" if dry_run else ""))

    # 3 · verify (a live call, unless the caller opted out)
    out.verdict = mcp_selfcheck.check(harness, project_dir=project_dir, live=live
                                      and not dry_run)

    # 4 · the two sentences — only once the door is proven open. In a DRY RUN the
    #     diff is still computed and displayed (nothing is written), because "what
    #     would you change in my instruction file" is the question a dry run is
    #     run to answer; the refusal states it is shown, not written.
    if instructions:
        if out.verdict.ok:
            ins = write_instructions(harness, project_dir, dry_run=dry_run)
            _absorb(out, ins)
        elif dry_run:
            ins = write_instructions(harness, project_dir, dry_run=True)
            _absorb(out, ins)
            out.refusals.append(
                "instructions: SHOWN ONLY — a real run will not write them until "
                f"the MCP verifies (currently {out.verdict.status}); writing "
                "`reads go through the MCP` while it is down would instruct the "
                "agent to use a door that does not open")
        else:
            out.refusals.append(
                "instructions: NOT written — the MCP did not verify "
                f"({out.verdict.status}); writing `reads go through the MCP` "
                "while it is down would instruct the agent to use a door that "
                "does not open")
    return out


def _absorb(target: InstallResult, other: InstallResult) -> None:
    target.wrote.extend(other.wrote)
    target.planned.extend(other.planned)
    target.diffs.extend(other.diffs)
    target.refusals.extend(other.refusals)
    # `actions` belong here too: `write_config` records the backup it took and a
    # whole-document rewrite there, and dropping them meant the two writes a user
    # most needs to see were the two that never rendered (#10241).
    target.actions.extend(other.actions)


def render(result: InstallResult, *, out=None) -> None:
    """Print what changed (as a diff), what was not done (and why), and ONE line."""
    stream = out or sys.stdout
    for diff in result.diffs:
        if diff:
            print(diff, file=stream)
    for action in result.actions:
        print(f"  · {action}", file=stream)
    for refusal in result.refusals:
        print(f"  ⛔ {refusal}", file=stream)
    if result.verdict is not None:
        print(result.verdict.line(), file=stream)
