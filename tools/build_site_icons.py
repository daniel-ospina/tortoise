"""Build the site icon set from the brand artwork: a rounded tile and a bigger mark.

WHY THIS EXISTS. #11318 shipped a straight downscale of the artwork, and a straight
downscale inherits the artwork's own padding and its opaque square ground. In a browser
tab that reads as a hard-edged slab with a small glyph inside it, next to neighbours
whose marks fill their tile (#11486). The two numbers that define the look are therefore
here, in one place, next to the crop they are measured against:

  * `MARK_WIDTH_FRACTION` — how much of the canvas the mark spans (0.84, was 0.68);
  * `CORNER_RADIUS_FRACTION` — the tile's corner radius (0.21 of the side), applied as an
    anti-aliased alpha mask so the tab background shows through the corners.

HOW THE LOOK IS PRODUCED. Crop the artwork to a square in which the mark's bounding box
is `MARK_WIDTH_FRACTION` of the side, resample that crop to each size, then mask the
corners. One crop and one resample per size — the ground is never composited from two
sources, so there is no paste seam to see at any size.

`apple-touch-icon.png` is deliberately OPAQUE and SQUARE: iOS applies its own mask, so a
rounded image here would be rounded twice and show dark corners on a light home screen.

THE BRAND ARTWORK IS NOT IN THIS REPO — pass it with `--source`. It is a 1254x1254 PNG
of the gradient triangle on an opaque ground; `MARK_BBOX` records the mark's bounding box
inside it, measured by scanning for pixels that differ from the corner colour. If the
artwork is ever replaced, re-measure that box — the crop is derived from it.

Uses `sips` for cropping and resampling (macOS); everything else is stdlib. Run it once
per asset change; CI only ever reads the resulting bytes:

    uv run python tools/build_site_icons.py --source path/to/mark.png
    uv run python tools/build_site_icons.py --source ... --out /tmp/icons --keep /tmp/icons/crops
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
#
# The message names the ONE form that always resolves. The bare path is NOT that
# form: `--source` is `required=True`, so it exits 2 on argparse's own usage
# error — a SECOND unattributed error — whereas `--help` needs no `--source`
# token. A refusal that points a stranded caller at a usage error has only moved
# the confusion.
#
# Exit 4, not 1: 1 is this tool's OWN failure status (`raise SystemExit(<message>)`
# on every other refusal), `main()` returns 0 only and argparse takes 2, so an
# environment refusal must not wear 1 or a caller cannot tell "the artwork is
# wrong" from "the interpreter is too old". Pinned in
# tests/test_entry_point_python_guard.py::REFUSAL_CODES.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/build_site_icons.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/build_site_icons.py --help`",
        file=sys.stderr,
    )
    raise SystemExit(4)

import argparse
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "website"

# The mark's bounding box in the 1254x1254 source artwork: x0, y0, x1, y1 inclusive.
MARK_BBOX = (200, 236, 1053, 957)
MARK_WIDTH_FRACTION = 0.84
MARK_CENTRE_Y_FRACTION = 0.476     # where the artwork puts it — kept on purpose
CORNER_RADIUS_FRACTION = 0.21
# A tab at 32 px draws this stroke about 1.2 px wide; the smallest sizes get a slightly
# fuller mark so it reads as a stroke rather than a smear.
SMALL_SIZE = 48
SMALL_MARK_FRACTION = 0.88


def sips(*args: str) -> None:
    result = subprocess.run(["sips", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"sips {' '.join(args)} failed: {result.stderr.strip()}")


def read_rgb_png(path: Path) -> tuple[int, int, list[list[tuple[int, int, int]]]]:
    """Decode an 8-bit non-interlaced PNG (grey/RGB/RGBA/palette) to rows of RGB."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path}: not a PNG")
    i = 8
    idat = bytearray()
    width = height = bits = ctype = None
    plte = None
    while i < len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        kind = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + length]
        if kind == b"IHDR":
            width, height, bits, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if bits != 8 or interlace != 0:
                raise SystemExit(f"{path}: {bits}-bit/interlaced PNG not handled")
        elif kind == b"IDAT":
            idat += body
        elif kind == b"PLTE":
            plte = body
        elif kind == b"IEND":
            break
        i += 12 + length
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[list[tuple[int, int, int]]] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        filt = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if filt == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif filt == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif filt == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif filt == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        elif filt != 0:
            raise SystemExit(f"{path}: filter {filt} not handled")
        row = []
        for x in range(width):
            o = x * channels
            if ctype in (2, 6):
                row.append((line[o], line[o + 1], line[o + 2]))
            elif ctype in (0, 4):
                row.append((line[o],) * 3)
            else:
                row.append((plte[line[o] * 3], plte[line[o] * 3 + 1], plte[line[o] * 3 + 2]))
        rows.append(row)
        prev = line
    return width, height, rows


