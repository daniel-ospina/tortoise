"""#11739 — the composed-text gate bundle's binding: each artifact must name the probe
that was actually committed, AND carry the evidence that probe produced.

WHY THIS FILE EXISTS (#12436 review). The bundle
(`docs/evidence/11739-composed-gate/`) ships the probes themselves instead of a head sha,
because the repo squash-merges and a head sha is unverifiable when a later lane re-runs the
probe to check the numbers. That design puts the artifact's provenance on
two digests of its own — `provenance.probe_sha256` (the CODE that produced it) and
`provenance.payload_sha256` (the NUMBERS it produced) — and until this pin existed
nothing enforced either.

The drift that matters is a probe edit, because #12436's acceptance test is *this same gate
re-run with no threshold, band or test adjusted*: re-tuning the 0.89 band inside
`composed_gate.py` is exactly the move the issue forbids, and it would leave both artifacts
asserting a digest that no longer matches the committed code while every check stayed green.
The instrument this bundle exists to preserve would then be silently self-invalidated.

The second drift is a hand-edit of the EVIDENCE: `probe_sha256` does not move when a verdict
or a count inside `gate.json` is edited. (The VERDICT alone was already caught before the
payload binding — the `test_the_bundle_records_a_failed_measurement` assertion reds a flipped
`all_pass`, measured `1 failed, 4 passed` at `bf7b848531`. What passed untouched was a NUMBER:
`global_worst_composed` 1.0 → 0.10, `items_displaced_from_top_k` 3706 → 3707, and the like, which
no assertion at that head read.) `provenance.payload_sha256` is what makes every such edit red.

Every test names the mutation that reds it. Hermetic: three probes are hashed and three JSON
artifacts are parsed, plus one float-pair read from `tortoise.embeddings` (no model, no key, no
network, no DB). No count describing this FILE'S CURRENT SIZE (collected cases, test functions,
pass count) is quoted in this file or its `ci-surfaces.yml` row: a hand-written count re-stales on
every case a review round adds. Counts that appear in the prose below are HISTORICAL — the observed
result of a specific mutation at a named head, evidence for a claim — never a description of how many
cases exist now.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import tempfile
from pathlib import Path

import pytest

#: The product's calibrated bands. The record's literals must equal these (see the
#: `test_the_declared_bands_are_the_products_bands` test).
from tortoise.embeddings import DEFAULT_THRESHOLD, NEAR_DUPLICATE_THRESHOLD

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "docs" / "evidence" / "11739-composed-gate"
PROBES = BUNDLE / "probes"


def _digest_of(name: str) -> str:
    """sha256 of the probe AS COMMITTED — the exact bytes `_provenance()` hashes at run time
    (`Path(__file__).read_bytes()`), so a trailing newline is part of the digest."""
    return hashlib.sha256((PROBES / name).read_bytes()).hexdigest()


def _artifact(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text())


def _payload_digest(artifact: dict) -> str:
    """The EVIDENCE binding, recomputed exactly as all three probes compute it: sha256 over every
    field of the artifact except `provenance`, TOGETHER WITH the row source those fields are
    attributed to, canonicalised `indent=2, sort_keys=True`.

    Deliberately a pure function of the parsed object, so it is a digest of the FIELDS and not of
    the file's formatting — reformatting the JSON does not red, editing a number does. The
    attribution is inside the digest (`rows_source`), because re-pointing it at a different cache
    is an edit to the evidence: measured before that, `rows_source` -> `/tmp/some-other-cache.jsonl`
    left the pin entirely GREEN (the suite's size at that head, `ec9f702c9608`, was 38 cases — see
    the payload test below, which names the same head).
    """
    body = {k: v for k, v in artifact.items() if k != "provenance"}
    canon = json.dumps(
        {"artifact": body, "rows_source": artifact["provenance"]["rows_source"]},
        indent=2, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def test_gate_json_was_produced_by_the_committed_composed_gate_probe():
    """FAILS IF `probes/composed_gate.py` is edited without re-running it.

    The repair is `composed_gate.py --attest` for a pure provenance rebind, or a full re-run
    (~950 s of encoding) when the probe's LOGIC changed — never a hand-edit of `gate.json`.
    The digest is recomputed from the committed probe bytes, not read from the artifact, so
    lowering the 0.89 band in the probe reds here instead of silently re-pointing the gate.
    (`test_artifact_evidence_is_the_evidence_that_was_attested` covers the other half: a
    hand-edit of the artifact's own numbers, which leaves this digest untouched.)
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


def test_gate3_json_was_produced_by_the_committed_gate3_probe():
    """FAILS IF `probes/gate3_false_proposals.py` is edited without re-running (or re-attesting).

    This artifact carries the FALSE-PROPOSAL count — the number #11739's verdict comment mis-stated
    as `13,691` (a NEW-proposal count). It is the only place in the bundle where a proposal is
    classified true/false, so re-pointing the probe's bytes silently would re-point the correction.
    The repair is a fresh run against the declared row cache, or `--attest` for a non-logic edit.
    """
    artifact = _artifact("gate3_false_proposals.json")
    assert artifact["provenance"]["probe_path"].rsplit("/", 1)[-1] == "gate3_false_proposals.py"
    assert artifact["provenance"]["probe_sha256"] == _digest_of("gate3_false_proposals.py"), (
        "gate3_false_proposals.json was produced by a DIFFERENT gate3_false_proposals.py — re-run "
        "the probe (or `--attest` it) rather than hand-editing the artifact")


def test_the_gate3_counts_are_the_ones_the_issue_quotes():
    """FAILS IF the false-proposal counts #11739 now quotes are edited.

    The correction is the deliverable: composed 4,202/4,220 false, raw 2,599/2,661, and the
    composed-ONLY delta 3,319/3,319 — all three derive from these fields (T2 + T3 exact, plus the
    audited strata whose LABELS live in the README, not here). A drift in any field below moves a
    published number, and `payload_sha256` alone would report it as "some byte changed".
    """
    gate3 = _artifact("gate3_false_proposals.json")
    assert gate3["proposals"] == {"raw": 2661, "composed": 4220, "shared": 901,
                                 "composed_only": 3319, "raw_only_lost": 1760,
                                 "composed_only_crossing_0_89": 0}
    assert gate3["composed"]["false_by_tier"] == {
        "T2_identical_composed_text": 2, "T3_below_calibrated_floor": 3319}
    assert gate3["composed"]["needs_label"] == 899
    assert gate3["composed_only"]["false_exact"] == 3319, (
        "the composed-ONLY delta is the switch's own effect — it must stay exactly false")
    assert gate3["composed_only"]["needs_label"] == 0
    assert gate3["raw"]["needs_label"] == 2659
    assert gate3["audit_samples"]["shared_T4"] == 100
    assert gate3["audit_samples"]["raw_only_T4"] == 40
    assert gate3["arms"]["threshold"] == DEFAULT_THRESHOLD


def test_the_three_artifacts_name_different_probes():
    """FAILS IF one artifact's provenance block is copy-pasted onto another.

    The three artifacts answer different questions (gate verdicts, the quote-identity root cause,
    the composed-arm false-proposal count) from different probes, so equal digests could only be a
    copy-paste — the shape that would make all the bindings pass while one of them described the
    wrong code.
    """
    gate = _artifact("gate.json")["provenance"]["probe_sha256"]
    identity = _artifact("quote_identity.json")["provenance"]["probe_sha256"]
    third = _artifact("gate3_false_proposals.json")["provenance"]["probe_sha256"]
    assert len({gate, identity, third}) == 3, (
        "two artifacts claim the same probe digest — one provenance block was copy-pasted")
    assert gate == _digest_of("composed_gate.py")
    assert identity == _digest_of("quote_identity.py")


def test_the_bundle_records_a_failed_measurement():
    """FAILS IF the recorded verdict is flipped to a pass IN PLACE.

    This bundle is the dated record of the 2026-11-13 run that #12436 is scoped against, and its
    headline is FAIL. A re-run that PASSES is a real and desirable outcome — it is how #12436
    closes — but it must land as a NEW artifact (its own bundle directory, its own index row, the
    PR that flips `TORTOISE_COMPOSED_EMBED_TEXT` citing it), because overwriting this one drops the
    baseline the fix was measured from. This assertion forces that decision instead of letting it
    happen silently.
    """
    verdict = _artifact("gate.json")["verdict"]
    assert verdict["all_pass"] is False, (
        "the recorded gate verdict is no longer FAIL — a passing re-run belongs in a NEW bundle, "
        "not in the 2026-11-13 baseline #12436 is scoped against")
    assert verdict["gates"] == {"gate1": False, "gate2": False, "gate3": False}


def test_the_declared_bands_are_the_products_bands():
    """FAILS IF the record's bands drift from the constants the product uses.

    The README's declared limitations state that the 0.89 / 0.72 literals in the probe EQUAL
    `NEAR_DUPLICATE_THRESHOLD` / `DEFAULT_THRESHOLD` in `tortoise/embeddings.py` — i.e. the gate
    changed no threshold. Pinning it from the artifact's side means a band re-tune that moves
    either the product constant or the recorded band reds HERE rather than quietly re-basing a
    gate whose whole authority is "no threshold was adjusted to make this pass".

    RESIDUAL, stated rather than implied: this cannot see a probe-literal edit followed by
    `--attest` — attest re-binds the digest to the edited probe and leaves the record's numbers
    alone. That hole is bounded (not closed) by the verdict test above and by review of any diff
    touching the probe.
    """
    gate = _artifact("gate.json")
    assert gate["gate1_similarity"]["threshold"] == NEAR_DUPLICATE_THRESHOLD == 0.89, (
        "GATE 1's recorded band is not the product's near-duplicate band")
    assert gate["gate3_false_proposal"]["threshold"] == DEFAULT_THRESHOLD == 0.72, (
        "GATE 3's recorded candidate band is not the product's default threshold")

# ---------------------------------------------------------------------------------------
# The EVIDENCE half of the binding. `probe_sha256` above binds each artifact to the CODE that
# produced it and says nothing about the numbers the code produced — so a hand-edit of a NUMBER
# (`global_worst_composed` 1.0 -> 0.10, `items_displaced_from_top_k` 3706 -> 3707) passed the pin
# unchanged at `bf7b848531`, where every assertion read either a digest, the verdict or a band.
# That is the "false PASS" class, on the one artifact that decides whether
# `TORTOISE_COMPOSED_EMBED_TEXT` may ship ON (#11739's gate, #12436's acceptance instrument), so it
# gets its own binding. The VERDICT was already pinned there — flipping `all_pass` to true reded
# `test_the_bundle_records_a_failed_measurement` (`1 failed, 4 passed`, measured at that head).
#
# `test_the_bundle_records_a_failed_measurement` above owns the VERDICT and
# `test_the_declared_bands_are_the_products_bands` owns the BANDS; the tests below cover what
# neither can see: that the artifact's own FIELDS are the fields that were attested.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["gate.json", "quote_identity.json",
                                      "gate3_false_proposals.json"])
