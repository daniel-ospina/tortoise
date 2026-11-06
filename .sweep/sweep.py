"""Differential sweep: head vs the REVIEWED head 7f834a6a03 vs MarkdownIt oracle."""
from __future__ import annotations

import importlib.util
import itertools
import random
import sys

from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


NEW = load("tools/closes_landing.py", "cl_new")
OLD = load(".sweep/closes_landing_base.py", "cl_old")

KW = "Closes #100"


def credited(body: str) -> bool:
    """Does GitHub really credit the keyword? (survives outside <pre>/<code>)"""
    html = MD.render(body)
    # crude but exact for our bodies: the keyword must not be inside pre/code
    depth = 0
    i = 0
    out = []
    while i < len(html):
        if html.startswith("<pre", i) or html.startswith("<code", i):
            depth += 1
            j = html.find(">", i)
            i = j + 1
            continue
        if html.startswith("</pre>", i) or html.startswith("</code>", i):
            depth = max(0, depth - 1)
            i += html.find(">", i) + 1 - i
            continue
        if depth == 0:
            out.append(html[i])
        i += 1
    return "Closes #100" in "".join(out)


def arms(mod, body):
    lenient = bool(mod.parse_closing_refs(body))
    strict = bool(mod.parse_closing_refs(body, None, strict=True))
    ind = mod.strip_code(body, indented_code=True)
    indented = "Closes #100" in ind
    return (lenient, strict, indented)


FAMILIES = {
    "lenient": 0,
    "strict": 1,
    "indented_code": 2,
}


def run(bodies):
    stats = {k: {"new_failopen": 0, "new_lostclose": 0, "fixed_failopen": 0,
                 "fixed_lostclose": 0} for k in FAMILIES}
    raises = 0
    examples = {k: [] for k in FAMILIES}
    for body in bodies:
        try:
            w = credited(body)
            old = arms(OLD, body)
            new = arms(NEW, body)
        except Exception as exc:  # noqa: BLE001
            raises += 1
            if raises < 4:
                print("RAISE", repr(body), type(exc).__name__, exc)
            continue
        for name, idx in FAMILIES.items():
            o, n = old[idx], new[idx]
            if w is False and n is True and o is False:
                stats[name]["new_failopen"] += 1
                if len(examples[name]) < 6:
                    examples[name].append(("FAILOPEN", body))
            if w is False and n is True and o is True:
                pass  # pre-existing fail-open, unchanged
            if w is True and o is True and n is False:
                stats[name]["new_lostclose"] += 1
                if len(examples[name]) < 6:
                    examples[name].append(("LOSTCLOSE", body))
            if w is False and o is True and n is False:
                stats[name]["fixed_failopen"] += 1
            if w is True and o is False and n is True:
                stats[name]["fixed_lostclose"] += 1
    return stats, raises, examples


def grid():
    leads = [
        "- item", "-", "1.", "  1.", "> - item", "> Notes", "Notes", "> text",
        "  1.\n        x", "- outer\n  - inner", "> <div>", "<pre>", "<span>",
        "**bold**", "- > q", "> - ", "- [ ] crit", "  - item",
    ]
    mids = [
        "", "    cont", "> Notes", "<span>x</span>", "    ```", "```",
        "        x", "> - ", "- ", "2. ", "    -", "<br />", ">     ", "---",
        "-     x", "  2. x",
    ]
    tails = [
        KW, "    " + KW, "-     " + KW, "2.     " + KW, "  2.      " + KW,
        "    2.      " + KW, "> " + KW, "> -     " + KW, "      " + KW,
    ]
    for lead, mid, tail in itertools.product(leads, mids, tails):
        body = "\n".join(x for x in (lead, mid, tail) if x != "")
        yield body


def random_bodies(n, seed=11):
    rng = random.Random(seed)
    frags = [
        "- a", "1. b", "2. c", "> q", "> - a", "<div>", "<span>x</span>",
        "<pre>", "```", "~~~", "    code", "      code", "    2.     " + KW,
        KW, "-     " + KW, "  2.      " + KW, "", ">", "- ", "2. ", "**b**",
        "text", "    -", "---", "# H", "<br />", "</style>", "<pre/>",
        ">     ", "  cont", "> >", "* x", "+ y", "10. z", "       - w",
    ]
    for _ in range(n):
        k = rng.randint(2, 7)
        lines = [rng.choice(frags) for _ in range(k)]
        lines.insert(rng.randint(0, len(lines)), KW)
        yield "\n".join(lines)


def random_with(parent, n, seed):
    """Bodies whose later lines inherit a random prefix/indent from a random earlier line."""
    rng = random.Random(seed)
    frags = [
        "text", "> q", "- a", "1. b", "2.     " + KW, "-     " + KW,
        "    code", "```", "<span>x</span>", "<pre>", "</pre>", "", "---",
        "  2.      " + KW, "      x", "> - a", "- ", "* ", ">", "<div>",
    ]
    for _ in range(n):
        lines = []
        for _ in range(rng.randint(2, 6)):
            base = rng.choice(frags)
            if lines and rng.random() < 0.6:
                prev = rng.choice(lines)
                pref = ""
                i = 0
                while i < len(prev):
                    if prev[i] in " >\t":
                        pref += prev[i]
                        i += 1
                    else:
                        break
                lines.append(pref + base if rng.random() < 0.5 else base)
            else:
                lines.append(base)
        yield "\n".join(lines)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    sets = {}
    if which in ("all", "grid"):
        sets["grid"] = list(grid())
    if which in ("all", "rand"):
        sets["rand"] = list(random_bodies(12000))
        sets["randin"] = list(random_with(None, 12000, 7))
    for name, bodies in sets.items():
        stats, raises, examples = run(bodies)
        print(f"== {name}: {len(bodies)} bodies, raises={raises}")
        for arm, s in stats.items():
            print(f"   {arm:14s} new_failopen={s['new_failopen']:5d} "
                  f"new_lostclose={s['new_lostclose']:5d} "
                  f"fixed_failopen={s['fixed_failopen']:5d} "
                  f"fixed_lostclose={s['fixed_lostclose']:5d}")
            for tag, body in examples[arm]:
                print(f"      {tag}: {body!r} -> {MD.render(body).replace(chr(10), '')[:110]}")
