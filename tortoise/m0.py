"""M0 spine: transcript file → extractor → JSONL log → projection → grid HTML.

    python -m tortoise.m0 <transcript.txt> [--out graph.html] [--log events.jsonl]

Proves the datatypes, the fold, and the render hang together end to end. No
FalkorDB, no streaming, no idempotency yet — those are M1+.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .api import EventAPI
from .extractor import MockExtractor
from .log import EventLog
from .projection import fold, split
from .render import render


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tortoise M0 spine")
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--out", type=Path, default=Path("graph.html"))
    ap.add_argument("--log", type=Path, default=Path("events.jsonl"))
    args = ap.parse_args(argv)

    text = args.transcript.read_text(encoding="utf-8")
    source_id = args.transcript.name

    # fresh log for the spike (idempotent re-ingest is M1)
    if args.log.exists():
        args.log.unlink()
    log = EventLog(args.log)
    api = EventAPI(log, initiated_by="extractor", agent_id=MockExtractor.version)

    MockExtractor().run(text, source_id, api)

    points = fold(log.read_all())
    statements, operators = split(points)
    args.out.write_text(render(points, title=f"Tortoise — {source_id}"),
                        encoding="utf-8")

    print(f"transcript : {args.transcript}  ({len(text)} chars)")
    print(f"events     : {len(log.read_all())}  → {args.log}")
    print(f"points     : {len(statements)} statements, {len(operators)} operators")
    print(f"render     : {args.out}")


if __name__ == "__main__":
    main()
