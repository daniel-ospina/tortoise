"""Measure the two costs the artifact quoted without a committed source.

Reproduces the figures the #9558 artifact and docstrings cite for:
  * one `_migrations_fingerprint` pass (the `stat` cost the cache pays per consult)
  * a blake2b content digest over the same fifteen files (the rejected alternative)

Writes `fingerprint-cost.json` beside itself so the numbers have a citable source,
exactly like `runs/*.json`.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path

from tortoise.record_projection import RECORD_MIGRATIONS, _migrations_fingerprint


def main() -> int:
    base = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
    names = list(RECORD_MIGRATIONS)
    total_bytes = sum((base / name).stat().st_size for name in names)

    def stat_pass() -> None:
        _migrations_fingerprint(base)

    def digest() -> None:
        handle = hashlib.blake2b(digest_size=16)
        for name in names:
            handle.update(name.encode())
            handle.update((base / name).read_bytes())

    reps = 200
    out: dict[str, object] = {
        "migrations": len(names),
        "total_bytes": total_bytes,
        "reps": reps,
        "note": ("wall-clock on the box this was taken on; the RATIO is the "
                 "transferable figure, the absolute values are not"),
    }
    for label, fn in (("fingerprint_pass", stat_pass),
                      ("blake2b_digest", digest)):
        fn()                                    # warm the page cache
        samples: list[float] = []
        for _ in range(reps):
            started = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - started) * 1000.0)
        out[label] = {
            "mean_ms": round(statistics.fmean(samples), 4),
            "median_ms": round(statistics.median(samples), 4),
            "min_ms": round(min(samples), 4),
            "max_ms": round(max(samples), 4),
        }
    target = Path(__file__).with_name("fingerprint-cost.json")
    target.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps(out, indent=2, sort_keys=True))
    print(f"\nwritten to {target}")
    return 0


# ⛔ GUARDED: an unguarded module-level call makes an IMPORT of this probe REWRITE the
# committed `fingerprint-cost.json` with the importing box's wall-clock, silently
# invalidating the numbers every docstring and the README cite. Every other probe in
# `docs/evidence/` guards it; this one is a probe, not a module.
if __name__ == "__main__":
    raise SystemExit(main())
