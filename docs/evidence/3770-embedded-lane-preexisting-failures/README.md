# PR #3770 / head `db858ad5c` — `test (b)` red: failure classification

**Verdict: PRE-EXISTING, deterministic, embedded-lane-only. Not introduced by this PR.**

The `python-ci-gate` red on this PR is `test (b)` (tier-2 **embedded** leg) with
`3 failed, 3115 passed, 25 skipped`. All three failures are in
`tests/test_rebuild_recreate_content_parity.py` — a file this PR does **not**
touch (the PR's whole delta vs its merge-base is `tortoise/extractor_v2.py`
+23 and `tests/test_extractor_v2.py` +26). No test was skipped, xfailed,
env-gated or weakened to make this go away.

## 1. The three failing test ids

The workflow log is not a usable source (it echoes its own script and prints an
unattributable `FAILED may` token), but the job uploads its `junit.xml`
(`pytest-log-test-b`, artifact id `10634336259`) — the authoritative observed
set. Extracted:

```
tests/test_rebuild_recreate_content_parity.py::test_bare_reemit_supersedes_revision
tests/test_rebuild_recreate_content_parity.py::test_intermediate_derived_write_wins
tests/test_rebuild_recreate_content_parity.py::test_bare_reemit_still_folds_annotator_dim
```

All three share one assertion:

```
AssertionError: embedding derives from content and must not drift independently
tests/test_rebuild_recreate_content_parity.py:128
```

Raw inputs: `ci-run-35588858762-test-b-pytest.log` (511 KB, the job's pytest
log) and `ci-run-35588858762-test-b-failures.txt` (the extraction above).

## 2. Reproduction — the same lane as `test (b)`

`test (b)` is the fast matrix half-b on the tier-2 shape: `TORTOISE_DB_URI`
unset and `TORTOISE_TEST_CARVE_OUT=1` (confirmed in the run's `changes` job —
`selection: {"surfaces": ["core"], "full": false, ..., "test_files": [... ,
"test_rebuild_recreate_content_parity.py", ...]}`, so the `core` surface a
`tortoise/extractor_v2.py` change selects includes this file). Reproduced with:

```bash
TORTOISE_TEST_CARVE_OUT=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run python -m pytest tests/test_rebuild_recreate_content_parity.py \
  -v --timeout=300 -p no:cacheprovider -m 'not track_b and not live' -r fEs
```

(`uv sync --extra embeddings --extra parity` — the embedding comparison is not
vacuous without the embedder.)

## 3. Results matrix

| revision | lane | result |
|---|---|---|
| HEAD `db858ad5c` | embedded (`CARVE_OUT=1`) | **3 failed**, 11 passed, 1 xfailed |
| merge-base `a36fd686e` | embedded | **3 failed**, 11 passed, 1 xfailed |
| `origin/main` `df2da76d0` | embedded | **3 failed**, 11 passed, 1 xfailed |
| `a4e560460` (#4263, adds these tests) | embedded | **3 failed**, 11 passed, 1 xfailed |
| HEAD `db858ad5c` | docker (`docker://:falkordb@localhost:6380/...`) | 14 passed, 1 xfailed |
| `origin/main` `df2da76d0` | docker | 14 passed, 1 xfailed |

Raw logs: `head-db858ad5c-embedded.log`,
`base-a36fd686e-embedded.log`, `main-df2da76d0-embedded.log`,
`head-db858ad5c-docker.log`, `main-df2da76d0-docker.log`,
`a4e560460-4263-adds-tests-embedded.log`.

The same 3 failures therefore reproduce on the merge-base, on current
`origin/main`, and on the commit that introduced the tests — and pass on the
docker lane. **No rebase clears them** (the failure is not time/shape drift).

## 4. Mechanism — a real, deterministic projection bug (not a flake)

The failing assertion compares the rebuilt node's `embedding` with the live
`apply()` oracle's. A probe (`probe.py.txt` — a verbatim recorded script, kept
with a `.txt` suffix so it is not linted as repo code; cosine similarity of each side's vector
against the candidate contents) shows the rebuilt node keeps the **original**
content's embedding while its `content` is the final one:

```
bare_reemit     content post='REEMITTED'  applied='REEMITTED'
  post    embedding: cos(post, 'ORIG')      = 1.000000   # STALE
  applied embedding: cos(applied,'REEMITTED')= 1.000000
intermediate    content post=''           applied=''
  post    embedding: cos(post, 'ORIG')      = 1.000000   # STALE
  applied embedding: cos(applied,'R1')      = 1.000000
```

`rebuild_all` writes `content` unconditionally but leaves the pre-supersession
`embedding` on the re-created node. That is the content/derived parity class
`tests/test_rebuild_recreate_content_parity.py` exists to guard (#4042/#4263),
and it is deterministic — not LLM/embedder variance.

## 5. Why this PR is red while `main`'s push CI is green — lane selection

`test (b)` is docker when the change is classed `full=true` (trunk backstop) and
embedded on a tier-2 PR. This PR's diff selects `core` at `full=false` → the
embedded lane runs the file. The neighbouring core-surface PR #4452
(`feat`… `fix/4305-graph-only-derived-v2`) took the docker lane and its
`test (b)` was `success`. The bug is present on `main` too; the docker lane
simply does not expose it. `main`'s push runs at `df2da76d0` are green.

## 6. Introducing commit

`a4e560460` — `fix(projection): rebuild_all content parity on delete→recreate
(#4042) (#4263)`, 2026-09-20T22:27:34-05:00, which both added the tests and the
projection fix they assert. The tests are red in the embedded lane at that
commit, i.e. #4263's fix does not hold on the embedded path.

## 7. Scope

This evidence is a **classification**, not a fix. The defect lives in
`tortoise/projection/__init__.py::rebuild_all` (stale `embedding`/`content_hash`
on a re-created node) and is out of scope for this PR (`Refs #2552`,
`tortoise/extractor_v2.py`). It is filed separately.
