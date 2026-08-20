---
title: "M5 — reader pinning: model + prompt constants for the run (epic #1509)"
type: plan
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
aboutSubjects: longmem-eval-reader
aboutObjects: reader-pinning, methodology-provenance
---

<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# M5 — Reader Pinning: Model + Prompt Constants for the Run (#1525)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the LongMemEval reader's model and prompt **pinned constants for the run** — identical across all run cells (50-Q pilot / 500-Q run / 50-Q confirmation) and recorded **verbatim** in the report methodology, so cross-cell and cross-run deltas can never again be confounded by silent reader drift.

**Team:** epistemic-team
**Tier:** micro (`complexity:micro`) — plan doc written at owner request (writing-plans normally posts micro plans as issue comments; the phased run protocol makes a durable plan worthwhile here). 4 tasks → **subagent-driven execution** (≤ 8 tasks).

**Architecture:** No new machinery. Three additive seams on existing surfaces: (1) a single pinned constant `READER_MODEL` in `tools/longmem_eval/reader.py` whose value matches the model the V2 runs actually used (`deepseek-v4-flash`, not the stale `deepseek-chat` default); (2) the reader carries its **resolved identity** (`model_spec`, `provider`, `pinned`) and the report methodology records it **plus the verbatim prompt constants** (`_SYSTEM_PROMPT` + `_TYPE_FRAGMENTS`); (3) a latent endpoint bug is fixed — `build_reader` validates the named provider but routes to the *first configured key*, so the recorded provider can lie about the endpoint. **No hash infrastructure** (owner boundary): the #1414 parity `reader_prompt_hash` input is untouched; the tripwire for prompt/model drift is a test that pins the live constants, not a hash.

### Pattern Research

> **Findings date:** 2026-08-20

> Gate skipped: zero third-party deps in this plan — pure in-repo Python. The reader uses `OpenAICompatModel` + `_PROVIDERS` (the same wiring as session extraction, already integrated); tests use pytest + existing committed fixtures. No library versions, no external API surface beyond already-integrated provider keys. All design decisions verified against in-repo code (see Design decisions, each with file/line refs).

### Integration Surface Map

