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
| compose (per **service** — one service arming does not arm its neighbour) | ``docker compose -f <file> --profile '*' --env-file /dev/null config --format json`` |
| Fly manifest | ``fly config show --local --config <file>`` when ``flyctl`` is available, else ``tomllib`` on the manifest's ``[env]`` table |
| image build file (``Dockerfile*``) | a strict ``ENV`` reader with a build-**stage** model (no external resolver exists): the shipped image is the LAST stage, its ``ENV`` layered over its parent chain |
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
their problem, so let's not worry about it"*.

⚠️ **The shell reader's control-flow model — the residual, and its closure (review at
`762f1c56d7`, `8aa35e5d56`; measured).** ``_shell_assignments`` was LINE-SHAPED: any indented
``export K=V`` counted as a statement, so a switch exported inside a function that is never called,
inside an ``if false`` branch, or after a top-level ``exit`` was credited as ``STATED`` — and the
report then printed ``ok entrypoint.sh TORTOISE_VALUE_GATE=1`` for a value the process never
receives (``entrypoint.sh`` carries ten top-level functions, so "move the export into the helper"
is an ordinary edit rather than a contrived one). It now tracks block depth (``if``/``then``/
``while``/``until``/``for``/``case``/``select``/``do`` and ``{`` bodies, with their closers)
plus a top-level ``exit``/``exec`` seen, and routes an assignment in any of those positions to the
existing ``ambiguous`` verdict — the fail-closed one, whose message says the shape was not read as
a statement. Two properties are deliberate: the model is SMALL, so an unmodelled form lands on
``ambiguous`` rather than a pass (a false ``STATED`` is the direction that lets a config through);
and a CONDITIONAL arm is refused, because a branch is not the shipped configuration stating a
value — ``entrypoint.sh``'s own switch exports are top level. ⛔ "Conditional" is not only
``if …; then``: the same branch is spelled ``&&``/``||`` (``[ -n "$MAYBE" ] && export K=1``,
``true || export K=1``) and its sibling is the BACKGROUND operator (``export K=1 &``, which exports
inside a subshell) — all three were measured fail-OPEN, because the statement splitter cut
``&&``/``||`` into ordinary statements and lost the guard, so a never-run export was credited and
the gate exited 0 over a process that receives nothing. The splitter now carries the separator and
the reader routes those to ``ambiguous`` too; ``;``-separated statements stay creditable, which is
why the split exists. There is no widening for an honest shorthand: ``=0`` still passes by design.

⛔ **Block keywords are matched against a STRUCTURAL view, in which every span the reader does not
evaluate is BLANKED whole** — quoted and ANSI-C strings, comments, command substitutions
(``$( … )``, backticks), subshell/``(`` bodies and heredoc bodies — with span state carried ACROSS
lines, and a ``case`` arm's ``)`` never closing a substitution. A block keyword inside such a span
is DATA (``echo "n/a; fi"``, ``$(case $M in off) … esac)``, ``$'don\'t; fi'``): reading it as
structure was a measured fail-OPEN class, five shapes deep, each of which credited a
never-running branch's ``export`` and exited 0. An ESCAPED character outside a span is data too,
so ``echo \"n/a\"`` cannot open a span and refuse an honest file. One ``export`` line may state
SEVERAL switches (``export A=1 B=2``); its words are tokenised on unquoted whitespace and shell
metacharacters, so the value each channel reports is what the process actually receives.

⛔ **Heredoc bodies are DATA, and are DROPPED before the depth walk** (review P1). An
``export K=V`` inside a ``cat <<EOF … EOF`` body is not a statement the shell runs, so the body is
removed (``_shell_code_lines``) rather than credited at whatever depth it sits at — scanning it was
a measured fail-open, because a ``fi`` at line start *inside* the body closed the enclosing block.
The verdict for a file whose only arm is that shape is therefore ``does not state <switch>``, not
the ``mentions …`` wording. Two bounds, named rather than left silent: an opener with no terminator
swallows the REST of the file (incomplete shell — fail-CLOSED, the channel is then refused), and the
terminator is compared the way the shell compares it (leading TABS stripped for ``<<-`` only, the
line otherwise EXACT), which is stricter than the sibling Dockerfile reader's ``_heredoc_end``.
The OPENER is located in the structural view, so a ``<<`` inside a string, a comment or an
arithmetic span is not one (a ``<<<`` here-string is excluded by the pattern), while every real
spelling — ``<<EOF``, ``<<'EOF'``, ``<<\\EOF``, ``<<-EOF`` — opens a body.

``.env.example`` is a template nothing deploys, so it is not a channel of this gate; the
read-only instrument (``tools/dod_5090_probe.py``) is what scores it.

⚠️ **Where it runs, and why there (review round 7).** As its own step in ``manifest-integrity`` — a
leg that is NOT in the required aggregate's ``DEFERRABLE_LEGS``, so its red is the PR's red. That
placement IS the fix for the bound this check first carried: it grew out of the compose parse step
in ``test-concurrency-falkor``, which is deferrable, and ``ci-introduced-failure`` excuses a
deferrable leg when every failing id in its log is main's own — a *gate-step* failure emits no
test-id at all, so where that leg's pytest step also redded for a main-owned reason the aggregate
could read green over an unrefused omission. Three constraints shaped the wiring, each a property
of this tree rather than a preference: the step is SEPARATE (that job's own pin refuses a shell
operator on its ``--integrity`` command, ``#2656``/``#5373``, and rightly); ``python-ci.yml`` keeps
its LINE COUNT, because it is cited by line number in ~20 pinned places, so the two lines the step
adds are reclaimed from that job's own header comment — reworded, no claim dropped; and the switch
names come from the gates' own modules, which import on a bare interpreter, because that job
installs only ``pyyaml`` and nothing here may need the installed package.

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
#: A build-stage boundary. `ENV` is STAGE-SCOPED: only the FINAL stage (and the stages it inherits
#: from) reach the image that ships — crediting a builder stage would read a two-stage file as
#: STATED while the final image says nothing (review P2). The stage NAME is captured so
#: `FROM builder` can inherit; Docker matches it case-insensitively (verified: `FROM scratch AS
#: Builder` + `FROM builder` builds). ⛔ A stage INDEX is NOT a `FROM` form — `FROM 0` asks for the
#: image `docker.io/library/0` and buildkit refuses it (measured) — indexes exist only on flags
#: such as `COPY --from=0`, which this reader does not model.
_FROM_INSTRUCTION = re.compile(
    r"(?i)^\s*FROM\s+(?:--\S+\s+)*(?P<image>\S+)(?:\s+AS\s+(?P<name>\S+))?"
)
#: The body of an `ENV` line that is NOTHING BUT `K=V` pairs (quotes honoured). Anything else is
#: not tiled, so it is never parsed as a set of assignments.
_ENV_PAIRS = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S*)\s*)+$")
#: ⛔ `export` IS NOT OPTIONAL (review P2 at `648842c31a`, measured). A bare `K=V` in an
#: entrypoint script never reaches the process: `entrypoint.sh` ends in `exec "$@"` (:1151), and a
#: non-exported shell variable is not inherited. Crediting the bare form would print `ok
#: entrypoint.sh TORTOISE_EMBED_GATE=1` for a value uvicorn never sees — the false PASS this gate
#: exists to prevent — so only an `export`ed assignment counts as a statement. Measured: with the
#: optional group, `TORTOISE_EMBED_GATE=1` alone read `stated={'TORTOISE_EMBED_GATE': '1'}`.
#: One `export` argument list — `export` followed by its words. A SINGLE `export` may state
#: several assignments (`export A=1 B=2`), so the words are tokenised rather than taking the whole
#: remainder as one value: the single-assignment regex refused that ordinary shell as not stating
#: the second switch while REPORTING `1 B=2` as the first switch's value (review P2, measured).
_SHELL_EXPORT = re.compile(r"^\s*export\s+(?P<body>\S.*)$")
#: A shell VARIABLE name: what may sit left of the `=` in an `export` word.
_SHELL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHELL_EXPORT_NAME = re.compile(r"^\s*export\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*$")

