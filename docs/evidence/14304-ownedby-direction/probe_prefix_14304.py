"""The #14304 non-vacuity probe: three assertions that RED on the PRE-FIX tree.

WHY THIS FILE IS COMMITTED
--------------------------
`README.md` §3a claims the suite's assertions are not vacuous — they fail on the
tree the fix replaced. That claim used to be backed by a *transcript* whose probe
lived nowhere and whose command named a file no reader has, and the suite's own
docstring pointed at the lane's `WORKTREE-REPORT.md`, which is **gitignored by
design** (`AGENTS.md` → "Every lane leaves a `WORKTREE-REPORT.md`" — "Its files …
are gitignored and never committed"). So the one assertion in this bundle that a
reviewer must be able to falsify was the one with no distributable artifact
behind it. A record that cannot be re-run is a claim, not evidence.

This file is the artifact. It is a standalone script, NOT a pytest module (it is
deliberately outside `tests/`, so no CI surface has to collect it), and it
imports ONLY what exists on the parent commit — that is the point: the committed
suite cannot be used for this, because `tests/test_relation_orientation_14304.py`
imports `orient_declared_pair`, which the parent tree does not have.

HOW TO RUN IT (reproduces §3a exactly; exit 1 with `3 failed` on the pre-fix tree)
----------------------------------------------------------------------------------
    # the parent of the fix commit — the tree the fix replaced
    git worktree add --detach /tmp/pfx-14304 344a1c086e06
    cp docs/evidence/14304-ownedby-direction/probe_prefix_14304.py /tmp/pfx-14304/probe_prefix_14304.py
    ( cd /tmp/pfx-14304 && python probe_prefix_14304.py )     # → exit 1, 3 failed
    git worktree remove --force /tmp/pfx-14304

On the FIXED tree the same script exits 0 with `3 passed` — run it from the repo
root the same way. Both directions are measured, not asserted; the two runs are
transcribed in `README.md` §3a and §3b.

⛔ The parent is `344a1c086e06` (the parent of the fix commit `2cea260550a3`).
The original run was made at `32ce876fbffa`, which is an ANCESTOR of it, not the
parent: `git diff 32ce876fbffa 344a1c086e06 -- tortoise/relation_write.py
tortoise/relation_guards.py` is EMPTY, so the two trees are identical in exactly
the code these assertions exercise, and the reds hold at either. The bundle names
both rather than quietly relabelling the run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tortoise.relation_write import (  # after the sys.path insert
    apply_relation_edges,
    plan_relation_edges,
)

#: The `#14592` corpus pair, with the kinds the census resolved (`out/census.json`:
#: `Marta Reyes`/`Dana` is a `core:naturalPerson`, a `:Subject`; the account is an
#: `Object`).
DANA = "subj-dana"
ACCOUNT = "obj-nordicfreight"
INDEX = {
    "Dana": [(DANA, "core:naturalPerson")],
    "NordicFreight account": [(ACCOUNT, "object")],
}
RELATIONS = [{"src": "Dana", "dst": "NordicFreight account", "predicate": "ownedBy"}]


class _RecordingWriter:
    """An SDK whose one linking verb records what it was asked to write."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def link_entities(self, predicate, src, dst, **kw):
        self.calls.append((predicate, src, dst, kw))
        return {"ok": True}


def test_the_corpus_pair_is_written_owned_first() -> None:
    """The plan hands the writer the DECLARED order: account -> Dana."""
    edges, drops = plan_relation_edges(RELATIONS, INDEX)
    assert drops == []
    assert len(edges) == 1
    assert (edges[0].src_id, edges[0].dst_id) == (ACCOUNT, DANA)


def test_the_writer_receives_the_declared_orientation() -> None:
    """The ENDS reach `link_entities` in the declared order — the whole fix."""
    writer = _RecordingWriter()
    apply_relation_edges(writer, RELATIONS, INDEX)
    assert [call[:3] for call in writer.calls] == [("ownedBy", ACCOUNT, DANA)]


def test_the_receipt_declares_the_correction() -> None:
    """AX Rule 1: a redirect is DECLARED — on the receipt AND the warnings."""
    writer = _RecordingWriter()
    warnings: list[str] = []
    result = apply_relation_edges(writer, RELATIONS, INDEX, warn=warnings.append)
    row = result["edges"][0]
    assert row["orientation"] is not None, "the pre-fix receipt has no `orientation` key at all"
    assert any("RE-ORIENTED (#14304)" in w for w in warnings), warnings


def main() -> int:
    tests = [
        test_the_corpus_pair_is_written_owned_first,
        test_the_writer_receives_the_declared_orientation,
        test_the_receipt_declares_the_correction,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # a red IS the measurement
            failed += 1
            print(f"FAILED {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"passed {test.__name__}")
    print(f"{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
