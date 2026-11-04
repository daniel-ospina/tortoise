"""Resolve every conflict block by keeping the PR side (HEAD), then re-derive the
`file:line` cites to the MERGED tree.

Why HEAD is the semantic side on every block in these files: origin/main's only
change to them (3d9b234d4, the #3590 checkpoint) re-derived the same cites over
text that predates #10214's seam, so it asserts `#9116` is OPEN and the surfaces
are dropped at the meta fold. Keeping it would revert the PR's own change.
Main's numbers are not discarded blindly, though: every number here is replaced
by the value measured on the merged tree, which is what the bundle's own cite
convention demands ("re-derived from the tree at the head it is stamped at").
"""
import sys

# measured on the merged tree (grep -n for the symbol each cite names)
NUMBERS = [
    ("sdk.py:14295", "sdk.py:14329"),   # "stats": _capture_stats_with_step_witness(...)
    ("sdk.py:14576", "sdk.py:14610"),   # proj.link_source_to_event(source_key, event_id)
    ("edges.py:2467-2492", "edges.py:2573-2598"),  # link_source_to_event
    ("sdk.py:13467", "sdk.py:13501"),   # def _extract_session_v2
    (":12504", ":12538"),               # the _extract_session_v2 call site
]


def resolve(path):
    out, mode = [], None
    for line in open(path, encoding="utf-8"):
        if line.startswith("<<<<<<<"):
            mode = "ours"
        elif line.startswith("=======") and mode == "ours":
            mode = "theirs"
        elif line.startswith(">>>>>>>") and mode == "theirs":
            mode = None
        elif mode == "theirs":
            continue
        else:
            out.append(line)
    text = "".join(out)
    for old, new in NUMBERS:
        text = text.replace(old, new)
    open(path, "w", encoding="utf-8").write(text)
    print(f"resolved {path}")


for p in sys.argv[1:]:
    resolve(p)
