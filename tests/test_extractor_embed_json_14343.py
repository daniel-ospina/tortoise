"""#14343 — S4's embed-list serialization: the default bytes, and the lever.

THE MEASUREMENT THIS PINS
-------------------------
`#14343` decomposed the extractor's 45,503-token input constant and found its
largest single region is **S2's embed list, re-sent to S4** — 14,164 of 45,503
tokens (32.5%). Measured on the 14 real S2 emissions in
`tests/eval/write_path/receipts/7947-arms-2026-10-18` (580 items):

    `indent=1`   77,561 tokens
    compact      52,346 tokens
    saved        25,215 tokens (32.5%), 1,801 per emission

So a third of that region is whitespace: `indent=1` writes a newline + a space
per key and per list element. The list's CONTENT is identical either way.

WHAT IS PINNED HERE
-------------------
1. **The default is today's bytes.** An unset flag, a blank value and garbage
   all render the SAME prompt as the pre-#14343 call (`indent=1`), byte for
   byte. A typo must never silently move a production prompt.
2. **`TORTOISE_S4_EMBED_JSON=compact` changes ONLY the layout.** The two
   renders carry the same parsed embed list, the compact one is strictly
   smaller, and nothing else in the prompt moves.
3. **The saving is real and the flag is honoured at CALL time**, so the arming
   is a per-run decision (the same contract `TORTOISE_S4_DELTA` has) and the
   quality run that proves `#7949` still holds is one env var away.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tortoise import extractor_v2 as v2  # noqa: E402

_TOGGLE = "TORTOISE_S4_EMBED_JSON"


@pytest.fixture(autouse=True)
def _clean_toggle(monkeypatch):
    monkeypatch.delenv(_TOGGLE, raising=False)


def _render(embed_list: dict) -> str:
    return v2.render_s4_prompt("the story", {"results": []}, embed_list,
                               v2.build_master_list(),
                               session_date="2026-11-21T00:00:00+00:00")


def _embed_list(n: int = 12) -> dict:
    return {
        "entities": [{"name": f"entity-{i}", "kind": "core:Project",
                      "lifecycle": "created"} for i in range(n)],
        "points": [{"content": f"claim number {i} with some words in it",
                    "pointKind": "statement",
                    "about_entities": [f"entity-{i}"],
                    "when": "2026-11-21"} for i in range(n)],
        "operators": [{"type": "IMPL", "from": f"claim number {i} with some "
                                              f"words in it",
                       "to": f"claim number {i} with some words in it"}
                      for i in range(n // 2)],
    }


def test_the_default_is_the_pre_change_bytes(monkeypatch):
    """Unset == blank == garbage == today's `indent=1` render."""
    default = _render(_embed_list())
    assert json.dumps({"a": [1]}, indent=1) in default or '"entities"' in default
    monkeypatch.setenv(_TOGGLE, "")
    assert _render(_embed_list()) == default
    monkeypatch.setenv(_TOGGLE, "not-a-mode")
    assert _render(_embed_list()) == default, (
        "an unrecognised value must fall back to the shipped default, never "
        "coerce — a typo would otherwise move every production S4 prompt")
    monkeypatch.setenv(_TOGGLE, "INDENT")
    assert _render(_embed_list()) == default


def test_compact_changes_only_the_layout(monkeypatch):
    embed = _embed_list()
    indented = _render(embed)
    monkeypatch.setenv(_TOGGLE, "compact")
    compact = _render(embed)
    assert compact != indented
    assert len(compact) < len(indented), (
        "compact separators must be SMALLER — the whole point is the "
        "whitespace `indent=1` writes")
    # Both renders carry the SAME data: each is literally its own
    # serialization of the same dict, so a "smaller" prompt that lost an item
    # cannot pass here.
    assert json.dumps(embed, indent=1) in indented
    assert json.dumps(embed, separators=(",", ":")) in compact
    # …and nothing but that region moved: removing the serialization from each
    # render leaves the same prompt body.
    assert indented.replace(json.dumps(embed, indent=1), "@@LIST@@") == \
        compact.replace(json.dumps(embed, separators=(",", ":")), "@@LIST@@")


def test_the_layout_flag_is_read_at_call_time(monkeypatch):
    """One value per render, never a module-import-time constant."""
    indented = _render(_embed_list())
    monkeypatch.setenv(_TOGGLE, "compact")
    compact = _render(_embed_list())
    monkeypatch.delenv(_TOGGLE)
    assert _render(_embed_list()) == indented
    assert compact != indented


def test_the_kwargs_helper_is_the_single_source_of_the_shape():
    assert v2._embed_json_kwargs() == {"indent": 1}
    os_value = "compact"
    import os as _os
    saved = _os.environ.get(_TOGGLE)
    try:
        _os.environ[_TOGGLE] = os_value
        assert v2._embed_json_kwargs() == {"separators": (",", ":")}
    finally:
        if saved is None:
            _os.environ.pop(_TOGGLE, None)
        else:
            _os.environ[_TOGGLE] = saved
    assert json.dumps({"a": 1}, **{"indent": 1}) == '{\n "a": 1\n}'
