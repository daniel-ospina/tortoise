"""W5 Phase D (#2104) — per-point dedup classification vocabulary + the pure
in-capture resolution over the capture write seams (epic #2080 indicator 4).

Every session->graph write-back response reports a ``dedup`` verdict per
point (``write_verb`` constants):

* ``new``                — the point was minted by THIS request;
* ``content_hash_hit``   — the claim resolved to an existing node with
  byte-identical content (no duplicate point minted);
* ``rephrase_linked``    — the claim is a deterministic paraphrase of an
  existing node (token-overlap band) and resolved to it (no duplicate point
  minted) — REPHRASE-linked dedup counts as survival of the existing unit,
  never a new point.

ANTI-GAMING: classification is emitted ONLY by the seam resolution that
actually compared the claim against a canonical node; when a write-back
cannot determine dedup state the point stays ``DEDUP_NEW`` (never
fabricated).

SCOPE (Phase D, deliberate): dedup resolution is scoped to the claims a
capture is actually ingesting against nodes it can truthfully claim —
in-capture repeats (the same claim extracted twice by one capture) and —
on the content-addressed v2 seam — an identical claim whose deterministic
``pt_<sha>`` node already exists in the graph (re-ingest).  Cross-session
re-WRITING of a shared node is NOT done here: per-session provenance is a
single ``eventId`` property, so a collapsed cross-session node would orphan
(or clobber) one session's memory layer (W2 snapshots are eventId-keyed).
Paraphrase detection is the repo's committed deterministic band
(``extractor_v2.NOOP_MIN_OVERLAP`` — token overlap), NEVER an LLM call: the
m2 mock lane (the W2 deterministic CI lane) runs with zero provider keys.
A real REPHRASE operator edge is not emitted (EventAPI validates NAND/IMPL
only; the e7-consolidation decision pins REPHRASE as a dedup label, not a
written operator; eval-spec §P5 defines the unimplemented edge contract).

Shared single source of truth for the capture seams (hosted + SDK mirror
consume the same ``sdk._extract_session_*`` helpers, so byte-parity is by
construction); pure here — no graph I/O — unit-testable in isolation.

Resolution vocabulary is deliberately BYTE-LEVEL for the content-hash leg
(SHA-256 of the raw content — mirrors ``create_point`` dedup): a repeated
claim that differs only in case/whitespace does NOT fold here (conservative
— never a fabricated hit) and is documented as a known dedup miss, not
silent behavior; the paraphrase band uses the committed normalized token
overlap for genuinely reworded claims.
"""

from __future__ import annotations

from tortoise.extractor_v2 import (  # stdlib-only top-level imports (safe)
    NOOP_MIN_OVERLAP,
    _norm,
    _token_overlap,
)
from tortoise.ids import content_hash
from tortoise.write_verb import (
    DEDUP_CONTENT_HASH_HIT,
    DEDUP_NEW,
    DEDUP_REPHRASE_LINKED,
)

# Max canonicals a claim is compared against before giving up on the
# paraphrase band.  Exact (content-hash) resolution never pays this cost —
# the exact check is a dict lookup.  The paraphrase band is O(n_canonicals)
# token overlaps; a capture mints tens of claims, so the bound only defends
# against pathological fold-order growth.  In-capture repeats resolve
# against claims minted EARLIER in the same capture (fold order).
_MAX_PARAPHRASE_CANDIDATES = 64

__all__ = [
    "DEDUP_CONTENT_HASH_HIT",
    "DEDUP_NEW",
    "DEDUP_REPHRASE_LINKED",
    "NOOP_MIN_OVERLAP",
    "content_hash",
    "exact_hit_id",
    "rephrase_hit",
]


def exact_hit_id(canonical_ids: dict[str, str], content: str) -> str | None:
    """Id of the canonical whose content is byte-identical (content-hash
    equal) to ``content``, else None.

    ``canonical_ids`` maps ``content_hash(content) -> canonical id`` for the
    claims already accepted by the current resolution run.  Byte-identical
    is the honest content-hash contract (mirrors ``create_point`` dedup:
    ``_content_hash(content)`` equality — never normalized fuzzy equality,
    which would fabricate hits for near-identical text).
    """
    return canonical_ids.get(content_hash(content))


def rephrase_hit(
    canonical_contents: list[tuple[str, str]],
    content: str,
) -> tuple[str, float] | None:
    """``(canonical_id, overlap)`` when ``content`` is a deterministic
    paraphrase of a canonical — token overlap in the committed
    ``NOOP_MIN_OVERLAP`` band — else None.

    Excludes exact text (byte-identical content is the content-hash leg).
    Uses extractor_v2's pinned overlap semantics (``_norm``/``_token_overlap``)
    and band constant so the classification can never drift from the
    consolidation classifier's paraphrase band.  Bounded candidate scan
    (``_MAX_PARAPHRASE_CANDIDATES``): overlap resolution is O(n) token
    comparisons and the capture seams call it per claim.
    """
    best: tuple[float, str] | None = None
    norm = _norm(content)
    for cid, ccontent in canonical_contents[:_MAX_PARAPHRASE_CANDIDATES]:
        if _norm(ccontent) == norm:
            continue  # exact text — the content-hash leg owns it
        ov = _token_overlap(ccontent, content)
        if ov < NOOP_MIN_OVERLAP:
            continue
        if best is None or ov > best[0]:
            best = (ov, cid)
    return (best[1], round(best[0], 3)) if best else None
