"""Map every STALE `extractor_v2.py:<line>` cite in the #7949 docs onto the line
that now carries the same code, using the pre-#10360 tree as the referent.

`#10360` (91dbc9a8885) inserted a block into `tortoise/extractor_v2.py`; every
cite below it shifted. "Stale" is decided by EVIDENCE, not arithmetic:

  * the number is one the document already carried BEFORE the insert (`BASE`),
    so it is a pre-shift number, and
  * the line it names no longer carries what the pre-shift tree carried there, and
  * the cite is LIVE prose: §10.x change-control entries in
    `PRE-REGISTRATION.md` and the frozen-log paragraph in `README.md` quote
    numbers the documents no longer claim, so both are skipped.

Shapes handled, and NOTHING else: `extractor_v2.py:<n>[-<m>]`, and a BARE `:<n>`
inside a paragraph that names `extractor_v2` (the bundle's continuation style).
Issue numbers (`#7949`) and `sdk.py:`/`edges.py:` cites are never matched.
"""
from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
BASE = "91dbc9a8885^"
SOURCE = "tortoise/extractor_v2.py"
DOCS = [
    "docs/evidence/7949-capstone/README.md",
    "docs/00_index.md",
    "docs/evidence/7949-capstone/PRE-REGISTRATION.md",
    "docs/evidence/7949-capstone/E1-E5-PROOFS.md",
    "docs/evidence/7949-capstone/probes/capstone_measure.py",
    "docs/evidence/7949-capstone/probes/capstone_run.py",
    "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
]

PREFIXED = re.compile(r"extractor_v2\.py:(\d{4,5})(?:-(\d{4,5}))?")
BARE = re.compile(r"(?<![#\w]):(\d{4,5})(?:-(\d{4,5}))?")
ANY_NUMBER = re.compile(r"(?:(?<=py:)|(?<=[:：-]))(\d{4,5})(?![0-9])")
#: the same row scoping `_BUNDLE_ROW_FILTER` applies: a shared router row or an
#: architecture doc full of OTHER epics' cites must not be re-pointed by us
ROW_SCOPE = {
    "docs/00_index.md": re.compile(r"#7949"),
    "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md": re.compile(r"run_s2\("),
}
HISTORICAL = re.compile(r"no longer\s+resolve|SUPERSEDED and its file is gone",
                        re.IGNORECASE)


