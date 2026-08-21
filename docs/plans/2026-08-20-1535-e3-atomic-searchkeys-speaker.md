<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# E3 — Atomic Points + search_keys + Speaker via Source-Turn Link (no `source_role`) — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make extracted Points atomic (single-claim) and findable (2–4 `search_keys` + verbatim `quote`), carry a `source_turn_id` reference, and attribute speaker **at read time** from the source turn's existing `speaker`/`[role]` — with **no new `source_role` property** (epic #1509 E3; issue #1535).

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Three changes ride the existing Layer-1 pipeline with **zero new kinds and zero new edges** (ontology invariant, owner-approved). (1) **Extractor** (`tortoise/extractor_v2.py`): the S2/S4 `OUTPUT_CONTRACT` gains additive point keys `quote`/`search_keys`/`source_turn_id`, a turn-indexed SOURCE TRANSCRIPT is injected into the S2/S4 prompts (capped), an atomicity + user-vs-assistant rule is added to both prompts, and a **deterministic quote→turn resolver** in `execute_embed` computes the authoritative `source_turn_id` (LLM output is advisory; the verbatim quote is the anchor — never-guess discipline). (2) **Schema/write** (`tortoise/commit_schema.py` + `tortoise/hosted_api.py` + `tools/longmem_eval/ingest_v2.py`): `Point` gains the two additive fields under `extra="forbid"`, `canonical_payload` folds them into the commit id **only when present** (the #1350 additive-parity pattern — old clients' commit ids stay byte-identical), and both graph write surfaces pass them through. (3) **Read** (`tools/longmem_eval/retrieve.py`): hits annotate `speaker` derived from `source_turn_id` → the turn point's existing `speaker` property, and `render_context` renders `[speaker]` (mirrors the deterministic leg's `[role] text`). Speaker is **never** written on extracted points.

### Pattern Research

> **Findings date:** 2026-08-20
> Gate skipped: the plan touches **zero third-party dependencies** — pure in-repo Python (`extractor_v2.py`, `commit_schema.py`, `hosted_api.py`, `tools/longmem_eval/*`) over the in-repo SDK + FalkorDB queries. No library docs preflight, no Perplexity gate (writing-plans workflow/02 skip rule).

**Prior research consumed (no re-query):**
- Epic `03-scope.md` E3 ("atomic points + speaker attribution — existing subject mechanism: `quote`/offsets → source-turn role; `aboutSubject`) + `search_keys`"), Data Model §4 rows ("Point carries `source_turn_id`; **no new `source_role` property** — speaker DERIVED at read time from the source-turn link; `quote` already in commit_schema"), Interfaces §6 S2/S4 OUTPUT_CONTRACT row.
- Epic `04-plan.md` §4 Data Model + §6 Interfaces (the review-gate patch of 2026-08-20 removed the stale `source_role` from §6 — implementer must NOT reintroduce it; issue #1535 review-gate note).
- Issue #1535 verification checklist (S15 → unit+integration: single-fact granularity; search_keys 2–4 aliases + verbatim tokens; point carries `source_turn_id`; speaker derived from the turn's `speaker`/`[role]` at read time) and MECE fix note (E2E-5's evidence-marked assertion is CONDITIONAL on M6 recalibrated marks).
- Codebase: `tortoise/extractor_v2.py` (S2/S4 prompts, `OUTPUT_CONTRACT` @350, `execute_embed` @1142, `extract_session_v2` @1467), `tortoise/commit_schema.py` (`Point.quote` @259, `canonical_payload` @829), `tortoise/hosted_api.py` (`_execute_commit_writes` @3727, capture turn ids `{session_id}_t{i}`), `tortoise/sdk.py` (`create_point` **props** passthrough @1304; SDK turn points write `speaker`), `tools/longmem_eval/ingest.py` (turn points `lme:{qid}:s{si}:t{ti}` with `speaker=str(role)`), `tools/longmem_eval/ingest_v2.py`, `tools/longmem_eval/retrieve.py`.

### Integration Surface Map

Boundaries this issue crosses (from the epic's test-design surface map #1515; only E3-owned surfaces listed):

| # | Surface | Change | Test layer | Bug-pattern flags |
|---|---|---|---|---|
| S15 | Pipeline state (S2/S4/S5) | OUTPUT_CONTRACT + prompts + deterministic resolver | unit (`tests/test_extractor_v2.py`) | LLM-emitted indices wrong → resolver must win (never trust the model index) |
| S22 | Layer-1 payload (Point schema) | additive `search_keys`/`source_turn_id` under `extra="forbid"` | unit (`tests/test_commit_schema.py`) | forgetting schema fields → 422 on every commit (extra="forbid"); `source_role` must 422 |
| S24 | `client_commit_id` | `canonical_payload` folds new fields only when present | unit (`tests/test_commit_schema.py`) | unconditional fold changes old clients' commit ids → replay/parity break (P4) |
| S12 | Graph writes (hosted commit) | `create_point` passthrough in `_execute_commit_writes` step 5 | integration (`tests/test_commit_endpoint.py`) | both supersede + plain branches must pass the fields |
| S27 | Dataset fixture (eval ingest) | turn points written in v2 mode + `source_turn_id` index→node-id resolution | integration (`tests/test_longmem_runner.py`) | v2-only runs have NO turn points today → speaker derivation would have nothing to resolve |
| S25 | Reader context format | `[speaker]` decoration at read time | unit (`tests/test_longmem_runner.py`) | no link → byte-identical render (backward-compat) |

### Verification Plan (test-routing)

- **Unit:** extractor contract/resolver + schema/canonical (above).
- **Integration:** hosted commit write + eval v2 ingest + retrieval annotation.
- **E2E:** full E2E-5 answer-level assertions are **conditional** on M6 marks + A1/A2 reader instructions (separate issues) — this issue owns the S15 surfaces E2E-5 builds on; see Conditional-gate notes.
- **Skipped:** no UX/accessibility surfaces (eval tooling + SDK, no user-facing UI — epic UX rating medium applies to reader context, covered by S25 unit tests here, A1/A2 owns the reader wording).

---

## 1. Design Decisions

### D1. Atomicity is prompt-enforced + warn-guarded (never hard-blocked)
The S2/S4 prompt gains an **ATOMIC POINTS** rule: one claim per point; compound statements split ("we moved to X and dropped Y" → two points); the claim's **value survives verbatim** — never replace "27:12" with "the value" (this is the E2 value-filter carve-out language, restated for E3's `quote`). A deterministic **soft guard** in `execute_embed` warns (only) when a point's content contains 2+ sentences (reuses `_split_sentences`), mirroring the chain warn+repair discipline. E2E-5's "verbatim value (27:12) retrievable" is the observable test — atomicity itself is semantic and can't be hard-checked deterministically.

### D2. OUTPUT_CONTRACT: three additive point keys — `quote`, `search_keys`, `source_turn_id`. NO `source_role`.
Points in the S2/S4 contract gain (all optional, all additive):
- `quote` — verbatim conversation text the claim came from (≤ 200 chars, matches the existing `Point.quote` schema cap).
- `search_keys` — 2–4 alias strings: paraphrases/synonyms a questioner might use + the verbatim value tokens ("27:12", "five-K time").
- `source_turn_id` — the `{index}:` turn marker from the SOURCE TRANSCRIPT that asserted the claim (0-based int).

The contract's inline comment states: **speaker is NOT a point property — derived at read time from the source turn's existing `speaker`/`[role]`** (the review-gate fix, verbatim intent).

### D3. Turn-indexed SOURCE TRANSCRIPT injected into S2/S4 (capped)
The S1 story is a compiled narrative — turn indices don't survive it. To let the model pick accurate `source_turn_id`s, `render_s2_prompt`/`render_s4_prompt` gain an optional `edus` kwarg that appends the already-indexed EDU stream (`_edus_to_text` produces `{index}: {role}: {text}`) as a **SOURCE TRANSCRIPT** block, capped at `_SOURCE_TRANSCRIPT_CAP = 8000` chars (~2k tokens) to protect the S2/S4 token budget (M3's bounded `max_tokens` is separate but adjacent). Over cap → block omitted; the deterministic resolver (D4) still works from `quote` alone. This is additive and backward-compatible (`edus=None` → prompt byte-identical).

### D4. Deterministic quote→turn resolution wins (execute_embed)
New helper `_resolve_source_turn(p, edus, *, warnings)` computes the authoritative turn index:
1. **Verbatim anchor:** find the turn whose text contains the normalized `quote` (whitespace-folded substring match).
2. **Model-index validation:** if the model emitted `source_turn_id` and that turn contains the quote → use it. If it disagrees with the deterministic match → **deterministic match wins + warning** (the model index is advisory; this is the never-guess discipline from `_resolve_superseded`).
3. **Quote empty but index present** → use the index if in range (warning: unverified).
4. **Fallback:** token overlap ≥ 0.6 against a single best turn (`_token_overlap` discipline).
5. **No match** → `source_turn_id = None` + warning (fail-open, mirrors supersession's fail-open).

`execute_embed` gains `edus: list[dict] | None = None`; the `pt_entry` replaces the hardcoded `"quote": ""` with the validated `quote`, and adds `search_keys` (cleaned: list, ≤ 4 entries, each 1–60 chars, deduped, non-str dropped w/ warning) and `source_turn_id` (resolved int or None). `extract_session_v2` passes the conversation's `edus` through to S2/S4/S5.

### D5. commit_schema: additive fields + additive canonical parity (#1350 pattern)
`Point` (`extra="forbid"` — new fields are REQUIRED or every commit 422s) gains:
- `search_keys: list[str] = Field(default_factory=list)` — validator: strip, 1–60 chars each, dedup, max 4.
- `source_turn_id: int | None = None` — the 0-based conversation turn index.

`canonical_payload`'s points entry folds both in **only when present** (the exact `supersessions` pattern from #1350: "the additive contract must not change the id of a payload that never had them"). A pre-E3-shaped point renders byte-identically → `client_commit_id` parity preserved (P4). Test locks this.

### D6. Two-level `source_turn_id`: payload int index ↔ graph resolved node id
The payload's `source_turn_id` is the **0-based conversation turn index** (the extractor's world). Each graph write surface resolves it to the session's turn-point id scheme at write time:
- **Hosted commit** (`_execute_commit_writes`): turns are `{session_id}_t{i}` (capture path). The commit path stores the int index as the property (the hosted join is a future read-side surface — see Open questions Q2).
- **Eval v2 ingest** (`_write_payload`): resolves index → `lme:{qid}:s{si}:t{index}` and stores the **node id** string as the property (the eval read path is this issue's derivation surface).

### D7. Read-time speaker derivation (retrieve.py)
- `point_props_for_hits` (ingest.py) extends its RETURN to also fetch `quote`, `search_keys`, `source_turn_id`, `speaker` (one Cypher query — no N+1).
- New `_speaker_for_turns(proj, turn_ids)` in retrieve.py: one `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.speaker,'')`.
- `_annotate_hits` adds `speaker` per hit: a hit with `source_turn_id` (node id) resolves via the batch; a hit that IS a turn point carries its own `speaker` prop. Also passes `quote`/`search_keys` through (R2's future query-expansion consumer).
- `render_context` renders `[speaker]` between the session prefix and content when known — e.g. `[session 0] [user] my personal best 5K time is 27:12` — byte-identical to today when unknown (backward-compat).

### D8. Speaker is derivation-only, and v2 mode must have turn points to derive from
`--ingest-mode v2` runs **only** `ingest_haystack_v2` — turn points are NOT written today, so a `source_turn_id` link would dangle. `ingest_haystack_v2` gains the v1 leg's turn-point loop (same ids `lme:{qid}:s{si}:t{ti}`, same `speaker=str(role)` property) with `has_answer=False` (v2's recall surface is the extracted evidence points — the deterministic turn branch must not double-enter the metric; evidence-point recall stays authoritative in v2 mode). This is the substrate D7 derives from. **No `speaker`/`role` is ever written on extracted points.**

---

## 2. Implementation Steps

### Task 1: OUTPUT_CONTRACT + S2/S4 prompt rules (atomicity, search_keys, quote, source_turn_id; NO source_role)

**Intent:** Give S2/S4 the E3 extraction contract — atomic single-claim points with verbatim `quote`, 2–4 `search_keys`, and a `source_turn_id` reference — while explicitly forbidding any speaker/role on the point (the review-gate fix).
**Acceptance:** `OUTPUT_CONTRACT` has the three new point keys with inline no-`source_role` guidance; S2_TMPL and S4_TMPL contain the ATOMIC POINTS + USER-VS-ASSISTANT rules; a test asserts `source_role` appears nowhere in the prompt module.
**Files:**
- Modify: `tortoise/extractor_v2.py:350` (OUTPUT_CONTRACT), `tortoise/extractor_v2.py:362` (S2_TMPL rules block), `tortoise/extractor_v2.py:667` (S4_TMPL rules block)
- Test: `tests/test_extractor_v2.py` (new `TestE3Contract`)

**Step 1 — Write the failing tests** (`tests/test_extractor_v2.py`):

```python
class TestE3Contract:
    def test_output_contract_has_e3_keys(self):
        for key in ("quote", "search_keys", "source_turn_id"):
            assert key in v2.OUTPUT_CONTRACT, f"contract missing {key}"
        # the contract's points block must carry all three
        pts_block = v2.OUTPUT_CONTRACT.split('"points":', 1)[1]
        pts_block = pts_block.split("]", 1)[0]
        assert "quote" in pts_block and "search_keys" in pts_block
        assert "source_turn_id" in pts_block

    def test_source_role_is_never_emitted(self):
        # review-gate fix (2026-08-20): plan docs patched to remove source_role
        for src in (v2.OUTPUT_CONTRACT, v2.S2_TMPL, v2.S4_TMPL):
            assert "source_role" not in src

    def test_atomicity_and_verbatim_value_rules_present(self):
        for tmpl in (v2.S2_TMPL, v2.S4_TMPL):
            assert "ATOMIC POINTS" in tmpl or "one claim per point" in tmpl
            assert "verbatim" in tmpl and "quote" in tmpl
            assert "USER VS ASSISTANT" in tmpl or "not a user fact" in tmpl
```

**Step 2 — Run to verify they fail:** `uv run pytest tests/test_extractor_v2.py::TestE3Contract -v` → FAIL (keys/rule text absent).

**Step 3 — Patch OUTPUT_CONTRACT** (`tortoise/extractor_v2.py:350`):

```python
  "points": [{"content": str, "pointKind": "statement", "about_entities": [str],
              "slots": {"subject": [...], "object": [...], "event": [...]},
              "quote": str|null,          # verbatim source text, <=200 chars (E3)
              "search_keys": [str, ...],  # 2-4 aliases + verbatim value tokens (E3)
              "source_turn_id": int|null}],  # {index}: turn in the SOURCE TRANSCRIPT (E3)
```

**Step 4 — Patch S2_TMPL and S4_TMPL** — insert the same rule block into both (after the VALUE FILTER block in S2, after the PARTICIPANT SLOTS block in S4):

```
- ATOMIC POINTS (E3): emit ONE claim per point. Split compound statements
  into separate points. The claim's VALUE survives verbatim — never compress
  a concrete value ("27:12", "6pm") into a label ("the value"); the verbatim
  value must be findable in the content or the point's `quote`.
- SOURCE ATTRIBUTION (E3): for every point emit `quote` = the EXACT
  conversation text the claim came from (verbatim, <=200 chars) and
  `source_turn_id` = the {index}: marker from the SOURCE TRANSCRIPT that
  asserted it. NEVER emit a speaker/role on the point — speaker is derived
  at read time from the source turn's existing speaker/[role].
- SEARCH KEYS (E3): emit 2-4 `search_keys` — paraphrases/synonyms a
  questioner might use, plus the verbatim value tokens ("27:12",
  "five-K time"). The fact is findable when asked with different words.
- USER VS ASSISTANT (E3): an assistant suggestion/proposal is NOT a user
  fact — do not emit it as a statement point unless the user confirmed it.
```

**Step 5 — Run to verify pass:** `uv run pytest tests/test_extractor_v2.py::TestE3Contract -v` → PASS.

**Step 6 — Commit** (via `commit-workflow` skill): `git add tortoise/extractor_v2.py tests/test_extractor_v2.py && git commit -m "feat(extractor): E3 contract — atomic points, quote/search_keys/source_turn_id, no source_role"`

---

### Task 2: Turn-indexed SOURCE TRANSCRIPT injection (S2/S4 edus kwarg)

**Intent:** Give the model a turn-indexed source to cite `source_turn_id` from (D3); without it the model's indices would be guesses.
**Acceptance:** `render_s2_prompt`/`render_s4_prompt` with `edus` append a `SOURCE TRANSCRIPT (turn-indexed)` block with `{index}: {role}: {text}` lines; over the 8000-char cap the block is omitted; `edus=None` renders byte-identically to today; `extract_session_v2` passes the conversation's EDUs through.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`_SOURCE_TRANSCRIPT_CAP` const near OUTPUT_CONTRACT; `render_s2_prompt` @473, `render_s4_prompt` @735; `run_s2` @481, `run_s4` @747; `extract_session_v2` @1467)
- Test: `tests/test_extractor_v2.py` (new `TestE3SourceTranscript`)

**Step 1 — Write the failing tests:**

```python
class TestE3SourceTranscript:
    def _edus(self):
        return [{"index": 0, "role": "user", "text": "my 5K best is 27:12"},
                {"index": 1, "role": "assistant", "text": "nice time"}]

    def test_transcript_injected_when_edus_present(self):
        p = v2.render_s2_prompt(edus=self._edus())
        assert "SOURCE TRANSCRIPT" in p
        assert "0: user: my 5K best is 27:12" in p
        assert "1: assistant: nice time" in p

    def test_s4_transcript_injected(self):
        p = v2.render_s4_prompt("story", {}, {}, edus=self._edus())
        assert "0: user:" in p

    def test_none_edus_renders_identical(self):
        base = v2.render_s2_prompt()
        assert "SOURCE TRANSCRIPT" not in base

    def test_cap_omits_block(self, monkeypatch):
        monkeypatch.setattr(v2, "_SOURCE_TRANSCRIPT_CAP", 10)
        assert "SOURCE TRANSCRIPT" not in v2.render_s2_prompt(edus=self._edus())
```

**Step 2 — Run to verify fail:** `uv run pytest tests/test_extractor_v2.py::TestE3SourceTranscript -v`.

**Step 3 — Implement:**

```python
_SOURCE_TRANSCRIPT_CAP = 8000  # chars — S2/S4 token budget guard (E3 D3)

def _render_source_transcript(edus: list[dict] | None) -> str:
    if not edus:
        return ""
    text = _edus_to_text(edus)
    if len(text) > _SOURCE_TRANSCRIPT_CAP:
        return ""  # over budget — quote-only resolution still works (D4)
    return ("SOURCE TRANSCRIPT (turn-indexed — cite source_turn_id from "
            "this; the numbers are the {index}: markers):\n" + text)
```

- `render_s2_prompt(master=None, *, edus=None)` and `render_s4_prompt(..., *, edus=None)` append `"\n\n" + _render_source_transcript(edus)` to the user message; `run_s2(model, story, master=None, *, edus=None)` / `run_s4(..., *, edus=None)` forward it.
- `extract_session_v2`: `embed_list = run_s2(model, story, master, edus=edus)` and `s4 = run_s4(model, story, search, embed_list, master, edus=edus)` (edus already computed as `_edus_from_conversation(conversation)`).

**Step 4 — Run to verify pass** (same command → PASS). **Step 5 — Commit** via `commit-workflow`.

---

### Task 3: Deterministic quote→turn resolution + execute_embed threading

**Intent:** Compute the authoritative `source_turn_id` deterministically from the verbatim `quote` (D4) and land `quote`/`search_keys`/`source_turn_id` on every payload point — replacing the hardcoded `"quote": ""` @`extractor_v2.py:1321`.
**Acceptance:** `execute_embed(embed_list, search, session_id=..., edus=edus)` emits payload points with validated `quote` (≤200), cleaned `search_keys` (0–4 × 1–60 chars, deduped, non-str dropped with warning), and `source_turn_id` (int|None) resolved quote-first; conflicting model indices warn and lose; no-match → None + warn; atomicity soft-guard warns on multi-sentence content.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`_resolve_source_turn` helper near `_resolve_superseded` @~1050; `_clean_search_keys`; `execute_embed` @1142 points loop @~1310-1325; atomicity warn in the points loop)
- Test: `tests/test_extractor_v2.py` (new `TestE3Resolution`)

**Step 1 — Write the failing tests:**

```python
class TestE3Resolution:
    EDUS = [{"index": 0, "role": "user", "text": "my 5K best is 27:12"},
            {"index": 1, "role": "assistant", "text": "maybe try intervals"}]

    def _embed(self, **point_kwargs):
        p = {"content": "the user's 5K best is 27:12", "pointKind": "statement"}
        p.update(point_kwargs)
        return {"entities": [], "points": [p], "events": [], "operators": []}

    def test_quote_resolves_to_turn(self):
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12"),
                             {}, session_id="s1", edus=self.EDUS)
        pt = r["payload"]["points"][0]
        assert pt["quote"] == "my 5K best is 27:12"
        assert pt["source_turn_id"] == 0
        assert pt["search_keys"] == []

    def test_conflicting_model_index_deterministic_wins(self):
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id=1),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 0
        assert any("source_turn_id" in w for w in r["warnings"])

    def test_no_quote_no_index_is_none(self):
        r = v2.execute_embed(self._embed(), {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] is None

    def test_search_keys_cleaned(self):
        r = v2.execute_embed(
            self._embed(search_keys=["personal best 5K", "27:12", "27:12", ""]),
            {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["search_keys"] == ["personal best 5K", "27:12"]
        assert any("search_keys" in w for w in r["warnings"])

    def test_quote_capped_at_200(self):
        r = v2.execute_embed(self._embed(quote="q" * 250), {}, session_id="s1",
                             edus=self.EDUS)
        assert len(r["payload"]["points"][0]["quote"]) == 200
```

**Step 2 — Run to verify fail.**

**Step 3 — Implement:**

```python
def _clean_search_keys(raw, warnings: list[str], ctx: str) -> list[str]:
    """E3: 2-4 search_key aliases + verbatim tokens — sanitize to a deduped
    list of 1-60-char strings (max 4). LLM output is advisory; malformed
    entries are dropped, not fatal."""
    out: list[str] = []
    if raw is None:
        return out
    if not isinstance(raw, list):
        warnings.append(f"{ctx}: search_keys must be a list — dropped")
        return out
    for k in raw:
        k = str(k).strip()
        if not k:
            continue
        if len(k) > 60:
            warnings.append(f"{ctx}: search_key >60 chars dropped")
            continue
        if k not in out:
            out.append(k)
    if len(out) > 4:
        warnings.append(f"{ctx}: search_keys capped at 4 (had {len(out)})")
        out = out[:4]
    return out


def _resolve_source_turn(p: dict, edus: list[dict] | None,
                         *, warnings: list[str]) -> int | None:
    """E3: the authoritative source-turn index for a point. The verbatim
    quote is the anchor; the model's source_turn_id is advisory (a wrong
    model index must never win — mirror of the never-guess discipline)."""
    if not edus:
        return None
    quote = re.sub(r"\s+", " ", str(p.get("quote") or "").strip().lower())
    model_idx = p.get("source_turn_id")
    def _contains(text: str) -> bool:
        if not quote:
            return False
        return re.sub(r"\s+", " ", str(text).lower()).find(quote) >= 0
    # 1) verbatim anchor — first turn containing the quote
    det_idx = next((e["index"] for e in edus if _contains(e.get("text", ""))), None)
    # 2) model index in range?
    if isinstance(model_idx, int) and 0 <= model_idx < len(edus):
        m_turn = edus[model_idx].get("text", "")
        if det_idx is None and _contains(m_turn):
            return model_idx                      # quote found only there
        if det_idx is not None and det_idx != model_idx:
            warnings.append(f"source_turn_id {model_idx} contradicts the quote's "
                            f"turn {det_idx} — deterministic match wins")
    if det_idx is not None:
        return det_idx
    # 3) no verbatim match → token-overlap fallback (single best >= 0.6)
    best, best_ov = None, 0.0
    for e in edus:
        ov = _token_overlap(str(e.get("text", "")), str(p.get("quote") or ""))
        if ov > best_ov:
            best, best_ov = e["index"], ov
    if best is not None and best_ov >= 0.6:
        return best
    # 4) no anchor at all — fail-open, never guess
    warnings.append(f"point '{str(p.get('content',''))[:40]}' has no resolvable "
                    "source turn (no quote match)")
    return None
```

- In `execute_embed`'s points loop (before building `pt_entry`):
```python
quote = str(p.get("quote") or "").strip()[:200]
search_keys = _clean_search_keys(p.get("search_keys"), warnings,
                                 f"point '{content[:60]}'")
turn_idx = _resolve_source_turn(p, edus, warnings=warnings)
sents = _split_sentences(content)                # D1 atomicity soft guard
if len(sents) > 1:
    warnings.append(f"point '{content[:60]}' has {len(sents)} sentences — "
                    "E3 atomicity expects ONE claim per point")
```
- Replace the hardcoded entry (was `"quote": ""`):
```python
pt_entry = {
    "id": pid, "content": content, "pointKind": pkind,
    "reason": reason, "confidence": 0.5, "c_cal": 0.5,
    "about_entities": [...], "source_ref": "session.md",
    "quote": quote, "status": "draft",
    "search_keys": search_keys,
    "source_turn_id": turn_idx,
}
```
- `execute_embed` signature gains `edus: list[dict] | None = None`; `extract_session_v2` passes `edus=edus` (the `_edus_from_conversation` result — already in scope).

**Step 4 — Run to verify pass.** **Step 5 — Commit** via `commit-workflow`.

---

### Task 4: commit_schema Point fields + additive canonical parity

**Intent:** Make the Layer-1 gate accept (and reject correctly) the E3 fields, and keep `client_commit_id` byte-stable for pre-E3 payloads (D5).
**Acceptance:** `Point` validates `search_keys` (≤4 × 1–60, deduped) and `source_turn_id` (int|None); `search_keys` violations 422; a `source_role` key 422s (extra="forbid" — regression-proofs the review-gate fix); a point WITHOUT the new fields computes an identical `client_commit_id` to today.
**Files:**
- Modify: `tortoise/commit_schema.py` (`Point` @~245-270; `canonical_payload` points entry @~849-859)
- Test: `tests/test_commit_schema.py` (extend `_point` fixture or add tests)

**Step 1 — Write the failing tests** (`tests/test_commit_schema.py` — uses the existing `_raw_payload`/`_check`/`_finalize` fixtures; `_point(i, **overrides)` @49):

```python
def test_e3_fields_validate():
    result, _ = _check(_raw_payload(points=[
        _point(0, quote="my 5K best is 27:12",
               search_keys=["personal best", "27:12"],
               source_turn_id=0)]))
    assert result.ok, result.errors

def test_search_keys_entries_capped_and_trimmed():
    result, _ = _check(_raw_payload(points=[_point(0, search_keys=["x" * 61])]))
    assert not result.ok
    assert result.errors["points[0].search_keys"]

def test_search_keys_max_four():
    result, _ = _check(_raw_payload(
        points=[_point(0, search_keys=["a", "b", "c", "d", "e"])]))
    assert not result.ok
    assert result.errors["points[0].search_keys"]

def test_source_role_extra_field_422():
    # review-gate fix: source_role must stay rejected — speaker is derived
    result, _ = _check(_raw_payload(points=[_point(0, source_role="user")]))
    assert not result.ok
    assert "source_role" in str(result.errors)

def test_e3_fields_do_not_change_legacy_commit_id():
    # #1350 additive pattern: absent keys keep the legacy id byte-identical;
    # present keys fold in and change it (that's the intended parity boundary)
    legacy = _finalize(_raw_payload(points=[_point(0)]))
    with_e3 = _finalize(_raw_payload(points=[_point(0)]))  # E3 keys default-absent
    assert legacy["client_commit_id"] == with_e3["client_commit_id"]
    with_keys = _finalize(_raw_payload(
        points=[_point(0, search_keys=["k1"], source_turn_id=0)]))
    assert with_keys["client_commit_id"] != legacy["client_commit_id"]
```

**Step 2 — Run to verify fail** (`uv run pytest tests/test_commit_schema.py -k "e3 or source_role" -v`).

**Step 3 — Implement** — Point model (after `quote` @259):

```python
    quote: str = Field(default="", max_length=200)
    search_keys: list[str] = Field(default_factory=list)  # E3: 2-4 aliases + verbatim tokens
    source_turn_id: int | None = None                     # E3: 0-based conversation turn index

    @field_validator("search_keys")
    @classmethod
    def _search_keys(cls, v: list[str]) -> list[str]:
        if v is None:
            return []
        out: list[str] = []
        for k in v:
            k = str(k).strip()
            if not k or len(k) > 60:
                raise ValueError("search_keys entries must be 1-60 characters")
            if k not in out:
                out.append(k)
        if len(out) > 4:
            raise ValueError("search_keys allows at most 4 entries")
        return out
```

`canonical_payload` points entry — fold only when present (the #1350 additive pattern; keep `quote` as-is):

```python
        "points": [
            {
                "id": _f(p, "id"),
                "content": _f(p, "content"),
                "pointKind": _f(p, "pointKind"),
                "about_entities": sorted(_f(p, "about_entities", []) or []),
                "source_ref": _f(p, "source_ref"),
                "quote": _f(p, "quote", "") or "",
                **({"search_keys": sorted(_f(p, "search_keys", []) or [])}
                   if _f(p, "search_keys", []) else {}),
                **({"source_turn_id": _f(p, "source_turn_id")}
                   if _f(p, "source_turn_id", None) is not None else {}),
            }
            for p in sorted(points, key=_point_key)
        ],
```

**Step 4 — Run to verify pass.** **Step 5 — Run the full schema suite:** `uv run pytest tests/test_commit_schema.py -v`. **Step 6 — Commit** via `commit-workflow`.

---

### Task 5: Hosted commit write path passes the fields

**Intent:** Extracted points actually land `search_keys`/`source_turn_id`/`quote` on the graph via the commit path (S12).
**Acceptance:** `_execute_commit_writes` step 5 passes the fields in BOTH the `supersede` and plain `create_point` branches; a commit test asserts the properties exist on the written Point node.
**Files:**
- Modify: `tortoise/hosted_api.py` (`_execute_commit_writes` @3727, step 5 `create_point` calls @~3884-3900)
- Test: `tests/test_commit_endpoint.py` (extend `test_commit_writes_four_node_chain` @273 or add a focused test)

**Step 1 — Write the failing test** (`tests/test_commit_endpoint.py`) — extend the four-node-chain test's point assertions:

```python
        # E3: search_keys / source_turn_id / quote persist on the Point node
        props = proj.g.query(
            "MATCH (p:Point {id:$pid}) "
            "RETURN p.quote, p.search_keys, p.source_turn_id",
            params={"pid": <extracted point id>}).result_set
        assert props[0][0] == "my 5K best is 27:12"
        assert props[0][1] == ["personal best", "27:12"]
        assert props[0][2] == 0
```

(Build the committed payload point with `quote`/`search_keys`/`source_turn_id` — the fixture's point dict at `tests/test_commit_endpoint.py:205` gains the keys.)

**Step 2 — Run to verify fail:** `uv run pytest tests/test_commit_endpoint.py::test_commit_writes_four_node_chain -v`.

**Step 3 — Implement** — both `create_point` call sites in step 5:

```python
            sdk.create_point(
                pr.point.pointKind, pr.point.content, dedup=True, id=pid,
                status=pr.point.status, confidence=pr.point.confidence,
                c_cal=pr.point.c_cal, quote=pr.point.quote,
                search_keys=pr.point.search_keys or None,
                source_turn_id=pr.point.source_turn_id,
                source_ref=pr.point.source_ref,
                extractedFrom=pr.point.source_ref, is_episodic=False,
            )
```

(identical in the `elif pr.action == "supersede"` branch). `create_point`'s `**props` passthrough + `_coerce_props`/`_sanitize_props` handle list and int property values (FalkorDB scalar/list property types — the same mechanism `keywords=$kw` uses in the event write).

**Step 4 — Run to verify pass.** **Step 5 — Run the endpoint suite:** `uv run pytest tests/test_commit_endpoint.py -v`. **Step 6 — Commit** via `commit-workflow`.

---

### Task 6: Eval v2 ingest — turn points + source_turn_id resolution

**Intent:** In v2-only eval runs the turn points must exist (D8) and extracted points must resolve `source_turn_id` (index) → turn node id at write time (D6), so the read path can derive speaker.
**Acceptance:** `ingest_haystack_v2` writes turn points `lme:{qid}:s{si}:t{ti}` with `speaker=str(role)` and `has_answer=False`; extracted points store `quote`, `search_keys`, and `source_turn_id` = the resolved turn node id; the existing v2-ingest test still passes with the CONTAINS count unchanged semantics.
**Files:**
- Modify: `tools/longmem_eval/ingest_v2.py` (`_write_payload` @74 points loop; `ingest_haystack_v2` @174 after the Session node write)
- Test: `tests/test_longmem_runner.py` (extend `test_v2_ingest_writes_payload_with_evidence_marks` @~661)

**Step 1 — Write the failing test additions** (and update the now-obsolete count):

```python
        # E3: turn points exist with speaker; extracted point resolves the link
        tr = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.speaker",
            params={"id": "lme:test_v2_q:s0:t0"}).result_set
        assert tr and tr[0][0] == "user"
        pt_props = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.quote, p.search_keys, p.source_turn_id",
            params={"id": "pt_alpha"}).result_set
        assert pt_props[0][0] == "quantum observation is key"
        assert pt_props[0][1] == ["quantum observation"]
        assert pt_props[0][2] == "lme:test_v2_q:s0:t0"
```

(The fixture payload's `pt_alpha` dict gains `quote="quantum observation is key"`, `search_keys=["quantum observation"]`, `source_turn_id=0`.) **Also update the existing CONTAINS-count assertion in this test from `== 3` to `== 5`** (raw + 2 extracted + 2 turn points now get CONTAINS edges).

**Step 2 — Run to verify fail:** `uv run pytest tests/test_longmem_runner.py::test_v2_ingest_writes_payload_with_evidence_marks -v`.

**Step 3 — Implement:**

- In `ingest_haystack_v2` (after the Session node write, mirroring ingest.py's turn loop but `has_answer=False` — the v2 recall surface is extracted points only):

```python
        # ── E3 (D8): turn points — the speaker-derivation substrate. Same
        # deterministic ids + speaker property as the v1 leg; has_answer is
        # NOT set (v2 turn/evidence recall measures extracted points). ──
        for ti, turn in enumerate(session):
            role = str(turn.get("role") or "unknown")
            turn_id = f"lme:{qid}:s{si}:t{ti}"
            if not _point_exists(sdk._get_proj(), turn_id):
                sdk.create_point(
                    "event", f"[{role}] {str(turn.get('content') or '')}",
                    id=turn_id, session_id=sid, lme_question_id=qid,
                    lme_session_index=si, speaker=role,
                    is_episodic=True, status="draft",
                )
            sdk._get_proj().g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": turn_id})
```

- In `_write_payload`'s points loop:

```python
        turn_idx = p.get("source_turn_id")
        turn_ref = (f"lme:{qid}:s{si}:t{turn_idx}"
                    if isinstance(turn_idx, int) and 0 <= turn_idx < 1000
                    else None)   # int index → turn node id (D6)
        sdk.create_point(
            "statement", content, id=pid, session_id=sid,
            lme_question_id=qid, lme_session_index=si,
            is_episodic=True, has_answer=is_evidence,
            quote=str(p.get("quote") or "")[:200],
            search_keys=p.get("search_keys") or None,
            source_turn_id=turn_ref,
            status="draft",
        )
```

**Step 4 — Run to verify pass.** **Step 5 — Commit** via `commit-workflow`.

---

### Task 7: Read-time speaker derivation + context decoration (retrieve.py)

**Intent:** Derive speaker from the source-turn link at read time (D7) so the reader sees who asserted each fact — the E3 surface E2E-5's "user-asserted wins" builds on (answer-level wording is A1/A2's).
**Acceptance:** `point_props_for_hits` returns `quote`/`search_keys`/`source_turn_id`/`speaker`; `_annotate_hits` adds `speaker` per hit (from `source_turn_id` → turn node, or the hit's own speaker prop) and passes `quote`/`search_keys` through; `render_context` renders `[speaker]` when known and is byte-identical when not; retrieval still passes all existing tests.
**Files:**
- Modify: `tools/longmem_eval/ingest.py` (`point_props_for_hits` @189), `tools/longmem_eval/retrieve.py` (`_speaker_for_turns` new; `_annotate_hits` @38; `render_context` @89)
- Test: `tests/test_longmem_runner.py` (new `TestE3SpeakerDerivation`)

**Step 1 — Write the failing tests:**

```python
class TestE3SpeakerDerivation:
    def test_context_renders_speaker_from_turn_link(self):
        from tools.longmem_eval import retrieve as rt
        hits = [{"id": "pt_x", "content": "my 5K best is 27:12",
                 "speaker": "user", "lme_session_index": 0,
                 "session_date": "", "has_answer": False,
                 "superseded_by": None, "supersedes": []}]
        ctx = rt.render_context(hits)
        assert "[user] my 5K best is 27:12" in ctx

    def test_context_unchanged_without_speaker(self):
        from tools.longmem_eval import retrieve as rt
        hits = [{"id": "pt_y", "content": "plain fact",
                 "speaker": None, "lme_session_index": 0,
                 "session_date": "", "has_answer": False,
                 "superseded_by": None, "supersedes": []}]
        assert "[speaker]" not in rt.render_context(hits)
        assert "plain fact" in rt.render_context(hits)

    def test_annotate_hits_resolves_turn_speaker(self, tmp_path):
        from tools.longmem_eval import retrieve as rt
        from tools.longmem_eval.ingest import point_props_for_hits
        sdk = _fresh_sdk(tmp_path)
        try:
            sdk.create_point("statement", "my 5K best is 27:12", id="pt_x",
                             source_turn_id="lme:q1:s0:t0",
                             lme_session_index=0, is_episodic=True)
            sdk.create_point("event", "[user] my 5K best is 27:12",
                             id="lme:q1:s0:t0", speaker="user",
                             lme_session_index=0, is_episodic=True)
            proj = sdk._get_proj()
            props = point_props_for_hits(proj, ["pt_x", "lme:q1:s0:t0"])
            assert props["pt_x"]["source_turn_id"] == "lme:q1:s0:t0"
            annotated = rt._annotate_hits(
                [{"id": "pt_x", "content": "my 5K best is 27:12",
                  "match_source": "fts"}], props, [])
            assert annotated[0]["speaker"] == "user"
        finally:
            sdk.close()
```

**Step 2 — Run to verify fail:** `uv run pytest tests/test_longmem_runner.py::TestE3SpeakerDerivation -v`.

**Step 3 — Implement:**

- `point_props_for_hits` RETURN gains `quote`, `search_keys`, `source_turn_id`, `speaker`:

```python
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.session_id, ''), coalesce(n.has_answer, false), "
        "       coalesce(n.lme_session_index, -1), "
        "       coalesce(n.quote, ''), coalesce(n.search_keys, []), "
        "       coalesce(n.source_turn_id, ''), coalesce(n.speaker, '')",
        params={"ids": point_ids},
    ).result_set
    return {row[0]: {"session_id": row[1], "has_answer": bool(row[2]),
                     "lme_session_index": row[3], "quote": row[4],
                     "search_keys": row[5], "source_turn_id": row[6],
                     "speaker": row[7]} for row in rows}
```

- `retrieve.py` new helper + annotation (in `retrieve_for_question`, after `props = point_props_for_hits(...)`):

```python
def _speaker_for_turns(proj, turn_ids: list[str]) -> dict[str, str]:
    """One-query speaker lookup for source-turn links (E3 D7)."""
    ids = [t for t in turn_ids if t]
    if not ids:
        return {}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.speaker, '')", params={"ids": ids}).result_set
    return {r[0]: r[1] for r in rows}
```

```python
    turn_ids = [p.get("source_turn_id") for p in props.values()
                if p.get("source_turn_id")]
    speaker_by_turn = _speaker_for_turns(sdk._get_proj(), turn_ids)
    for h in hits:
        p = props.get(h["id"], {})
        sp = p.get("speaker") or speaker_by_turn.get(p.get("source_turn_id", "")) or None
        props[h["id"]]["speaker"] = sp or ""
```

- `_annotate_hits` — add the pass-throughs:

```python
            "quote": p.get("quote", ""),
            "search_keys": p.get("search_keys") or [],
            "source_turn_id": p.get("source_turn_id", ""),
            "speaker": p.get("speaker") or "",
```

- `render_context` — render `[speaker]` when known (between the session prefix and the content):

```python
        spk = h.get("speaker") or ""
        if spk:
            prefix = f"{prefix} [{spk}]"
```

**Step 4 — Run to verify pass.** **Step 5 — Run the module suites:** `uv run pytest tests/test_longmem_runner.py tests/test_extractor_v2.py tests/test_commit_schema.py tests/test_commit_endpoint.py -v`. **Step 6 — Commit** via `commit-workflow`.

---

### Task 8: Regression sweep + verification

**Intent:** Prove the whole E3 slice is green and the review-gate invariant holds end-to-end.
**Acceptance:** all four suites pass; `source_role` appears nowhere in the touched modules (grep); plan doc updated with the plan-review signature when the gate runs.
**Files:** none (verification only)

**Step 1 — Run the full non-slow suite:**
```bash
uv run pytest tests/ -m "not slow" -q
```
Expected: PASS (pre-existing failures, if any, must be unrelated — note them in the PR body).

**Step 2 — Review-gate invariant grep:**
```bash
grep -rn "source_role" tortoise/ tools/longmem_eval/ tests/ || echo "CLEAN: no source_role anywhere"
```
Expected: `CLEAN` (the ONLY allowed occurrence is a test asserting absence / the error message string in `test_source_role_extra_field_422`).

**Step 3 — Commit** (via `commit-workflow`), then hand to code-review.

---

## 3. Tests

| Test | Layer | Asserts | File |
|---|---|---|---|
| `TestE3Contract::test_output_contract_has_e3_keys` | unit | contract carries quote/search_keys/source_turn_id | `tests/test_extractor_v2.py` |
| `TestE3Contract::test_source_role_is_never_emitted` | unit | review-gate fix invariant (prompt+contract) | `tests/test_extractor_v2.py` |
| `TestE3Contract::test_atomicity_and_verbatim_value_rules_present` | unit | atomic + verbatim-value + user-vs-assistant rules in S2/S4 | `tests/test_extractor_v2.py` |
| `TestE3SourceTranscript` (4 tests) | unit | SOURCE TRANSCRIPT injection, cap, None-compat | `tests/test_extractor_v2.py` |
| `TestE3Resolution` (5 tests) | unit | quote→turn, conflict resolution, no-match fail-open, search_keys clean, quote cap | `tests/test_extractor_v2.py` |
| `test_e3_fields_validate` / `test_search_keys_entries_capped_and_trimmed` / `test_search_keys_max_four` | unit | schema accepts + rejects correctly | `tests/test_commit_schema.py` |
| `test_source_role_extra_field_422` | unit | `source_role` rejected by extra="forbid" | `tests/test_commit_schema.py` |
| `test_e3_fields_do_not_change_legacy_commit_id` | unit | additive canonical parity (#1350 pattern) | `tests/test_commit_schema.py` |
| `test_commit_writes_four_node_chain` (extended) | integration | quote/search_keys/source_turn_id on the committed graph node | `tests/test_commit_endpoint.py` |
| `test_v2_ingest_writes_payload_with_evidence_marks` (extended) | integration | turn points + speaker; index→node-id resolution | `tests/test_longmem_runner.py` |
| `TestE3SpeakerDerivation` (3 tests) | unit/integration | read-time speaker derivation; backward-compat render | `tests/test_longmem_runner.py` |

**E2E-5 alignment:** this issue delivers the S15 surfaces E2E-5 depends on — single-fact granularity (D1), search_keys 2–4 + verbatim tokens (D2/D5), point carries `source_turn_id` (D3–D6), speaker derived from the turn's `speaker`/`[role]` at read time (D7/D8). The E2E-5 **answer-level** assertions ("user-asserted wins over the assistant suggestion", "evidence-marked") are owned by the epic's E2E run and gate on M6 recalibrated marks (MECE fix note) + A1/A2 reader instructions — see Conditional-gate notes.

---

## 4. Cross-Lane Interfaces

| Lane | Relation to E3 | Coordination |
|---|---|---|
| **E1** (`when` slot, session date) | `when` is an OUTPUT_CONTRACT point key E1 adds — E3 does NOT add it here | E3 adds `quote`/`search_keys`/`source_turn_id` only. Both are additive optional keys on the same contract/schema — merge-safe (no key collision). E3 does not touch `session_date`/`startedAt`. |
| **E2** (state-value facts, Tier A) | Shares the verbatim-value carve-out language (D1 restates it for atomicity) | E3's ATOMIC POINTS rule references the value-survives-verbatim carve-out; E2 adds the Tier-A marker as a separate property. No field overlap. |
| **R2** (OR-tolerant sparse + query expansion via search_keys) | The consumer of the `search_keys` E3 persists | E3 writes + passes `search_keys` through the retrieval annotation (Task 7); R2 expands queries with them. No behavior change here — E2E-1's paraphrased recall lands via R2. |
| **M6** (evidence-marking recalibration) | E2E-5's evidence-marked assertion is CONDITIONAL on M6 marks | E3 does not change evidence marking. The MECE fix note (2026-08-20) makes E2E-5's evidence assertion co-run/stub-conditional on M6. |
| **A1/A2** (reader instructions) | Consume the `[speaker]` decoration E3 renders | E3 renders speaker-attributed context; A1/A2 (separate issues) word the reader to weigh user-asserted evidence. E2E-5's full answer assertion needs both. |
| **P4** (parity) | (a) `client_commit_id` parity preserved by the additive canonical pattern (Task 4 test); (b) hosted capture `speaker` property parity is P4's item — E3's hosted read-side derivation joins on it (`{session_id}_t{i}`) | E3 stores the int index in the hosted payload; the hosted speaker join keys on `{session_id}_t{i}` + the `speaker` property P4 aligns. The eval path (this issue's derivation surface) writes its own turn points with speaker (Task 6). |
| **E5** (supersession) | Orthogonal — superseded points keep their E3 fields (new content → new content-addressed id, fields recomputed for the new content) | No interaction. `source_turn_id` on the superseding point references its own turn. |
| **Ontology** | Facts-as-Points; no new kinds/edges | `search_keys` + `source_turn_id` are additive point properties (approved); `quote` pre-exists. Invariant untouched. |

---

## 5. ⛔ Conditional-Gate Notes

- **APPROVED (epic 04-plan §4/§6, issue #1535 checklist):** `search_keys` (2–4 aliases + verbatim tokens) and `source_turn_id` as **additive point properties**. `commit_schema.Point` (`extra="forbid"`) MUST gain both fields or every E3 payload 422s — this is required schema work within the approved set, not a new-field request.
- **NOT APPROVED — `source_role`.** The 2026-08-20 review-gate fix removed it from 04-plan §6 OUTPUT_CONTRACT. **Implementer must NOT reintroduce it** — speaker is derived at read time from the source-turn link's existing `speaker`/`[role]`. Guarded by: contract/prompt tests (`test_source_role_is_never_emitted`), schema rejection (`test_source_role_extra_field_422`), and a repo-wide grep in Task 8 Step 2.
- **ADJACENT — `when` (E1):** NOT added by this issue. Adding the `when` contract key here would cross into E1's lane (E1 owns the `when` slot + Session `startedAt`). E3 leaves the contract's `when` introduction to E1.
- **ADJACENT — R2 query expansion:** E3 persists `search_keys`; R2 (separate issue) expands queries with them. No retrieval-behavior change in this issue beyond decoration.
- **ADJACENT — E2E-5 answer-level assertions:** gated on M6 recalibrated marks (MECE fix note, co-run/stub) + A1/A2 reader wording. This issue's completion bar is the S15 surface checklist in the issue body (unit+integration), not the full E2E-5 pass.
- **Ontology invariant:** no new kinds, no new edge types, no expansion packs. All changes are additive properties + prompt/contract text. The eval turn-point write in `ingest_haystack_v2` (Task 6) uses the existing `event` pointKind + `speaker` property already used by the v1 leg — nothing new.

---

## 6. Open Questions

1. **`source_turn_id` payload type (int index) vs graph property (resolved node id)** — resolved as: payload = 0-based int conversation index; eval graph property = resolved turn node id `lme:{qid}:s{si}:t{i}`; hosted graph property = the int index (hosted turn ids `{session_id}_t{i}` are derived from the same index, so the int IS the link). Confirm no cross-lane objection before execution (Q2 makes the hosted side concrete).
2. **Hosted read-side derivation is not wired in this issue.** No hosted endpoint today consumes speaker for extracted points (the eval `retrieve.py` is the E2E-5 derivation surface). The hosted join key is `{session_id}_t{i}` (+ P4's `speaker` property). Is a hosted read-side speaker surface (e.g. `get_session_detail` decoration) wanted in E3, or deferred to the hosted-reader lane? **Default: deferred** (YAGNI — nothing consumes it yet).
3. **`search_keys` minimum (2) is advisory, not enforced.** The epic says "2–4 aliases + verbatim tokens"; the plan repairs/sanitizes to 0–4 with warnings and 422s only >4 or >60 chars. A point with a single key passes. Acceptable? **Default: yes** — hard-blocking on a minimum would fail extraction over a hallucination; the contract asks, the schema enforces bounds.
4. **SOURCE TRANSCRIPT cap (8000 chars)** — protects the S2/S4 budget but means long sessions fall back to quote-only resolution. M3's bounded `max_tokens` applies to the completion, not the input. Confirm the cap is acceptable for the eval's session sizes (LongMemEval turns are short; production `chunk_size=50` sessions may exceed it — quote-only resolution still anchors correctly).
5. **v2-mode turn points carry `has_answer=False`** — v2 recall stays measured over extracted evidence points; the deterministic turn-recall branch never fires in v2 mode even when `evidence_point_count == 0` (extractor missed everything → turn_recall 0.0 instead of the v1 fallback). Confirm this metric-semantics choice (the #1369 docstring's extractor-vs-retrieval attribution is preserved).
