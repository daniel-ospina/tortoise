"""Idempotency — input-keyed, so a re-run doesn't duplicate the graph.

The dedup key is the *input*, not the output (an LLM extractor won't reproduce
identical output). Two regimes, unified by `IngestKey`:
  - document / batch : key = content_hash(document)
  - stream           : key = (stream_id, offset_range)

The gate itself lives in EventAPI.begin_ingest, which scans prior IngestStarted
events for a matching (key, extractor_version).
"""
from __future__ import annotations

from dataclasses import dataclass

from .ids import content_hash


@dataclass(frozen=True)
class IngestKey:
    kind: str    # "document" | "stream"
    value: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value}


def document_key(text: str) -> IngestKey:
    return IngestKey("document", content_hash(text))


def stream_key(stream_id: str, start: int, end: int) -> IngestKey:
    return IngestKey("stream", f"{stream_id}:{start}-{end}")


@dataclass
class IngestResult:
    run_id: str | None
    skip: bool
    reason: str = ""