#: Shell keywords that open a block whose body may never run at top level (`if`/`then`,
#: `while`/`until`/`for`/`case`/`select`/`do`, and a `{` body — `name() {` is the brace), and
#: the closers that end one. An assignment inside a block is a CONDITIONAL, not a statement the
#: shipped configuration makes; a top-level `exit`/`exec` ends what the rest of the file can
#: contribute (`exec` replaces the process). The model is deliberately small: an unmodelled form
#: lands on `ambiguous`, never on a pass, because a false STATED is the direction that lets a
#: config through.
#: ⛔ `then`/`else`/`elif` are NEITHER an opener nor a closer: they sit INSIDE an `if`, and
#: treating them as closers dropped the depth at the `then` line so the body after it read as top
#: level — the exact fail-open this model exists to close, on the most ordinary multi-line
#: `if`/`then`/`fi` in the language (review at `69ee7b1395`). Closers are `fi`/`done`/`esac` and a
#: closing brace/paren only; openers are a block keyword that STARTS A STATEMENT — at line start,
#: after a `;`/`&&`/`||`/`|` separator, or after the `then`/`else`/`elif`/`do` that begins the next
#: statement of an enclosing block (an INDENTED `  if false; then` is the shape that was missed) —
#: plus a `{`/`(` tail. ⛔ The `then` form is not decoration (review P1, measured): without it the
#: SECOND `if` on `if false; then if false; then echo x; fi` was invisible, so that line opened one
#: block and closed one and the depth returned to 0 while bash was still one level deep — both
#: exports of a never-running outer branch were credited STATED and the gate exited 0.
#: ⛔ `do` IS NOT AN OPENER (review P2, measured): it is the CONTINUATION of a loop header, and the
#: header itself (`while`/`until`/`for`/`select`) already counts. Counting both made a loop written
#: the ordinary two-line way (`while …` then `do` on its own line) net +2 against one `done`, so
#: every later top-level `export` read as conditional and an HONEST entrypoint was REFUSED — with a
#: message blaming the value (`does not state a value this gate can read`) instead of the depth.
#: Measured on this repo's own `entrypoint.sh`: with `do` counted, one line
#: (`while IFS= read -r line || [ -n "$line" ]; do`) matches TWICE and the file balances 100
#: openers to 91 closers, so the depth never returns to 0; without it, 91 to 91.
_SHELL_OPENERS = re.compile(
    # ⛔ A leading CONTROL WORD does not change what the keyword opens (review P2, measured).
    # `! if false; then export K=1; fi` and `time if …` are valid shell (`bash -n` accepts both)
    # and are the same never-running body this reader refuses — but the anchor demanded the
    # keyword immediately after the separator, so the block never opened, its body read at depth
    # 0, and both exports were credited (gate `ok`, exit 0, real process: no switch at all).
    r"(?:^|[;&|])\s*(?:(?:!|time)\s+)*(?:(?:then|else|elif|do)\s+)?"
    r"(?:if|while|until|for|case|select)\b"
    # ⛔ `{` is an opener WHEREVER it appears at a word start, not only at end of statement
    # (review P1, measured): a one-line function body (`f() { :; export K=1; }`) put the brace
    # mid-statement, so it never opened, the body ran at depth 0, and an export the process never
    # receives was credited `STATED` (exit 0). The preceding-character class is what keeps the
    # OTHER `{`s out: `${VAR}` has a `$` before it (parameter expansion, not a block) and brace
    # expansion (`a{b,c}`) has a word character before it, as does a `{` inside an arithmetic or
    # quoted span — which is blanked before this runs anyway.
    #
    # ⛔ There is no `(` arm here (review P3, measured): a trailing `(` used to have one, but this
    # pattern only ever runs on the STRUCTURAL view, where `_SPAN_SUBSHELL` has already blanked
    # every `(` — so the arm could never fire (`_shell_structure('f() (')` → `'f    '`,
    # `findall` → `[]`, against `['(']` on the raw line). A trailing `(` that opens anything is a
    # subshell, and the span already models it; keeping a dead arm only made the comment claim a
    # capability the regex did not have.
    r""
)
#: ⛔ A FUNCTION BODY is not a GROUP, and only the first opens a scope (review P1, BOTH
#: directions measured 2026-11-16). `f() { :; export K=1; }` must fail closed: the body never runs.
#: But `{ export K=1; }` is a compound group that runs in the CURRENT shell, so its `export` DOES
#: reach the process — treating every `{` as an opener REFUSED an honest entrypoint (measured: the
#: gate printed both switches MISSING while `env -i … bash script /usr/bin/env` printed both).
#: The two are told apart the only way a reader can: a function body's `{` follows `)`, or the word
#: `function NAME`. Everything else is a group and does NOT change the depth — which keeps the
#: fail-closed direction (a body is refused) while un-refusing the mirror (a group is credited).
#: ⛔ The brace arm cannot stay in `_SHELL_OPENERS`: that pattern only ever runs on the STRUCTURAL
#: view, where `f()`'s `()` are blanked, so the `)` that identifies a body is invisible there.
#: ⛔ It also covers BRACE EXPANSION, which a peer round landed a narrower guard for:
#: `mkdir -p {logs,cache}` has a `{` followed by a list character, opens no block, and leaking
#: the depth up there REFUSED an honest entrypoint while the message blamed its VALUE rather
#: than the depth. Under this rule NO `{` outside a function body changes the depth, so an
#: expansion, a group and a `${VAR}` are all out — one rule instead of a lookahead that has to
#: enumerate every non-block spelling.
_SHELL_FN_BRACE = re.compile(r"\)\s*\{|\bfunction\s+[A-Za-z_][A-Za-z0-9_.-]*\s*\{")


def _function_body_braces(raw: str, view: str) -> int:
    """How many of ``view``'s CODE braces open a FUNCTION BODY (and so a new scope).

    Matched on the RAW statement and confirmed against ``view``, because `_shell_structure` blanks
    the `()` of `f()` — the `)` that identifies a body exists only in the raw text, while a `{`
    inside a quote/comment/substitution is blanked in the view and must never count.
    """
    count = 0
    for match in _SHELL_FN_BRACE.finditer(raw):
        position = match.end() - 1
        if position < len(view) and view[position] == "{":
            count += 1
    return count
