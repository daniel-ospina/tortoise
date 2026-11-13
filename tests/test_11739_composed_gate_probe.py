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

Every test names the mutation that reds it. Hermetic: two probes are hashed and two JSON
artifacts are parsed, plus one float-pair read from `tortoise.embeddings` (no model, no key, no
network, no DB).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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
    """The EVIDENCE binding, recomputed exactly as both probes compute it: sha256 over every
    field of the artifact except `provenance`, TOGETHER WITH the row source those fields are
    attributed to, canonicalised `indent=2, sort_keys=True`.

    Deliberately a pure function of the parsed object, so it is a digest of the FIELDS and not of
    the file's formatting — reformatting the JSON does not red, editing a number does. The
    attribution is inside the digest (`rows_source`), because re-pointing it at a different cache
    is an edit to the evidence: measured before that, `rows_source` -> `/tmp/some-other-cache.jsonl`
    left the pin at 10 passed.
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
@pytest.mark.parametrize("name", ["gate.json", "quote_identity.json"])
def test_artifact_evidence_is_the_evidence_that_was_attested(name: str):
    """FAILS IF a number, a count or a verdict inside either artifact is hand-edited.

    `provenance.payload_sha256` is written by the probe (`_provenance()`), over every field
    except `provenance` itself. Recomputing it from the committed bytes is what makes
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
    this test existed: setting it to `/tmp/some-other-cache.jsonl` gave `9 passed`. `gate.json` also
    records the same string at the top level as `source`, so the two must agree.
    """
    gate = _artifact("gate.json")
    identity = _artifact("quote_identity.json")
    # The encoder key is named per probe (`encoder` describes the embedder that produced the
    # vectors; `composed_encoder` says the root-cause probe encodes nothing and reads the
    # composer's word budget) — so check the field that artifact actually publishes.
    for name, artifact, encoder_key in (("gate.json", gate, "encoder"),
                                        ("quote_identity.json", identity, "composed_encoder")):
        prov = artifact["provenance"]
        assert prov["rows_source"], f"{name} names no row source — nothing attributes the numbers"
        assert prov[encoder_key], (
            f"{name} names no `{encoder_key}` — the 0.89/0.72 bands are model-calibrated")
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
])
def test_quote_identity_attest_refuses_the_tamper_shapes(shape, mutate):
    """FAILS IF `quote_identity.py --attest` (an arm this PR ADDS) loses a refusal.

    The probe had no `--attest` at all before, so its provenance could only be rebound by a
    ~30 s re-run against a cache that is not committed.
    """
    rc, before, after = _attest_a_tmp_copy("quote_identity.py", "quote_identity.json", mutate)
    assert rc == 2, f"`{shape}` was attested instead of refused (rc={rc})"
    assert before == after, f"`{shape}` was refused but the file was REWRITTEN anyway"


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits the PROBE-WRITE arm needs")
def test_attest_reports_probe_write_on_an_unwritable_target():
    """FAILS IF an unwritable target reverts to a bare `PermissionError` traceback (exit 1).

    The arm is `PROBE-WRITE(…): …` with exit 2 — a refusal a caller can act on, not a crash.
    """
    with tempfile.TemporaryDirectory() as td:
        ro = Path(td) / "ro"
        ro.mkdir()
        target = ro / "gate.json"
        target.write_text(json.dumps(_artifact("gate.json"), indent=2) + "\n", encoding="utf-8")
        before = target.read_bytes()
        # The FILE's mode, not the directory's: `Path.write_text` truncates the existing file, so a
        # read-only DIRECTORY still lets the write through (measured -- that was this test's first
        # version, and it failed for that reason, not because the arm was broken).
        os.chmod(target, 0o400)
        try:
            rc = _load_probe("composed_gate.py")._attest(str(target))
        finally:
            os.chmod(target, 0o600)
        assert rc == 2, f"an unwritable target exited {rc}, not 2 (PROBE-WRITE)"
        assert target.read_bytes() == before
