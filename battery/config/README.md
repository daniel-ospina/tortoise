# Scenario Corpus v1 (battery/config)

The sealed, deterministic scenario corpus for the Agent-Reasoning Eval Battery
(epic [#1402](https://github.com/daniel-ospina/tortoise/issues/1402),
issue [#1407](https://github.com/daniel-ospina/tortoise/issues/1407)). This
package is the **single owner of ALL scenario content** — Tier-1 probes,
Tier-2 streams, and Tier-3 differential packs. Consumers (probes #1409,
streams #1411, differential #1412, parity #1413) load from here — never from
the authoring YAML.

## What is here

| File | Role |
|---|---|
| `corpus.yaml` | Authored source of truth (12 packs, 134 scenarios), **including gold answers** |
| `build_corpus.py` | Deterministic builder: validate → strip → seal → emit |
| `validate.py` | Shared per-scenario + cross-scenario validators (content bindings) |
| `corpus_loader.py` | Reader-safe loader (`load_corpus`, `Corpus.filter`, `GoldStore`, `render_reader_prompt`) |
| `schema.py` | Schema enums/constants — the single source, referenced by name |
| `corpus.json` | **Committed, pinned reader-facing artifact** (gold-free, `gold_sha256` per scenario + manifest) |
| `.gold_store/golds.json` | **Gitignored sealed gold store** (scorer path only) |

## Rebuild

```bash
uv run python -m battery.config.build_corpus          # in-place build
uv run python -m battery.config.build_corpus --check  # byte-diff vs committed (CI drift gate)
```

Determinism: two builds are byte-identical (canonical JSON, sorted keys, no
sets/timestamps/absolute paths; verified across `PYTHONHASHSEED` values in
`tests/test_battery_corpus.py`). Always invoke as `-m battery.config...` from
the repo root (package imports; script-path invocation is not supported).

## The seal model (S5)

* **Reader isolation, not cryptography.** Golds are authored in committed
  `corpus.yaml` (the corpus is the source of truth); the builder derives the
  gitignored store deterministically. The S5 flag is *reader-path leakage*:
  the agent under test must never receive gold answers.
* **Reader path:** `render_reader_prompt(scenario, session=None)` is the ONLY
  sanctioned agent-visible surface. It renders the arm-compatible prompt pack
  (and session content for multi-session packs) and **never** renders scorer
  metadata (`attack_type`, `hostile`, `gold_sha256`, `matched_control_for`,
  `variant_of`, `graph_script`). Adapters must never stringify scenario dicts.
* **Guards:** `assert_no_gold` (recursive, exact-key) runs at load AND at
  render; the builder strips golds and re-proves the emitted artifact is
  gold-free. `gold_sha256` never trips the walker (exact-key equality).
* **Tamper-evidence:** `corpus.json` manifest carries `golds_sha256` ==
  `GoldStore.digest()`; `verify_seal(corpus, store)` cross-checks them.
  `GoldStore` fails closed: `SealMissingError` (missing/corrupt store —
  rerun the builder), `SealMismatchError` (stale/tampered store),
  `StoreEntryMissingError` (unknown id).
* **Build-before-score:** a fresh clone's scorer must run the builder before
  scoring (tests rebuild hermetically into `tmp_path` — they never read the
  local gitignored store, which CI lacks).

## Digest recipe (for downstream #1406)

`golds_sha256` / `content_sha256` = `sha256(canonical_json(dict))` where
`canonical_json(obj) = json.dumps(obj, sort_keys=True, ensure_ascii=False,
separators=(",", ":")).encode("utf-8")`. **File bytes are never hashed** (the
indented files on disk are display format only).

## Downstream contract

* `run_artifact.json` (schema owner #1406) must record `corpus_version` +
  `content_sha256` from the manifest (S5 "pinned dataset versions"; the parity
  leg refuses on mismatch per epic plan §6). README prose is not the contract
  surface — the schema change is #1406's.
* Tier-3 pack selection must use `family`/`task_type`, never `tier` alone
  (`feedback_loop` is stream-shaped but Tier-3-scored; `adversarial` is
  differential-shaped).
* L4 cross-session delivery: `render_reader_prompt(scenario, session=N)` is
  the per-session form; the `session=None` accumulated full-history view is a
  **comparison/control surface**, not the L4 delivery default (per-session
  delivery + retrieval is owned by #1410).

## Threat-model note (file-read vector)

Committed `corpus.yaml` contains authored golds. Arm adapters must NOT grant
the agent-under-test repo read access to `battery/config` (folded into the S4
arm-isolation contract). Residual risk accepted: the agents under test are
sandboxed harness arms, not repo-exploring agents.

## Import/invocation convention

Run battery entry points as `uv run python -m battery.<mod>` from the repo
root (repo root on `sys.path`; tests use the same convention via
`tests/conftest.py`). Packaging `battery*` into the wheel is a #1406 concern.
