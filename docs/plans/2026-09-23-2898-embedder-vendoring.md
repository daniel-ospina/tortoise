# Plan — #2898: serve the pinned embedder from a first-party release asset (vendoring)

> **Research path:** `writing-plans` Step A consumed the scoping artifact
> `~/.pi/agent/state/lane-reports/2898-SCOPE-2026-09-23.md` (Revision 2, scope-verified:
> `NO ISSUES FOUND` ×2, cycle 3). Step B (fresh Perplexity gate) is **skipped**: this plan introduces
> **zero third-party dependencies** (stdlib `urllib`/`tarfile`/`hashlib` only) and every integration
> boundary is first-party.
> **Integration surface map:** `workflow/03` skipped — no DB/API/auth/UI boundary; the integration
> surfaces are two GitHub Actions workflows plus the provisioning tool, and they are enumerated in
> §Integration surfaces below.
> **Scope:** `~/.pi/agent/state/lane-reports/2898-SCOPE-2026-09-23.md` §Phase 5.
> **Issue:** #2898 (`complexity:standard`) — the unmet acceptance criterion is AC1's **vendored**
> disjunct. **Worktree:** `.worktrees/fix/2898-embedder-cold-path`.

## Design decisions (the "how", and why not otherwise)

| # | Decision | Rejected alternative + why |
|---|---|---|
| D1 | **Release asset, tag derived from `REVISION`** | OCI-in-GHCR (digest-addressed) — stronger but a *transport reopen* vs the owner's recorded decision; filed as **#4808**. |
| D2 | **Packaging in Python** (explicit `TarInfo` fields incl. `mtime` on **directory and symlink** members; `gzip.GzipFile(filename="", fileobj=…, mode="wb", mtime=0)`) | `tar --sort=name …` — measured unusable here (`bsdtar 3.5.3` rejects `--sort=name`), and `tarfile mode="w:gz"` is **not** byte-reproducible (gzip header embeds `mtime`). **Also rejected: omitting `filename=""`** — measured: CPython's gzip derives the FNAME header field from `fileobj.name` (`Lib/gzip.py`), so the same payload built to `alpha.tar.gz` and `beta.tar.gz` hashed differently. A non-reproducible tarball means a republish silently stops matching the pin. |
| D10 | **An env seam for the asset URL** (`TORTOISE_EMBEDDER_ASSET_URL`, default = the derived constant) | No seam — the behaviour tests execute the tool as a **subprocess** (`tests/test_embedder_provision.py::_run`), so a module constant cannot be monkeypatched across the process boundary and those tests would `urlopen` the **real** release (a 133 MB download into the developer's real HF cache, ×3 for the retry test). The seam cannot weaken transport integrity: the SHA-256 pin is verified regardless of which URL served the bytes. |
| D3 | **One pinned `ARTIFACT_SHA256`** (the tarball's hash), verified *before* extraction | Per-file hashes — the tarball hash already covers transport **and** content in one value. |
| D4 | **Delete the 4 embedder `actions/cache` steps** | Keep-the-cache-as-accelerator — the owner's §D asks for deletion, and with a first-party artifact an evictable cache is machinery without a guarantee; filed as **#4810** with a slow-fetch trigger. |
| D5 | **Delete the hub-download leg from the tool** | Keep HF as a fallback — "delete the network dependency rather than manage it" (owner §D). Hub egress leaves the test-time path entirely; `Dockerfile.hosted`'s build-time download is out of scope (**#4809**). |
| D6 | **Cache probe first, asset second** | Asset-always — the probe is free when the model is already present and the marker contract (`cached` / `downloaded`) is what the issue names. |
| D7 | **Cache-root resolution from `huggingface_hub.constants.HF_HUB_CACHE`, plus `SENTENCE_TRANSFORMERS_HOME` when set** | Hand-rolling `$HF_HOME/hub` — breaks when `HF_HUB_CACHE`/`HUGGINGFACE_HUB_CACHE`/`XDG_CACHE_HOME` is set; `Dockerfile.hosted:34-41` records the shipped bug this class causes. |
| D8 | **Gate invocation byte-identical** (`--attempts N --backoff N`) | New flags — would force an edit to `_GATE_COMMAND_RE`, weakening the anti-vacuity pin. The retry budget now bounds the *asset* fetch; that is a deliberate, named deviation from §D's "delete the retry loops". |
| D9 | **No publish workflow** | `workflow_dispatch` requires the file on the default branch, so it could not publish *before* this PR; a once-per-rotation action does not earn a CI surface. |

## Integration surfaces

| Surface | Boundary | Test layer |
|---|---|---|
| `tools/embedder_provision.py` ↔ HF cache on disk | extraction target **is** what the suite later reads | unit (fake `sentence_transformers`, temp cache root) |
| `tools/embedder_provision.py` ↔ GitHub release CDN | HTTP GET + 302 → `release-assets.githubusercontent.com` | unit (local `http.server`) + **real** in the PR's CI |
| 4 gate steps ↔ tool | the pinned invocation / step name / fail-closed contract | unit (workflow YAML pins) |
| `tools/publish_embedder_weights.py` ↔ release API | mutates a public release | unit (packaging determinism) + a manual publish-time check |

## Tasks

### Task 1: reproducible packager + publish tool

**Intent:** produce the artifact whose digest the whole design trusts, reproducibly, so a future rotation re-derives the same bytes.
**Acceptance:** `tools/publish_embedder_weights.py --print-sha256` prints a hash **identical across two consecutive runs**, **across two different output paths**, and **across mutated source modes/umask**; `--build <out>` writes the tarball; `--publish` creates/uploads the release. `python3 tools/ci_selection.py --integrity` exits 0.
**Files:**
- Create: `tools/publish_embedder_weights.py`
- Modify: `tools/ci_selection.py` (`TOOL_CARVEOUTS`), **`config/ci-surfaces.yml` — MANDATORY, TWO entries: (a) register `test_publish_embedder_weights.py` in the `core` surface; (b) add a `durations:` entry for it.** (a) alone is NOT sufficient: `--integrity` also runs `duration_coverage_issues`, which enforces `DURATION_COVERAGE_MIN = 0.90` over `fast_pool` — and the repo sits **exactly at the floor** today (545/605 = 90.08 %). One unclassified fast file makes it 545/606 = 89.93 % and `--integrity` exits 1, which also reddens `tests/test_ci_selection.py::test_duration_coverage_guard_boundary_and_realistic`. `manifest-integrity` (which runs `--integrity`) is in the **required** `python-ci-gate`'s `needs`, so this is a merge blocker, not a warning.
- Test: `tests/test_publish_embedder_weights.py` (determinism **across two dest names**, member list, `*.incomplete` exclusion, symlink preservation, tag derived from `REVISION`)

**Steps**
1. `build_archive(cache_root, dest)` — walk `models--BAAI--bge-small-en-v1.5/`, skip `*.incomplete`, sort names, emit `TarInfo` with explicit `uid=gid=0`, `uname=gname=""`, a **fixed `mtime` applied to regular, directory AND symlink members alike**, `format=PAX_FORMAT` with `pax_headers` cleared, and **`mode` NORMALIZED (regular `0o644`, directory `0o755`, symlink `0o777`) — never read from `os.lstat`.** Reading the mode leaks the build host: macOS `lstat` reports a symlink as `0o755` while Linux reports `0o777`, and a regular file's mode leaks the umask (measured: `chmod 0o664` on one member changed the digest) — so a rebuild on Linux would silently stop matching `ARTIFACT_SHA256`. Symlinks stay symlinks via `TarInfo.linkname`.
2. Compress with `gzip.GzipFile(filename="", fileobj=…, mode="wb", mtime=0)` — **not** `tarfile.open(mode="w:gz")`, and **not** without `filename=""` (CPython would embed the output basename in the FNAME header — measured to change the digest).
3. `--print-sha256` / `--build` / `--publish` (the latter shells out to `gh release create|upload` with the derived tag).
4. Register the tool in `TOOL_CARVEOUTS` **and** register its test file in `config/ci-surfaces.yml`.

**Test (Red→Green):** write `test_build_is_byte_reproducible` first — two builds to **two different dest names** from a tree whose **source modes have been mutated between builds** → equal digest. It fails against a naive `tarfile w:gz` implementation, against a missing `filename=""`, and against mode-from-`lstat`; each failure is the point.

---

### Task 2: swap the transport in the provisioning tool

**Intent:** make the download leg fetch the pinned revision from our own release asset instead of `huggingface.co`.
**Acceptance:** with an empty cache root and a reachable asset, `provision()` extracts, prints `embedding model: downloaded`, and a subsequent `local_files_only=True` load succeeds; with a corrupt asset it emits the named `::error::` and exits 1 **without extracting**; the hub host is no longer referenced in the acquisition path.
**Files:**
- Modify: `tools/embedder_provision.py`
- Test: `tests/test_embedder_provision.py`

**Steps**
1. Constants: `RELEASE_TAG` (derived from `REVISION`), `ASSET_NAME` (derived), `ASSET_URL` (derived), `ARTIFACT_SHA256` (pasted from Task 4's output), keeping `MODEL`/`REVISION`/markers verbatim. `_asset_url()` returns `os.environ.get("TORTOISE_EMBEDDER_ASSET_URL") or ASSET_URL` — the **test seam** (D10); it cannot weaken integrity because `ARTIFACT_SHA256` is verified whichever URL served the bytes.
2. `_cache_root()` → `Path(os.environ["SENTENCE_TRANSFORMERS_HOME"])` if set, else `huggingface_hub.constants.HF_HUB_CACHE`. Import lazily so the module stays importable without the extra.
3. `_fetch(attempts, backoff)` → `urllib.request.urlopen(_asset_url())` → stream to a temp file → `sha256` → compare → `tarfile.open(...).extractall(_cache_root(), filter="data")`. On mismatch: delete the temp file, return a named error, **never extract**. Log `download attempt N/M failed: <detail>` **byte-for-byte as today** (the wording is pinned by `test_exhausted_retries_report_attempt_count`).
4. `provision()` = probe → `_fetch` → re-probe. Keep `CACHED_MARKER` / `PROBE_FAILED_MARKER` / `DOWNLOADED_MARKER` and the warning+error annotation + step-summary contract byte-identical. **Pin the `detail` precedence explicitly for the both-failed case:** the returned detail is the **probe error** (the real multi-line `transformers`/`huggingface_hub` failure, so the `::error::` still names the real cause and `test_annotations_are_two_complete_single_lines`' `offline-mode` assertion still holds), with the fetch error appended — not substituted. State this in the code so a later edit cannot silently swap the precedence.
5. Delete the hub-download branch and the `HF_HUB_OFFLINE` prose that only explained it; keep the module docstring's history but point it at the new transport.

**Test:** re-target the download-path tests onto a local `http.server` serving a locally-built tarball, with **`TORTOISE_EMBEDDER_ASSET_URL` pointing at it and a per-test `SENTENCE_TRANSFORMERS_HOME=<tmp_path>` — mandatory, so no test can touch the developer's or the runner's real HF cache.** Add checksum-mismatch, 404-across-retries, and cache-root-precedence tests. **Explicitly rewritten (named, not left in "unedited" territory):** `test_download_path_survives_the_offline_env_latch` (+ `_FAITHFUL_DOWNLOAD`/`_LATCH_PREAMBLE` — inapplicable by construction), and the three `_ALWAYS_RAISES`-driven tests that currently reach the download leg — `test_unobtainable_model_fails_loudly`, `test_annotations_are_two_complete_single_lines`, `test_exhausted_retries_report_attempt_count` — each of which must gain the seam + temp cache root and must keep its assertion. Every wiring/anti-masking/name/lockstep pin stays unedited.

---

### Task 3: delete the four embedder cache steps

**Intent:** remove the evictable, write-once cache from the acquisition path (owner §D).
**Acceptance:** `python-ci.yml` + `post-merge-validation.yml` contain no step with `path: ~/.cache/huggingface` or `key: hf-embedding-cache-*`; the four `Embedding model REQUIRED …` steps still exist with their `name`, `timeout-minutes`, `run`, and no `continue-on-error`; `yaml.safe_load` still parses both files.
**Files:** Modify `.github/workflows/python-ci.yml` (`- name:` at 458, 961, 1283 + their trailing comments), `.github/workflows/post-merge-validation.yml` (`- name:` at 354)
**Test:** `tests/test_embedder_provision.py` (new pin #7 in the scope), plus the existing workflow-parsing guards re-run.

**Steps**
1. Delete each cache step **by content** (from its `- name: Cache HF embedding model…` through the line before the next `- name:`), never by line range.
2. Re-parse both workflows and re-run `tests/test_embedder_provision.py` + `tests/test_ci_selection.py -k integrity` + `tests/test_workflow_secret_interpolation.py` before any other step.

---

### Task 4: publish the artifact (operational, pre-merge)

**Intent:** make the prerequisite true, not assumed.
**Acceptance:** `curl -sSL "$ASSET_URL"` → HTTP 200; the downloaded bytes hash to `ARTIFACT_SHA256`; `gh release view "$TAG" --json assets` lists the asset; `gh release list` shows a non-`v*` tag; **no `push`-event workflow run was created**.
**Files:** none (operational)
**Steps**
1. Build from the local HF cache: `python3 tools/publish_embedder_weights.py --build /tmp/… --print-sha256`.
2. Paste the hash into `tools/embedder_provision.py` (Task 2 constant) and commit that with the rest.
3. `--publish`; then run the three verification commands above.
4. If any check fails, **do not open the PR** — the ordering guarantee depends on this.

---

### Task 5: verify, then ship

**Intent:** AC1's artifact is the PR's own CI run on a runner with no HF cache at all.
**Acceptance:** the PR's `test (a)` / `test (b)` / `test-slow (a|b)` / `test-concurrency-falkor` logs each show `embedding model: downloaded`; the run is green; `#2898`'s AC1 checkbox is closed by that run id.
**Files:** Modify `docs/ci/…` or the workflow header comments if a docs index references the cache
**Test:** the full pinned suite locally (`pytest tests/test_embedder_provision.py tests/test_publish_embedder_weights.py tests/test_ci_selection.py tests/test_workflow_secret_interpolation.py -v`), then CI.

**Steps**
1. Local: the pinned suites above (they are fake-driven and need no `sentence-transformers` — A8; `uv sync` in the shared venv is forbidden).
2. `commit-workflow` skill → PR → `code-review` gate → merge.
3. Record the merge run id on #2898 with the `downloaded` lines, then close it.
4. If CI shows `cached` instead of `downloaded`, the artifact path was not exercised → **stop and diagnose** rather than closing.

## Risks

| Risk | Mitigation |
|---|---|
| Release deleted / repo renamed → all provisioning jobs red at once | loud and named, not silent; recovery is one command (Task 4); recorded in the scope's residual-risk note |
| macOS-built tarball extracted on Linux | symlinks and modes are explicit `TarInfo` fields; a member-list test pins the shape |
| `filter="data"` availability | CI pins `python-version: 3.12`; the parameter exists from 3.12.0 — verified at implementation time, with a fallback to manual member validation if not |
| The new tool has no consumer if Task 2 is reverted | Task 1 is separately valuable as the rotation record; registered so its test runs |