#: ⛔ A closer may follow a STATEMENT SEPARATOR on the same line (review P1, measured). The model
#: was line-anchored, so a one-line `if [ -n "$X" ]; then echo hi; fi` opened a block and never
#: closed it — the depth then leaked UP and any later top-level `export` was read as nested and
#: refused. The repo's own `entrypoint.sh` carries nine such one-liners, so an honest `=1` file
#: was one appended export from a red. A brace closer may also carry a redirect (`} >&2`).
_SHELL_CLOSERS = re.compile(
    r"(?:^|[;&|])\s*(?:fi|done|esac)\b|^\s*\}\s*(?:[0-9]*>&[0-9]+)?\s*$|^\s*\)\s*$"
)
#: A heredoc opener: `<<WORD`, `<<-WORD`, and every QUOTING spelling of that word (`<<'WORD'`,
#: `<<"WORD"`, `<<\WORD`, `<<$'WORD'`). Its BODY is data, not shell (review P1, measured):
#: scanning the body let a `fi`/`done`/`esac` at line start inside it close the ENCLOSING block, so
#: an `export` in a branch that never runs was credited `STATED` and the gate exited 0 over an
#: unarmed process. `(?<!<)` keeps `<<<` (here-string) out.
#:
#: ⛔ **The delimiter is a bash WORD, not an identifier (review P1, measured).** This matched
#: `[A-Za-z_][A-Za-z0-9_]*`, so every delimiter bash accepts and this did not — `<<'END-OF-DATA'`,
#: `<<E.O.F`, `<<'EO\F'`, `<<$'EOF'` — left the opener unrecognised and its BODY was read as SHELL:
#: measured on `cat > /tmp/notes.txt <<'END-OF-DATA'` whose body carried a `fi` and two `export`s —
#: the gate printed `ok … =1 =1` (exit 0) while `env -i … bash script /usr/bin/env` printed no
#: `TORTOISE_*` at all and the body was written out verbatim, proving bash took it as DATA.
#: The word is matched loosely here and its QUOTING removed by `_heredoc_delimiter`, because the
#: terminator is compared against the unquoted word, exactly as bash does. ⛔ A delimiter is NOT
#: parameter-expanded (`<<$FOO` wants the literal `$FOO` — measured), which is why `$` is allowed
#: inside the word rather than making those openers "unmodelled".
_SHELL_HEREDOC = re.compile(
    r"(?<!<)<<-?\s*(?P<delim>(?:[^\s;&|<>()'\"\\]|\\.|'[^']*'|\"[^\"]*\"|\$'[^']*')+)"
)
#: Every `<<`/`<<-` that is not a HERE-STRING — the population the opener regex above must cover
#: entirely. A `<<` left over after matching (a form this reader cannot model) STOPS the read, the
#: same fail-CLOSED direction as an unterminated body: crediting nothing from the rest of the file
#: refuses the channel rather than passing it on a body nobody executes.
_SHELL_HEREDOC_START = re.compile(r"(?<!<)<<(?!<)")
_SHELL_TERMINATES = re.compile(r"^\s*(?:exit|exec)\b")
#: Two ways a LATER line takes an earlier `export K=1` back, both of which must drop the literal
#: from `stated` — Docker and the shell are last-assignment-wins, and `stated` outranks `ambiguous`
#: in `switch_problem`, so a kept literal reports STATED for a value the process never receives
#: (review at `69ee7b1395`): a bare `K=` (never exported), and `unset K` (removed from the
#: environment).
_SHELL_UNEXPORTED = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
#: ⛔ A TRAILING WORD must not defeat a retraction (review P1, measured). These two anchored on
#: `[\w\s-]+\s*$`, so ANY trailing token — a redirect, a quoted name — matched none of the three
#: retraction arms and the statement fell to the mention scan, leaving the earlier literal in
#: `stated` (which `switch_problem` PREFERS): measured `unset TORTOISE_EMBED_GATE 2>/dev/null`,
#: `unset K >/dev/null`, `unset "K"` and `export -n K 2>/dev/null` each printed `ok … =1` while
#: `env -i … bash script /usr/bin/env` showed the switch absent. `2>/dev/null` is the IDIOMATIC
#: spelling precisely because the name may be absent, so this was a realistic disarm, not a
#: contrived one. The tail is tolerated the same way `_export_tokens` already tolerates it
#: (`export A=1;B=2` states two assignments) — the two readers are symmetric again.
#: Names are unquoted afterwards (`unset "K"`), because quoting a NAME is not a different statement.
_SHELL_UNSET = re.compile(
    r"^\s*unset(?:\s+-[A-Za-z]+)*\s+(?P<names>[^<>|;&]+?)\s*(?:[<>|;&].*)?$"
)
#: ⛔ A THIRD way a later line takes an earlier `export K=1` back (review P3, measured):
#: `export -n K` removes K from the exported environment WITHOUT unsetting it, so `exec "$@"`
#: passes nothing on. `entrypoint.sh` is `#!/bin/bash`, where `-n` means exactly that; the exported
#: literal survived and the gate printed `ok … TORTOISE_EMBED_GATE=1` over a process that receives
#: no value at all (`env -i … bash script` printed `NOT IN ENVIRONMENT`).
_SHELL_EXPORT_UNEXPORT = re.compile(
    r"^\s*export\s+-n\s+(?P<names>[^<>|;&]+?)\s*(?:[<>|;&].*)?$"
)
#: ⛔ A BUILTIN can take a switch back without the `export` spelling this reader credits (review
#: P2, measured): `declare -x K=` after `export K=1` leaves K EMPTY and still exported (bash prints
#: `K=`), while the gate kept the earlier `=1` and printed `ok`. It is a RETRACTION (fail closed),
#: not a new spelling to credit: `declare`/`local` inside a function is local to it, `readonly`
#: can never be reassigned, and none of them is the shipped configuration stating a usable value.
_SHELL_BUILTIN_ASSIGN = re.compile(r"^\s*(?:declare|typeset|readonly|local|printf)\b(?P<rest>.*)$")
#: ⛔ A FOURTH way a later statement takes an earlier `export K=1` back (review P3, measured):
#: the statement is an `env` INVOCATION that strips the variable from the child's environment.
#: `export K=1; exec env -u K "$@"` leaves the process without K, and `env -i` strips everything —
#: both were reported `STATED` because the reader only modelled shell BUILTINS (`unset`,
#: `export -n`, a bare `K=`). `env VAR=1 cmd` (no `-u`/`-i`) states nothing and is left alone.
_SHELL_ENV_CALL = re.compile(r"^\s*(?:exec\s+)?(?:command\s+)?env\b(?P<args>.*)$")
_SHELL_ENV_UNSET = re.compile(
    r"(?:^|\s)(?:-u|--unset)(?:\s+|=)(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_SHELL_ENV_IGNORE = re.compile(
    # ⛔ the BARE `-` is bash's short spelling of `-i` — `exec env - "$@"` clears the environment
    # exactly as `-i` does, and it was the one shape that still slipped through (measured)
    r"(?:^|\s)(?:-i|--ignore-environment|-)(?=\s|$)"
)

#: A trailing `#` comment (idiomatic after a shell assignment) is NOT part of the value — leaving
#: it in made `export K=""  # off for now` read as a non-empty statement and skip the EMPTY-value
#: refusal (review round 6, P3). Cut only outside quotes and only where a comment can start.
def _strip_inline_comment(text: str) -> str:
    """``text`` without a trailing shell/YAML-style comment (quote-aware)."""
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#" and (index == 0 or text[index - 1].isspace()):
            return text[:index]
    return text


def _unquote(value: str) -> str:
    """``value`` without one layer of matching surrounding quotes."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _unquote_all(names: str) -> list[str]:
    """Whitespace-separated NAMEs, each unquoted.

    A retraction may spell its name quoted — `unset "TORTOISE_EMBED_GATE"` removes the same
    variable `unset TORTOISE_EMBED_GATE` does (measured), so the quoting is not a different
    statement and must not make the name unrecognisable.
    """
    return [_unquote(token) for token in names.split()]


#: A value that is NOTHING BUT a variable interpolation, with its optional operator captured.
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
    text = _unquote(_strip_inline_comment(value))
    match = _PURE_INTERPOLATION.match(text)
    if not match:
        return True  # a literal (a partial interpolation like `a-${X}` is literal enough)
    operator = match.group("op") or ""
    # ⛔ ONLY `-`/`:-` (default-when-UNSET) survives an unset variable. `${X:+1}`/`${X+1}` expand
    # to EMPTY exactly when X is unset — the shipped case — so accepting them was a fail-open in a
    # reader built to fail closed (review round 6, P3).
    if operator not in ("-", ":-"):
        return False
    fallback = _unquote((match.group("fallback") or "").strip())
    # ⛔ THE FALLBACK MUST ITSELF BE A LITERAL (review P1, measured). `bool(fallback)` only tested
    # that TEXT was present, so `${X:-$FALLBACK}` — a default that is ITSELF a variable (or a
    # command substitution) — was read as a non-empty literal fallback while the process resolves
    # EMPTY when both are unset. Measured: a script exporting `K="${K:-$FALLBACK}"` printed an
    # empty value to the process while the gate had already said `ok`, exit 0. This reader resolves
    # no interpolation, so an EMPTY fallback still states nothing and a `$`/backtick anywhere inside
    # it is refused; the fail-CLOSED direction is the only one a reader with no resolver may take.
    if not fallback:
        return False
    return not ("$" in fallback or "`" in fallback)

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
    #: Switches stated ONLY in a build stage the shipped image does not inherit: switch -> stage.
    #: A builder-stage `ENV` never reaches the process, so crediting it would be a false PASS
    #: (review P2 at `648842c31a`).
    shadowed: dict[str, str] = field(default_factory=dict)
    #: A channel-shaped entry that is NOT a deployment channel, with the reason (reported only).
    noted: str = ""
    #: True when the governor refuses the file on principle (a compose patch) rather than
    #: failing to read it — a different fact, so a different word in the report.
    refused: bool = False

    def switch_problem(self, switch: str) -> str:
        """The refusal for ``switch`` on this channel, or ``""`` when it is stated.

        Three shapes, and the ORDER is the point: a readable assignment WINS over a prose
        mention (a file that states the switch in an `ENV` line and also names it in a log
        message is fine), an empty value is a placeholder rather than an arm, a statement the
        SHIPPED image never receives is its own refusal (it names the build stage), and a mention
        with no readable assignment is a **failure** — a shape this reader does not model must
        never read as "not stated", which would be indistinguishable from a genuine omission.
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
        if switch in self.shadowed:
            return (
                f"{self.channel} states {switch} only in build stage "
                f"{self.shadowed[switch]!r}, which the shipped image does not inherit — a "
                f"builder-stage ENV never reaches the process ({self.resolver})"
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
            "shadowed": dict(self.shadowed),
            "noted": self.noted,
            "problems": self.problems(),
            "ok": self.ok,
        }


# ── channel readers ────────────────────────────────────────────────────────────────────────────


def _scrubbed_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}


def _join_logical(raw_lines: list[str]) -> list[str]:
    """``raw_lines`` as logical lines: backslash continuations joined, full-line comments dropped.

    Comment lines are dropped BEFORE joining because a comment inside a continuation is not
    part of the value (Docker and the shell both strip it) — leaving it in would make an
    innocent ``ENV A=1 \\`` + ``# note`` + ``B=2`` read as one unparseable blob.

    ⛔ **The SHELL reader applies this AFTER removing heredoc bodies, not before (review P2,
    measured).** Joining first let a BODY line's trailing backslash swallow the terminator: in a
    QUOTED heredoc bash does not continue, so it ends the body there and runs the statements after
    it, while the reader ate them — and REFUSED an honest entrypoint whose two switches do reach
    the process, with a message that blamed the value. One physical line at a time is the order the
    shell itself uses, so the body pass must see physical lines.
    """
    lines: list[str] = []
    current = ""
    for raw in raw_lines:
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


