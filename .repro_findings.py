"""Reproduce the six open review findings at head 7f834a6a03."""
from __future__ import annotations

import re

from markdown_it import MarkdownIt

from tools.closes_landing import parse_closing_refs, strip_code

MD = MarkdownIt("commonmark")


def render(body: str) -> str:
    return MD.render(body)


def kind(body: str) -> str:
    html = render(body)
    if "<pre>" in html:
        return "CODE"
    return "PROSE"


def arms(body: str) -> tuple:
    l = parse_closing_refs(body)
    s = parse_closing_refs(body, None, strict=True)
    out = strip_code(body, indented_code=True)
    return (bool(l), bool(s), bool(out.strip()) and _keeps_keyword(body, out))


def _keeps_keyword(body: str, stripped: str) -> bool:
    return "Closes" in stripped


def show(label: str, body: str) -> None:
    print(f"--- {label}")
    print("  body:", repr(body))
    print("  renderer:", kind(body), "|", re.sub(r"\s+", " ", render(body))[:150])
    print("  lenient:", parse_closing_refs(body),
          " strict:", parse_closing_refs(body, None, strict=True),
          " indented_keeps:", "Closes" in strip_code(body, indented_code=True),
          " indented:", repr(strip_code(body, indented_code=True)))


# 1. P1 fail-open all three arms
show("1 fail-open all arms (bare `-` setext, no depth term)",
     "> text\n> Notes\n- \n  cont\n2.     Closes #100")

# 2. P1 lost close all three arms (pop before lazy)
show("2 lost close all arms (pop-before-lazy)",
     "  1.\n        x\n    2.      Closes #100")

# 3. P1 lost content in indented_code arm
show("3 lost content indented_code (4-col line under an open paragraph)",
     "Notes\n1. \n    Closes #100")
show("3b strip_code live-marker content",
     "Notes\n1. \n    IN FLIGHT: 3.2c — branch fix/x")
print("  3b strip_code(indented_code=True) =",
      repr(strip_code("Notes\n1. \n    IN FLIGHT: 3.2c — branch fix/x", indented_code=True)))
print("  3c strip_code(indented_code=True) =",
      repr(strip_code("Notes\n1. \n    - [ ] criterion", indented_code=True)))

# 4. P1 fail-open strict when carry crosses a lazy line that dropped a `>`
show("4 fail-open strict (carry depth from the lazy line)",
     "> Notes\n<span>x</span>\n    ```\n2.     Closes #100")

# 5. NEW: self-closing type-1 start tag
for tag in ("pre", "script", "style", "textarea"):
    show(f"5 self-closing <{tag}/>", f"<{tag}/>\n\n    Closes #100")

# 6. P2: closer table pairs each opener with its own closer
show("6a closer table <pre> ... </style>",
     "<pre>\nx\n</style>\n-     Closes #100")
show("6b closer table <style> ... </pre>",
     "<style>\nx\n</pre>\n-     Closes #100")
