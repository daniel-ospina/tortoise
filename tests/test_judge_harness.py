"""Unit tests for tools/judge_harness.py — epic #909 slice 1a (#945).

Covers the DE2E-1 harness plumbing: utterance-tagged parsing, prompt assembly,
deterministic fixtures (mocked judge model — no LLM calls), the degenerate-
labeling flag (DE2E-1 neg (a)), and the labeled-window JSON contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: I001

from tools.judge_harness import (
    RUBRIC_SYSTEM,
    Edu,
    Label,  # noqa: F401
    LabelParseError,
    LabeledWindow,  # noqa: F401
    TranscriptError,
    build_user_prompt,
    label_window,
    main as jh_main,
    parse_labels,
    parse_transcript,
)

TRANSCRIPT = (
    "0: owner: We decided to ship the extractor first.\n"
    "1: agent: The fix was merged and deployed.\n"
    "2: agent: should we validate on two windows first\n"
)


class MockModel:
    """Scripted judge — the deterministic fixture (never calls an LLM)."""

    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.response


def labels_for(indices: list[int], cls: str = "claim") -> list[dict]:
    return [
        {"edu_index": i, "class": cls, "kind": None, "atomicity": True,
         "source_ref": None, "relations": []}
        for i in indices
    ]


# ── Transcript parsing ──────────────────────────────────────────────────────

def test_parse_transcript_basic():
    edus = parse_transcript(TRANSCRIPT)
    assert edus == [
        Edu(index=0, role="owner", text="We decided to ship the extractor first."),
        Edu(index=1, role="agent", text="The fix was merged and deployed."),
        Edu(index=2, role="agent", text="should we validate on two windows first"),
    ]


def test_parse_transcript_skips_blanks_and_comments():
    text = "# a comment\n\n0: owner: first\n\n# another\n1: tool: second\n"
    edus = parse_transcript(text)
    assert [e.index for e in edus] == [0, 1]
    assert edus[1].role == "tool"


def test_parse_transcript_malformed_line():
    with pytest.raises(TranscriptError, match="line 2"):
        parse_transcript("0: owner: fine\nthis is not tagged\n")


def test_parse_transcript_missing_text():
    with pytest.raises(TranscriptError):
        parse_transcript("0: owner:\n")


def test_parse_transcript_duplicate_index():
    with pytest.raises(TranscriptError, match="duplicate EDU index 0"):
        parse_transcript("0: owner: a\n0: agent: b\n")


def test_parse_transcript_negative_index_rejected():
    # The format is <index>: <role>: <text> with a non-negative int index.
    with pytest.raises(TranscriptError):
        parse_transcript("-1: owner: a\n")


# ── Prompt assembly ─────────────────────────────────────────────────────────

def test_build_user_prompt_numbered_edus():
    edus = parse_transcript(TRANSCRIPT)
    prompt = build_user_prompt(edus)
    assert "0. [owner] We decided to ship the extractor first." in prompt
    assert "2. [agent] should we validate on two windows first" in prompt


def test_rubric_prompt_carries_the_spec_core():
    """The rubric prompt must keep the spec §1-2 content (drift guard)."""
    for fragment in (
        "DECISION", "EVENT", "CLAIM",           # two-axis class table
        "commissive", "assertive",
        "R1\u2227R3",                            # the decision conjunction
        '"should" is a RECOMMENDATION',         # should-vs-decision discriminator
        "process", "none",                      # closed vocabulary
        "edu_index",                             # output schema
        "fixed/repaired/shipped/completed",     # event cues
        "decided, chose, agreed to",            # decision cues
        "watch-gate not a statistical test",    # mitigation cues (R9)
        "atomicity",
    ):
        assert fragment in RUBRIC_SYSTEM, f"rubric drifted: missing {fragment!r}"


# ── Judge-response parsing ──────────────────────────────────────────────────

def test_parse_labels_valid():
    edus = parse_transcript(TRANSCRIPT)
    labels = parse_labels(
        json.dumps({"labels": labels_for([0, 1, 2], "decision")}), edus
    )
    assert [l.edu_index for l in labels] == [0, 1, 2]  # noqa: E741
    assert labels[0].class_ == "decision"
    assert labels[0].atomicity is True
    assert labels[0].kind is None and labels[0].source_ref is None
    assert labels[0].relations == []


def test_parse_labels_strips_markdown_fences():
    edus = parse_transcript(TRANSCRIPT)
    raw = "```json\n" + json.dumps({"labels": labels_for([0])}) + "\n```"
    assert [l.edu_index for l in parse_labels(raw, edus)] == [0]  # noqa: E741


def test_parse_labels_relations_roundtrip():
    edus = parse_transcript(TRANSCRIPT)
    raw = json.dumps({
        "labels": [{
            "edu_index": 0,
            "class": "claim",
            "relations": [
                {"type": "IMPL", "source": 0, "target": 1},
                {"type": "MITIGATES", "source": 2, "target": "[0\u21921]",
                 "bias": 0.35},
            ],
        }]
    })
    labels = parse_labels(raw, edus)
    assert labels[0].relations[0].type == "IMPL"
    assert labels[0].relations[0].target == 1
    assert labels[0].relations[1].type == "MITIGATES"
    assert labels[0].relations[1].bias == 0.35


def test_parse_labels_defaults_for_optional_fields():
    edus = parse_transcript(TRANSCRIPT)
    labels = parse_labels('{"labels": [{"edu_index": 1, "class": "event"}]}', edus)
    assert labels[0].kind is None
    assert labels[0].atomicity is True
    assert labels[0].source_ref is None
    assert labels[0].relations == []


def test_parse_labels_not_json():
    with pytest.raises(LabelParseError, match="not JSON"):
        parse_labels("I think the answer is no", parse_transcript(TRANSCRIPT))


def test_parse_labels_missing_labels_key():
    with pytest.raises(LabelParseError, match="missing 'labels'"):
        parse_labels('{"foo": 1}', parse_transcript(TRANSCRIPT))


def test_parse_labels_invalid_class():
    edus = parse_transcript(TRANSCRIPT)
    with pytest.raises(LabelParseError, match="unknown class"):
        parse_labels('{"labels": [{"edu_index": 0, "class": "recommendation"}]}', edus)


def test_parse_labels_missing_class():
    edus = parse_transcript(TRANSCRIPT)
    with pytest.raises(LabelParseError, match="missing 'class'"):
        parse_labels('{"labels": [{"edu_index": 0}]}', edus)


def test_parse_labels_edu_index_not_in_window():
    edus = parse_transcript(TRANSCRIPT)
    with pytest.raises(LabelParseError, match="edu_index 9 not in window"):
        parse_labels('{"labels": [{"edu_index": 9, "class": "claim"}]}', edus)


def test_parse_labels_duplicate_edu():
    edus = parse_transcript(TRANSCRIPT)
    with pytest.raises(LabelParseError, match="duplicate label"):
        parse_labels(
            '{"labels": [{"edu_index": 0, "class": "claim"}, '
            '{"edu_index": 0, "class": "event"}]}',
            edus,
        )


def test_parse_labels_bad_relation_type():
    edus = parse_transcript(TRANSCRIPT)
    with pytest.raises(LabelParseError, match="unknown relation type"):
        parse_labels(
            '{"labels": [{"edu_index": 0, "class": "claim", "relations": '
            '[{"type": "SUPPORTS"}]}]}',
            edus,
        )


def test_parse_labels_bad_bias_range():
    edus = parse_transcript(TRANSCRIPT)
    for bias in (0.05, 0.60, 1.5):
        payload = (
            '{"labels": [{"edu_index": 0, "class": "claim", "relations": '
            '[{"type": "MITIGATES", "bias": ' + str(bias) + '}]}]}'
        )
        with pytest.raises(LabelParseError, match="canonical MITIGATES range"):
            parse_labels(payload, edus)


def test_parse_labels_type_validation_branches():
    """Every garbage-defense branch of parse_labels (uncontrolled LLM output)."""
    edus = parse_transcript(TRANSCRIPT)
    cases = [
        ('{"labels": {}}', "'labels' is not an array"),
        ('{"labels": ["x"]}', "not an object"),
        ('{"labels": [{"edu_index": "1", "class": "claim"}]}', "must be an int"),
        ('{"labels": [{"edu_index": true, "class": "claim"}]}', "must be an int"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "kind": 7}]}', "'kind' must be a string"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "atomicity": 1}]}', "'atomicity' must be a bool"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "source_ref": 9}]}', "'source_ref' must be a string"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "relations": {}}]}', "'relations' must be an array"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "relations": [{}]}]}', "missing 'type'"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "relations": '
         '[{"type": "IMPL", "source": "0"}]}]}', "'source' must be an int"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "relations": '
         '[{"type": "IMPL", "source": true}]}]}', "'source' must be an int"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "relations": '
         '[{"type": "IMPL", "target": true}]}]}', "'target' must be an int"),
        ('{"labels": [{"edu_index": 0, "class": "claim", "relations": '
         '[{"type": "MITIGATES", "bias": true}]}]}', "'bias' must be a number"),
    ]
    for payload, message in cases:
        with pytest.raises(LabelParseError, match=message):
            parse_labels(payload, edus)


# ── The labeling pipeline (deterministic, mocked judge) ─────────────────────

def test_label_window_end_to_end_mocked():
    edus = parse_transcript(TRANSCRIPT)
    model = MockModel(json.dumps({"labels": labels_for([0, 1, 2])}))
    window = label_window(edus, model, window_id="w1", window_type="design",
                          judge="frontier")
    assert window.window_id == "w1"
    assert window.window_type == "design"
    assert window.judge == "frontier"
    assert window.n_edus == 3
    assert len(window.labels) == 3
    assert window.degenerate is False
    assert window.incomplete is False
    # The rubric prompt is what was sent as system; EDUs as the user payload.
    assert model.calls[0]["system"] == RUBRIC_SYSTEM
    assert "0. [owner] We decided to ship the extractor first." in model.calls[0]["user"]


def test_label_window_json_contract():
    """Output shape per issue #945: {window_id, labels: [{edu_index, class,
    kind, atomicity, source_ref, relations}]}."""
    edus = parse_transcript(TRANSCRIPT)
    model = MockModel(json.dumps({"labels": labels_for([0, 1, 2])}))
    data = label_window(edus, model, window_id="w1").to_json()
    assert set(data) >= {"window_id", "labels"}
    assert data["window_id"] == "w1"
    for label in data["labels"]:
        assert set(label) == {
            "edu_index", "class", "kind", "atomicity", "source_ref", "relations",
        }
        assert label["class"] in ("decision", "event", "claim", "process", "none")


def test_degenerate_labeling_flagged():
    """DE2E-1 neg (a): judge emits EMPTY labels on a NON-empty window."""
    edus = parse_transcript(TRANSCRIPT)
    model = MockModel('{"labels": []}')
    window = label_window(edus, model, window_id="w1")
    assert window.n_edus == 3
    assert window.labels == []
    assert window.degenerate is True


def test_incomplete_labeling_flag():
    """Fewer labels than EDUs is not degenerate but is flagged (informational)."""
    edus = parse_transcript(TRANSCRIPT)
    model = MockModel(json.dumps({"labels": labels_for([0])}))
    window = label_window(edus, model, window_id="w1")
    assert window.degenerate is False
    assert window.incomplete is True


# ── CLI (dependency-injected model factory — still no LLM) ──────────────────

def test_main_cli_end_to_end(tmp_path, capsys):
    transcript = tmp_path / "w1.txt"
    transcript.write_text(TRANSCRIPT)
    out = tmp_path / "w1.json"
    code = jh_main(["--transcript", str(transcript), "--window-id", "w1",
                    "--window-type", "design", "--model", "mock", "--out",
                    str(out)], model_factory=lambda name: MockModel(
                        json.dumps({"labels": labels_for([0, 1, 2])})))
    assert code == 0
    data = json.loads(out.read_text())
    assert data["window_id"] == "w1"
    assert len(data["labels"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""  # JSON went to the file, not stdout


def test_main_cli_degenerate_exit_2(tmp_path):
    """Degenerate labeling is CI-findable: exit 2 + flag in the output."""
    transcript = tmp_path / "w1.txt"
    transcript.write_text(TRANSCRIPT)
    out = tmp_path / "w1.json"
    code = jh_main(["--transcript", str(transcript), "--window-id", "w1",
                    "--out", str(out)],
                   model_factory=lambda name: MockModel('{"labels": []}'))
    assert code == 2
    assert json.loads(out.read_text())["degenerate"] is True


def test_main_cli_malformed_transcript_exit_1(tmp_path):
    transcript = tmp_path / "bad.txt"
    transcript.write_text("not tagged\n")
    code = jh_main(["--transcript", str(transcript), "--window-id", "w1"],
                   model_factory=lambda name: MockModel("{}"))
    assert code == 1


def test_empty_window_not_degenerate():
    """An empty transcript is legitimate — never flagged degenerate (the
    degenerate guard is n_edus > 0)."""
    model = MockModel('{"labels": []}')
    window = label_window([], model, window_id="w0")
    assert window.n_edus == 0
    assert window.labels == []
    assert window.degenerate is False
    assert window.incomplete is False


def test_main_cli_list_models(capsys):
    code = jh_main(["--list-models"])
    assert code == 0
    assert "deepseek-flash" in capsys.readouterr().out


def test_main_cli_unwritable_out_exit_1(tmp_path):
    transcript = tmp_path / "w1.txt"
    transcript.write_text(TRANSCRIPT)
    code = jh_main(["--transcript", str(transcript), "--window-id", "w1",
                    "--out", str(tmp_path / "no-such-dir" / "w1.json")],
                   model_factory=lambda name: MockModel(
                       json.dumps({"labels": labels_for([0, 1, 2])})))
    assert code == 1


def test_apply_tuning_sets_adapter_knobs():
    from tools.judge_harness import _apply_tuning

    class Adapter:
        def __init__(self):
            self.max_tokens = 500  # constructor-default like OpenRouterModel
            self.temperature = 0.0

    model = Adapter()
    _apply_tuning(model, max_tokens=2048, temperature=0.2)
    assert model.max_tokens == 2048
    assert model.temperature == 0.2


def test_apply_tuning_skips_adapters_without_knobs():
    from tools.judge_harness import _apply_tuning

    class MinimalAdapter:
        """An adapter with no tuning attributes must be left untouched."""

    model = MinimalAdapter()
    _apply_tuning(model, max_tokens=2048, temperature=0.2)  # no crash
    assert not hasattr(model, "max_tokens")
    assert not hasattr(model, "temperature")
