"""#11739 — the composed-text gate bundle's binding: each artifact must name the probe
that was actually committed.

WHY THIS FILE EXISTS (#12436 review). The bundle
(`docs/evidence/11739-composed-gate/`) ships the probes themselves instead of a head sha,
because the repo squash-merges and a head sha is unverifiable when a later lane re-runs the
probe to check the numbers. That design puts the WHOLE provenance on
`provenance.probe_sha256` — a digest of the probe's own bytes — and until this pin existed
nothing enforced it.

The drift that matters is a probe edit, because #12436's acceptance test is *this same gate
re-run with no threshold, band or test adjusted*: re-tuning the 0.89 band inside
`composed_gate.py` is exactly the move the issue forbids, and it would leave both artifacts
asserting a digest that no longer matches the committed code while every check stayed green.
The instrument this bundle exists to preserve would then be silently self-invalidated.

Every test names the mutation that reds it. Hermetic: two probes are hashed and two JSON
artifacts are parsed. No DB, no graph, no network, no key, no model call — safe in the docker
lane and in CI.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "docs" / "evidence" / "11739-composed-gate"
PROBES = BUNDLE / "probes"


def _digest_of(name: str) -> str:
    """sha256 of the probe AS COMMITTED — the exact bytes `_provenance()` hashes at run time
    (`Path(__file__).read_bytes()`), so a trailing newline is part of the digest."""
    return hashlib.sha256((PROBES / name).read_bytes()).hexdigest()


def _artifact(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text())


def test_gate_json_was_produced_by_the_committed_composed_gate_probe():
    """FAILS IF `probes/composed_gate.py` is edited without re-running it.

    The repair is `composed_gate.py --attest` for a pure provenance rebind, or a full re-run
    (~950 s of encoding) when the probe's LOGIC changed — never a hand-edit of `gate.json`,
    never a hand-edit of the digest. Both hand-edits are the mutation this test catches: the
    digest is recomputed from the committed probe bytes, not read from the artifact.
    """
    artifact = _artifact("gate.json")
    # `probe_path` is recorded with the `probes/` prefix here (`quote_identity.json` records
    # the bare filename), so the binding that matters is the FILE it names, not the prefix
    # style — the artifact is dated evidence and is never hand-edited to normalise it.
    assert artifact["provenance"]["probe_path"].rsplit("/", 1)[-1] == "composed_gate.py"
    assert artifact["provenance"]["probe_sha256"] == _digest_of("composed_gate.py"), (
        "gate.json was produced by a DIFFERENT composed_gate.py — re-run the probe "
        "(`composed_gate.py --attest` for provenance only); do not hand-edit the artifact")


def test_quote_identity_json_was_produced_by_the_committed_quote_identity_probe():
    """FAILS IF `probes/quote_identity.py` is edited without re-running it.

    Its artifact carries the ROOT-CAUSE counts the fix (#12436) is scoped against — 2,059
    Points (13.72%) sharing a quote, 28,994 byte-identical composed pairs — so a silent probe
    edit would re-point the root cause without moving a single published number's provenance.
    """
    artifact = _artifact("quote_identity.json")
    assert artifact["provenance"]["probe_path"].rsplit("/", 1)[-1] == "quote_identity.py"
    assert artifact["provenance"]["probe_sha256"] == _digest_of("quote_identity.py"), (
        "quote_identity.json was produced by a DIFFERENT quote_identity.py — re-run the probe "
        "(`--rows <cache> --out …`); do not hand-edit the artifact")


def test_the_two_artifacts_name_different_probes():
    """FAILS IF one artifact's provenance block is copy-pasted onto the other.

    The two artifacts answer different questions (gate verdicts vs the quote-identity root
    cause) from different probes, so equal digests could only be a copy-paste — the shape that
    would make both bindings pass while one of them was describing the wrong code.
    """
    gate = _artifact("gate.json")["provenance"]["probe_sha256"]
    identity = _artifact("quote_identity.json")["provenance"]["probe_sha256"]
    assert gate != identity, (
        "both artifacts claim the same probe digest — one provenance block was copy-pasted")
    assert gate == _digest_of("composed_gate.py")
    assert identity == _digest_of("quote_identity.py")