def test_artifact_evidence_is_the_evidence_that_was_attested(name: str):
    """FAILS IF a number, a count or a verdict inside either artifact is hand-edited.

    `provenance.payload_sha256` is written by the probe (`_provenance()`), over every field
    except `provenance` itself. Recomputing it from the committed file's parsed fields (plus its `rows_source`) is what makes
    "the artifact is dated evidence and is never hand-edited" a machine-checked claim rather
    than a sentence in the README: an edit that leaves `probe_sha256` intact (which is EVERY
    edit to the numbers) still moves this digest.

    Mutation, measured: `gate2_rank.top_k.items_displaced_from_top_k` 3706 -> 3707 alone reds
    this test (and the headline-figures test below). The repair is never a hand-edit of the
    digest — it is `composed_gate.py --attest` / `quote_identity.py --attest` (provenance only,
    no re-measurement) after a deliberate change, or a fresh run when the logic changed.
    """
    artifact = _artifact(name)
    assert artifact["provenance"]["payload_sha256"] == _payload_digest(artifact), (
        f"{name}'s numbers changed after it was attested — restore the artifact, or re-run "
        "the probe (or `--attest` it, deliberately) rather than editing the digest")


def test_the_readme_quotes_each_probes_digest_in_its_own_context():
    """FAILS IF the README's three quoted `probe_sha256` values are stale or SWAPPED.

    The README's binding paragraph is the human-readable half of `provenance.probe_sha256`:
    it prints one digest per artifact, each after its own `gate.json:` / `quote_identity.json:`
    / `gate3_false_proposals.json:` label. Nothing pinned that before this test, and the failure
    it guards is not hypothetical —
    it happened WHILE the digests were being refreshed by a script in #12490: the refresh mapped
    the old values by SORTED order rather than by artifact, so `b66f8a17…` (quote_identity's)
    landed after the `gate.json:` label and `4f066693…` (gate.json's) after
    `quote_identity.json:` — every digest PRESENT, correct in count, and each attached to the
    wrong artifact. `payload_sha256` cannot see it (the README is not inside the artifact), and
    the suite stayed green. So: parse the digest that FOLLOWS each label and require it to be
    that artifact's own.
    """
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    assert "`provenance.probe_sha256`" in readme, (
        "the README no longer carries the probe-digest binding paragraph this test parses")
    paragraph = readme.split("`provenance.probe_sha256`", 1)[1].split("payload_sha256", 1)[0]
    for label, name in (("gate.json:", "gate.json"),
                        ("quote_identity.json:", "quote_identity.json"),
                        ("gate3_false_proposals.json:", "gate3_false_proposals.json")):
        assert label in paragraph, f"the README's binding paragraph lost its `{label}` label"
        quoted = _first_hex_after(paragraph, label)
        own = _artifact(name)["provenance"]["probe_sha256"]
        assert quoted == own, (
            f"the README quotes {quoted[:12]}… after `{label}`, but {name}'s attested probe digest "
            f"is {own[:12]}… — refresh the README from the artifacts (and check the two did not "
            "get swapped while being refreshed)")


