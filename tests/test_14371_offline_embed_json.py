"""#14371 — the S4 embed-list saving, measured with NO provider key.

WHY THIS FILE EXISTS
--------------------
`#14343` shipped `TORTOISE_S4_EMBED_JSON=compact` disarmed and MEASURED the
whitespace it removes, but the numbers lived as prose in an evidence bundle and
only as a side-section of `decompose`, whose sibling sections need a paid corpus.
The token half of that lever is arithmetic over artefacts in the tree — two
`json.dumps` calls on the SAME objects, counted — so it must be reproducible on
a box with **no key at all**. `#14371` delivers exactly that half; the quality
half (`#7949` E1–E4) still needs a provider and is explicitly NOT delivered.

What is pinned here, in the order the risks run:

1. **The two layouts parse back to the IDENTICAL object** on every real emission
   (no tokenizer). This is the brief's own named risk: "a format change the
   downstream parser tolerates but reads differently". A saving is only
   information-neutral if the region round-trips — key order included.
2. **The recorded cells are reachable from the committed artifact**, so a stale
   or hand-edited `embed-json-offline.md` reds instead of shipping. The figures
   themselves are NOT re-derived here on a cold tiktoken cache: `cl100k_base`
   fetches ~1.6 MB of BPE ranks over the network on a cold cache, which is real
   egress from a deterministic test (`tests/conftest.py`'s egress guard cannot
   see it — it goes over `urllib`, not `httpx`). Where the cache IS warm the
   full measurement runs and is asserted against the recorded cells, so the
   instrument is live on a box that can run it offline and honest on one that
   cannot.
3. **A non-production ambient posture is REFUSED**, before the tokenizer is
   touched: the solved embed region is only comparable to the recorded figure
   under the posture the corpus was scored in. `TORTOISE_PACKS_DIR` is graded by
   RESOLUTION, so pointing it at the in-tree `packs/` is not a refusal — a guard
   that blocks a correct measurement is a false block, not diligence.
4. **The verb is offline by construction** — it calls no provider. Pinned by the
   absence of any model/builder call in its source, and by the run itself
   reporting `network_used: False`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools import extractor_cost_measure as m  # noqa: E402

_RECEIPTS = _REPO_ROOT / "tests/eval/write_path/receipts/7947-arms-2026-10-18"
_EVIDENCE = _REPO_ROOT / "docs/evidence/14277-extractor-cost/measurements.jsonl"
_ARTIFACT_MD = _REPO_ROOT / "docs/evidence/14343-amplify/embed-json-offline.md"
_ARTIFACT_JSON = _REPO_ROOT / "docs/evidence/14343-amplify/embed-json-offline.json"

#: The corpus's measured `cl100k_base` cells — the same ones
#: `tests/test_14343_embed_list_serialization.py` pins for §1 of the record, and
#: the same ones `docs/evidence/14343-amplify/README.md` §4a publishes. A
#: re-measurement that moves any of them is a change to the CORPUS or to the
#: serialization, and must be recorded, not absorbed.
_RECORDED = {
    "sessions": 7,
    "emissions": 14,
    "items": 580,
    "indent_tokens": 77_561,
    "compact_tokens": 52_346,
    "saved_tokens": 25_215,
    "saved_per_emission": 1_801,
}


def _emissions() -> list[tuple[str, str, dict]]:
    out = []
    for path in sorted(_RECEIPTS.glob("*.arms.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        for arm, embed in (rec.get("embed_list") or {}).items():
            if isinstance(embed, dict) and any(
                    isinstance(v, list) and v for v in embed.values()):
                out.append((path.name.split(".")[0], arm, embed))
    return out


def _host_bpe_cache_dir() -> Path | None:
    """A BPE cache that ALREADY holds `cl100k_base`, anywhere on this box.

    ⛔ `tests/conftest.py` redirects `tempfile.tempdir` into a private session
    root at import, so tiktoken's DEFAULT cache location is empty inside a
    suite run even when the host cache is warm — the skip this helper feeds
    would then be permanent and the live measurement would never execute
    anywhere. Pointing `TIKTOKEN_CACHE_DIR` at the host's existing copy is what
    "offline" means here: no fetch, whatever directory it lives in. The run
    still ASSERTS `network_used is False`, so a cache that turns out not to
    hold the right blob reds instead of silently downloading.
    """
    import os
    import tempfile
    candidates = [os.environ.get("TIKTOKEN_CACHE_DIR"),
                  str(Path(tempfile.gettempdir()) / "data-gym-cache"),
                  "/tmp/data-gym-cache"]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        try:
            if path.is_dir() and any(p.is_file() for p in path.iterdir()):
                return path
        except OSError:
            continue
    return None


_BPE_CACHE = _host_bpe_cache_dir()


def test_the_verb_is_registered_and_takes_the_two_offline_inputs(capsys):
    """`--help` is the machine-checkable registration.

    A verb that is not wired cannot be run from the reproduce command the
    evidence bundles publish, and a bundle whose command does not exist reads as
    a measurement nobody can re-take. The top-level help must ALSO say the verb
    is offline, so a reader looking for the provider-key work does not mistake
    it for it.
    """
    with pytest.raises(SystemExit) as exc:
        m.main(["embed-json", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--receipts" in help_text
    assert "--evidence" in help_text

    with pytest.raises(SystemExit) as exc:
        m.main(["--help"])
    assert exc.value.code == 0
    top = capsys.readouterr().out
    assert "embed-json" in top and "OFFLINE" in top


def test_the_verb_reuses_the_decomposition_s_instrument():
    """⛔ One harness, not two: the verb must call the instrument, not re-derive.

    The brief forbids "a second harness" by name, and a second measurement of
    the same quantity is how two records drift while both look measured. This is
    a structural pin (the AST/source is cheaper than a mock that could itself
    drift); the numeric pins below are what make it a *measurement*.
    """
    import inspect
    source = inspect.getsource(m.embed_json_measurement)
    assert "serialization_lever(" in source, (
        "the verb must COUNT through `serialization_lever`, the instrument "
        "#14343 used, not a second serializer")
    assert "decompose(" in source, (
        "the solved embed region must come from `decompose`, or its per-document "
        "figure is a literal that cannot track the corpus")


def test_the_verb_makes_no_provider_call():
    """No model, no key, no network: the verb's source must not build a model."""
    import inspect
    source = inspect.getsource(m.embed_json_measurement)
    for forbidden in ("_build_model(", "build_extractor_model(", "measure_one(",
                      "run_measurement("):
        assert forbidden not in source, (
            f"{forbidden} in `embed_json_measurement` — that is a paid path, and "
            "this verb's whole point is that the token half needs no key")