def write_rgba_png(path: Path, width: int, height: int,
                   rows: list[list[tuple[int, int, int, int]]]) -> None:
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                     + chunk(b"IEND", b""))


def corner_coverage(size: int, radius: float, samples: int = 4) -> list[list[float]]:
    """Anti-aliased coverage of a rounded square, supersampled `samples` x `samples`."""
    centre = size / 2.0
    inner = size / 2.0 - radius
    cov: list[list[float]] = []
    for y in range(size):
        row = []
        for x in range(size):
            hits = 0
            for sy in range(samples):
                py = y + (sy + 0.5) / samples - centre
                for sx in range(samples):
                    px = x + (sx + 0.5) / samples - centre
                    dx = max(abs(px) - inner, 0.0)
                    dy = max(abs(py) - inner, 0.0)
                    if dx * dx + dy * dy <= radius * radius:
                        hits += 1
            row.append(hits / (samples * samples))
        cov.append(row)
    return cov


def build_size(source: Path, size: int, out: Path, rounded: bool, tmp: Path) -> Path:
    fraction = SMALL_MARK_FRACTION if size <= SMALL_SIZE else MARK_WIDTH_FRACTION
    side = round((MARK_BBOX[2] - MARK_BBOX[0] + 1) / fraction)
    cx = (MARK_BBOX[0] + MARK_BBOX[2]) / 2.0
    cy = (MARK_BBOX[1] + MARK_BBOX[3]) / 2.0
    x0 = round(cx - side / 2.0)
    y0 = round(cy - MARK_CENTRE_Y_FRACTION * side)

    crop = tmp / f"crop_{side}.png"
    scaled = tmp / f"scaled_{size}.png"
    sips("-c", str(side), str(side), "--cropOffset", str(y0), str(x0),
         str(source), "--out", str(crop))
    sips("-z", str(size), str(size), str(crop), "--out", str(scaled))

    width, height, rows = read_rgb_png(scaled)
    if (width, height) != (size, size):
        raise SystemExit(f"{scaled}: {width}x{height}, expected {size}x{size}")

    if rounded:
        cov = corner_coverage(size, CORNER_RADIUS_FRACTION * size)
        out_rows = [[(r, g, b, round(cov[y][x] * 255))
                     for x, (r, g, b) in enumerate(rows[y])] for y in range(size)]
    else:
        out_rows = [[(r, g, b, 255) for (r, g, b) in row] for row in rows]
    write_rgba_png(out, size, size, out_rows)
    return out


def build_ico(out: Path, payloads: list[tuple[int, Path]]) -> None:
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = len(header) + 16 * len(payloads)
    directory = b""
    blobs = []
    for size, path in payloads:
        data = path.read_bytes()
        declared = struct.unpack(">II", data[16:24])
        if declared != (size, size):
            raise SystemExit(f"{path}: ICO payload is {declared}, expected {(size, size)}")
        directory += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs.append(data)
    out.write_bytes(header + directory + b"".join(blobs))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build website/favicon.png, favicon.ico "
                                                 "and apple-touch-icon.png from the artwork.")
    parser.add_argument("--source", type=Path, required=True,
                        help="the brand artwork: a square PNG with the mark on a flat ground")
    parser.add_argument("--out", type=Path, default=WEB,
                        help="output directory (default: website/)")
    parser.add_argument("--keep", type=Path, default=None,
                        help="keep the crops here, for looking at them")
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"{args.source}: not a file")
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        build_size(args.source, 256, args.out / "favicon.png", rounded=True, tmp=tmp)
        build_size(args.source, 180, args.out / "apple-touch-icon.png", rounded=False, tmp=tmp)
        small = [build_size(args.source, s, tmp / f"ico_{s}.png", rounded=True, tmp=tmp)
                 for s in (32, 48)]
        build_ico(args.out / "favicon.ico", list(zip((32, 48), small, strict=True)))
        if args.keep:
            args.keep.mkdir(parents=True, exist_ok=True)
            for f in tmp.iterdir():
                (args.keep / f.name).write_bytes(f.read_bytes())

    for name in ("favicon.png", "favicon.ico", "apple-touch-icon.png"):
        p = args.out / name
        print(f"{p}  {p.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