def test_the_readme_quotes_each_artifacts_PAYLOAD_digest_too():
    """FAILS IF the README's three `payload_sha256` quotations are stale or SWAPPED.

    The sibling probe-digest pin closed only the HALF that binds the code. The payload paragraph
    carries the same three labels over the digests that bind the NUMBERS, and it was unpinned —
    swapping `gate.json`'s payload digest with `quote_identity.json`'s left all 38 cases green at
    `ec9f702c9608` (#12593's second review round, finding 4). That is not a hypothetical shape: #12490's refresh mapped
    old values by SORTED order rather than by artifact, so it would have swapped BOTH digests; the
    repair that fixed the probe half left the evidence half exposed, which is the half a future
    hand-edit of a NUMBER would need to survive.
    """
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    assert "`provenance.payload_sha256`" in readme, (
        "the README no longer carries the payload-digest paragraph this test parses")
    paragraph = readme.split("`provenance.payload_sha256`", 1)[1]
    for label, name in (("gate.json:", "gate.json"),
                        ("quote_identity.json:", "quote_identity.json"),
                        ("gate3_false_proposals.json:", "gate3_false_proposals.json")):
        assert label in paragraph, f"the README's payload paragraph lost its `{label}` label"
        quoted = _first_hex_after(paragraph, label)
        own = _artifact(name)["provenance"]["payload_sha256"]
        assert quoted == own, (
            f"the README quotes {quoted[:12]}… after `{label}` in the PAYLOAD paragraph, but "
            f"{name}'s attested payload digest is {own[:12]}… — refresh the README from the "
            "artifacts (and check the three did not get swapped while being refreshed)")


def test_the_published_gate3_totals_are_derivable_and_quoted():
    """FAILS IF the bundle's headline 4,202 / 2,599 drifts from the artifact, or is misquoted.

    This is the number the bundle EXISTS to publish — the false-proposal COUNT — and until this
    test it was asserted nowhere: a hand-edit of `4,202` to `4,302` in the README (and the
    `docs/00_index.md` row) left every case green while `config/ci-surfaces.yml` claimed the
    README's figures were asserted (#12593's second review round, finding 3). That is the
    `false PASS` class the whole bundle is built to close, one layer below the digests.

    The derivation is the artifact's OWN fields, not a restatement: `false_exact` (mechanical
    tiers) plus the audited tier scaled by the recorded hand labels — `labels_false` over the
    sampled `shared_T4` / `raw_only_T4` counts, times the tier sizes. A reader who distrusts the
    labels re-audits the dumped `--pairs-out` sample; what this pin guarantees is that the
    published totals are a FUNCTION of the artifact rather than prose that drifted.
    """
    a = _artifact("gate3_false_proposals.json")
    s = a["audit_samples"]
    shared_false = round(s["labels_false"]["shared_T4"] / s["shared_T4"] * s["shared_T4_tier"])
    raw_only_false = round(s["labels_false"]["raw_only_T4"] / s["raw_only_T4"]
                           * s["raw_only_T4_tier"])
    composed_false = a["composed"]["false_exact"] + shared_false
    raw_false = a["raw"]["false_exact"] + shared_false + raw_only_false
    assert (composed_false, raw_false) == (4202, 2599), (
        f"the artifact derives {composed_false} composed / {raw_false} raw false proposals, not "
        "4,202 / 2,599 — a tier count or a recorded audit label moved")

    composed_rate = round(composed_false / a["proposals"]["composed"] * 100, 1)
    raw_rate = round(raw_false / a["proposals"]["raw"] * 100, 1)
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    index = (BUNDLE.parents[2] / "docs" / "00_index.md").read_text(encoding="utf-8")
    # ⛔ The exact SENTENCES, not `"4,202" in doc`: the README carries the figure twice (the prose
    # paragraph and the table row), so a one-site `in` check stayed green when only one of them was
    # edited to 4,302 (measured — the first version of this pin did exactly that).
    claims = {
        "README": (
            f"4,202 of 4,220 composed proposals are FALSE ({composed_rate}%), 2,599 of 2,661 raw",
            f"| **false total** | **4,202 ({composed_rate}% of 4,220)** | "
            f"**2,599 ({raw_rate}% of 2,661)** |",
        ),
        "docs/00_index.md": (
            f"4,202 of 4,220 composed proposals false ({composed_rate}%), 2,599 of 2,661 raw "
            f"({raw_rate}%)",
        ),
    }
    for doc_name, doc in (("README", readme), ("docs/00_index.md", index)):
        for sentence in claims[doc_name]:
            assert sentence in doc, (
                f"{doc_name} no longer carries the published figure sentence {sentence!r}")
    # The composed-ONLY delta is the part the switch itself causes, and it is exact (all T3).
    assert a["composed_only"]["proposals"] == 3319
    assert a["composed_only"]["false_exact"] == 3319