# ── the information-neutrality pin (no tokenizer, runs everywhere) ────────


def test_both_layouts_parse_back_to_the_identical_object():
    """The saving is information-neutral ONLY if the region round-trips.

    Both renders come from `_embed_json_kwargs()` — the PRODUCTION seam, not a
    re-spelling of the same call — so this pins what the pipeline would actually
    send, key order included (Python dicts preserve insertion order through
    `json`), on every real emission in the corpus.
    """
    emissions = _emissions()
    assert len(emissions) == _RECORDED["emissions"]
    for session, arm, embed in emissions:
        today = json.dumps(embed, indent=1)
        compact = json.dumps(embed, separators=(",", ":"))
        assert today != compact, (
            f"{session}/{arm}: the two layouts are byte-identical, so the "
            "measurement would be comparing a render with itself")
        assert json.loads(today) == json.loads(compact), (
            f"{session}/{arm}: the compact layout parses to a DIFFERENT object "
            "— the saving is not information-neutral on this emission")
        # key order is part of what a downstream reader sees
        assert list(json.loads(today)) == list(json.loads(compact))


def test_the_production_seam_resolves_to_the_two_measured_renders(monkeypatch):
    """Unset → `indent=1` (today's bytes); `compact` → the measured separators.

    The verb measures the two layouts the SEAM can produce, so a change to the
    seam's normalization (unset/blank/garbage) must red here rather than silently
    measuring a third layout the pipeline cannot produce.
    """
    from tortoise import extractor_v2 as v2
    for value in (None, "", "   ", "garbage", "COMPACT-ish"):
        if value is None:
            monkeypatch.delenv(m._TOGGLE, raising=False)
        else:
            monkeypatch.setenv(m._TOGGLE, value)
        assert v2._embed_json_kwargs() == {"indent": 1}, (
            f"{value!r} must render today's bytes (the #4097 tristate contract)")
    monkeypatch.setenv(m._TOGGLE, "compact")
    assert v2._embed_json_kwargs() == {"separators": (",", ":")}


# ── the posture guard: fail closed, but never on a correct posture ───────


@pytest.fixture
def clean_posture(monkeypatch):
    """The production posture: every switch unset.

    ⛔ This repo's own `tests/conftest.py` exports `TORTOISE_S4_DELTA=0` for
    determinism, so a test that assumes an already-clean environment measures
    the SUITE's posture, not the tool's.
    """
    for switch in (*m._POSTURE_SWITCHES, m._POSTURE_PACKS_DIR):
        monkeypatch.delenv(switch, raising=False)
    return monkeypatch


