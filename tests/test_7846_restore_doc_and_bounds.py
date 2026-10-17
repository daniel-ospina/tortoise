"""#7846 — hermetic guards for the restore test's docstring and its bounds.

The live restore contract lives in ``test_indexed_graph_key_restore_7846.py``
(docker/server FalkorDB only). This module needs no server at all: it pins the
two review findings that are checkable without one.

1. The restore test's "WHAT WAS MEASURED" table must agree with the committed
   evidence it cites (``docs/evidence/7846-indexed-graph-restore/README.md`` §2).
   Before this guard four of its five rows disagreed — ``0.36 s``, ``4.70 s``,
   ``37.67 MB`` and ``1.57 s`` appear in no committed log — and the ``37.67 MB``
   payload was ~2.6× the actual one.
2. The restore assertion bound must stay BELOW the client socket timeout, so
   "the restore overran its bound" and "the socket timed out" remain
   distinguishable failure modes — the stated point of acceptance criterion 3.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests import test_indexed_graph_key_restore_7846 as restore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_README = _REPO_ROOT / "docs" / "evidence" / "7846-indexed-graph-restore" / "README.md"

#: The first number (if any) in a measurement cell, e.g. ``0.42 s`` → ``0.42``.
_NUMERIC = re.compile(r"\d+(?:\.\d+)?")

#: Column headers shared by both measurement tables.
_COLUMNS = ("nodes", "DUMP", "RESTORE")


def _measurement_rows(text: str) -> list[dict[str, str]]:
    """The rows of the first ``engine (graph module)`` measurement table."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("|") or "engine (graph module)" not in line:
            continue
        header = [cell.strip() for cell in line.strip().strip("|").split("|")]
        index = {name: header.index(name) for name in _COLUMNS}
        rows: list[dict[str, str]] = []
        for row in lines[i + 2 :]:
            if not row.startswith("|"):
                break
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            rows.append({name: cells[j] for name, j in index.items()})
        return rows
    raise AssertionError("no `engine (graph module)` measurement table found")


def _norm(value: str) -> str:
    """Strip markdown emphasis and whitespace so ``14.42 MB`` == ``14.42  MB``."""
    return re.sub(r"\s+", "", value).replace("`", "")


def test_docstring_measurements_match_the_evidence_readme() -> None:
    """Every row the test docstring reports must be a committed evidence row.

    The docstring points the reader at the evidence directory for "full evidence
    and raw logs", so the two must not disagree. This fails on the pre-fix
    docstring, whose ``0.36 s`` / ``4.70 s`` / ``37.67 MB`` / ``1.57 s`` cells
    match no row in the README.
    """
    doc_rows = _measurement_rows(restore.__doc__ or "")
    evidence_rows = _measurement_rows(_README.read_text())

    assert doc_rows, "the restore test docstring carries no measurement table"

    for row in doc_rows:
        same_size = [r for r in evidence_rows if _norm(r["nodes"]) == _norm(row["nodes"])]
        assert same_size, (
            f"the docstring reports {row['nodes']} nodes, a size in no committed "
            f"evidence row ({_README})"
        )
        same_size_and_dump = [r for r in same_size if _norm(r["DUMP"]) == _norm(row["DUMP"])]
        assert same_size_and_dump, (
            f"the docstring's DUMP {row['DUMP']!r} at {row['nodes']} nodes matches "
            f"no committed evidence row ({_README})"
        )
        tokens = _NUMERIC.findall(_norm(row["RESTORE"]))
        assert any(
            all(token in _norm(r["RESTORE"]) for token in tokens) for r in same_size_and_dump
        ), (
            f"the docstring's RESTORE {row['RESTORE']!r} at {row['nodes']} nodes "
            f"matches no committed evidence row ({_README})"
        )


def test_restore_bound_is_below_the_socket_timeout() -> None:
    """The two #7846 failure modes must remain distinguishable (criterion 3).

    If the assertion bound equalled the client socket timeout, a restore that
    overran its bound would raise a socket timeout at the same instant the
    assertion would fire, so the caller could not tell "overran its bound" from
    "the socket timed out". The bound is deliberately the lower of the two.
    """
    assert restore._RESTORE_BOUND_S < restore._RESTORE_TIMEOUT_S, (
        f"the restore bound ({restore._RESTORE_BOUND_S}s) must stay below the "
        f"client socket timeout ({restore._RESTORE_TIMEOUT_S}s), or the two "
        f"failure modes are indistinguishable"
    )