def _logical_lines(text: str) -> list[str]:
    """``text`` as logical lines (see `_join_logical`)."""
    return _join_logical(text.splitlines())


#: A heredoc opener on an instruction line — `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`.
#: ⛔ **The delimiter is a bash WORD here too (review P1, measured).** This matched
#: `[A-Za-z_][A-Za-z0-9_]*`, so every delimiter a shell accepts and this did not —
#: `<<END-OF-DATA`, `<<E.O.F`, `<<'E.O.F'` — left the opener unrecognised and its BODY was read as
#: INSTRUCTIONS. Measured on a Dockerfile whose only "ENV" lines were heredoc data:
#: `RUN cat > /tmp/notes.txt <<END-OF-DATA` / `ENV TORTOISE_VALUE_GATE=1` / `ENV …` / `END-OF-DATA`
#: → the gate printed `ok … =1 =1` (exit 0) while `sh` wrote those two lines OUT verbatim, so they
#: are text a program prints, not statements the image receives. The spelling table is the one
#: `_SHELL_HEREDOC` already pins (`END-OF-DATA`, `E.O.F`, `'E.O.F'`, `\EOF`); the two readers were
#: the last place still disagreeing, and `_heredoc_delimiter` (already shared) removes the quoting
#: so the terminator is compared against the unquoted word, exactly as a shell does.
_HEREDOC_OPEN = re.compile(
    r"(?<!<)<<(?P<dash>-?)\s*"
    r"(?P<word>(?:[^\s;&|<>()'\"\\]|\\.|'[^']*'|\"[^\"]*\"|\$'[^']*')+)"
)


def _heredoc_end(lines: list[str], opener: int, after: int) -> int:
    """``after``, or the index just past the heredoc body the line at ``opener`` opens.

    A heredoc body is DATA: `RUN cat <<EOF` followed by a line starting with `FROM` does not begin
    a build stage, and an `ENV …` line inside one is text a program may print, not a statement the
    image receives. Stepping over the body is what keeps both readings from inverting (measured:
    without this, the `FROM` example above made the SHIPPED stage unreadable and refused a correct
    file, naming a stage that does not exist).

    An opener with NO terminator later in the file is IGNORED rather than swallowing the rest of
    the file: a `<<` that is not a heredoc (a quoted string, a shell shift) must not turn every
    later instruction into body text.
    """
    pending = [
        (_heredoc_delimiter(match.group("word")), bool(match.group("dash")))
        for match in _HEREDOC_OPEN.finditer(lines[opener])
    ]
    if not pending:
        return after
    cursor = after
    while pending and cursor < len(lines):
        word, dash = pending[0]
        candidate = lines[cursor].lstrip("\t") if dash else lines[cursor]
        if candidate.rstrip("\r") == word:
            pending.pop(0)
        cursor += 1
    return after if pending else cursor


def _parent_stage(index: int, base: str, names: list[str]) -> int | None:
    """The EARLIER stage ``base`` names, or ``None`` when it is an external image.

    ``FROM builder`` inherits that stage's ``ENV``; anything else is a registry image and inherits
    nothing this file declares.
    """
    if not base:
        return None
    folded = base.lower()
    for prior in range(index):
        if names[prior] and names[prior].lower() == folded:
            return prior
    return None


def _shipped_env(
    names: list[str], bases: list[str], envs: list[dict[str, str]]
) -> tuple[dict[str, str], set[int]]:
    """The FINAL stage's effective ``ENV`` and the stage indexes it inherits from.

    The last stage in the file is the image that ships (a `--target` build is this reader's
    declared bound — no manifest in this tree names one). Its environment is its own ``ENV``
    lines over its parent chain's, so a statement in an ANCESTOR build stage is credited and one
    in a sibling builder stage is not.
    """
    inherited: set[int] = set()

    def resolve(index: int) -> dict[str, str]:
        effective: dict[str, str] = {}
        parent = _parent_stage(index, bases[index], names)
        if parent is not None and parent not in inherited:
            inherited.add(parent)
            effective.update(resolve(parent))
        effective.update(envs[index])
        return effective

    final = len(envs) - 1
    inherited.add(final)
    return resolve(final), inherited


def _dockerfile_env(text: str) -> tuple[dict[str, str], set[str], dict[str, str]]:
    """``ENV`` statements the SHIPPED image receives (last wins) + unreadable/ambiguous keys.

    Both Dockerfile forms are handled — ``ENV K=V K2=V2`` and the legacy ``ENV K V``. A line that
    names a switch in a shape neither form matches is reported as AMBIGUOUS rather than ignored,
    so a spelling this reader does not model fails the gate instead of passing it.

    ⛔ **Build stages are modelled, not flattened (review P2 at `648842c31a`, measured).** A
    ``Dockerfile`` is a SEQUENCE of images, and only the last one ships: a builder stage's
    ``ENV TORTOISE_EMBED_GATE=1`` never reaches the process, so crediting it was a false PASS one
    ``FROM … AS builder`` away. Three facts are separated here: what the final stage inherits (a
    stage's own ``ENV`` over its parent chain — ``FROM builder`` inherits), what it loses (a
    sibling/earlier stage's statement — reported as ``shadowed``, which REFUSES and names the
    stage, because a silent drop would read exactly like a genuine omission), and what was never a
    statement at all (``ambiguous``).

    ⚠️ **A heredoc BODY is data** (see ``_heredoc_end``): `RUN cat <<EOF` followed by a line that
    starts with ``FROM`` is text, and an ``ENV`` line inside the body is text too. Bounded, not
    modelled: a stage selected only through `docker build --target` is outside this reader's
    reading (no manifest in this tree names one), so the LAST stage is taken as the shipped one.
    """
    names: list[str] = []
    bases: list[str] = []
    envs: list[dict[str, str]] = []
    #: `ENV` before the first `FROM` is not a build stage at all (Docker rejects one there). It is
    #: kept apart so it can never occupy the first stage's INDEX.
    before_from: dict[str, str] = {}
    ambiguous: set[str] = set()
    lines = _logical_lines(text)
    cursor = 0
    while cursor < len(lines):
        line = lines[cursor]
        cursor += 1
        boundary = _FROM_INSTRUCTION.match(line)
        if boundary:
            names.append(boundary.group("name") or "")
            bases.append(boundary.group("image"))
            envs.append({})
            continue
        match = _ENV_INSTRUCTION.match(line)
        if not match:
            # Any other instruction may OPEN a heredoc whose body is data, not instructions.
            cursor = _heredoc_end(lines, cursor - 1, cursor)
            continue
        stage = envs[-1] if envs else before_from
        # ⛔ NO inline-comment stripping here, unlike the shell reader: in the `K=V` form a
        # Dockerfile has no trailing-comment form — Docker itself REFUSES `ENV K=1  # x` with
        # `Syntax error - can't find = in "#"` (measured), so the line can never ship. Stripping
        # it would REPORT that value as stated for a file that does not build, and the direction
        # that matters here is a false STATED (review at `3e2b944afd`, P3). Left intact, the
        # tiling rule refuses the line instead. ⚠️ The LEGACY `ENV K V` form is the exception and
        # is not treated as a trailing comment either: Docker FOLDS the text into the value
        # (`ENV K 1 # on` ships `K=1 # on`), which is why this reader stores the whole remaining
        # body — reporting the folded value is what makes the `ok` line true about what ships,
        # even though the folded text is truthy-false to the gate's own resolver.
        body = match.group("body").strip()
        if _ENV_PAIRS.match(body):
            for key, value in _ENV_ASSIGNMENT.findall(body):
                if key in SWITCHES and not _states_a_literal(value):
                    # ⛔ LAST ASSIGNMENT WINS in Docker and in the shell, so an ambiguous
                    # assignment must also DROP an earlier literal: otherwise `ENV K=1` followed
                    # by `ENV K=${X}` reports the stale `1` as STATED while the image resolves
                    # empty (review P2). `stated` outranks `ambiguous` in `switch_problem`.
                    ambiguous.add(key)
                    stage.pop(key, None)
                    continue
                stage[key] = _unquote(value)
            continue
        parts = body.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parts[0]):
            if parts[0] in SWITCHES and not _states_a_literal(parts[1]):
                ambiguous.add(parts[0])
                stage.pop(parts[0], None)
                continue
            stage[parts[0]] = _unquote(parts[1])
            continue
        # ⛔ Nothing here TILES the body, so a switch mentioned anywhere in it (including in
        # trailing text after an unrelated `K=V`, which Docker folds into THAT value) is not a
        # statement this reader can credit (review round 6, P3).
        ambiguous.update(_mentioned_switches(body))

    if not envs:
        # No `FROM` at all: a fragment read on its own (the readers are also unit-tested that way),
        # where what the text states IS what ships.
        return before_from, ambiguous, {}

    stated, inherited = _shipped_env(names, bases, envs)
    shadowed: dict[str, str] = {}
    for index, stage in enumerate(envs):
        if index in inherited:
            continue
        label = names[index] or f"stage {index}"
        for key in stage:
            if key in SWITCHES and key not in stated:
                shadowed.setdefault(key, label)
    for key in shadowed:
        ambiguous.discard(key)  # the precise verdict supersedes the generic mention
    return stated, ambiguous, shadowed