def test_the_readme_wilson_bands_are_the_outward_ones():
    """FAILS IF the README's Wilson endpoints drift from the artifact, or are rounded INWARD.

    The published bands were "rounded outward" in prose while 5 of 6 endpoints were rounded inward
    (#12593's first review round) — and after that was fixed, nothing pinned it: a future edit to
    `92.9` → `93.0` (inward, the exact defect) would have left every case green. The bands are
    DERIVED here from the artifact's own recorded labels and sample sizes with the probe's z, then
    rounded floor-on-low / ceil-on-high, which is the only reading under which "outward" is true.
    """
    a = _artifact("gate3_false_proposals.json")
    s = a["audit_samples"]

    def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
        p = k / n
        d = 1 + z * z / n
        centre = (p + z * z / (2 * n)) / d
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
        return centre - half, centre + half

    lo1, hi1 = wilson(s["labels_false"]["shared_T4"], s["shared_T4"])
    lo2, hi2 = wilson(s["labels_false"]["raw_only_T4"], s["raw_only_T4"])
    composed_lo = a["composed"]["false_exact"] + s["shared_T4_tier"] * lo1
    composed_hi = a["composed"]["false_exact"] + s["shared_T4_tier"] * hi1
    raw_lo = a["raw"]["false_exact"] + s["shared_T4_tier"] * lo1 + s["raw_only_T4_tier"] * lo2
    raw_hi = a["raw"]["false_exact"] + s["shared_T4_tier"] * hi1 + s["raw_only_T4_tier"] * hi2
    outward = (math.floor(lo1 * 1000) / 10, math.ceil(hi1 * 1000) / 10,
               math.floor(lo2 * 1000) / 10, math.ceil(hi2 * 1000) / 10,
               math.floor(composed_lo), math.ceil(composed_hi),
               math.floor(raw_lo), math.ceil(raw_hi))
    assert outward == (92.9, 99.5, 87.1, 99.6, 4157, 4216, 2371, 2649), (
        f"the artifact's recorded labels/sizes now derive {outward} — the README's published bands "
        "were re-derived from different numbers")
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    for band in ("92.9–99.5", "87.1–99.6", "4,157–4,216", "2,371–2,649"):
        assert band in readme, f"the README no longer quotes the outward band {band}"
    # ⛔ and the INWARD spellings must be gone: `in` alone would pass on a doc that quoted both.
    for inward in ("93.0–99.4", "4,157–4,215", "2,372–2,648"):
        assert inward not in readme, (
            f"the README still quotes the inward bound {inward} somewhere — the outward claim and "
            "the inward spelling cannot both be in a document whose value is declaration honesty")


def _first_hex_after(text: str, label: str) -> str:
    """The first 64-hex token after `label` — the digest that label presents."""
    tail = text.split(label, 1)[1]
    match = re.search(r"\b[0-9a-f]{64}\b", tail)
    assert match is not None, f"no 64-hex digest follows `{label}` in the README"
    return match.group(0)


def test_the_headline_figures_are_the_ones_the_index_quotes():
    """FAILS IF a headline number the README and `docs/00_index.md` quote is edited.

    `payload_sha256` makes ANY edit detectable; this makes the specific figures legible at the
    point of drift, so a reader sees WHICH number moved. It also pins the one internal
    consistency the probe computes (`all_pass` must be the conjunction of the three gate flags
    — exactly the shape `--attest` refuses to bless).
    """
    gate = _artifact("gate.json")
    assert gate["verdict"]["all_pass"] == all(gate["verdict"]["gates"].values())
    # GATE 1 — the composition, not the graph, causes the failure.
    assert gate["gate1_similarity"]["global_worst_composed"] == 1.0
    assert gate["gate1_similarity"]["global_worst_raw"] == 1.0
    assert gate["subject_groups"]["worst_session"]["worst_composed"] == 1.0
    # GATE 2 — rank distribution.
    assert gate["gate2_rank"]["transitions"]["crossed_089"] == 27356
    assert gate["gate2_rank"]["counts"]["b070_085"] == {"raw": 2316407, "composed": 3661251}
    assert gate["gate2_rank"]["top_k"]["items_displaced_from_top_k"] == 3706
    assert gate["gate2_rank"]["top_k"]["raw_relevant_items_in_raw_top_k"] == 4186
    assert gate["gate2_rank"]["top_k"]["raw_relevant_items_lost_from_composed_top_k"] == 3033
    # GATE 3 — false proposals.
    assert gate["gate3_false_proposal"]["new_proposals"] == 13691
    assert gate["gate3_false_proposal"]["proposals_composed"] == 17305
    assert gate["gate3_false_proposal"]["new_proposals_crossing_0_89"] == 20
    # The figures the README's GATE block and the `docs/00_index.md` row quote beyond the three
    # gates' own totals — pinned here so a drift names WHICH figure moved rather than only reding
    # the payload digest. (`rows_walked`/`live_rows` are equal BY CONSTRUCTION in cache mode; the
    # README's declared limitation says so, and pinning both is what keeps that sentence true.)
    identity = _artifact("quote_identity.json")
    assert identity["of_those_raw_below_0_89"] == 16114
    assert gate["gate2_rank"]["transitions"]["entered_070_085"] == 2795990
    assert gate["gate3_false_proposal"]["proposals_raw"] == 10232
    assert gate["gate3_false_proposal"]["max_similarity_among_new_proposals"] == 1.0
    assert gate["rows_walked"] == gate["live_rows"] == 15842
    assert gate["subject_groups"]["session_groups"] == 997
    assert gate["subject_groups"]["points_in_session_groups"] == 14941
    assert gate["subject_groups"]["worst_session"]["worst_raw"] == 0.9973
    assert gate["population"]["encoder_provenance_mean_cosine_vs_stored"] == 1.0
    assert identity["raw_exact_duplicate_pairs_ge_0_99999"] == 12


