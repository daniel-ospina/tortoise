#!/usr/bin/env python3
"""Parity validation (issue #1350 verification checklist) — does the v2
5-stage pipeline preserve the logic vs the current v1 extractor?

- Path A (current): value_extractor.summarize → {decisions, state, logic}
  (the v1 production summary stream; construct/ground adds wiring on top).
- Path B (new): extractor_v2.extract_session_v2 (S1→S2→S3→S4→S5) → embed
  list, mapped to the comparison surface:
      decisions ← events (eventKind decision/occurrence)
      state     ← entities
      logic     ← points

Parity = set containment: every decision/state/logic item in A must appear
in B (no logic loss A→B); B may find more (gain is not loss). Cost =
measured tokens both paths.

Usage:
  uv run python tools/experiments/extractor-v2/run_parity_v2.py \
      [transcript.txt] [--runs N] [--chunk-size N]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from tests.model_adapters import MODELS  # noqa: E402
from tortoise import extractor_v2 as v2  # noqa: E402
from tortoise.value_extractor import summarize  # noqa: E402

DEFAULT_TRANSCRIPT = Path(__file__).resolve().parents[3] / \
    "tests/eval/w-1272/w-design-bounded.txt"
OUT = Path("/tmp/v2-parity-report.json")


def _load_edus(path: Path) -> list[dict]:
    edus = []
    for i, line in enumerate(path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        parts = line.split(": ", 2)
        if len(parts) == 3:
            role, text = parts[1].strip(), parts[2].strip()
        else:
            role, text = "assistant", line
        if text:
            edus.append({"index": i, "role": role, "text": text})
    return edus


def _norm(items: list[dict]) -> set[str]:
    norm = set()
    for it in items or []:
        for key in ("content", "point", "name", "text"):
            if it.get(key):
                norm.add(re.sub(r"\s+", " ", str(it[key]).strip().lower())[:120])
    return norm


def _tokens(model) -> dict:
    return {"in": getattr(model, "last_prompt_tokens", 0),
            "out": getattr(model, "last_completion_tokens", 0)}


def _path_a(flash, edus: list[dict]) -> dict:
    """v1: the current production summary stream (decisions/state/logic)."""
    summary = summarize(flash, edus, chunk_size=6)
    return {
        "decisions": summary.get("decisions", []) or [],
        "state": summary.get("state", []) or [],
        "logic": summary.get("logic", []) or [],
        "tokens": _tokens(flash),
    }


def _path_b(flash, edus: list[dict], chunk_size: int = 50) -> dict:
    """v2: S1→S5, mapped to the comparison surface."""
    conv = [{"role": e["role"], "content": e["text"]} for e in edus]
    out = v2.extract_session_v2(flash, conv, chunk_size=chunk_size)
    embed = out.get("embed_list") or {}
    return {
        "decisions": [{"content": ev.get("content", "")}
                      for ev in (embed.get("events") or [])
                      if str(ev.get("eventKind", "")).lower().endswith(
                          ("decision", "occurrence", "meeting", "deployment"))],
        "state": [{"name": e.get("name", "")}
                  for e in (embed.get("entities") or [])],
        "logic": [{"point": p.get("content", "")}
                  for p in (embed.get("points") or [])],
        "tokens": _tokens(flash),
        "payload": out.get("payload"),
        "minted": out.get("minted_kinds", []),
        "chain_notes": out.get("chain_notes", []),
        "warnings": out.get("warnings", []),
        "errors": out.get("errors", []),
        "search_degraded": (out.get("search") or {}).get("degraded"),
    }


def _containment(a: dict, b: dict) -> dict:
    a_sets = {k: _norm(a.get(k)) for k in ("decisions", "state", "logic")}
    b_sets = {k: _norm(b.get(k)) for k in ("decisions", "state", "logic")}
    b_raw = {k: (b.get(k) or []) for k in ("decisions", "state", "logic")}

    def _near(item: str, b_items: list[dict], b_norm: set[str]) -> dict | None:
        """Closest B-side item by token overlap (0 = none). Distinguishes
        'rephrased' (high overlap) from 'truly missing' (low/none)."""
        import re
        t = set(re.sub(r"[^a-z0-9 ]", " ", item).split())
        if not t:
            return None
        best, best_o = None, 0.0
        for bi in b_items:
            for key in ("content", "point", "name", "text"):
                if not bi.get(key):
                    continue
                s = re.sub(r"[^a-z0-9 ]", " ", str(bi[key]).strip().lower())
                ts = set(s.split())
                if not ts:
                    continue
                o = len(t & ts) / min(len(t), len(ts))
                if o > best_o:
                    best_o, best = o, str(bi[key])[:80]
        return {"best": best, "overlap": round(best_o, 2)}

    near = {k: [] for k in a_sets}
    for k in a_sets:
        for item in sorted(a_sets[k] - b_sets[k]):
            near[k].append({"item": item[:100], "near": _near(item, b_raw[k], b_sets[k])})
    return {
        "loss": {k: sorted(a_sets[k] - b_sets[k]) for k in a_sets},
        "gain": {k: sorted(b_sets[k] - a_sets[k]) for k in a_sets},
        "counts_a": {k: len(a_sets[k]) for k in a_sets},
        "counts_b": {k: len(b_sets[k]) for k in a_sets},
        "loss_total": sum(len(a_sets[k] - b_sets[k]) for k in a_sets),
        "contained": all(not (a_sets[k] - b_sets[k]) for k in a_sets),
        # per-item nearest B match: overlap >= 0.5 → rephrased, else → real drop
        "loss_analysis": near,
    }


def main() -> None:
    args = [a for a in sys.argv[1:]]
    path = Path(args.pop(0)) if args and not args[0].startswith("--") \
        else DEFAULT_TRANSCRIPT
    runs = 3
    chunk_size = 50
    i = 0
    while i < len(args):
        if args[i] == "--runs" and i + 1 < len(args):
            runs = int(args[i + 1]); i += 2
        elif args[i] == "--chunk-size" and i + 1 < len(args):
            chunk_size = int(args[i + 1]); i += 2
        else:
            print(f"[run_parity_v2] unknown arg: {args[i]}", file=sys.stderr)
            sys.exit(2)

    edus = _load_edus(path)
    print(f"[parity] {path.name}: {len(edus)} EDUs, {runs} run(s), "
          f"backend={v2.resolve_backend_mode()}", flush=True)
    flash = MODELS["deepseek-flash"]()
    report = {"window": path.name, "runs": []}
    for r in range(runs):
        print(f"=== RUN {r} ===", flush=True)
        t0 = time.time()
        a = _path_a(flash, edus)
        a_secs = round(time.time() - t0)
        t0 = time.time()
        b = _path_b(flash, edus, chunk_size=chunk_size)
        b_secs = round(time.time() - t0)
        c = _containment(a, b)
        run = {
            "run": r,
            "A": {"decisions": len(a["decisions"]), "state": len(a["state"]),
                  "logic": len(a["logic"]), "tokens": a["tokens"], "secs": a_secs},
            "B": {"decisions": len(b["decisions"]), "state": len(b["state"]),
                  "logic": len(b["logic"]), "tokens": b["tokens"], "secs": b_secs,
                  "minted_kinds": b["minted"], "errors": b["errors"],
                  "search_degraded": b["search_degraded"],
                  "payload_ok": b["payload"] is not None},
            "containment": {"contained": c["contained"], "loss_total": c["loss_total"],
                            "loss": c["loss"], "gain_counts": {k: len(v)
                                                               for k, v in c["gain"].items()}},
            "loss_analysis": c.get("loss_analysis", {}),
        }
        report["runs"].append(run)
        rephrased = sum(1 for k, lst in c.get("loss_analysis", {}).items()
                        for it in lst if (it.get("near") or {}).get("overlap", 0) >= 0.5)
        real_drop = c["loss_total"] - rephrased
        print(f"  A: dec={len(a['decisions'])} state={len(a['state'])} "
              f"logic={len(a['logic'])} tok={a['tokens']} {a_secs}s", flush=True)
        print(f"  B: dec={len(b['decisions'])} state={len(b['state'])} "
              f"logic={len(b['logic'])} tok={b['tokens']} {b_secs}s", flush=True)
        print(f"  containment: contained={c['contained']} loss={c['loss_total']} "
              f"(rephrased≈{rephrased}, real_drop≈{real_drop})", flush=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(f"\nreport -> {OUT}")


if __name__ == "__main__":
    main()
