## Review — #9408 · `fix(9202): an end that names no instant bounds no instant`

**Reviewed head:** `cb8a828c4` (merge of `origin/main` `77660fe5b`; note the PR body's
`review recorded:` line names `fc50966e3`, which predates `a1c77805f` — the §4.7
sentence edit — and the main merge. Everything below is against `cb8a828c4`.)
**Verdict: CLEAN.** No correctness defect, no regression, no vacuous test, no
scope/claim mismatch, no repo-rule violation found.

Method: source-read only (this checkout has no interpreter, and I did not create one).
Nothing below rests on execution; the two mechanical checks I ran were plain
text/regex reads of the committed files, not the repo's code.

---

### 1. The change is correct and minimal

`tortoise/sdk.py` differs from the merge base in exactly three hunks (verified:
`git diff 77660fe5b cb8a828c4 -- tortoise/sdk.py | grep '^@@'` → 3 headers): the
`_covers` docstring, the END gate, and the `nearest` annotation arm. No merge-resolution
drift.

`_covers`' END arm now reads

```python
if vt is not None:
    kt = _created_sort_key(vt)
    if kt[0] == 0 and kt < t_key:
        return False
```

`_created_sort_key` returns `(0, epoch)` for a value naming an instant and `(1, str(v))`
for one that does not (`tortoise/search_engine.py:2873`+`2940`: `return (1, s)` where
`s = str(value or "")`). So `kt[0] == 0` is exactly "this bound names an instant", and
the excluded set is unchanged for every parseable END — `(0, e) < (0, t) ⟺ e < t`. The
only cells whose verdict moves are (stored END names no instant) × (probe names no
instant), which is exactly the defect.

Direction of the remaining asymmetry is deliberate and *pre-existing*: a parseable END
still excludes an unparseable probe, because `(0, ·) < (1, ·)` for every text. That is
the pinned control at `tests/test_validity_windows.py:817-825` ("CONTROL, unchanged by
the ruling"). I recomputed that control under the fixed code: `old2`
(`vf="2026-06-01"`, `vt="2026-06-20"`) still returns `_covers=False` for probe `"zzz"`
(START arm not taken — `(0,e) > (1,"zzz")` is False; END arm taken — `(0,e) < (1,"zzz")`
is True), so `out2b` stays `found` on the successor and `ambiguous is not True` holds.

The `nearest` hunk is provably inert, and I checked it case-by-case rather than taking
the PR's word: the old outer `_created_sort_key(vt) < t_key` and the new inner
`kt[0] == 0 and t_key[0] == 0 and kt < t_key` agree on all four bucket combinations
(both parseable → identical comparison in bucket 0; either non-parseable → `dist` stays
`None` in both versions). `# noqa: SIM103` removal is fine: the mirrored START arm on
`main` (`if vf is not None:` / inner `if kf[0] == 0 and ...`) has the same nested shape
and carries no such noqa, so the linter's treatment is already settled by shipped code.

### 2. The new test is not vacuous — proof from the source, not from a run

`tests/test_validity_windows.py::test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe`
is parametrized over `"TBD"` and `""`, and each param's `after` assertion reds on the
pre-fix code:

| param | `_created_sort_key(vt)` | probe `"zzz"` | pre-fix END arm | pre-fix status | post-fix status |
|---|---|---|---|---|---|
| `"TBD"` | `(1,"TBD")` | `(1,"zzz")` | `(1,"TBD") < (1,"zzz")` → **True** | `absent` | `found` |
| `""` | `(1,"")` | `(1,"zzz")` | `(1,"") < (1,"zzz")` → **True** | `absent` | `found` |

The test cannot silently degrade into a no-op either, because it guards storage first:
`assert _props(sdk, p["id"])["validTo"] == stored_end` — if the write path ever dropped
or normalized the value, the test errors instead of passing on `vt is None` (where the
END arm is never reached). The `"AAA"` assertion is honestly labelled in the docstring
as green pre-fix; the discriminating assertion is the `"zzz"` one, which the docstring
also names. The in-test CONTROL (dated END `2024-03-01` vs probe `2050-01-01`) keeps the
fix from being "windows stop ending": `(0,e) < (0,e)` → excluded → `absent`,
`found is False`.

### 3. The doc↔code pins actually hold at the reviewed head

I re-ran the pin assertions' *text* predicates against `cb8a828c4` (plain `re`, no repo
import):

* `tests/test_7818_ontology_record.py` — all four END-arm assertions match
  (`kt = _created_sort_key(vt)` present; `if kt[0] == 0 and kt < t_key:` present;
  no `kt <= t_key`; no `_created_sort_key(vt) <= t_key`), and both `_inverted`
  assertions still hold (`return kt < kf`, no `<=`). `_method_body`'s sibling slice
  (`_covers` → `window_entry`, `_inverted` → `nearest` loop) still resolves with the new
  nesting, so the pins are live, not retired.