def test_a_nonproduction_posture_is_refused_before_any_measurement(clean_posture):
    """A switch that moves the static render makes the solve incomparable.

    The refusal must happen BEFORE the tokenizer loads (so a box in the wrong
    posture does not spend a network fetch to learn it is in the wrong posture),
    and it must be exit 2 — argparse's usage code, the one the tool already uses
    for "you invoked this wrongly" — not 1, which the tool uses for a failed run.
    """
    clean_posture.setenv("TORTOISE_S4_DELTA", "0")
    with pytest.raises(SystemExit) as exc:
        m.embed_json_measurement(_RECEIPTS, _EVIDENCE)
    assert exc.value.code == 2


@pytest.mark.parametrize("switch,value", [
    ("TORTOISE_S4_DELTA", "0"),
    ("TORTOISE_S4_DELTA", "off"),
    ("TORTOISE_EXTRACTOR_PROMPT", "compact"),
    ("TORTOISE_CLASSIFY_LATER", "1"),
    ("TORTOISE_REFERENTIALITY", "1"),
])
def test_every_ambient_prompt_switch_is_guarded(clean_posture, switch, value):
    clean_posture.setenv(switch, value)
    problems = m._posture_problems()
    assert any(switch in p for p in problems), (
        f"{switch}={value} moves a static region the embed solve subtracts, so "
        "it must be refused rather than measured under")


@pytest.mark.parametrize("switch,value", [
    # resolves to the SHIPPED default (delta on) — the recorded render
    ("TORTOISE_S4_DELTA", "1"),
    ("TORTOISE_S4_DELTA", "true"),
    # the #4097 contract: garbage resolves to the default, never coerced
    ("TORTOISE_S4_DELTA", "garbage"),
    # the verbose (default) master render is the recorded one
    ("TORTOISE_EXTRACTOR_PROMPT", "verbose"),
    ("TORTOISE_CLASSIFY_LATER", "0"),
    ("TORTOISE_REFERENTIALITY", "0"),
])
def test_a_switch_that_resolves_to_the_recorded_render_is_NOT_refused(
        clean_posture, switch, value):
    """⛔ The other half of the guard: a false block is also a defect.

    Grading by PRESENCE would refuse `TORTOISE_S4_DELTA=1` — the shipped
    default — and every fleet lane that exports it. The guard resolves each
    value through the pipeline's own predicate, so only a posture that changes
    the render is refused.
    """
    clean_posture.setenv(switch, value)
    assert m._posture_problems() == []


def test_the_in_tree_packs_dir_is_a_production_posture(clean_posture):
    """Graded by RESOLUTION: the in-tree tree renders the recorded vocabulary."""
    clean_posture.setenv("TORTOISE_PACKS_DIR", str(_REPO_ROOT / "packs"))
    assert m._posture_problems() == []


def test_an_out_of_tree_packs_dir_is_refused(clean_posture, tmp_path):
    clean_posture.setenv("TORTOISE_PACKS_DIR", str(tmp_path))
    problems = m._posture_problems()
    assert problems and "TORTOISE_PACKS_DIR" in problems[0]


def test_a_clean_environment_has_no_posture_problems(clean_posture):
    assert m._posture_problems() == []


# ── the committed artifact: the recorded cells must be REACHABLE ─────────


def test_the_committed_artifact_records_the_measured_cells():
    """The §4a cells must appear in the artifact the PR cites (no tokenizer).

    The token figures are recorded rather than re-derived on a cold cache (§
    module docstring), so they are made REACHABLE this way instead: a
    regenerated artifact that moved a cell, or a hand-edited one, reds here.
    """
    assert _ARTIFACT_MD.exists(), (
        f"{_ARTIFACT_MD} is missing — regenerate it with "
        "`uv run python tools/extractor_cost_measure.py embed-json ...`")
    text = _ARTIFACT_MD.read_text(encoding="utf-8")
    for cell in (f"{_RECORDED['indent_tokens']:,}",
                 f"{_RECORDED['compact_tokens']:,}",
                 f"{_RECORDED['saved_tokens']:,}",
                 f"{_RECORDED['saved_per_emission']:,}"):
        assert cell in text, f"the artifact no longer records {cell} tokens"
    assert f"{_RECORDED['items']:,} items" in text
    assert f"{_RECORDED['emissions']} emissions" in text


