"""The two embedding switches, stated out loud at startup (#12926).

**Why a startup line, of all things.** `#11445` decided the guarantee for "what earns an
embedding" is the **shipped configuration**, and `#12926` added the pre-merge refusal for a
channel that omits it. Both of those act on the *repository*. Neither tells an operator what
the **process that is now running** resolved — and the danger is asymmetric: the two switches
read **opposite** ways when unset (`tortoise/embed_gate.py` is OFF unless enabled,
`tortoise/value_gate.py` is ON unless disabled), so an unstated EMBED switch means every
written row keeps its vector, silently, with nothing crashing. Silence is what makes that
direction dangerous, so the value is stated once at boot.

**The values are the gates' OWN.** This module never re-derives a default: it calls each
module's `resolve_enabled(raw)` — the one function that applies that module's default, and the
function its own `*_gate_enabled()` delegates to — and reads the raw statement from
`os.environ` only to say *where* the value came from. A second resolution order here is the drift `env_truthy` (`#4097`) exists to prevent,
and it would let the log say ON while the gate behaved OFF — a worse silence than no line at
all.

**Where it fires.** At boot of both daemon entry modules (`tortoise/selfhost.py` for the
self-host image, `tortoise/hosted_api.py` for the hosted one), following
`selfhost.emit_off_box_backup_signal`'s precedent (`#8543`): printed to **stderr** so the
`docker compose up` / deploy ladder shows it, **and** logged so a container log aggregator gets
it. It is a record, never a refusal — a runtime refusal for a storage-hygiene switch is the
blast radius `#12926` explicitly declined.

⚠️ What it does NOT do: it does not change either default, and it is not a gate. Changing a
default is a **reopen of `#11445`**; the enforcement lives pre-merge in
`tools/embed_switch_gate.py`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from typing import Any

from . import embed_gate, value_gate

_logger = logging.getLogger(__name__)

#: ``(switch, resolver)`` — the resolver is the module's own
#: ``resolve_enabled``, i.e. the ONE place each default is applied, so this table cannot
#: disagree with the write path about what "absent" means. (It calls ``resolve_enabled`` rather
#: than ``*_gate_enabled()`` so the *flag read* stays at the one seam each module documents —
#: `tests/test_embed_gate_5008.py::test_the_EMBED_flag_is_read_at_the_submission_seam_only`
#: pins that — while the *resolution* is still shared, never re-spelled here.) Ordered as the
#: epic states the two questions: the DISCARD one, then the EMBED one.
SWITCHES: tuple[tuple[str, Any], ...] = (
    (value_gate.ENV_FLAG, value_gate.resolve_enabled),
    (embed_gate.ENV_FLAG, embed_gate.resolve_enabled),
)


def effective_switches() -> tuple[dict, ...]:
    """Each switch: its effective value, the value it was stated with, and where it came from.

    Three SOURCES, and the middle one is a distinct fact rather than a rounding of the other two
    (review P3): ``absent`` is the case the whole issue is about; ``blank`` is a variable that
    exists and states nothing — and the truthy contract reads a blank value as **OFF for BOTH**
    switches, so calling it "unset → code default ON" for the value gate would print a default
    that is NOT the value in force; ``environment`` is an operator's real statement.

    ``code_default`` is DERIVED by resolving ``None`` through the same function, never written
    out as a literal here: a hand-written "ON"/"OFF" copy is a second declaration of each default
    that no test compares, so a future reopen of `#11445` could leave this line contradicting the
    gate it reports on — the exact failure the module exists to prevent.
    """
    reported: list[dict] = []
    for switch, resolve in SWITCHES:
        raw = os.environ.get(switch)
        if raw is None:
            source = "absent"
        elif not str(raw).strip():
            source = "blank"
        else:
            source = "environment"
        reported.append(
            {
                "switch": switch,
                "effective": bool(resolve(raw)),
                "stated": raw,
                "source": source,
                "code_default": bool(resolve(None)),
            }
        )
    return tuple(reported)


def startup_line() -> str:
    """The one-line startup record: both switches, both values, and where each came from."""
    parts = []
    for report in effective_switches():
        value = "ON" if report["effective"] else "OFF"
        if report["source"] == "absent":
            origin = f"unset → code default {'ON' if report['code_default'] else 'OFF'}"
        elif report["source"] == "blank":
            origin = f"stated {report['stated']!r} — blank reads as unset, so this value is the residual"
        else:
            origin = f"stated {report['stated']!r}"
        parts.append(f"{report['switch']}={value} ({origin})")
    return "tortoise embedding gates: " + " ".join(parts)


def report_effective_switches(
    *,
    logger: logging.Logger | None = None,
    stream: Any = None,
) -> str:
    """Emit the startup line (stderr + a log record) and return it.

    Both arms on purpose, mirroring ``emit_off_box_backup_signal``: the print is what a
    ``docker compose up`` / ``fly logs`` ladder shows, and the record is what a configured
    aggregator keeps. Never raises — a diagnostic that can break a boot is worse than no
    diagnostic (the `#8543` precedent).
    """
    line = startup_line()
    out = sys.stderr if stream is None else stream
    # A closed/absent stream, or a handler that raises in its formatter, must not break a boot.
    with contextlib.suppress(Exception):
        print(line, file=out)
    with contextlib.suppress(Exception):
        (logger or _logger).info(line)
    return line
