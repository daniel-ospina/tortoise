#!/usr/bin/env python3
"""Extractor v2 pipeline runner — the owner-in-the-loop loop (design doc
§5.5) on the calibration window.

Runs the full 5-stage narrative-first pipeline (extractor_v2.py) on a
conversation transcript and saves EVERY stage's raw output to a results dir
so the owner can judge each stage's output directly:

  <out>/01-s1-story.md        compiled S1 narrative (chunked + compiled)
  <out>/02-s2-embed.json      S2 embed list (raw model output)
  <out>/03-s3-search.json     S3 graph-search results (degraded flag + reason)
  <out>/04-s4-embed.json      S4 complete embed list
  <out>/05-payload.json       S5 Layer-1 commit payload (+ notes/stats)
  <out>/REPORT.md             human-readable summary incl. minted kinds,
                              chain notes, warnings, token/cost telemetry

S3 reads the REAL graph backend when TORTOISE_DB_URI points at FalkorDB
(docker:// / redis://); with the backend unset/embedded it degrades
gracefully (the run still completes — every item treated as new).

Usage:
  uv run python tools/experiments/extractor-v2/run_v2_pipeline.py \
      [transcript.txt] [--out DIR] [--chunk-size N] [--dry]

  --dry  no model calls — print the pipeline wiring (chunk count, S1/S2/S4
         prompt sizes) and exit. Useful before spending tokens.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from tests.model_adapters import MODELS  # noqa: E402, RUF100
from tortoise import extractor_v2 as v2  # noqa: E402, RUF100

DEFAULT_TRANSCRIPT = Path(__file__).resolve().parents[3] / \
    "tests/eval/w-1272/w-design-bounded.txt"
DEFAULT_OUT = Path(__file__).resolve().parent / "v2-run-outputs"


def _load_transcript(path: Path) -> list[dict]:
    """The harness transcript format: '<index>: <role>: <text>' per line."""
    edus = []
    for i, line in enumerate(path.read_text().splitlines()):  # noqa: B007
        line = line.strip()
        if not line:
            continue
        parts = line.split(": ", 2)
        if len(parts) == 3:
            role, text = parts[1].strip(), parts[2].strip()
        else:
            role, text = "assistant", line
        if text:
            edus.append({"role": role, "content": text})
    return edus


def _write(out_dir: Path, name: str, data) -> None:
    if isinstance(data, str):
        (out_dir / name).write_text(data)
    else:
        (out_dir / name).write_text(json.dumps(data, indent=2))
    print(f"  → {out_dir.name}/{name}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    transcript_path = Path(args.pop(0)) if args and not args[0].startswith("--") \
        else DEFAULT_TRANSCRIPT
    out_dir = DEFAULT_OUT
    chunk_size = 50
    dry = False
    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_dir = Path(args[i + 1]); i += 2  # noqa: E702
        elif args[i] == "--chunk-size" and i + 1 < len(args):
            chunk_size = int(args[i + 1]); i += 2  # noqa: E702
        elif args[i] == "--dry":
            dry = True; i += 1  # noqa: E702
        else:
            print(f"[run_v2_pipeline] unknown arg: {args[i]}", file=sys.stderr)
            sys.exit(2)

    conversation = _load_transcript(transcript_path)
    if not conversation:
        print(f"[run_v2_pipeline] empty transcript: {transcript_path}",
              file=sys.stderr)
        sys.exit(2)
    out_dir.mkdir(parents=True, exist_ok=True)

    flash = MODELS["deepseek-flash"]()  # temperature 0.0, max_tokens None
    edus = [{"index": i, "role": e["role"], "text": e["content"]}
            for i, e in enumerate(conversation)]
    chunks = v2.chunk_transcript(edus, target=chunk_size)
    print(f"[run_v2_pipeline] transcript: {transcript_path.name} "
          f"({len(conversation)} turns → {len(chunks)} chunk(s))")
    print(f"[run_v2_pipeline] backend mode: {v2.resolve_backend_mode()} "
          f"(S3 {'reads the real graph' if v2.resolve_backend_mode() == 'real' else 'will degrade gracefully'})")
    print(f"[run_v2_pipeline] model: {flash.id} (temp {flash.temperature}, "
          f"max_tokens {flash.max_tokens})")

    if dry:
        story = v2.compile_stories([v2.S1_TMPL[:80]] * 0) or "…"  # noqa: F841
        s2 = v2.render_s2_prompt()
        s4 = v2.render_s4_prompt("…", {}, {"entities": [], "points": [],
                                           "events": [], "operators": []})
        print(f"[dry] S1 prompt: {len(v2.S1_TMPL)} chars "
              f"(× {len(chunks)} chunks)")
        print(f"[dry] S2 prompt: {len(s2)} chars (with master list)")
        print(f"[dry] S4 prompt: {len(s4)} chars")
        print("[dry] no model calls made — wiring OK")
        sys.exit(0)

    t0 = time.time()
    out = v2.extract_session_v2(flash, conversation, chunk_size=chunk_size)
    elapsed = round(time.time() - t0, 1)

    print(f"\n[run_v2_pipeline] {elapsed}s — S1→S5 complete", flush=True)
    _write(out_dir, "01-s1-story.md", out.get("story_arc") or "")
    _write(out_dir, "02-s2-embed.json", out.get("s2_embed") or {})
    _write(out_dir, "03-s3-search.json", out.get("search") or {})
    _write(out_dir, "04-s4-embed.json", out.get("embed_list") or {})
    _write(out_dir, "05-payload.json", out.get("payload") or {})

    report = [
        "# Extractor v2 pipeline run",
        "",
        f"- transcript: `{transcript_path.name}` ({len(conversation)} turns)",
        f"- chunks: {out['stats'].get('chunks')} (failed: "
        f"{out['stats'].get('failed_chunks')})",
        f"- backend: {out['search'].get('mode')} "
        f"(degraded: {out['search'].get('degraded')})",
        f"- elapsed: {elapsed}s",
        "",
        "## Stage outputs",
        "",
        "| Stage | Count |",
        "|---|---|",
        f"| S2/S4 entities | {len((out.get('embed_list') or {}).get('entities', []))} |",
        f"| S2/S4 events | {len((out.get('embed_list') or {}).get('events', []))} |",
        f"| S2/S4 points | {len((out.get('embed_list') or {}).get('points', []))} |",
        f"| S2/S4 operators | {len((out.get('embed_list') or {}).get('operators', []))} |",
        f"| S5 payload entities | {out['stats'].get('entities')} |",
        f"| S5 payload events | {out['stats'].get('events')} |",
        f"| S5 payload points | {out['stats'].get('points')} |",
        f"| S5 payload operators | {out['stats'].get('operators')} |",
        "",
    ]
    report.append("## Minted kinds (indicator b: must be 0)")
    report.append("\n".join(f"- {m}" for m in out.get("minted_kinds", []))
                  or "- (none)")
    report.append("\n## Chain notes (warn + repair, never block)")
    report.append("\n".join(
        f"- [{n.get('action')}] {n.get('chain')}: {n.get('finding')} — "
        f"{n.get('note')}" for n in out.get("chain_notes", [])) or "- (none)")
    report.append("\n## Warnings (S5 deterministic)")
    report.append("\n".join(f"- {w}" for w in out.get("warnings", []))
                  or "- (none)")
    report.append("\n## Errors")
    report.append("\n".join(f"- {e}" for e in out.get("errors", []))
                  or "- (none)")
    (out_dir / "REPORT.md").write_text("\n".join(report) + "\n")
    print(f"\n  → {out_dir.name}/REPORT.md\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