Micro-tier surface map (test-design #1515, surface 4 — reader LLM — is the M5 owner surface):

| Surface | Test layer | Notes |
|---|---|---|
| Reader LLM external API (S4) | unit + integration | `build_reader` resolution → pinned spec/provider; named-provider endpoint routing; drift warning |
| Report methodology (S4) | unit + integration | additive fields `reader_model_spec` / `reader_provider` / `reader_pinned` / `reader_system_prompt` / `reader_type_fragments`; existing fields unchanged |
| Parity unchanged-check (#1414) | unit guard | `reader_prompt_source()` description + `reader_prompt_hash` input **must not move** — M5 does not change what is hashed |
| CLI/env seams | integration | `TORTOISE_LME_READER_MODEL` / `--reader-model` override → `pinned=false` + stderr warning (never silent) |

**Bug pattern flags:** silent reader-model drift between cells (the v2 confound this issue fixes — `deepseek-chat` default vs `deepseek-v4-flash` actual runs); spec-provider validated but not honored (endpoint selection by first-configured key, `reader.py:build_reader`); prompt drift invisible in report (only a prose description + hash-of-description recorded today, `run.py:reader_prompt_source`).

---

## 1. Problem Statement (verified current state)

The V2 runs were confounded: the reader **model changed between runs** (`deepseek-chat` → `deepseek-v4-flash`). The code default still says `deepseek-chat` — so a default-config run silently uses a different reader than the run cells, and the V3 protocol's cross-cell deltas (pilot → 500 → confirmation) would inherit the same confound.

Verified gaps in `tools/longmem_eval/`:

1. **Model pin gap** — `reader.py:35` `DEFAULT_READER_MODEL = "openrouter:deepseek/deepseek-chat"` ≠ the model runs actually used. A run without `TORTOISE_LME_READER_MODEL` gets a different reader than the run cells.
2. **Provider invisible in methodology** — `report.methodology.reader_model` = bare model id (`deepseek/deepseek-chat`), provider stripped by `_parse_model_spec` (`reader.py`). `build_reader` then picks the endpoint as the **first provider with a key set** (`_resolve_provider` → `_PROVIDER_PRIORITY = ("openrouter", "deepseek", "openai", "gemini")`), and only *validates* the spec's named provider (`reader.py:build_reader`). Same model id can be served by different endpoints depending on which keys are set — the recorded `reader_model` cannot distinguish.
3. **Prompt not recorded** — methodology records `reader_context_format` (prose) and `reader_prompt_hash` (sha16 of the prose *description* in `run.py:reader_prompt_source()`, for the #1414 parity check). The **actual** prompt constants (`_SYSTEM_PROMPT`, `_TYPE_FRAGMENTS`) never appear in the report — prompt drift between cells is invisible.
4. **No tripwire** — nothing asserts that a run's reader matches the pinned constant, or that the methodology carries the exact constants.

Out of scope (owner): no hash/versioning infrastructure; no hard enforcement (record + warn is M5; a hard gate is M7's checkpoint fingerprint); no ontology change; judge pinning is the official model and already correct.

## 2. Design Decisions

### D1 — One pinned constant, matching run reality

Replace `DEFAULT_READER_MODEL` with `READER_MODEL = "openrouter:deepseek/deepseek-v4-flash"` in `tools/longmem_eval/reader.py`. Rationale: (a) it matches what the V2 runs actually used (the confound is closed by making the code default equal the run constant — a default-config run is now a pinned run); (b) `deepseek-v4-flash` is the repo's cheap-tier model (`AGENTS.md` model selection; extractor default `tortoise/sdk.py:13881`); (c) one-line owner-changeable constant. The env/CLI override path stays (legit for experiments) but becomes **loud**: any resolved spec ≠ `READER_MODEL` prints a stderr warning and records `reader_pinned=false`.

- Rename is safe: `DEFAULT_READER_MODEL` is referenced only at `reader.py:35` and `reader.py:198` (plus prose in `reader.py` docstring, `run.py:405` help text, `tools/longmem_eval/README.md:29`).

### D2 — Reader carries its resolved identity; methodology records it

`LLMReader` gains three attributes set by `build_reader`:
- `model_spec` — the full `<provider>:<model>` spec actually used (post env/CLI resolution)
- `provider` — the **resolved endpoint provider name** (the truth about where the call goes)
- `pinned` — `bool` (spec == `READER_MODEL`), computed in `build_reader`

`MockReader` sets `model_spec="mock-reader"`, `provider="mock"`, `pinned=None` (pin N/A for CI smoke — a mock run isn't a model run).

Report methodology gains additive fields (no consumer depends on their absence — verified: `tests/test_longmem_runner.py` accesses methodology by field, never exact-key):
`reader_model_spec`, `reader_provider`, `reader_pinned`. Existing `reader_model` (bare id) stays for backward compat.

### D3 — Actual prompt constants recorded verbatim

`reader.py` exposes `reader_prompt_constants() -> tuple[str, dict[str, str]]` returning `(_SYSTEM_PROMPT, dict(_TYPE_FRAGMENTS))`. `run.py` passes both into `outcomes_to_report` → methodology fields `reader_system_prompt` and `reader_type_fragments`. A dict copy handles future fragment additions (A1 abstention, A2 aggregation — same epic) without shape changes.

**Parity boundary:** `reader_prompt_source()` and the `reader_prompt_hash` input stay exactly as-is (hash of the *description*). Changing what is hashed would move the #1414 battery baseline — explicitly excluded (no hash infra). The verbatim fields make the real prompt auditable in the report; the parity check continues to guard the *description*.

### D4 — Honest endpoint routing (fix the latent bug)

`build_reader` currently validates the spec's named provider but routes to the **first configured key** (`reader.py` `_resolve_provider` + `build_reader`). With both `OPENROUTER_API_KEY` and `DEEPSEEK_API_KEY` set, `openrouter:deepseek/deepseek-v4-flash` is served by openrouter (first in `_SESSION_LLM_PROVIDER_PRIORITY`); with only `DEEPSEEK_API_KEY` set, the *same* spec is served by DeepSeek-direct. A pinned model must pin its endpoint.

Fix: `_resolve_provider(named: str | None)` returns the **named** provider's endpoint when the spec names one (key validated by the caller first, as today), else falls back to first-configured. Small, unit-testable, behavior change only for the multi-key case (where today's behavior is arguably wrong). Deferral option in ⛔ CONDITIONAL GATE.

### D5 — Soft pin (record + warn), not a hard gate

M5 = constants + verbatim recording + loud warning on drift. Rejecting a run whose reader deviates is run-hygiene (M7 checkpoint fingerprint: git_sha + config + prompt) — cross-lane, explicitly not M5. The tripwire is a test that pins the live constants' *identity* (report == constants), not frozen text: A1/A2 legitimately change prompt text later, and those changes show up as reviewable diffs.

## 3. Implementation Steps

### Task 1: Pin the model constant + record resolved identity

**Intent:** Close the v2 confound (code default == run constant) and make the resolved provider/endpoint visible in the report.
**Acceptance:** `build_reader()` with no env/spec returns a reader whose `model_spec == READER_MODEL`, `provider == "openrouter"`, `pinned is True`; an override returns `pinned is False` with a stderr warning; report methodology carries `reader_model_spec`/`reader_provider`/`reader_pinned`.
**Files:**
- Modify: `tools/longmem_eval/reader.py`
- Modify: `tools/longmem_eval/run.py`
- Modify: `tools/longmem_eval/report.py`
- Test: `tests/test_longmem_reader_pinning.py` (new)

**Step 1: Write the failing tests**

```python
# tests/test_longmem_reader_pinning.py
"""M5 reader pinning tests — model + prompt constants for the run (#1525)."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.reader import (  # noqa: E402
    LLMReader, READER_MODEL, build_reader, reader_prompt_constants,
)


def test_reader_model_is_pinned_constant():
    assert READER_MODEL == "openrouter:deepseek/deepseek-v4-flash"


def test_default_build_resolves_to_pin(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    r = build_reader()
    assert isinstance(r, LLMReader)
    assert r.model_spec == READER_MODEL
    assert r.model_id == "deepseek/deepseek-v4-flash"
    assert r.provider == "openrouter"
    assert r.pinned is True


def test_override_is_not_pinned_and_warns(monkeypatch, capsys):
    """The v2 confound made loud: a non-pinned reader warns + records false."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    r = build_reader("openrouter:deepseek/deepseek-chat")
    assert r.pinned is False
    assert "NOT pinned" in capsys.readouterr().err
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_longmem_reader_pinning.py -v`
Expected: FAIL — `READER_MODEL` undefined, `LLMReader` lacks `model_spec/provider/pinned`.

**Step 3: Implement**

`tools/longmem_eval/reader.py`:
- Replace the constant (line 35) and update the module docstring default (line ~12):

```python
# Pinned reader identity for the run (M5 #1525). The V2 runs were confounded
# by reader-model drift between runs (deepseek-chat → deepseek-v4-flash); the
# pin makes the code default equal what runs use. Any override is recorded
# (reader_pinned=false) + warned on stderr — never silent.
READER_MODEL = "openrouter:deepseek/deepseek-v4-flash"
```

- `_resolve_provider` — honor the named provider:

```python
def _resolve_provider(named: str | None = None) -> tuple[str, str, str] | None:
    """Return (provider, base_url, key_env) of the endpoint that will serve
    the reader: the spec's named provider (its key is validated by the
    caller) when named, else the first configured provider in priority
    order. Fixes the M5 endpoint-truth gap: the recorded provider must be
    the endpoint actually used."""
    if named is not None:
        base_url, key_env = _PROVIDERS[named]
        return named, base_url, key_env
    for provider in _PROVIDER_PRIORITY:
        base_url, key_env = _PROVIDERS[provider]
        if os.environ.get(key_env):
            return provider, base_url, key_env
    return None
```

- `LLMReader.__init__` gains identity attrs; `build_reader` sets them and warns on drift:

```python
class LLMReader:
    def __init__(self, model, model_id: str, *, model_spec: str | None = None,
                 provider: str | None = None, pinned: bool | None = None):
        self._model = model
        self.model_id = model_id
        self.model_spec = model_spec or model_id
        self.provider = provider
        self.pinned = pinned
```

```python
    if provider is not None:
        if provider not in _PROVIDERS:
            raise ValueError(...)  # unchanged
        if not os.environ.get(_PROVIDERS[provider][1]):
            raise ValueError(...)  # unchanged
    resolved = _resolve_provider(named=provider)
    if resolved is None:
        raise RuntimeError(...)  # unchanged
    provider_name, base_url, key_env = resolved
    if raw_spec != READER_MODEL:
        print(f"[longmem_eval] WARNING: reader model spec {raw_spec!r} != "
              f"pinned READER_MODEL {READER_MODEL!r} — run is NOT pinned "
              f"(M5 #1525); report records reader_pinned=false",
              file=sys.stderr)
    from tortoise.models import OpenAICompatModel
    model = OpenAICompatModel(
        id=model_id, base_url=base_url, api_key_env=key_env,
        response_format=None, max_tokens=DEFAULT_READER_MAX_TOKENS,
    )
    return LLMReader(model, model_id=model_id, model_spec=raw_spec,
                     provider=provider_name,
                     pinned=(raw_spec == READER_MODEL))
```

- `MockReader`: add `model_spec = "mock-reader"`, `provider = "mock"`, `pinned = None` class attributes.
- Add the public accessor:

```python
def reader_prompt_constants() -> tuple[str, dict[str, str]]:
    """The run's reader prompt constants (M5): the generic system prompt +
    type fragments, recorded verbatim in report methodology so prompt drift
    across run cells is visible in the report."""
    return _SYSTEM_PROMPT, dict(_TYPE_FRAGMENTS)
```

- Update the `Reader` protocol with the three optional identity attributes (`model_spec: str | None = None`, `provider: str | None = None`, `pinned: bool | None = None`).

`tools/longmem_eval/report.py` — `build_report` gains params (defaults keep all callers working):

```python
    reader_model_spec: str = "",
    reader_provider: str | None = None,
    reader_pinned: bool | None = None,
    reader_system_prompt: str = "",
    reader_type_fragments: dict[str, str] | None = None,
```

…and in the `methodology` dict (after `reader_model`):

```python
            "reader_model_spec": reader_model_spec,
            "reader_provider": reader_provider,
            "reader_pinned": reader_pinned,
            "reader_system_prompt": reader_system_prompt,
            "reader_type_fragments": reader_type_fragments or {},
```

`tools/longmem_eval/run.py` — `outcomes_to_report` signature gains the same kwargs; `run_evaluation`'s call forwards:

```python
    outcomes_to_report(
        outcomes,
        reader_model=reader.model_id,
        reader_model_spec=getattr(reader, "model_spec", ""),
        reader_provider=getattr(reader, "provider", None),
        reader_pinned=getattr(reader, "pinned", None),
        reader_system_prompt=system_prompt,
        reader_type_fragments=type_fragments,
        ...)
```

where `system_prompt, type_fragments = reader_prompt_constants()` is imported in `run_evaluation` (`from .reader import reader_prompt_constants`).

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_longmem_reader_pinning.py tests/test_longmem_runner.py tests/test_longmem_reader_prompting.py -v`
Expected: PASS (new) + PASS (no regression — existing methodology assertions like `reader_model == "mock-reader"` untouched).

### Task 2: Record the prompt constants verbatim in methodology

**Intent:** The report must carry the actual prompt, not a prose description, so cross-cell prompt drift is auditable (M7 self-explanatory report).
**Acceptance:** A mock `run_evaluation` report's methodology has `reader_system_prompt == _SYSTEM_PROMPT` and `reader_type_fragments == _TYPE_FRAGMENTS`; `reader_prompt_source()`/`reader_prompt_hash` unchanged.
**Files:**
- Modify: `tools/longmem_eval/run.py`
- Test: `tests/test_longmem_reader_pinning.py`

**Step 1: Write the failing test**

```python
def test_report_methodology_records_prompt_constants(tmp_path):
    import json
    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import MockReader, _SYSTEM_PROMPT, _TYPE_FRAGMENTS
    from tools.longmem_eval.run import run_evaluation
    MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"
    instances = json.loads(MINI.read_text(encoding="utf-8"))
    outcomes, report = run_evaluation(
        instances, reader=MockReader(), judge=MockJudge(),
        ks=(5, 10, 20), top_k=20, split="s", work_dir=str(tmp_path))
    m = report["methodology"]
    assert m["reader_model"] == "mock-reader"
    assert m["reader_model_spec"] == "mock-reader"
    assert m["reader_provider"] == "mock"
    assert m["reader_pinned"] is None          # mock: pin N/A
    assert m["reader_system_prompt"] == _SYSTEM_PROMPT
    assert m["reader_type_fragments"] == _TYPE_FRAGMENTS
    # parity unchanged-check input must not move (no-hash-infra boundary)
    assert m["reader_prompt_hash"]  # still present
```

**Step 2: Run to verify it fails** — `uv run pytest tests/test_longmem_reader_pinning.py::test_report_methodology_records_prompt_constants -v`
Expected: FAIL — `reader_system_prompt`/`reader_type_fragments` missing from methodology.

**Step 3: Implement** — the Task 1 `run.py`/`report.py` changes already define the plumbing; wire the constants in `run_evaluation`'s call to `outcomes_to_report` (import `reader_prompt_constants` from `.reader`, unpack once before the loop). Update `tools/longmem_eval/README.md` methodology description (~line 88) to mention the verbatim prompt fields.

**Step 4: Run to verify it passes** — same command as Step 2.
Expected: PASS.

### Task 3: Named-provider endpoint routing + drift guard tests

**Intent:** Make the recorded provider truthful (D4) and lock the multi-key routing behavior.
**Acceptance:** With both OPENROUTER + DEEPSEEK keys set: `build_reader("deepseek:...")` → `provider == "deepseek"`; bare spec → `provider == "openrouter"` (priority order); existing no-key RuntimeError and unknown-provider ValueError behavior unchanged.
**Files:**
- Modify: `tools/longmem_eval/reader.py` (done in Task 1)
- Test: `tests/test_longmem_reader_pinning.py`

**Step 1: Write the failing tests**

```python
def test_named_provider_routes_endpoint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    r = build_reader("deepseek:deepseek/deepseek-v4-flash")
    assert r.provider == "deepseek"          # spec is authoritative
    r2 = build_reader("deepseek/deepseek-v4-flash")  # bare spec
    assert r2.provider == "openrouter"       # first configured in priority
    assert r2.pinned is False                # bare spec != pinned spec


def test_named_provider_missing_key_still_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    with pytest.raises(ValueError):
        build_reader("deepseek:deepseek/deepseek-v4-flash")


def test_no_keys_still_fail_closed(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
              "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError):
        build_reader()
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_longmem_reader_pinning.py -v` (first two fail pre-Task-1; third is the existing behavior regression guard).

**Step 3: Implement** — already landed in Task 1 (`_resolve_provider(named=...)`). Nothing new to write; run the tests.

**Step 4: Run to verify they pass** — `uv run pytest tests/test_longmem_reader_pinning.py -v`
Expected: PASS.

### Task 4: Cross-cell pin verification + docs

**Intent:** Prove the pin is stable across run cells and document the run-time checks for the phased protocol.
**Acceptance:** Two identical mock runs produce identical reader methodology; a drift report records the divergent spec with `pinned=false`; README/CLI help describe the pin and the run-time verification.
**Files:**
- Modify: `tools/longmem_eval/run.py` (help text, line ~405)
- Modify: `tools/longmem_eval/README.md` (defaults table + methodology notes)
- Test: `tests/test_longmem_reader_pinning.py`

**Step 1: Write the failing test**

```python
def test_cross_cell_reader_pin_is_stable(tmp_path):
    """Two runs with the same reader produce identical reader methodology;
    a divergent reader is recorded with its real spec — drift visible, never
    silent (the run protocol's cross-cell comparability contract)."""
    import json
    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import MockReader
    from tools.longmem_eval.run import outcomes_to_report, run_evaluation
    MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"
    instances = json.loads(MINI.read_text(encoding="utf-8"))

    def _run():
        outcomes, report = run_evaluation(
            instances, reader=MockReader(), judge=MockJudge(),
            ks=(5,), top_k=20, split="s", work_dir=str(tmp_path))
        m = report["methodology"]
        return (m["reader_model_spec"], m["reader_provider"],
                m["reader_pinned"], m["reader_system_prompt"])

    cell_a, cell_b = _run(), _run()
    assert cell_a == cell_b                       # pinned across cells

    drift = outcomes_to_report(
        [], reader_model="deepseek/deepseek-chat",
        reader_model_spec="openrouter:deepseek/deepseek-chat",
        reader_provider="openrouter", reader_pinned=False,
        judge_model="mock-judge", ks=(5,), top_k=20, split="s")
    m = drift["methodology"]
    assert m["reader_model_spec"] == "openrouter:deepseek/deepseek-chat"
    assert m["reader_pinned"] is False            # drift recorded, not hidden
```

**Step 2: Run to verify it fails** — `uv run pytest tests/test_longmem_reader_pinning.py::test_cross_cell_reader_pin_is_stable -v`
Expected: FAIL — `outcomes_to_report` lacks the new kwargs.

**Step 3: Implement**
- `run.py` `--reader-model` help text (line ~405): default mention → `openrouter:deepseek/deepseek-v4-flash (pinned — M5 #1525; override records reader_pinned=false + warns)`.
- `tools/longmem_eval/README.md` line 29 default → `openrouter:deepseek/deepseek-v4-flash`; add a short "Reader pinning (M5)" note: constants live in `reader.py` (`READER_MODEL`, `_SYSTEM_PROMPT`, `_TYPE_FRAGMENTS`); run cells must not override `TORTOISE_LME_READER_MODEL`; verify after each cell: `jq '.methodology | {reader_model_spec, reader_pinned, reader_system_prompt, reader_type_fragments}' <report>` shows `reader_pinned: true` and identical values across the three cell reports.

**Step 4: Run to verify it passes** — `uv run pytest tests/ -v -k "longmem"` (full longmem suite: runner + prompting + pinning).
Expected: PASS. Run once more with `-m "not slow"` if Docker is absent — longmem tests are offline (embedded FalkorDBLite + mocks).

## 4. Tests

| Test | Layer | Assertion |
|---|---|---|
| `test_reader_model_is_pinned_constant` | unit | `READER_MODEL == "openrouter:deepseek/deepseek-v4-flash"` |
| `test_default_build_resolves_to_pin` | unit | default build → pinned spec/provider/model |
| `test_override_is_not_pinned_and_warns` | unit | drift → `pinned=False` + stderr warning |
| `test_named_provider_routes_endpoint` | unit | multi-key: named provider authoritative; bare → priority |
| `test_named_provider_missing_key_still_raises` | unit | existing ValueError behavior locked |
| `test_no_keys_still_fail_closed` | unit | existing RuntimeError behavior locked |
| `test_report_methodology_records_prompt_constants` | integration | mock run report carries verbatim `_SYSTEM_PROMPT` + `_TYPE_FRAGMENTS` |
| `test_cross_cell_reader_pin_is_stable` | integration | cell A == cell B; drift recorded with real spec |

**Run commands:**
```bash
uv run pytest tests/test_longmem_reader_pinning.py -v
uv run pytest tests/test_longmem_runner.py tests/test_longmem_reader_prompting.py -v   # no-regression
uv run pytest tests/ -v -k longmem                                                     # full longmem gate
```

## 5. Cross-Lane Interfaces

- **A1 / A2 (same epic)** — will append to the prompt constants (abstention clause, aggregation/supersede instructions). M5's tests reference the **live** constants, so those changes land cleanly; the methodology's verbatim `reader_type_fragments` dict grows without shape change, and every run report records exactly which prompt text produced its numbers.
- **E2E-7 (reader pinned)** — M5's contribution is the pinned-constant guarantee + the report carrying model+prompt. The `_abs` never crosses assertion (reader call site receives `question_type` only) is A1's, already covered by `tests/test_longmem_reader_prompting.py::test_question_type_reaches_reader`.
- **E2E-2 (run integrity)** — methodology is part of the integrity block's provenance; pinned constants make `reader_model_spec`/`reader_system_prompt` part of every report.
- **M7 run hygiene** — checkpoint fingerprint (git_sha + config + prompt) consumes M5's constants as input; M5 deliberately does **not** build the fingerprint (no-hash-infra owner boundary).
- **M8 statistical discipline / shared-qid deltas** — cross-cell deltas are attributable only because the reader is pinned; the run protocol's step-7 confirmation delta reads cleanly.
- **Parity #1414 unchanged-check** — `reader_prompt_source()` + `reader_prompt_hash` input untouched; the battery baseline does not move.
- **M2 judge pinning** — out of scope; judge is the official gpt-4o model and already pinned in `judge.py`.

## 6. ⛔ CONDITIONAL GATE

No ontology change (no new kinds/edges — epic scope explicitly excludes ontology changes) and no architecture change (additive constants + methodology fields + one in-module routing fix). **New-field need flagged:** the report methodology gains 5 additive fields (`reader_model_spec`, `reader_provider`, `reader_pinned`, `reader_system_prompt`, `reader_type_fragments`). This is a JSON report-shape change only — no DB, no migration, no schema; verified no test asserts exact methodology key-sets (access-by-field only). **No gate blocks default execution.**

Owner decisions that WOULD activate a gate:

1. **Hard fail on drift** — if the owner wants a non-pinned reader to *abort* the run instead of record+warn: this crosses into M7 run-hygiene (checkpoint fingerprint + pre-flight). Requires M7 coordination and a decision on where the rejection lives (build_reader vs pre-flight). Default: soft (record + warn).
2. **Parity hash over the actual prompt** — if the owner wants `reader_prompt_hash` to hash `_SYSTEM_PROMPT` + `_TYPE_FRAGMENTS` instead of the description: changes the #1414 battery baseline + the unchanged-check — separate issue, outside M5's no-hash-infra boundary.
3. **Endpoint-routing fix deferred (D4)** — if the D4 behavior change is not wanted in M5, the plan still records `reader_provider` (truthful after-the-fact). Deferral is acceptable; the field makes the drift visible either way.

## 7. Open Questions

1. **Pinned model value** — plan pins `openrouter:deepseek/deepseek-v4-flash` (matches V2 run reality + repo cheap tier). Confirm this is the model the V3 run cells will use; it is a one-line change in Task 1.
2. **Provider route for the pin** — when both OPENROUTER and DEEPSEEK keys are set, which endpoint should the pinned spec hit? Plan: the named provider is authoritative (D4). Confirm openrouter is the intended V3 route.
3. **Mock-run semantics** — plan records `reader_pinned: None` (N/A) for mock runs. Acceptable, or should mock record `true`?
4. **Constant naming** — plan renames `DEFAULT_READER_MODEL` → `READER_MODEL` (2 internal refs + docstrings). Prefer keeping the old name as an alias?

## 8. Run Notes (phased protocol)

For steps 3/5/7 of the run protocol (pilot → 500 → confirmation): run **without** `TORTOISE_LME_READER_MODEL` (or set it exactly to `READER_MODEL`); after each cell, verify `jq '.methodology.reader_pinned'` is `true` and the three cells' `reader_model_spec` + `reader_system_prompt` are byte-identical — that is the M5 cross-cell pin assertion in the live run.