def paragraphs(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for sep in re.finditer(r"\n[ \t]*\n", text):
        spans.append((start, sep.start()))
        start = sep.end()
    spans.append((start, len(text)))
    return spans


def history_lines(text: str) -> set[int]:
    """1-based line numbers inside a `### 10.x` change-control entry."""
    dead, skipping = set(), False
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^### 10\.", line):
            skipping = True
            dead.add(i)
            continue
        if skipping and re.match(r"^#{1,3} ", line):
            skipping = False
        if skipping:
            dead.add(i)
    return dead


def main():
    apply = "--apply" in sys.argv
    old = subprocess.run(["git", "show", f"{BASE}:{SOURCE}"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    new = (REPO / SOURCE).read_text(encoding="utf-8").splitlines()
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    forward: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(i2 - i1):
                forward[i1 + k + 1] = j1 + k + 1
    total = 0
    for doc in DOCS:
        path = REPO / doc
        text = path.read_text(encoding="utf-8")
        base = subprocess.run(["git", "show", f"{BASE}:{doc}"],
                              capture_output=True, text=True)
        if base.returncode != 0:
            print(f"(no BASE copy of {doc}; skipped)")
            continue
        base_numbers = {int(n) for n in ANY_NUMBER.findall(base.stdout)}
        # LINE-LEVEL provenance: a cite is only ours to move if the number it
        # carries is the one the BASE document had on the aligned line. A line
        # already re-derived carries a number BASE never had there, and moving it
        # again would double-shift it (`:12924 -> :13000` then `:13000 -> :13076`).
        base_lines = base.stdout.splitlines()
        cur_lines = text.splitlines()
        aligned: dict[int, str] = {}
        dmatcher = difflib.SequenceMatcher(None, base_lines, cur_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in dmatcher.get_opcodes():
            if tag == "equal" or (tag == "replace" and (i2 - i1) == (j2 - j1)):
                for k in range(i2 - i1):
                    aligned[j1 + k] = base_lines[i1 + k]
        para_spans = paragraphs(text)
        dead = history_lines(text)
        line_of = lambda pos: text.count("\n", 0, pos) + 1

        def inherited(pos: int, number: int) -> bool:
            """Was THIS number already on the aligned line of the BASE document?"""
            base_line = aligned.get(line_of(pos) - 1)
            if base_line is None:
                # A shared-router row is ONE enormous line, so a single edit makes
                # the whole line a `replace` block with no index alignment. The row
                # is still identifiable by its own scope token (#7949 / run_s2()).
                scope = ROW_SCOPE.get(doc)
                if scope is None:
                    return False
                rows = [ln for ln in base_lines if scope.search(ln)]
                if not rows:
                    return False
                # the scope token is not unique across the router (#7949 is named
                # by many rows), so the ROW is identified by the number itself:
                # it must be one BASE carried under the same scope.
                base_line = "\n".join(rows)
            return bool(re.search(rf"(?<![0-9]){number}(?![0-9])", base_line))

        def live(pos: int) -> bool:
            if line_of(pos) in dead:
                return False
            para = next((p for p in para_spans if p[0] <= pos < p[1]), None)
            return para is not None and not HISTORICAL.search(text[para[0]:para[1]])

        def stale(n: int, m: int | None) -> bool:
            if n not in base_numbers:
                return False
            if not (1 <= n <= len(old)) or n > len(new):
                return False
            if new[n - 1] != old[n - 1]:
                return True
            return bool(m and m <= len(new) and m <= len(old)
                        and new[m - 1] != old[m - 1])

        def target(n: int) -> int | None:
            got = forward.get(n)
            return got if got not in (None, n) else None

        edits, taken = [], []
        for match in PREFIXED.finditer(text):
            if not live(match.start()):
                continue
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else None
            if not stale(start, end) or not inherited(match.start(), start):
                continue
            new_start = target(start)
            new_end = target(end) if end else None
            if new_start is None or (end and new_end is None):
                continue
            edits.append((match.start(), match.end(), match.group(0),
                          f"extractor_v2.py:{new_start}"
                          + (f"-{new_end}" if end else "")))
            taken.append((match.start(), match.end()))
        for match in BARE.finditer(text):
            if any(s <= match.start() < e for s, e in taken) or not live(match.start()):
                continue
            para = next(p for p in para_spans if p[0] <= match.start() < p[1])
            body = text[para[0]:para[1]]
            own_line = text.splitlines()[line_of(match.start()) - 1]
            scope = ROW_SCOPE.get(doc)
            if scope is not None and not scope.search(own_line):
                continue
            # the cite's OWN line must not be another file's: a bare `:N` beside a
            # `sdk.py:`/`edges.py:` cite belongs to THAT file (the entry-point
            # paragraph's `called at :N` is the sdk call site).
            # a bare `:N` is an EXTRACTOR continuation by house style; the one
            # exception is the entry-point paragraph, whose continuation is the
            # sdk CALL site — skipped because its own line names `sdk.py`.
            if "sdk.py" in own_line or "edges.py" in own_line:
                continue
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else None
            if not stale(start, end) or not inherited(match.start(), start):
                continue
            new_start = target(start)
            new_end = target(end) if end else None
            if new_start is None or (end and new_end is None):
                continue
            edits.append((match.start(), match.end(), match.group(0),
                          f":{new_start}" + (f"-{new_end}" if end else "")))
        for _s, _e, was, now in edits:
            print(f"{doc}: {was} -> {now}")
        total += len(edits)
        if edits and apply:
            for start, end, _was, now in sorted(edits, reverse=True):
                text = text[:start] + now + text[end:]
            path.write_text(text, encoding="utf-8")
    print(f"{total} cites {'applied' if apply else 'to change (DRY RUN)'}")


if __name__ == "__main__":
    main()