* `docs/ONTOLOGY.md` §4.7 — every anchor the two doc tests assert is still present in
  the head slice (flattened): the boundary sentence, `` `_covers` ``, `` `vF <= t <= vT` ``,
  `#5374`, `entry-point-dependent`, `[vF, vT)`, `Which is canonical is an **open decision**`,
  `tracked at **#7818**`, `not a predicate-only change`, `zero-length `[t, t]` window is
  already persistable`, `` `malformed` flag stays **silent** ``, `` `_inverted` returning `kt < kf` ``,
  `well-formed-**empty**`. The `a1c77805f` clause ("…and only once `vT` names an instant")
  is added mid-blockquote and breaks none of them; the citation-rot scan
  (`\S*\.py:\d+`) finds **no** line citation in the section.
* `docs/ONTOLOGY-CHANGELOG.md` — appended at the end, which is this file's
  oldest→newest order (`… #8745 (2026-10-15)`, `… #6140 (2026-10-19)`,
  `… #9202 (2026-10-20)`). No `file.py:NNN` citation added.

### 4. Manifest / repo-rule checks

* **No new test file.** All six changed paths are pre-existing; `tests/test_validity_windows.py`
  and `tests/test_7818_ontology_record.py` are both already registered in
  `config/ci-surfaces.yml` (`:3764` and `:1582`, the latter also `:5128` as the
  `tier1` dual-registration the docs-only path needs), so the manifest-integrity leg has
  nothing to select that it cannot find.
* **No surface change** — `_covers` is a closure inside `get_historical_knowledge`; no
  `TOOL_REGISTRY` entry, no public `TortoiseSdk` method, no new reply field. So the
  MCP/SDK-approval rule is not engaged and `config/surface-manifest.yml` is untouched.
* **`MEMORY.md`** — one line added, file is 79 lines, well under the 150-line cap, and
  `markdownlint`'s MD013 (line length) is disabled repo-wide
  (`.markdownlint-cli2.jsonc`), so the long-line style is consistent with the file.
* The other cross-bucket comparisons the PR claims are already gated — `audit.py:950-956`,
  `commit_schema.py:545-556`, `sdk.py::_inverted` — are indeed gated on `key[0] == 0`
  before comparing. I grepped every `_created_sort_key` use in `tortoise/`: the `_covers`
  END arm was the last ungated one on the window-coverage path. `time_aware.is_stale_entry`
  is a different surface (eval staleness) and already skips unparseable values
  conservatively, which agrees with the new reading rather than contradicting it.

### 5. Contradiction-test compliance

The PR separates the **coverage** half from the **reported-state** half and leaves the
latter alone. I confirmed from source that nothing about the state changes:
`validity.end_state` (`tortoise/validity.py:128`) raises `END_UNKNOWN` only on
`validToPrecision == "unknown"` + absent `validTo`, and `effective_precision`
(`:123`) is untouched — so `end_state: "known"` / `valid_to_precision: null` for a
present-but-un-datable END are unchanged, and the `#2350` pin
(`test_unorderable_end_is_emitted_as_null_on_every_read_surface`) never touches
`status`. #9202's owner decision stays open. The two documents that record it as the
owner's call (`docs/ONTOLOGY.md` §4.7's closing paragraph, `docs/product/mcp-sdk-surface.md`)
are amended only to relocate *which layer* the asymmetry lives in, not to settle it.

### 6. Regression surface

A move is only possible for (unparseable stored END × unparseable probe). I grepped every
`get_historical_knowledge(...)` call site in `tests/` with a non-date argument: there are
two, both in `tests/test_validity_windows.py` — the new test (`:937`, `:944`) and the
pre-existing CONTROL (`:821`), which I recomputed as still green. Dated probes are
bit-identical by construction (`kt[0] == 0` is a no-op for a parseable END; for an
unparseable END, `(1,x) < (0,e)` was already False). The three failures the PR attributes
to #9406 all live in `assembly.window_rows(today, today)` and never reach `_covers` — I
read all three (`test_assembly_window_rows_carry_the_precision_block`,
`test_falsey_but_present_bound_resolves_identically_on_all_three_read_paths`,
`test_unorderable_end_is_emitted_as_null_on_every_read_surface`): each asserts precision /
`end_state` fields on `chain[0]` or on `assembly` rows, none asserts a `status`. They
cannot be caused by this diff.

---

### Residuals (both outside this diff's correctness, neither blocking)

* **Execution residual, not a defect claim.** I did not run the suite. One command would
  confirm the parametrized reds on the pre-fix code that I derived analytically above:
  `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_validity_windows.py -k unparseable_probe -q`
  on a worktree with `tortoise/sdk.py`'s END arm reverted to
  `if vt is not None and _created_sort_key(vt) < t_key: return False`. Recorded so the
  next lane has the exact reproduction; the verdict does not depend on it.
* **CI state.** At the time I read it, the checks on `cb8a828c4` were `queued` (the head
  was pushed ~4 minutes earlier), so this review makes no claim about them; my evidence
  for the pins is the source read in §3, which is what those legs would assert anyway.