#: Spans whose BODY is data for the depth walk, never block structure. A command substitution or a
#: subshell runs in its OWN shell, so an `if`/`fi` inside it neither opens nor closes a block in the
#: script being read (and an `export` inside it never reaches the process, which is why a
#: substituted export must not be credited either). Quotes are spans for the same reason: their
#: content is a string.
_SPAN_SINGLE = "single-quote"
_SPAN_DOUBLE = "double-quote"
_SPAN_ANSI_C = "ansi-c"
_SPAN_CMDSUB = "command-substitution"
_SPAN_BACKTICK = "backtick"
_SPAN_SUBSHELL = "subshell"
#: ⛔ ARITHMETIC is its own span, not a command substitution (review P1, measured). `$(( 1 << n ))`
#: IS a `$(` and was read as a command substitution, which happened to blank the `<<` — the right
#: verdict for the wrong reason. Keeping the two apart is what lets the heredoc OPENER scan read a
#: substitution's body as code (so a `<<'EOF'` inside `$( … )` is found and its body dropped)
#: without turning a SHIFT into a phantom heredoc opener.
_SPAN_ARITH = "arithmetic"

#: Characters after which an unquoted `#` BEGINS A COMMENT (bash starts one at any WORD start).
#: Metacharacters plus blanks; `$#`/`a#b`/`${1#/}` are not comments, which is why this is a set
#: rather than `isspace()` — `set -x;# n/a; fi` is an ordinary comment that the whitespace-only
#: rule read as CODE, and the `; fi` in it closed the enclosing block (review, measured).
_COMMENT_STARTS_AFTER = " \t;&|()<>"


def _word_at(text: str, index: int, word: str) -> bool:
    """True when ``word`` sits at ``index`` as a whole shell WORD (not a substring of one)."""
    end = index + len(word)
    if text[index:end] != word:
        return False
    if index and (text[index - 1].isalnum() or text[index - 1] == "_"):
        return False
    return end >= len(text) or not (text[end].isalnum() or text[end] == "_")


def _shell_is_code(position: int, views: tuple[str, ...]) -> bool:
    """True when ``position`` is CODE in at least one structural view.

    Position-aligned: blanking preserves length, so `<` at the match start means the `<<` is CODE.
    A match inside a span, or past a comment cut, is not an opener — and passing the SECOND
    (substitution-transparent) view is what makes "inside a SUBSTITUTION" mean code here.
    """
    return any(position < len(view) and view[position] == "<" for view in views)


def _heredoc_delimiter(word: str) -> str:
    r"""``word`` with ONE layer of shell quoting removed — the terminator bash compares against.

    A heredoc delimiter is a shell WORD: `<<'END-OF-DATA'`, `<<"E.O.F"`, `<<\EO\F` and `<<$'EOF'`
    all name the same terminator their unquoted spelling does, because bash removes the quoting
    before it stores it (the quoting only suppresses expansion in the BODY). Comparing the quoted
    spelling against the raw terminator line — which is what a reader that never unquotes does —
    leaves the body OPEN, so every statement in it runs as code (review P1, measured).

    ⛔ Nothing else is expanded: `<<$FOO` wants the LITERAL `$FOO` (measured on bash 5.2), which is
    why the opener regex admits `$` inside the word and this leaves it alone.
    """
    out: list[str] = []
    index = 0
    length = len(word)
    while index < length:
        char = word[index]
        if char == "\\" and index + 1 < length:
            out.append(word[index + 1])
            index += 2
            continue
        if word.startswith("$'", index):
            end = word.find("'", index + 2)
            end = length if end < 0 else end
            out.append(word[index + 2 : end])
            index = end + 1
            continue
        if char in "\"'":
            end = word.find(char, index + 1)
            end = length if end < 0 else end
            out.append(word[index + 1 : end])
            index = end + 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _shell_code_lines(text: str) -> list[str]:
    """``text``'s logical lines with every HEREDOC BODY removed (review P1).

    A heredoc body is DATA the script writes out, not a statement it runs, so it must not be read
    as shell. Scanning it was a measured fail-OPEN: a ``fi``/``done``/``esac`` at line start
    inside the body closed the enclosing block, the depth leaked down, and an ``export`` in a
    branch that never runs was credited ``STATED`` — the gate then exited 0 over an unarmed
    process. ``entrypoint.sh`` already opens a ``<<'PY'`` body, and writing a config snippet from
    a heredoc is an ordinary entrypoint job, so the shape is not contrived.

    A heredoc whose delimiter never appears swallows the rest of the file: the script is
    incomplete shell, and crediting nothing from it is the fail-CLOSED direction (the channel is
    then refused for not stating the switches rather than passing on a body nobody executes).

    ⛔ **The opener is found in the STRUCTURAL view, not the raw line (review P2, measured).** A
    ``<<WORD`` inside a quoted string or an inline comment is NOT a heredoc, but reading the raw
    line opened a PHANTOM body that swallowed the next real statement — including an
    ``if false; then`` header — so the never-running ``export``s below it were credited ``STATED``
    (exit 0). The view keeps span DELIMITERS, so a quoted delimiter (``<<'EOF'``, the commonest
    spelling) is still recognised while the ``<<`` inside a string is blanked, and a ``<<`` inside
    ``$(( … ))`` is blanked too because the whole arithmetic span is a substitution.

    ⛔ **A heredoc opened INSIDE a substitution is dropped too (review P1, measured).** The opener
    scan used the ordinary structural view, which blanks a substitution's body — so a
    ``usage=$(cat <<'EOF' … EOF)`` opener was invisible, its body was read as SHELL, and a `)`
    inside the body closed the substitution frame early so the rest of the body ran as code: an
    `export` on a help line was credited ``STATED`` (exit 0) over a process that receives neither
    switch. The opener scan therefore uses a SECOND view that keeps substitution bodies as code
    (strings, comments and arithmetic still blanked), which locates the opener wherever it sits.
    """
    out: list[str] = []
    pending: list[tuple[str, bool]] = []
    stack: tuple = ()
    # The opener scan's own span state: same lines, substitutions kept as code. It is carried
    # separately because the two views open different frames, and mixing them would carry a
    # string frame from one into the other.
    scan_stack: tuple = ()
    for line in text.splitlines():
        if pending:
            # ⛔ The terminator must match what the SHELL accepts, not `strip()` (review P2,
            # measured). Bash strips leading TABS only for `<<-`, and compares the line EXACTLY
            # for `<<`; `strip()` also accepted spaces, so an indented terminator ended the body
            # early and exports bash never reaches were read as top level. This mirrors the
            # Dockerfile reader's rule so the two heredoc readers in this file agree.
            delim, dash = pending[0]
            candidate = line.lstrip("\t") if dash else line
            if candidate.rstrip("\r") == delim:
                pending.pop(0)
            continue
        out.append(line)
        structure, stack = _shell_structure(line, stack, keep_delimiters=True)
        scan, scan_stack = _shell_structure(
            line, scan_stack, keep_delimiters=True, keep_substitutions=True
        )

        views = (structure, scan)
        openers = [
            match for match in _SHELL_HEREDOC.finditer(line) if _shell_is_code(match.start(), views)
        ]
        if any(
            _shell_is_code(candidate.start(), views)
            and not any(o.start() <= candidate.start() < o.end() for o in openers)
            for candidate in _SHELL_HEREDOC_START.finditer(line)
        ):
            # ⛔ A `<<` this reader cannot model: STOP, do not guess. The rest of the file is not
            # read, which is the same fail-CLOSED direction as an unterminated body — the channel
            # is then refused for not stating the switches instead of being credited from lines
            # that may sit inside a body nobody executes.
            return out
        for match in openers:
            pending.append((_heredoc_delimiter(match.group("delim")), match.group(0).startswith("<<-")))
    return _join_logical(out)