def test_the_artifacts_name_the_row_cache_and_encoder_the_dates_rest_on():
    """FAILS IF `provenance`'s reproducibility anchors are edited — the one block no digest covers.

    `payload_sha256` deliberately excludes `provenance` (it is the field being written), so the
    fields inside it that the artifact itself can corroborate have to be checked directly: the
    probe's own bytes, its basename, the ROW SOURCE the numbers are attributed to, and a named
    encoder.

    `rows_source` is the anchor the README's reproducibility paragraph leans on ("the committed
    numbers are reproducible from the row cache named in `provenance.rows_source`"), and editing it
    re-points the evidence at a different cache while leaving both digests intact — measured before
    this test existed: setting it to `/tmp/some-other-cache.jsonl` left the pin GREEN at the head
    that added `gate3` (a smaller suite than today's — historical, and it names no count of its own
    because the head it was measured at is not recorded; the CLAIM is what matters here, not the
    tally). `gate.json` also
    records the same string at the top level as `source`, so the two must agree.
    """
    gate = _artifact("gate.json")
    identity = _artifact("quote_identity.json")
    # The encoder key is named per probe (`encoder` describes the embedder that produced the
    # vectors; `composed_encoder` says the root-cause probe encodes nothing and reads the
    # composer's word budget) — so check the field that artifact actually publishes.
    gate3 = _artifact("gate3_false_proposals.json")
    for name, artifact, encoder_key in (("gate.json", gate, "encoder"),
                                        ("quote_identity.json", identity, "composed_encoder"),
                                        ("gate3_false_proposals.json", gate3, "encoder")):
        prov = artifact["provenance"]
        assert prov["rows_source"], f"{name} names no row source — nothing attributes the numbers"
        assert prov[encoder_key], (
            f"{name} names no `{encoder_key}` — the 0.89/0.72 bands are model-calibrated")
    assert gate3["provenance"]["rows_source"] == gate3["source"], (
        "gate3_false_proposals.json's `source` and `provenance.rows_source` disagree — the numbers "
        "must stay attributable to ONE cache")
    assert gate["provenance"]["rows_source"] == gate["source"], (
        "gate.json's `source` and `provenance.rows_source` disagree — a cache-sourced artifact must "
        "name ONE cache, or `--attest` (which derives the second from the first) can silently move it")


def test_the_root_cause_counts_are_the_ones_the_issue_quotes():
    """FAILS IF the root-cause numbers the fix (#12436) is scoped against are edited.

    #11739's conclusion — a verbatim `quote` is not a fact identity — rests on exactly these
    counts, and they are quoted in `docs/00_index.md`, the README and the PR body.
    """
    identity = _artifact("quote_identity.json")
    assert identity["measured_points"] == 15008
    assert identity["distinct_composed_texts"] == 13341
    assert identity["shared_composed_text_groups"] == 392
    assert identity["points_in_shared_groups"] == 2059
    assert identity["pairs_composing_to_identical_text"] == 28994
    assert identity["of_those_with_DIFFERENT_content"] == 28994
    assert identity["of_those_with_same_content"] == 0
    assert identity["largest_group_points"] == 117