def test_the_committed_artifact_states_what_it_does_not_deliver():
    """The one sentence a reader must not have to infer.

    A token measurement that stays silent about the unrun quality bar is how a
    disarmed lever gets armed by someone reading a green table (`#14371`'s own
    reason to exist). The artifact AND the JSON carry it.
    """
    text = _ARTIFACT_MD.read_text(encoding="utf-8")
    assert "#7949" in text and "NOT delivered here" in text
    assert "stays **OFF**" in text
    data = json.loads(_ARTIFACT_JSON.read_text(encoding="utf-8"))
    assert "#7949" in data["not_delivered"]
    assert data["offline"] is True and data["provider_calls"] == 0
    assert data["encoder_cache"]["network_used"] is False, (
        "the recorded run fetched the BPE ranks — it is not the offline run the "
        "artifact claims; re-run on a warm cache and re-record")


def test_the_committed_artifact_carries_the_reproduce_command():
    text = _ARTIFACT_MD.read_text(encoding="utf-8")
    assert "tools/extractor_cost_measure.py embed-json" in text
    assert "7947-arms-2026-10-18" in text


def test_the_committed_allowance_cells_are_the_recorded_floors():
    """The capacity cells, from the artifact's own unit — no tokenizer needed.

    `reduction-measured.md` (§2) and `README.md` (§4a) publish 46→49 / 61→66 /
    370→398. A regeneration that moved the unit must move these by RECORDING
    them, and this pin is what makes that a decision instead of a drift.
    """
    data = json.loads(_ARTIFACT_JSON.read_text(encoding="utf-8"))
    cells = {b["tier"]: (b["documents_before"], b["documents_after"])
             for b in data["documents_per_month"]}
    assert cells == {"solo": (46, 49), "pro": (61, 66), "team": (370, 398)}


# ── the live measurement (runs where the BPE ranks are already cached) ───


@pytest.mark.skipif(
    _BPE_CACHE is None,
    reason="no tiktoken BPE cache on this box; fetching cl100k_base's ranks is "
           "network egress from a deterministic test, so the cells stay pinned "
           "in the committed artifact (module docstring) instead of downloaded")
def test_the_offline_measurement_reproduces_the_recorded_cells(clean_posture):
    """End to end: the verb, on the real corpus, with no provider and no fetch.

    ⛔ The assertions are the PUBLISHED cells, not a re-derivation: this run and
    `docs/evidence/14343-amplify/README.md` §4a must agree exactly, or one of
    them is wrong and neither is a measurement.
    """
    clean_posture.setenv("TIKTOKEN_CACHE_DIR", str(_BPE_CACHE))
    measured = m.embed_json_measurement(_RECEIPTS, _EVIDENCE)

    agg = measured["aggregate"]
    assert measured["offline"] is True
    assert measured["provider_calls"] == 0
    assert measured["encoder_cache"]["network_used"] is False
    assert agg["emissions"] == _RECORDED["emissions"]
    assert agg["items"] == _RECORDED["items"]
    assert agg["indented_tokens"] == _RECORDED["indent_tokens"]
    assert agg["compact_tokens"] == _RECORDED["compact_tokens"]
    assert agg["saved_tokens"] == _RECORDED["saved_tokens"]
    assert agg["saved_per_emission"] == _RECORDED["saved_per_emission"]
    assert agg["saved_pct"] == 32.5

    # the per-document figure and the arithmetic that produces it
    doc = measured["per_document"]
    assert doc["embed_region_per_s4_call"] == pytest.approx(12_411.5, abs=0.1)
    assert doc["s4_calls_per_doc"] == pytest.approx(1.14, abs=0.01)
    assert doc["embed_region_per_doc"] == pytest.approx(14_149.1, abs=1.0)
    assert doc["before_total"] == 64_799
    assert doc["saved_per_doc"] == pytest.approx(4_599.9, abs=1.0)
    assert doc["after_total"] == pytest.approx(60_199.1, abs=1.0)

    # what it buys — the FLOORS, and the arithmetic that keeps them floors
    cells = {b["tier"]: (b["allowance"], b["documents_before"],
                         b["documents_after"])
             for b in measured["documents_per_month"]}
    assert cells["solo"] == (3_000_000, 46, 49)
    assert cells["pro"] == (4_000_000, 61, 66)
    assert cells["team"] == (24_000_000, 370, 398)
    for _, allowance, after in cells.values():
        n = int(allowance // doc["after_total"])
        assert n * doc["after_total"] <= allowance
        assert (n + 1) * doc["after_total"] > allowance, (
            f"{n} is not the FLOOR for {allowance:,} — one more document would "
            "still fit, and a capacity cell that under-promises its own tier is "
            "as wrong as one that over-promises")

    rendered = m.render_embed_json_measurement(measured)
    assert "NOT delivered here" in rendered
    assert "#7949" in rendered
    assert "embed-json" in rendered