def _shell_structure(
    text: str,
    stack: tuple = (),
    keep_delimiters: bool = False,
    keep_substitutions: bool = False,
) -> tuple[str, tuple]:
    """``text`` with every span body and a trailing comment blanked — the STRUCTURAL view.

    Block keywords are matched against THIS, never against the raw line (review P1 at
    `19afbd0865`, measured): `_SHELL_CLOSERS` searched the raw logical line, so a `; fi` inside a
    quoted argument (`echo "n/a; fi"`) or inside a trailing comment (`echo hi  # see; fi below`)
    closed the ENCLOSING block. The depth then read 0 for a branch that never runs, and both
    `export`s inside it were credited ``STATED`` — the gate exited 0 over a process that receives
    neither switch.

    ⛔ Why spans and not quote-cleaning (fresh reviewers measured four more fail-opens in the
    quote-only version, then two more in the first span version, all the same root): a block
    keyword is data wherever it sits inside a construct this reader does not evaluate — a quoted
    string, a comment, an ANSI-C string (`$'don\\'t; fi'`), a command substitution
    (`$(if …; fi)`, `` `if …; fi` ``) or a subshell (`( if …; fi )`). Each leaked a closer into the
    enclosing depth and re-armed the never-running-branch credit, exit 0. So every span is
    BLANKED, contents included, and the span can itself contain quotes, further substitutions and
    parens. The module's contract is that an unmodelled form lands on ``ambiguous`` rather than a
    pass, and this is that contract made structural: what the reader does not evaluate contributes
    nothing to the depth.

    ⛔ **A `case` ARM terminator is not a closer (review P1, measured).** Inside a substitution,
    ``)`` had no way to know it was ending a pattern (``$(case $M in off) echo 0 ;; esac)``), so the
    span ENDED at the first arm; the leftover ``esac )`` was then read as code that closed a REAL
    enclosing block, and a never-running branch's exports were credited STATED (exit 0). `case` is
    therefore counted inside each span and its arms cannot close it.

    The mirror direction matters just as much, and it is why an ESCAPED character is blanked
    rather than read as a delimiter: `echo \\"n/a\\"` is a literal quote in bash, and reading it as
    a span opener left the span open for the rest of the file, blanked the real `fi`, and REFUSED
    an honest `=1` script that passed before this change (measured — a false refusal is a bug on
    this gate too).

    ``stack`` is the span state still open when the caller's previous line ended, returned for the
    next line (⛔ a span may SPAN LINES — measured fail-open: `echo "n/a` then `fi"` puts a `fi` at
    column 0 that no per-line reader can know is data; and `$(` … `)` on separate lines is
    ordinary shell). Carrying the state is what makes the verdict about the SCRIPT rather than
    about each line.

    ``keep_delimiters`` emits the span DELIMITERS instead of blanking them, for a caller that has
    to locate a construct inside code without reading string content (the heredoc opener scan).

    ``keep_substitutions`` goes one step further for that caller: a substitution's BODY stays CODE
    (only strings, comments and arithmetic are still blanked), because a heredoc opener may sit
    inside `$( … )` and is invisible if the whole span is blank. Nothing that reads the DEPTH may
    use this view — a `fi` inside `$( … )` would then close a real block.
    """
    out: list[str] = []
    frames = list(stack)
    index = 0
    length = len(text)

    def blank(count: int) -> None:
        out.extend(" " * count)

    def delim(segment: str) -> None:
        out.append(segment if keep_delimiters else " " * len(segment))

    while index < length:
        char = text[index]
        kind = frames[-1][0] if frames else ""

        if kind in (_SPAN_SINGLE, _SPAN_DOUBLE, _SPAN_ANSI_C):
            # Inside a string: only its own delimiter ends it (no escapes in '…'; escapes in "…"
            # and $'…'). Everything is data.
            if kind != _SPAN_SINGLE and char == "\\" and index + 1 < length:
                blank(2)
                index += 2
                continue
            closer = '"' if kind == _SPAN_DOUBLE else "'"
            if char == closer:
                frames.pop()
                delim(char)
            else:
                blank(1)
            index += 1
            continue

        if kind == _SPAN_ARITH:
            # Arithmetic is DATA for this reader in BOTH views: `1 << n` is a SHIFT, never a
            # heredoc opener, and `case`/`esac` inside it is a string test, not a shell block.
            # Parens nest (and close it) the same way a substitution's do.
            depth = frames[-1][1]
            if char == "(":
                frames[-1] = (kind, depth + 1, 0)
            elif char == ")":
                if depth <= 1:
                    frames.pop()
                else:
                    frames[-1] = (kind, depth - 1, 0)
            blank(1)
            index += 1
            continue

        if kind in (_SPAN_CMDSUB, _SPAN_SUBSHELL, _SPAN_BACKTICK):
            # Inside a substitution/subshell: parens (or the closing backtick) end it, quoted
            # spans are stepped over, `case` arms are not closers, and nothing here opens or
            # closes an enclosing block.
            depth, cases = frames[-1][1], frames[-1][2]

            def body(segment: str) -> None:
                # ⛔ In `keep_substitutions` mode the BODY is code (the heredoc opener scan needs
                # to see a `<<'EOF'` sitting inside `$( … )`); every other caller blanks it so a
                # `fi` in a substitution cannot close a real block.
                out.append(segment if keep_substitutions else " " * len(segment))

            if char == "\\" and index + 1 < length:
                body(text[index : index + 2])
                index += 2
                continue
            if char in "\"'":
                frames.append((_SPAN_DOUBLE if char == '"' else _SPAN_SINGLE, 0, 0))
                delim(char)
                index += 1
                continue
            if char == "$" and index + 1 < length and text[index + 1] == "(":
                if text[index + 2 : index + 3] == "(":
                    # `$(( … ))` is ARITHMETIC even inside a substitution: keep `1 << n` a SHIFT.
                    frames.append((_SPAN_ARITH, 2, 0))
                    delim("$((")
                    index += 3
                    continue
                frames.append((_SPAN_CMDSUB, 1, 0))
                delim("$(")
                index += 2
                continue
            if kind == _SPAN_BACKTICK:
                if char == "`":
                    frames.pop()
                    delim(char)
                else:
                    body(char)
                index += 1
                continue
            if char == "`":
                frames.append((_SPAN_BACKTICK, 0, 0))
                delim(char)
                index += 1
                continue
            if _word_at(text, index, "case"):
                frames[-1] = (kind, depth, cases + 1)
                body(text[index : index + 4])
                index += 4
                continue
            if _word_at(text, index, "esac"):
                frames[-1] = (kind, depth, max(0, cases - 1))
                body(text[index : index + 4])
                index += 4
                continue
            if char == "(":
                frames[-1] = (kind, depth + 1, cases)
                delim(char)
                index += 1
                continue
            if char == ")":
                if cases:
                    # ⛔ A `case` arm's terminator: the substitution continues.
                    body(char)
                    index += 1
                    continue
                if depth <= 1:
                    frames.pop()
                    delim(char)
                else:
                    frames[-1] = (kind, depth - 1, cases)
                    delim(char)
                index += 1
                continue
            body(char)
            index += 1
            continue

        # ---- no span open: this character is CODE ----
        if char == "\\" and index + 1 < length:
            blank(2)  # an escaped character is data, never a delimiter
            index += 2
            continue
        if char == "'":
            frames.append((_SPAN_SINGLE, 0, 0))
            delim(char)
            index += 1
            continue
        if char == '"':
            frames.append((_SPAN_DOUBLE, 0, 0))
            delim(char)
            index += 1
            continue
        if char == "$" and index + 1 < length:
            following = text[index + 1]
            if following == "'":
                frames.append((_SPAN_ANSI_C, 0, 0))
                delim("$'")
                index += 2
                continue
            if following == "(":
                if text[index + 2 : index + 3] == "(":
                    # ⛔ `$((` is ARITHMETIC, not a command substitution (review P1, measured).
                    # Treating it as one was only accidentally right: it blanked the span, which
                    # hid a `1 << n` SHIFT. The two are separated now so the heredoc opener scan
                    # can read a real substitution's body as code without inventing a heredoc
                    # out of a shift.
                    frames.append((_SPAN_ARITH, 2, 0))
                    delim("$((")
                    index += 3
                    continue
                frames.append((_SPAN_CMDSUB, 1, 0))
                delim("$(")
                index += 2
                continue
        if char == "`":
            frames.append((_SPAN_BACKTICK, 0, 0))
            delim(char)
            index += 1
            continue
        if char == "(":
            # A subshell/group body or a function's `()`: blanked either way, so `( if …; fi )`
            # cannot leak a closer and `name() {` still leaves its `{` to open the block.
            frames.append((_SPAN_SUBSHELL, 1, 0))
            delim(char)
            index += 1
            continue
        if char == "#" and (index == 0 or text[index - 1] in _COMMENT_STARTS_AFTER):
            break  # a comment: nothing after it is shell
        out.append(char)
        index += 1
    return "".join(out), tuple(frames)


