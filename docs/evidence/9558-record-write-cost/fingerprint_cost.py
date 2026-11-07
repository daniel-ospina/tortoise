"""Measure the two costs the artifact quoted without a committed source.

Reproduces the figures the #9558 artifact and docstrings cite for:
  * one `_migrations_fingerprint` pass (the `stat` cost the cache pays per consult)
  * a blake2b content digest over the same fifteen files (the rejected alternative)

Writes `fingerprint-cost.json` beside itself so the numbers have a citable source,
exactly like `runs/*.json`.

⛔ `--column-consult` is the RE-TAKE for the three medians the README names as a
local observation rather than a committed figure: the base resolution a consult
pays before the key is built (`_migrations_dir()` -> `_complete()`, fifteen
`Path.exists()` calls), the fingerprint pass, and a WARM `table_columns()`. It
PRINTS and writes nothing, deliberately: it describes the AFTER-side tree (the
one carrying the cache), which is not necessarily this one, and a committed file
is only honest when it names the tree it was taken on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from tortoise.record_projection import (
    RECORD_MIGRATIONS,
    _migrations_dir,
    _migrations_fingerprint,
)


def column_consult_medians(reps: int = 200) -> dict[str, object]:
    """The three medians the README's residual bullet quotes (print only)."""
    from tortoise.record_projection import table_columns

    base = _migrations_dir()

    def resolution() -> None:
        _migrations_dir()

    def fingerprint() -> None:
        _migrations_fingerprint(base)

    def warm_columns() -> None:
        # ⛔ NO ARGUMENT: the per-record path calls `table_columns()` and so pays
        # the base resolution too. Passing `base` would skip `_migrations_dir()`
        # and measure one half while the README calls it the whole warm call.
        table_columns()

    out: dict[str, object] = {
        "tree": "the tree this was run in — state its HEAD in the report",
        "reps": reps,
        "note": ("wall-clock on the box this was taken on; the RATIO between the "
                 "two halves is the transferable figure, the absolute values are not"),
    }
    for label, fn in (("resolution", resolution),
                      ("fingerprint_pass", fingerprint),
                      ("warm_table_columns", warm_columns)):
        fn()                                    # warm the page cache / the map
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
    return out


def measure(base: Path, reps: int = 200) -> dict[str, object]:
    names = list(RECORD_MIGRATIONS)
    total_bytes = sum((base / name).stat().st_size for name in names)

    def stat_pass() -> None:
        _migrations_fingerprint(base)

    def digest() -> None:
        handle = hashlib.blake2b(digest_size=16)
        for name in names:
            handle.update(name.encode())
            handle.update((base / name).read_bytes())

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
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="re-take the #9558 cost figures")
    parser.add_argument("--column-consult", action="store_true",
                        help="print the three consult medians and write nothing")
    parser.add_argument("--reps", type=int, default=200)
    args = parser.parse_args(argv)

    if args.column_consult:
        print(json.dumps(column_consult_medians(args.reps), indent=2, sort_keys=True))
        return 0

    # ⛔ NO `--tree`, deliberately (both directions of the same hazard):
    #   * this path WRITES `fingerprint-cost.json`, the committed source the artifact,
    #     the docs index, the epic's R10 paragraph and a module docstring all cite — a
    #     foreign tree's numbers written here would carry no field saying
    #     so, silently replacing the band every reader trusts; and
    #   * `--column-consult` measures the module THIS PROCESS imported, so a
    #     `--tree` there would print one tree's medians under another's name.
    # The probe therefore measures THIS checkout only: run it FROM the tree you mean
    # to measure — item 6 of the artifact's exception list says exactly that, and
    # carries the re-take command (`--column-consult`, print-only).
    # ⛔ NOT "as the artifact's Reproducing section does": that section invokes the
    # OTHER probe, and it points it AT a tree with `--tree` rather than running it
    # from one. A citation that names the wrong section is the same class of defect
    # as a stale line number, and it was this removal's stated basis.
    base = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
    out = measure(base, args.reps)
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
