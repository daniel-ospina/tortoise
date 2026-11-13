"""The repo-root ``.env`` reader — ONE parser, and the ONE path it is read from.

WHY THIS MODULE EXISTS (#12407). The documented configure step is
``cp .env.example .env``. For the GRAPH TARGET, exactly one entry point read that
file: the MCP daemon, via an import-time side effect in
:mod:`tortoise.mcp_server`. Every CLI verb resolved its graph target from
``os.environ`` alone, so on the documented setup the same file selected a store
for the daemon and was silently ignored by ``tortoise doctor`` — which then
printed ``Graph: health ✅`` about a DIFFERENT database than the one the daemon
served.

"The only reader" is about the graph target, deliberately: one other module,
:mod:`tortoise.s2_decision_seam`, reads the repo-root ``.env`` for a provider
credential with its own small parser. It resolves no store and is not part of
this divergence — but it is a second reader of the FILE, so the claim below is
scoped rather than absolute.

The fix direction #12407 settled on is *disclose, do not widen*: the CLI still
does not load the file, and ``config show`` / ``doctor`` now REPORT it. That
disclosure must not become a second implementation of the loader — a parser that
disagreed with the one the daemon applies would report a divergence that does
not exist, or miss one that does. So the parser and the path live HERE, and both
the daemon (:func:`tortoise.mcp_server._load_dotenv`) and the reporting verbs
(:func:`tortoise.__main__._dotenv_divergence`) call them:

* :func:`dotenv_path` — the single path the daemon reads. Derived from this
  package's own location, so it follows an editable install, a venv, or a
  checkout, and it is the repo root for all three.
* :func:`parse_dotenv` — PARSES ONLY; never touches ``os.environ``. This is the
  shape python-dotenv calls ``dotenv_values``, and for this repo it is
  load-bearing: a diagnostic must not apply what it is reporting on.
* :func:`load_dotenv` — applies :func:`parse_dotenv`'s result, filling only
  keys ABSENT from the environment.

⚠️ The fill-absent rule is the whole semantics, not a detail. A key that is
explicitly set — even to the empty string — is never overridden, and the
presence test is what makes a divergent target computable at all: a ``.env``
key that ``os.environ`` already carries cannot change any resolution, so
reporting a divergence for it would send the user hunting a difference that
cannot exist.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: The file name, named once so a directory listing and this module cannot
#: disagree about what the daemon reads.
DOTENV_NAME = ".env"

#: Every path THIS PROCESS has actually applied, by :func:`load_dotenv`.
#: The disclosure reads it instead of guessing from `sys.modules`: importing
#: `tortoise.mcp_server` under pytest runs the loader's `pytest` guard, so the
#: module being present says nothing about whether the file was applied — and a
#: disclosure that inferred "already applied" from an import would assert a
#: falsehood in the one place it exists to be truthful. A recorded fact beats an
#: inferred one, and it survives a module reload.
LOADED_PATHS: set[str] = set()

#: The key/value pairs THIS PROCESS actually wrote from a `.env`, recorded by
#: :func:`load_dotenv` **with the file they came from**. ``LOADED_PATHS`` answers
#: WHICH paths were applied; this answers WHAT they put in the environment, which
#: is the only way a report can say "the value it rejects came from the file"
#: without inferring it from "no key was pending" — the applied state has nothing
#: pending precisely because its values are already in `os.environ`.
#: ⛔ Keyed by PATH as well as key: a value-only record makes a value written by
#: ANOTHER `.env` earlier in the process look like this file's, and the report is
#: about ONE file (#12428 review).
APPLIED: dict[str, tuple[str, str]] = {}


def applied_value(key: str, path: str | None = None) -> str | None:
    """The value THIS process wrote for ``key`` from the file at ``path``.

    Compare it with ``os.environ.get(key)``: equal means the process is holding
    the FILE's value (so the file is a cause of whatever that value does),
    unequal-or-None means the user's own environment supplied it. ``path=None``
    matches any file — only for callers that do not report on a specific one.
    """
    recorded = APPLIED.get(key)
    if recorded is None:
        return None
    from_path, value = recorded
    if path is not None and _norm(path) != from_path:
        return None
    return value


def _norm(path: str) -> str:
    return os.path.abspath(path)


def dotenv_path() -> str:
    """The ONE path the daemon's ``.env`` read uses: the repo root.

    ``tortoise/mcp_server.py`` -> ``tortoise/`` -> the package's parent.
    Normalised so the path a report prints is one a user can paste into an
    editor (the un-normalised ``…/tortoise/../.env`` is the same file but reads
    as a different one).

    ⚠️ For a CHECKOUT (or an editable install) that parent is the repo root. For
    a WHEEL install it is ``site-packages`` — so a user who follows
    ``cp .env.example .env`` in their project directory beside a pip install
    configures a file at neither path, which is exactly what the "no entry point
    reads this" disclosure reports rather than papering over.
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(package_dir, os.pardir, DOTENV_NAME))