def _shell_structures(lines: list[str]) -> list[str]:
    """The structural view of every logical line, with SPAN state carried ACROSS lines."""
    out: list[str] = []
    stack: tuple = ()
    for line in lines:
        structure, stack = _shell_structure(line, stack)
        out.append(structure)
    return out


def _export_tokens(body: str) -> list[str]:
    """``export``'s argument words, split on UNQUOTED whitespace and keeping their quotes.

    Quotes are kept because the value is unwrapped later (``_unquote``) and its trailing comment
    is cut quote-aware (``_strip_inline_comment``); splitting with a quote-stripping tokeniser
    would make ``K="a # b"`` lose its value.
    """
    tokens: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(body):
        char = body[index]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(body):
                current.append(body[index + 1])
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char == "\\" and index + 1 < len(body):
            current.append(char)
            current.append(body[index + 1])
            index += 2
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            index += 1
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            index += 1
            continue
        if char in ";&|<>":
            # A shell METACHARACTER ends a word (and is not part of any value): `export A=1;B=2`
            # states two assignments, and `A=1` — not `1;` — is what the process receives, which
            # is what the `ok` line reports (review P2, measured).
            if current:
                tokens.append("".join(current))
                current = []
            index += 1
            continue
        if char == "#" and not current:
            break  # a comment starts at this word start
        current.append(char)
        index += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _shell_exports(line: str, structure: str) -> list[tuple[str, str]]:
    """The ``(key, value)`` pairs ONE `export` line states, head-guarded against spans.

    ⛔ **ONE `export` may state SEVERAL switches (review P2, measured).** The single-assignment
    regex took the whole remainder as the first value, so `export TORTOISE_VALUE_GATE=1
    TORTOISE_EMBED_GATE=1` — ordinary shell — was REFUSED as not stating the second switch while
    REPORTING the fabricated value `1 TORTOISE_EMBED_GATE=1` for the first. Both halves are bugs:
    the refusal is wrong, and the value cell a passing channel prints must be what the process
    receives.

    The head guard is the other half: the assignment text runs on the RAW line (the value needs its
    quotes intact), so an ``export TORTOISE_*_GATE=1`` that is merely the TEXT of a multi-line
    string was credited ``STATED`` — measured: a script whose only "arm" is a line of help text
    inside ``echo "…"`` printed ``ok`` with exit 0 over a process that receives neither switch.
    Blanking preserves length, so the head (``export ``) is compared byte-for-byte against the
    same slice of the structural view: anything inside a span differs there.
    """
    match = _SHELL_EXPORT.match(line)
    if not match:
        return []
    head = line[: match.start("body")]
    if structure[: len(head)] != head:
        return []  # the `export` is inside a span: text, not a statement
    pairs: list[tuple[str, str]] = []
    for token in _export_tokens(match.group("body")):
        key, separator, value = token.partition("=")
        if not separator or not _SHELL_KEY.match(key):
            continue  # `export NAME` states no value; not an arm
        pairs.append((key, value))
    return pairs


#: Statement separators that end ONE command on a line. A shared line is processed as SEVERAL
#: statements because a block closer may sit BESIDE a later assignment (review P2, measured):
#: the closer branch `continue`d before the assignment scan, so `fi; export K="${MAYBE_EMPTY}"`
#: neither credited nor retracted — an earlier literal `export K=1` survived as `STATED` while the
#: process receives the later, empty value, and the gate exited 0. `;;` (a `case` terminator) is
#: matched FIRST so it cannot read as two empty statements.
_SHELL_STATEMENT = re.compile(r";;|;|&&|\|\|")

#: A statement pushed into a SUBSHELL never reaches the process the image starts, so its `export`
#: is not the shipped configuration stating a value. Three spellings, all measured against bash:
#:
#: * a BARE `&` (the background operator) — `export K=1 & wait` leaves the parent with K UNSET;
#: * a PIPE — either side of `|` runs in its own subshell, so `export K=1 | cat` leaves the parent
#:   with K UNSET. ⚠️ A `|&` (pipe-with-stderr) is the same operator and was measured the same way;
#: * `&&`/`||` are NOT here — they are statement SEPARATORS and are carried as the third element
#:   of `_shell_statements`, because only the RIGHT-hand side is conditional.
#:
#: Redirects are excluded on purpose: `&>`, `>&2`, `>|` and `&>>` put the metacharacter beside
#: another one and only move a stream, so reading one as a fork would REFUSE an honest config
#: (`test_the_short_circuit_guard_does_not_refuse_an_honest_entrypoint` and
#: `test_a_redirect_is_not_a_fork` pin that direction, which is why the pipe arm carries a `>`
#: lookbehind: `>|` is bash's CLOBBER redirect, not a pipe).
_SHELL_FORK = re.compile(r"(?<!>)\|&?(?![&|])|(?<![&>|])&(?![&>])")


def _shell_statements(line: str, structure: str) -> list[tuple[str, str, str]]:
    """Split one logical line into ``(raw, structural, separator)`` STATEMENT segments.

    Offsets come from the STRUCTURAL view (span bodies and the trailing comment already blanked,
    length preserved), so a `;`/`&&`/`||` inside a string, a comment or a `$( … )` splits nothing,
    and each raw slice stays byte-aligned with its structural slice — which is what the readers'
    head guards compare. A trailing comment is not a statement, so the final raw slice is cut to
    the structure's length rather than to the line's.

    ⛔ **The SEPARATOR is returned because `&&`/`||` change what a segment MEANS.** Splitting them
    into equal statements lost the guard: `[ -n "$MAYBE" ] && export K=1` handed the right-hand
    slice to the reader as an ordinary top-level statement, so a short-circuited (never-run) export
    was credited `STATED` and the gate exited 0 over a process that receives no value at all
    (review, measured: `true || export K=1` and `[ -n "$MAYBE" ] && export K=1` both printed `ok`,
    while `env -i … bash` printed neither switch). It is the same class as an `if`-guarded export,
    which this reader already refuses — so the caller needs the guard, not just the text.
    ``;``-separated statements stay creditable, which is why the split exists.
    """
    segments: list[tuple[str, str, str]] = []
    start = 0
    separator = ""
    for match in _SHELL_STATEMENT.finditer(structure):
        segments.append((line[start:match.start()], structure[start:match.start()], separator))
        separator = match.group(0)
        start = match.end()
    segments.append((line[start:len(structure)], structure[start:], separator))
    return segments


