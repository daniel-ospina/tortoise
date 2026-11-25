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

    Returns the edited text, or "" when `name` cannot be located under
    `mcpServers` (the caller then falls back to a full dump, and SAYS so).
    """
    servers = _member_span(text, "mcpServers")
    if servers is None:
        return ""
    span = _member_span(text, name, servers[0])
    if span is None:
        return ""
    start, end = span
    # Match the indentation of the value being replaced: everything after the
    # first line of the dumped entry shifts right by the original indent.
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    indent = indent[:len(indent) - len(indent.lstrip())]
    dumped = json.dumps(entry, indent=2, ensure_ascii=False)
    body = dumped.replace("\n", "\n" + indent)
    return text[:start] + body + text[end:]


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
            before = path.read_text(encoding="utf-8")
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
        # No existing entry to splice (a fresh file): dump, and SAY that the
        # whole file was written rather than implying a one-entry merge.
        after = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if before:
            out.actions.append(
                f"{path}: the `{mcp_selfcheck.SERVER_NAME}` entry could not be "
                f"located in the file's text — the whole document was re-written")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after, encoding="utf-8")
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
    spans: list[tuple[int, int]] = []
    open_char: str | None = None
    open_len = 0
    open_at = 0
    open_line = 0
    open_indent = 0
    offset = 0
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        candidate = stripped[:1]
        run = 0
        while run < len(stripped) and stripped[run] == candidate:
            run += 1
        is_fence = candidate in ("`", "~") and run >= 3
        if open_char is None:
            if is_fence:
                open_char, open_len, open_at, open_line = candidate, run, offset, number
                open_indent = indent
        # ⛔ A CLOSER MAY NOT BE INDENTED MORE THAN ITS OPENER. The relaxation that
        # reads an indented fence as a fence (so a doc example in a list item is
        # left alone) must NOT apply to the closer as well: then a fence nested
        # INSIDE an outer one closes the OUTER one early, the markers shown in the
        # example are read as the real block, and `--uninstall` deletes the
        # example's body — measured, exit 0 with NO refusal, on a doc that fences
        # the block inside a list item. Erring toward "this is documentation, leave
        # it" is the only direction that cannot destroy text.
        elif (is_fence and candidate == open_char and run >= open_len
              and indent <= open_indent):
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
    # Collapse the blank run the removal may leave at the tail.
    return "".join(out).rstrip("\n") + "\n" if out else ""


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
    before = path.read_text(encoding="utf-8") if path.exists() else ""
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
        # Idempotent: replace the block in place, preserving everything around it.
        # ⛔ Edits the user made INSIDE the block are NOT preserved — the block is
        # ours and is rewritten whole. (The previous comment claimed otherwise; the
        # `v1` tag in `BEGIN_MARK` is the seam for changing what we own — bump it and
        # the old block is no longer matched, so both blocks show up in the diff
        # instead of one silently rewriting the other.)
        after_body = _strip_block(before)
        after = (after_body.rstrip("\n") + "\n\n" + INSTRUCTIONS
                 if after_body.strip() else INSTRUCTIONS)
    else:
        after = (before.rstrip("\n") + "\n\n" + INSTRUCTIONS
                 if before.strip() else INSTRUCTIONS)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after, encoding="utf-8")
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
    before = path.read_text(encoding="utf-8")
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
        path.write_text(after, encoding="utf-8")
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