# ---------------------------------------------------------------------------------------
# `--attest`'s FAIL-CLOSED ARMS. The arms are new code in this PR and they are the only thing
# standing between a hand-edit and a re-blessed digest, so they are pinned like any other guard:
# each refusal shape below is driven directly (the refusal lives in `_attest(path)`, a pure
# function of the path, so no CLI and no measurement is involved) and each case asserts BOTH the
# exit code and that the file was left byte-identical. Without these, a regression to the
# fail-open shape the probe's own comment records (`verdict` present, no `gates` -> attested,
# exit 0) would red nothing.
# ---------------------------------------------------------------------------------------
def _load_probe(filename: str):
    """Import a bundle probe by PATH — its directory name (`11739-composed-gate`) is not a legal
    module name, so a plain `import` cannot reach it (the sibling census pin's own pattern)."""
    spec = importlib.util.spec_from_file_location(
        "bundle_" + filename.removesuffix(".py"), PROBES / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _attest_a_tmp_copy(probe_name: str, artifact_name: str, mutate) -> tuple[int, bytes, bytes]:
    """Run `<probe>._attest` on a mutated TEMP COPY of a committed artifact.
    Returns (rc, bytes_before, bytes_after) so a test can assert the refusal left the file alone.
    The committed artifact is only ever READ — a test that mutated it in place would be the very
    hand-edit these arms exist to refuse.
    """
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / artifact_name
        artifact = _artifact(artifact_name)
        mutate(artifact)
        target.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        before = target.read_bytes()
        rc = _load_probe(probe_name)._attest(str(target))
        after = target.read_bytes()
    return rc, before, after


@pytest.mark.parametrize("shape,mutate", [
    ("no-verdict", lambda a: a.pop("verdict")),
    ("no-gate1", lambda a: a.pop("gate1_similarity")),
    ("no-gates-block", lambda a: a["verdict"].pop("gates")),
    # The DISCRIMINATING shape for the `if not gates:` guard: with `all_pass` still false the
    # case above is refused by ATTEST-INCONSISTENT before that guard decides, so deleting the
    # guard would leave it green (measured). Green + no flags is the true fail-open: the guard
    # is the ONLY thing that refuses it, so this case reds if it is removed.
    ("no-gates-but-green", lambda a: (a["verdict"].pop("gates"),
                                      a["verdict"].__setitem__("all_pass", True))),
    # #12476 — the PARTIAL deletion. One flag removed leaves `all_pass: false` AGREEING with
    # `all({False, False})`, so neither the inconsistency check nor a non-empty test can see it:
    # measured, deleting `gate2` was attested, exit 0. This case reds if the exact-name bound is
    # weakened back to "non-empty".
    ("gates-missing-one", lambda a: a["verdict"]["gates"].pop("gate2")),
    # …and the block being the WRONG TYPE. Both raise out of `main()` in the pre-#12476 code
    # (`AttributeError`: 'list'/'str' object has no attribute 'values'/'get'), so the documented
    # exit 2 became 1 — the shape a lane script cannot tell from a crash.
    ("gates-non-dict", lambda a: a["verdict"].__setitem__("gates", ["gate1"])),
    ("verdict-non-dict", lambda a: a.__setitem__("verdict", "green")),
    # A parseable artifact whose ROOT is not an object is the same defect one level up (reachable
    # by the same hand-edit, or by a probe that wrote a bare list) — it is driven by its own test
    # below, because it cannot be expressed as a mutation of a dict.
    ("all-pass-disagrees", lambda a: a["verdict"].__setitem__("all_pass", True)),
    ("no-source", lambda a: a.pop("source")),
])
def test_composed_gate_attest_refuses_the_tamper_shapes(shape, mutate):
    """FAILS IF any of `composed_gate.py --attest`'s refusals is weakened into a blessing.

    `no-gates-block` is the arm that was measured fail-open in review (`if gates and ...` skipped
    the check when a hand-edit DELETED the flags) and `all-pass-disagrees` is the shape a
    hand-edit to "green" leaves behind — each must exit `2` and leave the bytes untouched.
    """
    rc, before, after = _attest_a_tmp_copy("composed_gate.py", "gate.json", mutate)
    assert rc == 2, f"`{shape}` was attested instead of refused (rc={rc})"
    assert before == after, f"`{shape}` was refused but the file was REWRITTEN anyway"


@pytest.mark.parametrize("shape,mutate", [
    ("no-measurement", lambda a: a.pop("measured_points")),
    ("no-groups", lambda a: a.pop("shared_composed_text_groups")),
    ("no-rows-source", lambda a: a["provenance"].pop("rows_source")),
    # #12476 — the same type discipline as `composed_gate.py`. `(report.get("provenance") or {})`
    # passes a non-object through to `.get`, so this exited 1 with an AttributeError before the
    # guard; the README documents exit 2 for every arm.
    ("provenance-non-dict", lambda a: a.__setitem__("provenance", ["rows_source"])),
])
def test_quote_identity_attest_refuses_the_tamper_shapes(shape, mutate):
    """FAILS IF `quote_identity.py --attest` (an arm this PR ADDS) loses a refusal.

    The probe had no `--attest` at all before, so its provenance could only be rebound by a
    ~30 s re-run against a cache that is not committed.
    """
    rc, before, after = _attest_a_tmp_copy("quote_identity.py", "quote_identity.json", mutate)
    assert rc == 2, f"`{shape}` was attested instead of refused (rc={rc})"
    assert before == after, f"`{shape}` was refused but the file was REWRITTEN anyway"


@pytest.mark.parametrize("probe_name,root", [
    # ⛔ The root must be one whose MEMBERS satisfy the probe's own membership arm below, so the
    # unchecked `.get` is actually REACHED. A bare `[]` does NOT discriminate: with the guard
    # deleted it still exits 2, because `"verdict" not in []` is True and the membership arm
    # answers first (measured — this test's first version asserted on `[]` and was green in both
    # worlds, i.e. it pinned nothing).
    ("composed_gate.py", ["verdict", "gate1_similarity"]),
    ("quote_identity.py", ["measured_points", "shared_composed_text_groups"]),
    ("gate3_false_proposals.py", ["proposals", "frame"]),
])
def test_attest_refuses_an_artifact_whose_root_is_not_an_object(probe_name, root):
    """FAILS IF any probe's shape guard is removed, so a JSON ARRAY exits 1 instead of 2.

    `json.loads("[…]")` parses fine, so `ATTEST-UNREADABLE` cannot catch it. Once the membership
    arm passes, the pre-#12476 code called `.get` on a LIST and raised `AttributeError` out of
    `main()` — exit 1, the one code a lane script cannot tell from a crash; the documented code
    for every refusal is 2 (`README`, `config/ci-surfaces.yml`). Driven on a TEMP file, never the
    committed artifact.
    """
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "artifact.json"
        target.write_text(json.dumps(root) + "\n", encoding="utf-8")
        before = target.read_bytes()
        rc = _load_probe(probe_name)._attest(str(target))
        assert rc == 2, f"{probe_name} exited {rc}, not 2, on a non-object artifact root"
        assert target.read_bytes() == before, "a refused artifact was rewritten anyway"


def test_attest_rewrites_on_a_legitimate_rebind_and_still_carries_the_residual():
    """Documents — as a MECHANISM, not as prevention — the residual the README discloses.

    A CONSISTENT artifact is attested (rc 0), and `--attest` then blesses the bytes it was handed:
    a hand-edit that flipped the verdict AND all three gate flags together produces a new, VALID
    `payload_sha256`. That is the disclosed limit (*"nothing edited this artifact after the attest"*,
    not *"these numbers came from a run"*), and this test keeps the disclosure honest by showing the
    mechanism still behaves that way — a future change that silently CLOSED the hole would red here
    and have to update the README's wording with it.
    """
    def green_flip(a):
        a["verdict"]["all_pass"] = True
        for k in a["verdict"]["gates"]:
            a["verdict"]["gates"][k] = True

    rc, before, after = _attest_a_tmp_copy("composed_gate.py", "gate.json", green_flip)
    assert rc == 0, "a self-CONSISTENT artifact must still be attestable (the documented repair)"
    assert after != before
    rebound = json.loads(after)
    assert rebound["provenance"]["payload_sha256"] == _payload_digest(rebound), (
        "the rebound payload digest must describe the bytes it was given — this is the residual, "
        "and it is what makes the README's 'not prevention' wording the correct one")
    assert rebound["verdict"]["all_pass"] is True


@pytest.mark.parametrize("probe_name", ["composed_gate.py", "quote_identity.py",
                                        "gate3_false_proposals.py"])
def test_attest_refuses_an_unreadable_artifact(probe_name):
    """FAILS IF any probe's `ATTEST-UNREADABLE` arm is weakened into a crash or a blessing.

    All three probes are driven, and this is the ONE refusal shape the other cases cannot reach: every
    other arm needs a PARSEABLE artifact to reason about, so nothing else exercises the
    `except (OSError, ValueError)` at the top of `_attest`. Measured before this test existed:
    deleting that `except` from `quote_identity.py` made the arm exit **1** (`FileNotFoundError`
    out of `main`) instead of **2** with nothing red — `probe_sha256` is not a backstop, because
    the documented repair (`--attest`) deliberately clears it. No mode bits involved, so unlike
    the `PROBE-WRITE` arm below this needs no root skip (ENOENT for every uid).
    """
    rc = _load_probe(probe_name)._attest("/nonexistent-attest-target.json")
    assert rc == 2, f"{probe_name}'s unreadable-artifact arm exited {rc}, not 2 (ATTEST-UNREADABLE)"
    # The OTHER half of the same `except (OSError, ValueError)`: a path that EXISTS but does not
    # parse. A probe killed mid-write leaves exactly this, and `OSError` alone does not cover it.
    with tempfile.TemporaryDirectory() as td:
        truncated = Path(td) / "artifact.json"
        truncated.write_text("{not json", encoding="utf-8")
        before = truncated.read_bytes()
        rc = _load_probe(probe_name)._attest(str(truncated))
        assert rc == 2, f"{probe_name}'s unreadable-artifact arm exited {rc} on invalid JSON, not 2"
        assert truncated.read_bytes() == before, "a refused artifact was rewritten anyway"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits the PROBE-WRITE arm needs")
@pytest.mark.parametrize("probe_name,artifact_name", [
    ("composed_gate.py", "gate.json"),
    ("quote_identity.py", "quote_identity.json"),
    ("gate3_false_proposals.py", "gate3_false_proposals.json"),
])
def test_attest_reports_probe_write_on_an_unwritable_target(probe_name, artifact_name):
    """FAILS IF a probe's unwritable-target arm reverts to a bare `PermissionError` (exit 1).

    The arm is `PROBE-WRITE(…): …` with exit 2 — a refusal a caller can act on, not a crash. ALL
    THREE probes are driven: the README and `config/ci-surfaces.yml` describe the refusal set as the
    probes' property, and until this was parametrized only `composed_gate.py`'s arm had a case, so
    deleting `quote_identity.py`'s `try/except OSError` reddened nothing but the probe digest
    (measured).

    ⚠️ This drives `_attest`'s arm only. gate3's MEASUREMENT writes (`--out`, `--pairs-out`, the row
    cache) are a separate surface and are covered by the next test — before it, gate3 documented
    that arm in its docstring and README while implementing it nowhere, which a green run here did
    not reveal (#12593 review).
    """
    with tempfile.TemporaryDirectory() as td:
        ro = Path(td) / "ro"
        ro.mkdir()
        target = ro / "gate.json"
        target.write_text(json.dumps(_artifact(artifact_name), indent=2) + "\n", encoding="utf-8")
        before = target.read_bytes()
        # The FILE's mode, not the directory's: `Path.write_text` truncates the existing file, so a
        # read-only DIRECTORY still lets the write through (measured -- that was this test's first
        # version, and it failed for that reason, not because the arm was broken).
        os.chmod(target, 0o400)
        try:
            rc = _load_probe(probe_name)._attest(str(target))
        finally:
            os.chmod(target, 0o600)
        assert rc == 2, f"an unwritable target exited {rc}, not 2 (PROBE-WRITE)"
        assert target.read_bytes() == before


def test_gate3_read_cache_refuses_a_cache_that_is_not_a_row_cache():
    """FAILS IF gate3's `PROBE-READ` cache arm is deleted or weakened into a crash.

    The arm was driven by NOTHING after the round that introduced it (#12593's second review round,
    finding 2): every other refusal this bundle added had a case, so deleting the guard left only
    `probe_sha256` to notice — and `--attest` is the documented way to clear that. A `1\n2\n3\n`
    cache parses cleanly and then dies on `row.get` in `_live()`: exit 1 with a traceback, the code
    the probe's own docstring says a refusal must never return. Driven through `_read_cache`, the
    extraction that makes it testable without a live read plus an encode.
    """
    probe = _load_probe("gate3_false_proposals.py")
    with tempfile.TemporaryDirectory() as td:
        bad_json = Path(td) / "bad.jsonl"
        bad_json.write_text('{"id": \n', encoding="utf-8")
        assert probe._read_cache(bad_json)[1] == 2, "a malformed cache was not refused (2)"
        assert probe._read_cache(bad_json)[0] is None
        not_rows = Path(td) / "not-rows.jsonl"
        not_rows.write_text("1\n2\n3\n", encoding="utf-8")
        rows, rc = probe._read_cache(not_rows)
        assert rc == 2 and rows is None, (
            "a cache whose lines parse but are not row objects was accepted — `_live()` would "
            "then raise AttributeError out of main(), exit 1")
        # …an EMPTY cache is NOT a refusal: main() owns that answer (GATE-EMPTY, exit 3).
        empty = Path(td) / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        assert probe._read_cache(empty) == ([], 0)
        # …and a real row cache still loads, so the guard cannot be a blanket refusal.
        good = Path(td) / "good.jsonl"
        good.write_text('{"id": "p1"}\n{"id": "p2"}\n', encoding="utf-8")
        rows, rc = probe._read_cache(good)
        assert rc == 0 and [r["id"] for r in rows] == ["p1", "p2"]


def test_gate3_attest_refuses_an_artifact_with_no_source():
    """FAILS IF gate3's `ATTEST-REFUSED` arm (no `source`) is deleted.

    `config/ci-surfaces.yml` asserts that ALL THREE probes' `--attest` refusal arms are DRIVEN —
    gate3's `ATTEST-REFUSED` arm was driven by nothing (the tamper parametrization covers
    `composed_gate.py` and `quote_identity.py` only), so deleting `if not source: return 2` reddened
    nothing while the row claimed coverage (#12593 review finding 5). The arm matters because
    without it `--attest` blesses an artifact whose numbers are attributable to NO cache: the
    `rows_source` it writes would be invented.
    """
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "gate3_false_proposals.json"
        artifact = _artifact("gate3_false_proposals.json")
        artifact.pop("source")
        target.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        before = target.read_bytes()
        rc = _load_probe("gate3_false_proposals.py")._attest(str(target))
        assert rc == 2, f"a source-less artifact was attested (exit {rc}), not refused (2)"
        assert target.read_bytes() == before, "a refused attest must leave the artifact untouched"


def test_gate3s_measurement_writes_all_route_through_the_one_guard():
    """FAILS IF gate3 regrows a write path that bypasses `_write` (the PROBE-WRITE arm).

    The probe's docstring, the README's refusal inventory and `config/ci-surfaces.yml` all describe
    `PROBE-WRITE` as this probe's property, but the guard had to be EXTRACTED (`_write`) before it
    could be driven without a live read plus a full encode: the first version of this bundle
    documented the arm and implemented it only in `_attest`, so `--out /unwritable/path` raised
    `OSError` out of `main()` — exit 1 with a traceback, after the whole measurement (#12593 review
    finding 1). Driving `_write` directly is what makes the promise mechanical; THIS assertion is
    what keeps the WIRING honest, because the behavioural case drives the helper and would stay
    green over a `main()` that went back to a bare `open()`.

    ⛔ A TEXTUAL `open(` scan is defeatable and this is an AST walk instead: `Path.write_text`,
    `write_bytes`, `os.open`, `io.open` and `open ("x")` (a space) all slip past a line regex while
    doing exactly the damage — and `Path.write_text` is the idiom THIS repo uses, this very file
    included, so it is the plausible shape of a future regression (measured: the regex version left
    `pathlib.Path(p).write_text(...)` in `main()` green).
    """
    import ast

    src = (PROBES / "gate3_false_proposals.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    WRITE_ATTRS = {"write_text", "write_bytes", "open"}

    writes = []          # (lineno, enclosing function or None)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else "")
        if name not in WRITE_ATTRS:
            continue
        # `open()` with NO arguments at all is prose, not a call; every real one passes an
        # argument OR a keyword. ⛔ `not node.args` alone is a HOLE: `open(file=p, mode="w")` and
        # `Path(p).open(mode="w")` are real writes with empty `args` (measured green on that
        # version), so a keyword-only call must be caught too.
        if name == "open" and not node.args and not node.keywords:
            continue
        writes.append(node)

    def enclosing_ok(node) -> bool:
        # Walk the tree once more for the innermost FunctionDef containing `node`.
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(child is node for child in ast.walk(fn)):
                    return fn.name == "_write"
        return False

    stray = [(w.lineno, getattr(w.func, "id", getattr(w.func, "attr", "")))
             for w in writes if not enclosing_ok(w)]
    assert not stray, (
        "gate3 performs a file write outside its `_write` guard — a bare write on --out / "
        "--pairs-out / the row cache raises OSError out of main() (exit 1, the code it promises "
        f"never to return): {sorted(stray)}")

    probe = _load_probe("gate3_false_proposals.py")
    # …and the same guard is the one a WRITABLE target goes through, so it cannot be a
    # refusal-only stub: a pass must actually write.
    with tempfile.TemporaryDirectory() as td:
        ok = Path(td) / "ok.json"
        rc = probe._write(str(ok), lambda fh: fh.write('{"a": 1}'))
        assert rc == 0, f"a writable target exited {rc}, not 0"
        assert json.loads(ok.read_text(encoding="utf-8")) == {"a": 1}


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root ignores the mode bits the PROBE-WRITE arm needs")
def test_gate3s_write_guard_refuses_an_unwritable_target():
    """FAILS IF gate3's `_write` re-raises `OSError` instead of returning the documented exit 2.

    ⛔ The root guard is NOT boilerplate: the sibling `test_attest_reports_probe_write_on_an_
    unwritable_target` carries it for the same reason, and this case drives the same mechanism
    (`chmod 0o400` + a write). Without it a uid-0 runner — a container lane, a cell, the docker
    test lane — sees the `open` SUCCEED, gets `rc == 0`, and reds a file dual-registered `core` +
    `tier1`, i.e. on exactly the docs-only diffs this pin exists for (#12593 review finding 2).
    """
    probe = _load_probe("gate3_false_proposals.py")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "out.json"
        target.write_text("prior", encoding="utf-8")
        # The FILE's mode, not the directory's — `open(..., "w")` truncates an existing file, so a
        # read-only DIRECTORY still lets the write through.
        os.chmod(target, 0o400)
        try:
            rc = probe._write(str(target), lambda fh: fh.write("x"))
        finally:
            os.chmod(target, 0o600)
        assert rc == 2, f"an unwritable --out exited {rc}, not 2 (PROBE-WRITE)"
        assert target.read_bytes() == b"prior", "a refused write must leave the target untouched"