def _shell_assignments(text: str) -> tuple[dict[str, str], set[str]]:
    """Assignments an entrypoint script exports at TOP LEVEL (last wins) + ambiguous switch names.

    ``export K`` with no value states nothing (it exports whatever the caller had — the absence
    this gate exists to refuse). An ``export`` inside a function body, a branch or a loop, or
    after a top-level ``exit``/``exec``, is routed to ``ambiguous`` instead of being credited: it
    is a conditional the gate does not evaluate, so it is not a statement the shipped
    configuration makes (review at `762f1c56d7`/`8aa35e5d56`, closed here).

    ⛔ **The block model is COUNT-based and PER STATEMENT (review P1+P2, measured).** Per-LINE,
    boolean accounting had two holes, one in each direction:

    * a line that opens TWO blocks and closes ONE read as ``opens and closes → net zero``, so
      ``if false; then if false; then echo x; fi`` left the depth at 0 and BOTH exports of a
      never-running outer branch were credited ``STATED`` — exit 0 over a process that receives
      neither switch (verified: the same script run by bash prints both UNSET);
    * a line that closes a block and carries a later assignment ``continue``d before the
      assignment scan, so an earlier ``=1`` survived as ``STATED`` while the process receives the
      later value (``fi; export K="${MAYBE_EMPTY}"``) — the same last-wins rule the two
      retraction regexes exist to enforce.

    So each statement is judged at the depth it ACTUALLY runs at: its closers are applied BEFORE
    it (a ``fi`` earlier on the line means the statement after it is top level), then the statement
    is scanned, then its openers are applied. A closer saturates at 0, so a surplus ``esac )`` can
    never drive the depth negative and re-arm a nested export.
    """
    stated: dict[str, str] = {}
    ambiguous: set[str] = set()
    depth = 0
    terminated = False
    #: The separator a PREVIOUS line ended with. ⛔ It has to cross the line boundary (review P1,
    #: measured): `_shell_statements` carries a separator only WITHIN one logical line, so the
    #: idiomatic multi-line guard
    #:
    #:     [ -n "${MAYBE_ARM:-}" ] &&
    #:       export TORTOISE_VALUE_GATE=1 &&
    #:       export TORTOISE_EMBED_GATE=1
    #:
    #: handed each export a first-segment `separator == ""`, so both were credited `STATED` —
    #: gate `ok`, exit 0 — while `bash` (MAYBE_ARM unset) ran no `export` at all and the process
    #: had neither switch. The verdict then flips on an invisible line break, which is exactly the
    #: fail-open the one-line pin was meant to close. The same applies to `||`, and to a trailing
    #: FORK operator (`|`), which `_SHELL_FORK` sees only on its own line.
    carried = ""
    code_lines = _shell_code_lines(text)
    for line, structure in zip(code_lines, _shell_structures(code_lines), strict=True):
        # ⛔ STRUCTURE, not the raw line: a closer inside a quote or a comment is DATA (review P1),
        # and quote state carried across lines covers a quoted string that spans a newline.
        segments = _shell_statements(line, structure)
        for index, (raw, view, separator) in enumerate(segments):
            if index == 0:
                separator = separator or carried
            if index == len(segments) - 1 and not raw.strip():
                # The trailing operator of this line is the final, empty segment's separator.
                carried = separator
            depth = max(0, depth - len(_SHELL_CLOSERS.findall(view)))
            # ⛔ `depth > 0 or terminated` is NOT the whole population of statements the process
            # may never receive, and the gaps were all measured fail-OPEN (review):
            #
            #   * a SHORT-CIRCUIT right-hand side — `[ -n "$MAYBE" ] && export K=1`, `true ||
            #     export K=1`, `false && export K=1`. `&&`/`||` are split out as separate
            #     statements (so a closer beside an assignment is seen), and the RHS then reads at
            #     depth 0 and was CREDITED — `ok`, exit 0, while `env -i … bash` printed neither
            #     switch. `;`-separated statements are ordinary and stay creditable, which is the
            #     whole reason the split exists, so the separator has to be carried, not guessed;
            #   * a FORKED statement — `export K=1 &` and `export K=1 | cat` (and `|&`) both run in
            #     a SUBSHELL, so the parent never has K. Measured: `bash -c 'export K=1 & wait; …'`
            #     and `bash -c 'export K=1 | cat; …'` both leave K UNSET.
            #
            # All land on `ambiguous`, the direction that fails CLOSED. A branch or a fork is not
            # the shipped configuration stating a value, whether it is spelled `if …; then`, `&&`,
            # `&` or `|`. The `export` in the shipped `entrypoint.sh` is a plain top-level one, so
            # this cannot refuse an honest channel (`test_the_real_entrypoint_statements_are_unconditional`).
            short_circuit = separator in ("&&", "||")
            forked = _SHELL_FORK.search(view) is not None
            conditional = depth > 0 or terminated or short_circuit or forked
            # ⛔ CREDITING and RETRACTING are not the same question, and folding `short_circuit`
            # into the one flag answered both — measured fail-OPEN in the mirror direction
            # (review P0 at `a0d5b9ae4908`):
            #
            #     export K=1 && unset K          -> stated={'K': '1'}   bash: K UNSET
            #     export K=1 && export -n K      -> stated={'K': '1'}   bash: K UNSET
            #     export K=1 && K=               -> stated={'K': '1'}   bash: K=""
            #
            # The retraction branches below are gated on `not conditional`, so once the RHS of a
            # `&&` was declared conditional they did NOTHING AT ALL — not even `ambiguous.add` —
            # and the earlier literal survived in `stated`, which `switch_problem` PREFERS over
            # `ambiguous`. The gate then reported a value the process does not have.
            #
            # Which statements may retract is decided by whether they can affect the PARENT
            # shell, which is a DIFFERENT question from whether they can state a value:
            #   * `depth > 0` — a helper body may never run, and its local `unset` must not
            #     retract a top-level export the process DOES receive (a false refusal, review P2);
            #   * `terminated` — after a top-level `exit`/`exec` nothing runs;
            #   * `forked`/`backgrounded` — a subshell cannot change the parent's environment, so
            #     `export K=1; unset K | cat` leaves K SET and the literal must SURVIVE.
            # A SHORT-CIRCUITED retraction is deliberately NOT in that list: it runs in the PARENT
            # and may or may not execute, so whether the value survives is unknowable here — and
            # "cannot be shown to hold" must fail CLOSED (`ambiguous`, i.e. refused), never report
            # `STATED`. (`export K=1 && unset K` — the ordinary spelling — always removes it; the
            # contrived `false && unset K` is exactly the shape this reader says it does not model.)
            retracts = not (depth > 0 or terminated or forked)
            exports = _shell_exports(raw, view)
            if exports:
                for key, raw_value in exports:
                    value = _strip_inline_comment(raw_value)
                    if key in SWITCHES and (conditional or not _states_a_literal(value)):
                        # Same last-wins rule as the Dockerfile reader: an ambiguous assignment
                        # must drop an earlier literal, or the stale literal is reported as
                        # STATED while the script resolves empty (review P2). A CONDITIONAL
                        # assignment is the same verdict for the same reason: inside a function
                        # body, a branch or a loop it may never run, and after a top-level
                        # `exit`/`exec` it cannot.
                        ambiguous.add(key)
                        stated.pop(key, None)
                        continue
                    if not conditional:
                        stated[key] = _unquote(value)
            elif (unset := _SHELL_UNSET.match(raw)) or (
                unexport := _SHELL_EXPORT_UNEXPORT.match(raw)
            ):
                for name in _unquote_all((unset or unexport).group("names")):
                    if name in SWITCHES and retracts:
                        # ⛔ A NESTED `unset` neither credits nor retracts (review P2, measured).
                        # These branches ran before, and ignored, the `conditional` guard, so a
                        # helper's local `unset K` retracted a TOP-LEVEL `export K=1` the process
                        # does receive — a false refusal of an honest config.
                        ambiguous.add(name)
                        stated.pop(name, None)
            elif (unexported := _SHELL_UNEXPORTED.match(raw)) and (
                unexported.group("key") in SWITCHES
            ):
                # Same rule, same reason: only a TOP-LEVEL bare assignment is a statement about
                # what the process gets. `retracts` (not `conditional`), because a top-level
                # `K=` really does clear it and a short-circuited one is unknowable — see the
                # CREDIT/RETRACT note above.
                if retracts:
                    ambiguous.add(unexported.group("key"))
                    stated.pop(unexported.group("key"), None)
            elif (env_call := _SHELL_ENV_CALL.match(view)) and retracts:
                # Same gate as the builtins above: a forked or short-circuited `env` cannot change
                # the parent's environment, so only a statement that provably runs is a retraction.
                stripped = set(_SHELL_ENV_UNSET.findall(env_call.group("args")))
                if _SHELL_ENV_IGNORE.search(env_call.group("args")):
                    stripped |= set(SWITCHES)
                for name in stripped & set(SWITCHES):
                    ambiguous.add(name)
                    stated.pop(name, None)
            else:
                # A statement that states no switch is where a MENTION lives (prose, a help line,
                # a `[[ … == *TORTOISE_* ]]` test): recorded, never credited. A segment that DID
                # resolve an assignment is deliberately not scanned again — `ambiguous` is the
                # "seen but not creditable" set, and the readers above already decided.
                ambiguous.update(_mentioned_switches(raw))
                # ⛔ A BUILTIN that names a switch takes it back without the `export` spelling
                # this reader credits (review P2, measured): `declare -x K=` after `export K=1`
                # leaves K EMPTY and still exported while the gate kept the earlier `=1`.
                if retracts and (builtin := _SHELL_BUILTIN_ASSIGN.match(raw)):
                    for name in _mentioned_switches(builtin.group("rest")):
                        ambiguous.add(name)
                        stated.pop(name, None)
            depth += len(_SHELL_OPENERS.findall(view)) + _function_body_braces(raw, view)
            if depth == 0 and _SHELL_TERMINATES.match(raw):
                terminated = True
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
        # ⛔ THE ALL-PROFILES FLAG IS NOT OPTIONAL (review P2 at `543a1b0467`, measured).
        # Compose filters out every service behind `profiles:` UNLESS they are enabled, and it
        # does so SILENTLY — no stderr, no `warnings`, rc 0. So without this flag a Tortoise
        # service behind a profile is simply absent from the model, and this gate reports OK over
        # a channel it never saw. Measured on Compose 2.40.3: an armed `app` beside
        # `worker: {build: ., profiles: ["prod"], environment: {TORTOISE_DB_URI: …}}` resolves to
        # a model containing only `app`; with the flag, `worker` is present. One `--profile`
        # deploy away from real is exactly the "discovered channel passes quietly" fail-open the
        # module docstring forbids, so the widening is unconditional ("*" enables every profile).
        "--profile",
        "*",
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
        stated, ambiguous, shadowed = _dockerfile_env(text)
        channels.append(
            Channel(
                label,
                "dockerfile",
                "Dockerfile ENV",
                stated=stated,
                ambiguous=tuple(sorted(ambiguous)),
                shadowed=shadowed,
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