def parse_dotenv(path: str | None = None) -> dict[str, str]:
    """Parse a ``.env`` file into an ordered ``{key: value}`` map — read-only.

    ``os.environ`` is never touched, so a caller may report what the file WOULD
    do without doing it. Rules (the daemon's, verbatim — python-dotenv's file
    format, as the loader has always implemented it):

    * blank lines and ``#`` comments are skipped; an ``export `` prefix is
      stripped;
    * a line with no ``=`` is skipped, and so is a bare ``KEY`` with no value
      (python-dotenv's ``load_dotenv`` ignores those too);
    * a quoted value is LITERAL (``"a # b"`` keeps its ``#``); an unquoted value
      loses an inline comment (whitespace + ``#``), and a bare ``#`` inside an
      unquoted value is preserved;
    * a DUPLICATE KEY keeps the FIRST value, which is what the pre-#12407
      loader did (it wrote a key only when absent, so a later line for the same
      key was already "present" and skipped). python-dotenv's ``dotenv_values``
      is last-wins, and following it here would silently change the DAEMON's
      graph target for anyone who appended a second ``TORTOISE_DB_URI`` instead
      of editing the first — a split-graph change with no migration note. The
      two behaviours are cheap to differ on and expensive to confuse, so this is
      pinned by a test.

    REGULAR FILES ONLY. ``Path.read_text()`` on a FIFO blocks in ``open()`` with
    no writer — a hang in the very diagnostic a user ran to find out what is
    wrong, with no output and no exit code. A device, socket or directory at
    this path is treated as "no ``.env``" rather than read.

    An unreadable file (permissions, a vanishing mount) is logged at debug and
    reported as empty: the caller's job is to say the file exists and could not
    be read, which is a different statement from "it sets nothing".
    """
    if path is None:
        path = dotenv_path()
    if not os.path.isfile(path):
        return {}
    assignments: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        logger.debug("Could not read %s (%s) — treating as empty", path, exc)
        return {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key and key not in assignments:   # FIRST wins — see the docstring
            assignments[key] = value
    return assignments


def pending_assignments(path: str | None = None) -> dict[str, str]:
    """The subset of the file that WOULD be applied — the fill-absent rule itself.

    ONE implementation, TWO callers: :func:`load_dotenv` writes exactly this map,
    and the disclosure (`__main__._dotenv_divergence`) reads it to know what the
    daemon's environment would become. A second comprehension at the reporting
    site was the shape review round 1 flagged: it agreed by coincidence, so any
    tightening added here would leave the report describing a loader that no
    longer runs (#12428).
    """
    return {key: value for key, value in parse_dotenv(path).items()
            if key not in os.environ}


def load_dotenv(path: str | None = None) -> list[str]:
    """Apply :func:`pending_assignments` — only keys ABSENT from the environment.

    Returns the key names actually written, so a caller can say what landed
    without re-deriving it.
    """
    written: list[str] = []
    applied_path = os.path.abspath(path if path is not None else dotenv_path())
    for key, value in pending_assignments(path).items():
        os.environ[key] = value
        # Recorded even though the environment now carries it: after the fill,
        # "present" no longer says where the value came from — and the PATH says
        # which file, so a value another `.env` wrote cannot be read as this
        # one's (#12428 review).
        APPLIED[key] = (applied_path, value)
        written.append(key)
    LOADED_PATHS.add(os.path.abspath(path if path is not None else dotenv_path()))
    return written
