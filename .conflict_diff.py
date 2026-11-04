"""Show a word-level diff of each conflict hunk's two sides, so a resolution can
name what actually differs (a re-derived number vs a semantic claim)."""
import difflib
import re
import sys

MARK = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.M)


def hunks(text):
    """Yield (ours, theirs) for each conflict block."""
    out = []
    ours, theirs, mode = [], [], None
    for line in text.splitlines(keepends=True):
        if line.startswith("<<<<<<<"):
            mode, ours, theirs = "ours", [], []
        elif line.startswith("=======") and mode == "ours":
            mode = "theirs"
        elif line.startswith(">>>>>>>") and mode == "theirs":
            out.append(("".join(ours), "".join(theirs)))
            mode = None
        elif mode == "ours":
            ours.append(line)
        elif mode == "theirs":
            theirs.append(line)
    return out


def words(s):
    return re.findall(r"\S+|\s+", s)


for path in sys.argv[1:]:
    text = open(path, encoding="utf-8").read()
    hs = hunks(text)
    print(f"\n########## {path} — {len(hs)} hunk(s)")
    for i, (o, t) in enumerate(hs, 1):
        print(f"\n===== hunk {i}")
        ow, tw = words(o), words(t)
        sm = difflib.SequenceMatcher(None, ow, tw, autojunk=False)
        for op, a1, a2, b1, b2 in sm.get_opcodes():
            if op == "equal":
                continue
            ours_seg = "".join(ow[a1:a2]).strip()
            theirs_seg = "".join(tw[b1:b2]).strip()
            print(f"  {op}: OURS={ours_seg!r}")
            print(f"        THEIRS={theirs_seg!r}")
