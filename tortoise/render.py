"""Static grid renderer — a self-contained HTML/SVG picture of the graph.

Deterministic lattice layout (README): points are placed in creation order into
grid cells; nothing is force-directed. Statement-points are boxes; operator-points
are diamonds (NAND red / IMPL green) with lines to the points they relate — the
honest bipartite/hypergraph render, not edges. M0 is a one-shot snapshot; the
live streaming version is M4.
"""
from __future__ import annotations

import html
import math

CELL_W, CELL_H, GAP, MARGIN = 210, 120, 46, 40
COLORS = {"NAND": "#c0392b", "IMPL": "#2e8b57"}


def _wrap(text: str, width: int = 26, max_lines: int = 4) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and (len(" ".join(lines)) < len(text)):
        lines[-1] = lines[-1][:width - 1] + "…"
    return lines


def render(points: dict[str, dict], *, title: str = "Tortoise graph") -> str:
    ordered = sorted(points.values(), key=lambda p: p.get("createdAt") or p.get("created_at", ""))
    n = len(ordered)
    cols = max(1, math.ceil(math.sqrt(n * 1.6)))
    rows = max(1, math.ceil(n / cols))
    pos = {}
    for i, p in enumerate(ordered):
        r, c = divmod(i, cols)
        pos[p["id"]] = (MARGIN + c * (CELL_W + GAP), MARGIN + r * (CELL_H + GAP))

    def center(pid):
        x, y = pos[pid]
        return x + CELL_W / 2, y + CELL_H / 2

    w = MARGIN * 2 + cols * CELL_W + (cols - 1) * GAP
    h = MARGIN * 2 + rows * CELL_H + (rows - 1) * GAP

    lines, nodes = [], []
    for p in ordered:
        op = p.get("operator")
        x, y = pos[p["id"]]
        if op:
            ox, oy = center(p["id"])
            color = COLORS.get(op["op_type"], "#7f8c8d")
            for src in op["inputs"]:
                if src in pos:
                    sx, sy = center(src)
                    lines.append(
                        f'<line x1="{sx:.0f}" y1="{sy:.0f}" x2="{ox:.0f}" y2="{oy:.0f}" '
                        f'stroke="{color}" stroke-width="2" opacity="0.7"/>'
                    )
            s = 30
            nodes.append(
                f'<polygon points="{ox:.0f},{oy-s:.0f} {ox+s:.0f},{oy:.0f} '
                f'{ox:.0f},{oy+s:.0f} {ox-s:.0f},{oy:.0f}" fill="{color}"/>'
                f'<text x="{ox:.0f}" y="{oy+4:.0f}" text-anchor="middle" '
                f'fill="#fff" font-size="12" font-weight="700">{op["op_type"]}</text>'
            )
        else:
            spk = html.escape(p["provenance"].get("speaker") or "")
            body = "".join(
                f'<tspan x="{x+14}" dy="{18 if k else 0}">{html.escape(ln)}</tspan>'
                for k, ln in enumerate(_wrap(p["content"]))
            )
            nodes.append(
                f'<g><rect x="{x}" y="{y}" width="{CELL_W}" height="{CELL_H}" rx="10" '
                f'fill="#fbf7ee" stroke="#c9bfa8" stroke-width="1.5"/>'
                f'<text x="{x+14}" y="{y+16}" font-size="10" fill="#a2957a" '
                f'font-weight="700">{spk}</text>'
                f'<text x="{x+14}" y="{y+38}" font-size="13" fill="#3a342a">{body}</text></g>'
            )

    legend = (
        '<div class="legend"><span><i style="background:#fbf7ee;border:1px solid #c9bfa8">'
        '</i> point</span>'
        f'<span><i style="background:{COLORS["IMPL"]}"></i> IMPL (supports)</span>'
        f'<span><i style="background:{COLORS["NAND"]}"></i> NAND (refutes)</span></div>'
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>
body{{margin:0;font-family:-apple-system,system-ui,sans-serif;background:#f4efe4;color:#3a342a}}
header{{padding:16px 24px;border-bottom:1px solid #dcd2bd}}
h1{{margin:0;font-size:17px}} .meta{{color:#8a7f68;font-size:13px;margin-top:4px}}
.legend{{display:flex;gap:20px;padding:10px 24px;font-size:13px;color:#6b6252}}
.legend i{{display:inline-block;width:13px;height:13px;border-radius:3px;vertical-align:-2px;margin-right:5px}}
.canvas{{overflow:auto}}
</style></head><body>
<header><h1>{html.escape(title)}</h1>
<div class="meta">{n} points · deterministic lattice layout · M0 snapshot</div></header>
{legend}
<div class="canvas"><svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">
{''.join(lines)}
{''.join(nodes)}
</svg></div></body></html>"""
