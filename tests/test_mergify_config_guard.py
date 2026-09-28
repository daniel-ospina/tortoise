"""Tests for tools/mergify_config_guard.py — the protection-invariant guard.

Spec: the #5215 merge-throughput plan §10 Task 4, §3 (protection invariants
I2/I2b/I3/I10/I11), §7 (TH4/TH6), §11 (S12).

Every numbered clause carries a NAMED FIXTURE (cycle 7), and the
load-bearing properties get a MUTATION PROOF: break the safety property and the
guard must go red. The guard is also run against the REAL tree here — that is
the green-on-landing proof, and it is asserted on the DIGEST, not on the wall
clock, so it cannot become a 7-day time bomb separate from the guard itself.

Hermetic: every tree is a scratch directory; the live I1 read is injected.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mergify_config_guard as mcg  # noqa: E402

SIX = [
    "docs",
    "legal-e2e",
    "license-surface",
    "pricing-artifact",
    "python-ci-gate",
    "test-isolation",
]
CHEAP_FIVE = [c for c in SIX if c != "python-ci-gate"]

DEFAULT_SETTINGS = """\
repository:
  branch-protection:
    - branch: main
      required_status_checks:
        strict: true
        contexts:
          - redis-guard
"""


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _dump(doc: object) -> str:
    return yaml.safe_dump(doc, sort_keys=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def merge_config(**overrides: object) -> dict:
    """The LIVE post-#5384 shape: `merge` injection, five cheap entry checks."""
    rule: dict = {
        "name": "main",
        "queue_conditions": [
            "base=main",
            "-draft",
            *[f"check-success={c}" for c in CHEAP_FIVE],
        ],
        "branch_protection_injection_mode": "merge",
        "merge_conditions": ["check-success=python-ci-gate"],
    }
    rule.update(overrides.pop("rule", {}))
    doc: dict = {"queue_rules": [rule], "merge_protections_settings": {"auto_merge_conditions": True}}
    doc.update(overrides)
    return doc


def queue_config(**overrides: object) -> dict:
    """The pre-#5384 shape: default (`queue`) injection, heavy check at entry."""
    rule: dict = {
        "name": "main",
        "queue_conditions": [
            "base=main",
            "-draft",
            *[f"check-success={c}" for c in SIX],
        ],
        "merge_conditions": ["check-success=python-ci-gate"],
    }
    rule.update(overrides.pop("rule", {}))
    doc: dict = {"queue_rules": [rule], "merge_protections_settings": {"auto_merge_conditions": True}}
    doc.update(overrides)
    return doc


def default_workflows(**overrides: object) -> dict:
    ci_jobs = {
        name: {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}
        for name in CHEAP_FIVE
    }
    py_jobs = {"python-ci-gate": {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}}
    py_jobs.update(overrides.pop("python_ci_jobs", {}))
    ci_jobs.update(overrides.pop("ci_jobs", {}))
    docs = {
        "ci.yml": {"on": "pull_request", "jobs": ci_jobs},
        "python-ci.yml": {"on": ["push", "pull_request"], "jobs": py_jobs},
    }
    docs.update(overrides.pop("extra", {}))
    return docs


def make_tree(
    tmp_path: Path,
    config: dict | str,
    *,
    workflows: dict | None = None,
    settings: str | None = DEFAULT_SETTINGS,
    record: bool = True,
    verified_days_ago: int = 0,
    record_overrides: dict | None = None,
    attrs: str | None = None,
    files: dict | None = None,
) -> Path:
    root = tmp_path / "repo"
    _write(root / ".mergify.yml", config if isinstance(config, str) else _dump(config))
    if workflows is None:
        workflows = default_workflows()
    for name, doc in workflows.items():
        _write(root / ".github/workflows" / name, doc if isinstance(doc, str) else _dump(doc))
    if settings is not None:
        _write(root / ".github/settings.yml", settings)
    if attrs is not None:
        _write(root / ".gitattributes", attrs)
    for rel, text in (files or {}).items():
        _write(root / rel, text)
    if record:
        write_record(root, verified_days_ago=verified_days_ago, overrides=record_overrides)
    return root


def write_record(root: Path, *, verified_days_ago: int = 0, overrides: dict | None = None) -> Path:
    record: dict = {
        "gate_digest": mcg.gate_digest(root),
        "required_contexts": sorted(SIX),
        "emitters": mcg._emitter_map(mcg._emitters(mcg._workflow_docs(root))),
        "verified_at": mcg._iso(mcg._now() - timedelta(days=verified_days_ago)),
        "read_sha": "0" * 40,
    }
    if overrides:
        record.update(overrides)
    path = root / mcg.RECORD_REL
    _write(path, json.dumps(record, indent=2))
    return path


def clause(root: Path, name: str) -> int:
    """Run one clause and return its exit code."""
    doc = mcg._load_mergify(root)
    record = mcg._load_record(root)
    docs = mcg._workflow_docs(root)
    if name == "i":
        return mcg._clause_i(doc)[0]
    if name == "ii":
        return mcg._clause_ii(doc)[0]
    if name == "iii":
        return mcg._clause_iii(doc)[0]
    if name == "iv":
        return mcg._clause_iv(doc, record)[0]
    if name == "v":
        return mcg._clause_v(doc)[0]
    if name == "vi":
        return mcg._clause_vi(doc, mcg._emitters(docs))[0]
    if name == "vii":
        return mcg._validate_union_protection(root, docs)[0]
    if name == "viii_a":
        return mcg._clause_viii_a(root, record)[0]
    if name == "viii_b":
        return mcg._clause_viii_b(root, record)[0]
    raise AssertionError(name)


# ---------------------------------------------------------------------------
# Green-on-landing: the REAL tree
# ---------------------------------------------------------------------------


def test_clause_inventory_is_pinned() -> None:
    """A clause cannot be deleted or renamed silently (#5649 bounds the residual)."""
    assert mcg.CLAUSE_IDS == ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii")


def test_real_tree_clauses_i_to_vii_pass() -> None:
    """Green-on-landing proof on the committed tree (Task 4 Step 1)."""
    for name in ("i", "ii", "iii", "iv", "v", "vi", "vii"):
        assert clause(ROOT, name) == 0, name


def test_real_tree_record_matches_head_digest() -> None:
    """I10 on the REAL tree, asserted on the DIGEST — never on the wall clock.

    The 7-day freshness is the guard's own, CI-enforced property; re-asserting
    it in the pytest corpus would make this file fail a week after it landed for
    no reason the file's own change caused.
    """
    record = mcg._load_record(ROOT)
    assert record is not None, f"{mcg.RECORD_REL} must exist"
    assert record["gate_digest"] == mcg.gate_digest(ROOT)
    assert sorted(record["required_contexts"]) == sorted(SIX)


def test_real_tree_i1_live_read_was_recorded() -> None:
    """I1's record is provenance: it must name the live six and its source sha."""
    record = mcg._load_record(ROOT)
    assert record is not None
    assert sorted(record.get("live_observed_contexts") or []) == sorted(SIX)
    assert record.get("live_result") == "SATISFIED"


# ---------------------------------------------------------------------------
# Clause fixtures (i)-(viii) — the plan's named fixture per clause
# ---------------------------------------------------------------------------


def test_clause_i_duplicate_check_within_one_list(tmp_path: Path) -> None:
    config = merge_config(rule={"queue_conditions": ["base=main", "-draft", "check-success=docs", "check-success=docs"]})
    root = make_tree(tmp_path, config)
    assert clause(root, "i") == 1


def test_clause_i_merge_conditions_lacking_python_ci_gate(tmp_path: Path) -> None:
    """The conjunct I1's `∅ == ∅` escape depends on."""
    config = merge_config(rule={"merge_conditions": ["check-success=docs"]})
    root = make_tree(tmp_path, config)
    assert clause(root, "i") == 1


def test_clause_ii_queue_mode_requires_merge_subset_of_queue(tmp_path: Path) -> None:
    """Fixture for the DEFAULT config state (mode absent => `queue`)."""
    config = queue_config(rule={"queue_conditions": ["base=main", "-draft"]})
    root = make_tree(tmp_path, config)
    assert clause(root, "ii") == 1


def test_clause_ii_merge_mode_requires_disjoint_lists(tmp_path: Path) -> None:
    """Fixture for the OTHER config state."""
    config = merge_config(
        rule={"queue_conditions": ["base=main", "-draft", "check-success=python-ci-gate"]}
    )
    root = make_tree(tmp_path, config)
    assert clause(root, "ii") == 1


def test_clause_ii_unknown_mode_fails_closed(tmp_path: Path) -> None:
    """⛔ An unrecognised injection mode is exit 2, never a fall-through to 0."""
    config = merge_config(rule={"branch_protection_injection_mode": "Merge"})
    root = make_tree(tmp_path, config)
    code, _ = mcg.run_static(root)
    assert code == 2


def test_clause_iii_check_pending_is_refused(tmp_path: Path) -> None:
    config = merge_config(rule={"queue_conditions": ["base=main", "check-pending=docs"]})
    root = make_tree(tmp_path, config)
    assert clause(root, "iii") == 1


def test_clause_iii_negative_check_failure_is_refused(tmp_path: Path) -> None:
    config = merge_config(rule={"merge_conditions": ["-check-failure=docs"]})
    root = make_tree(tmp_path, config)
    assert clause(root, "iii") == 1


def test_clause_iii_non_check_conditions_pass(tmp_path: Path) -> None:
    """`base=main` / `-draft` / a label are legitimate; an exception list would false-fail."""
    config = merge_config(rule={"queue_conditions": ["base=main", "-draft", "label=queue-accelerator", *[f"check-success={c}" for c in CHEAP_FIVE]]})
    root = make_tree(tmp_path, config)
    assert clause(root, "iii") == 0


def test_clause_iv_merge_mode_requires_cheap_entry_conditions(tmp_path: Path) -> None:
    config = merge_config(
        rule={"queue_conditions": ["base=main", "-draft", "check-success=docs"]}
    )
    root = make_tree(tmp_path, config)
    assert clause(root, "iv") == 1


def test_clause_iv_inactive_under_queue_mode(tmp_path: Path) -> None:
    root = make_tree(tmp_path, queue_config())
    assert clause(root, "iv") == 0


def test_clause_v_autoqueue_and_auto_merge_conditions_are_exclusive(tmp_path: Path) -> None:
    config = merge_config(rule={"autoqueue": True})
    root = make_tree(tmp_path, config)
    assert clause(root, "v") == 1


def test_clause_v_autoqueue_alone_is_fine(tmp_path: Path) -> None:
    """The deliberate non-use: `autoqueue` alone is not the violation."""
    config = merge_config(rule={"autoqueue": True})
    config["merge_protections_settings"] = {}
    root = make_tree(tmp_path, config)
    assert clause(root, "v") == 0


def test_clause_vi_check_named_in_no_pull_request_job(tmp_path: Path) -> None:
    workflows = default_workflows()
    del workflows["ci.yml"]["jobs"]["docs"]
    root = make_tree(tmp_path, merge_config(), workflows=workflows)
    assert clause(root, "vi") == 1


def test_clause_vi_push_only_workflow_does_not_emit(tmp_path: Path) -> None:
    workflows = default_workflows()
    workflows["ci.yml"]["on"] = "push"
    root = make_tree(tmp_path, merge_config(), workflows=workflows)
    assert clause(root, "vi") == 1


# --- clause (vii): the TH4 union validator ---------------------------------


VALIDATOR_SRC = """\
import sys

REGISTRIES = ("config/ci-surfaces.yml",)


def main() -> int:
    return 1


raise SystemExit(main())
"""

UNION_ATTRS = "config/ci-surfaces.yml merge=union\n"


def union_tree(
    tmp_path: Path,
    step_run: str,
    *,
    validator_src: str | None = VALIDATOR_SRC,
    container_ok: bool = True,
    step_if: str | None = None,
    step_env: dict | None = None,
    job_env: dict | None = None,
    workflow_shell: str | None = None,
) -> tuple[Path, dict]:
    step: dict = {"name": "Registry integrity", "run": step_run}
    if step_if is not None:
        step["if"] = step_if
    if step_env is not None:
        step["env"] = step_env
    manifest: dict = {"runs-on": "ubuntu-latest", "steps": [step]}
    if job_env is not None:
        manifest["env"] = job_env
    jobs = {"manifest-integrity": manifest}
    if container_ok:
        jobs["python-ci-gate"] = {
            "runs-on": "ubuntu-latest",
            "needs": ["manifest-integrity"],
            "steps": [{"run": "true"}],
        }
    workflows = default_workflows()
    workflows["python-ci.yml"]["jobs"].update(jobs)
    if workflow_shell is not None:
        workflows["python-ci.yml"]["defaults"] = {"run": {"shell": workflow_shell}}
    files = {}
    if validator_src is not None:
        files["tools/registry_integrity.py"] = validator_src
    root = make_tree(
        tmp_path,
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files=files,
    )
    return root, workflows


def test_clause_vii_no_union_no_validator_required(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    assert clause(root, "vii") == 0


def test_clause_vii_union_without_invocation_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "true")
    assert clause(root, "vii") == 1


def test_clause_vii_help_only_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py --help")
    assert clause(root, "vii") == 1


def test_clause_vii_shell_silenced_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py || exit 0")
    assert clause(root, "vii") == 1


def test_clause_vii_trailing_statement_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py\necho done")
    assert clause(root, "vii") == 1


def test_clause_vii_nested_gitattributes_is_not_missed(tmp_path: Path) -> None:
    """cycle 8(a): a nested attributes file must trigger the precondition.

    No validator is present, so if the nested file is SEEN the precondition is
    active and the clause is red; if it were missed, no union would be active and
    the clause would pass. That asymmetry is what makes this a proof.
    """
    root = make_tree(tmp_path, merge_config())
    _write(root / "docs" / ".gitattributes", UNION_ATTRS)
    assert clause(root, "vii") == 1


def test_clause_vii_single_fail_propagating_invocation_is_green(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py")
    assert clause(root, "vii") == 0


def test_clause_vii_noop_tool_not_naming_the_unioned_set_is_red(tmp_path: Path) -> None:
    """(c): a no-op tool satisfies the shape rule while validating nothing."""
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src="print('ok')\n"
    )
    assert clause(root, "vii") == 1


def test_clause_vii_python_c_payload_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "python3 -c 'exit(0)'")
    assert clause(root, "vii") == 1


def test_clause_vii_env_shadowing_is_red(tmp_path: Path) -> None:
    step = {
        "name": "Registry integrity",
        "run": "python3 tools/registry_integrity.py",
        "env": {"PYTHONPATH": "attacker"},
    }
    workflows = default_workflows()
    workflows["python-ci.yml"]["jobs"]["manifest-integrity"] = {
        "runs-on": "ubuntu-latest",
        "steps": [step],
    }
    workflows["python-ci.yml"]["jobs"]["python-ci-gate"] = {
        "runs-on": "ubuntu-latest",
        "needs": ["manifest-integrity"],
        "steps": [{"run": "true"}],
    }
    root = make_tree(
        tmp_path,
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 1


# --- clause (viii)(a): I10, the HEAD-COMPUTED digest -----------------------


def test_clause_viii_a_green_today(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    assert clause(root, "viii_a") == 0


def test_clause_viii_a_removed_draft_is_red(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    config = merge_config(
        rule={
            "queue_conditions": [
                "base=main",
                *[f"check-success={c}" for c in CHEAP_FIVE],
            ]
        }
    )
    _write(root / ".mergify.yml", _dump(config))
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_verified_at_only_restamp_after_change_is_red(tmp_path: Path) -> None:
    """A definition change + only a `verified_at` bump must NOT pass (cycle 10)."""
    root = make_tree(tmp_path, merge_config())
    record = json.loads((root / mcg.RECORD_REL).read_text())
    record["verified_at"] = mcg._iso(mcg._now())
    (root / mcg.RECORD_REL).write_text(json.dumps(record))
    config = merge_config(rule={"queue_conditions": ["base=main", "check-success=docs"]})
    _write(root / ".mergify.yml", _dump(config))
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_definition_change_with_recut_is_green(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    config = merge_config(rule={"queue_conditions": ["base=main", "-draft", "check-success=docs"]})
    _write(root / ".mergify.yml", _dump(config))
    assert mcg.run_recut(root)[0] == 0
    assert clause(root, "viii_a") == 0


def test_clause_viii_a_queue_rule_reorder_changes_digest(tmp_path: Path) -> None:
    """Order is semantic (first-match-wins), so it is in the digest."""
    first = merge_config()
    second = merge_config()
    second["queue_rules"] = [first["queue_rules"][0], dict(first["queue_rules"][0], name="other")]
    root = make_tree(tmp_path, second)
    reordered = dict(second)
    reordered["queue_rules"] = list(reversed(second["queue_rules"]))
    _write(root / ".mergify.yml", _dump(reordered))
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_second_emitter_yaml_is_in_the_digest(tmp_path: Path) -> None:
    """Cycle 10: a `*.yaml` emitter must be in the map, not just `*.yml`."""
    root = make_tree(tmp_path, merge_config())
    extra = {"on": "pull_request", "jobs": {"python-ci-gate": {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}}}
    _write(root / ".github" / "workflows" / "extra.yaml", _dump(extra))
    assert mcg._gate_projection(root)["emitters"]["python-ci-gate"] == [
        ["extra.yaml", "python-ci-gate"],
        ["python-ci.yml", "python-ci-gate"],
    ]
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_settings_change_changes_digest(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    _write(root / ".github/settings.yml", DEFAULT_SETTINGS.replace("redis-guard", "other"))
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_stale_verified_at_is_red(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config(), verified_days_ago=mcg.RECORD_FRESH_DAYS + 1)
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_duplicate_yaml_key_exits_2(tmp_path: Path) -> None:
    duplicate = (
        "queue_rules:\n"
        "  - name: main\n"
        "    name: other\n"
        "    queue_conditions: []\n"
        "    merge_conditions: []\n"
    )
    root = make_tree(tmp_path, duplicate, record=False)
    code, _ = mcg.run_static(root)
    assert code == 2


def test_clause_viii_a_absent_record_exits_2(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config(), record=False)
    code, _ = mcg.run_static(root)
    assert code == 2


# --- clause (viii)(b): I11, the declaration home ---------------------------


def test_clause_viii_b_new_reader_of_settings_is_red(tmp_path: Path) -> None:
    """Fixture for (1): a scratch repo whose workflow reads the file."""
    root = make_tree(tmp_path, merge_config())
    _write(
        root / ".github" / "workflows" / "reader.yml",
        _dump({"on": "pull_request", "jobs": {"r": {"runs-on": "ubuntu-latest", "steps": [{"run": "cat .github/settings.yml"}]}}}),
    )
    assert clause(root, "viii_b") == 1


def test_clause_viii_b_equality_inert_when_flag_absent(tmp_path: Path) -> None:
    """`.github/settings.yml` still declares `[redis-guard]`; the default is inert."""
    root = make_tree(tmp_path, merge_config())
    assert clause(root, "viii_b") == 0


def test_clause_viii_b_consistency_opt_in_enforces_equality(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path, merge_config(), record_overrides={"settings_home_consistent": True}
    )
    assert clause(root, "viii_b") == 1  # file declares redis-guard, record has six


# ---------------------------------------------------------------------------
# Result tokens (S12): SATISFIED / DIVERGED / UNAVAILABLE are distinguishable
# ---------------------------------------------------------------------------


def _static_code(root: Path) -> int:
    return mcg.run_static(root)[0]


def test_result_token_satisfied(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = make_tree(tmp_path, merge_config())
    assert mcg.main(["--static", "--root", str(root)]) == 0
    assert "STATIC RESULT: SATISFIED" in capsys.readouterr().out


def test_result_token_diverged(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config = merge_config(rule={"merge_conditions": ["check-success=docs"]})
    root = make_tree(tmp_path, config)
    assert mcg.main(["--static", "--root", str(root)]) == 1
    assert "STATIC RESULT: DIVERGED" in capsys.readouterr().out


def test_result_token_unavailable_is_distinct_from_diverged(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = make_tree(tmp_path, merge_config(), record=False)
    assert mcg.main(["--static", "--root", str(root)]) == 2
    out = capsys.readouterr().out
    assert "STATIC RESULT: UNAVAILABLE" in out
    assert "DIVERGED" not in out


# ---------------------------------------------------------------------------
# Live (I1) — injected fetcher, no network
# ---------------------------------------------------------------------------


def test_live_satisfied(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    code, lines = mcg.run_live(
        root,
        root / mcg.RECORD_REL,
        slug="o/r",
        fetch=lambda _: {"required_status_checks": {"contexts": SIX}},
        write=False,
    )
    assert code == 0, lines


def test_live_diverged(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    code, lines = mcg.run_live(
        root,
        root / mcg.RECORD_REL,
        slug="o/r",
        fetch=lambda _: {"required_status_checks": {"contexts": CHEAP_FIVE}},
        write=False,
    )
    assert code == 1, lines


def test_live_unavailable_when_read_fails(tmp_path: Path) -> None:
    def boom(_: str) -> object:
        raise mcg.GuardUnreadable("403")

    root = make_tree(tmp_path, merge_config())
    code, lines = mcg.run_live(
        root, root / mcg.RECORD_REL, slug="o/r", fetch=boom, write=False
    )
    assert code == 2, lines


def test_live_result_tokens_are_distinct() -> None:
    assert mcg.RESULT_TOKEN == {0: "SATISFIED", 1: "DIVERGED", 2: "UNAVAILABLE"}


def test_live_unavailable_does_not_overwrite_authoritative_fields(tmp_path: Path) -> None:
    """Fail-closed persistence: an UNAVAILABLE read is recorded as non-clean and
    must never overwrite the digest / required set a satisfied read established."""
    root = make_tree(tmp_path, merge_config())
    record_path = root / mcg.RECORD_REL
    before = json.loads(record_path.read_text())

    def boom(_: str) -> object:
        raise mcg.GuardUnreadable("403")

    mcg.run_live(root, record_path, slug="o/r", fetch=boom, write=True)
    after = json.loads(record_path.read_text())
    assert after["gate_digest"] == before["gate_digest"]
    assert after["required_contexts"] == before["required_contexts"]
    assert after["live_result"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Mutation proofs — break the safety property, the guard must go red
# ---------------------------------------------------------------------------


def test_mutation_shell_silencing_is_detected_by_the_shape_rule() -> None:
    """The property: a silenced validator is not a validator.

    The shape rule must reject every silencing idiom the workflow has used, and
    accept the single fail-propagating form.
    """
    assert mcg._single_command_run("python3 tools/registry_integrity.py") == [
        "python3",
        "tools/registry_integrity.py",
    ]
    for silenced in (
        "python3 tools/registry_integrity.py || true",
        "python3 tools/registry_integrity.py; exit 0",
        "python3 tools/registry_integrity.py && echo ok",
        "python3 tools/registry_integrity.py\necho done",
        "python3 tools/registry_integrity.py \\\n  --flag",
        "python3 tools/registry_integrity.py | tee out",
    ):
        assert mcg._single_command_run(silenced) is None, silenced


def test_mutation_removing_the_guard_step_would_remove_the_enforcement() -> None:
    """Mutation proof for the CI invocation: the guard step exists and is direct.

    If the step is deleted from the workflow, this test goes red — which is the
    observable difference between "enforced" and "a tool that exists".
    """
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-ci.yml").read_text())
    steps = workflow["jobs"]["manifest-integrity"]["steps"]
    matching = [s for s in steps if "mergify_config_guard.py" in (s.get("run") or "")]
    assert len(matching) == 1, "the guard must be invoked exactly once in manifest-integrity"
    step = matching[0]
    assert mcg._single_command_run(step["run"]) == [
        "python3",
        "tools/mergify_config_guard.py",
        "--static",
    ]
    assert not step.get("continue-on-error")
    assert not step.get("shell")
    assert not step.get("if")


def test_mutation_gate_definition_change_is_not_silent(tmp_path: Path) -> None:
    """The whole point of I10: any gate-definition change forces a re-cut.

    A mutation that carries the definition change WITHOUT the record change must
    be red; the re-cut restores green. This is the non-vacuity proof — a
    detector that passed both would be fail-vain.
    """
    root = make_tree(tmp_path, merge_config())
    before = mcg.gate_digest(root)
    config = merge_config(
        rule={
            "queue_conditions": [
                "base=main",
                *[f"check-success={c}" for c in CHEAP_FIVE],
            ]
        }
    )
    _write(root / ".mergify.yml", _dump(config))
    after = mcg.gate_digest(root)
    assert before != after, "the mutation must actually change the digest"
    assert clause(root, "viii_a") == 1
    assert mcg.run_recut(root)[0] == 0
    assert clause(root, "viii_a") == 0


def test_static_is_hermetic_head_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cycle 10: I10 is HEAD-computed — no base ref, no diff, no fetch, no shell.

    Proven by mutation: if any static path shelled out, this would raise.
    """
    root = make_tree(tmp_path, merge_config())

    def boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the static clauses must not shell out")

    monkeypatch.setattr(mcg.subprocess, "run", boom)
    assert mcg.run_static(root)[0] == 0


# ---------------------------------------------------------------------------
# Review-cycle regressions — the defects a fresh-context review found
# ---------------------------------------------------------------------------


def test_clause_i_negated_check_success_is_red(tmp_path: Path) -> None:
    """P0: `-check-success=python-ci-gate` requires the OPPOSITE of the invariant.

    Mergify's grammar is `[ "-" ] <attribute> ...`; stripping the `-` made an
    inverted merge gate say "python-ci-gate gates every merge".
    """
    config = merge_config(rule={"merge_conditions": ["-check-success=python-ci-gate"]})
    root = make_tree(tmp_path, config)
    assert clause(root, "i") == 1


def test_clause_iii_negated_check_success_is_red(tmp_path: Path) -> None:
    entry = [f"check-success={c}" for c in CHEAP_FIVE if c != "docs"]
    config = merge_config(rule={"queue_conditions": ["base=main", "-draft", "-check-success=docs", *entry]})
    root = make_tree(tmp_path, config)
    assert clause(root, "iii") == 1


def test_clause_iv_shrunk_record_is_red(tmp_path: Path) -> None:
    """P1: the record's required set must agree with the config's check set."""
    config = merge_config(rule={"queue_conditions": ["base=main", "-draft"]})
    root = make_tree(tmp_path, config, record_overrides={"required_contexts": ["python-ci-gate"]})
    assert clause(root, "iv") == 1


def test_clause_iv_cheap_set_cannot_be_empty(tmp_path: Path) -> None:
    config = merge_config(rule={"merge_conditions": [], "queue_conditions": ["base=main", "-draft"]})
    root = make_tree(tmp_path, config, record_overrides={"required_contexts": ["python-ci-gate"]})
    assert clause(root, "iv") == 1


def test_clause_vii_validator_outside_required_needs_is_red(tmp_path: Path) -> None:
    """§7: the validator must run in a job `python-ci-gate` reaches via `needs`."""
    workflows = default_workflows()
    workflows["python-ci.yml"]["jobs"]["stray"] = {
        "runs-on": "ubuntu-latest",
        "steps": [{"run": "python3 tools/registry_integrity.py"}],
    }
    root = make_tree(
        tmp_path,
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_job_continue_on_error_is_red(tmp_path: Path) -> None:
    step = {"name": "Registry integrity", "run": "python3 tools/registry_integrity.py"}
    workflows = default_workflows()
    workflows["python-ci.yml"]["jobs"]["manifest-integrity"] = {
        "runs-on": "ubuntu-latest",
        "continue-on-error": True,
        "steps": [step],
    }
    workflows["python-ci.yml"]["jobs"]["python-ci-gate"] = {
        "runs-on": "ubuntu-latest",
        "needs": ["manifest-integrity"],
        "steps": [{"run": "true"}],
    }
    root = make_tree(
        tmp_path, merge_config(), workflows=workflows, attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_step_if_push_is_red(tmp_path: Path) -> None:
    step = {
        "name": "Registry integrity",
        "run": "python3 tools/registry_integrity.py",
        "if": "github.event_name == 'push'",
    }
    workflows = default_workflows()
    workflows["python-ci.yml"]["jobs"]["manifest-integrity"] = {
        "runs-on": "ubuntu-latest",
        "steps": [step],
    }
    workflows["python-ci.yml"]["jobs"]["python-ci-gate"] = {
        "runs-on": "ubuntu-latest",
        "needs": ["manifest-integrity"],
        "steps": [{"run": "true"}],
    }
    root = make_tree(
        tmp_path, merge_config(), workflows=workflows, attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_paths_not_covering_the_union_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py --paths README.md")
    assert clause(root, "vii") == 1


def test_clause_vii_naming_only_without_a_failing_statement_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        validator_src='REGISTRIES = ("config/ci-surfaces.yml",)\n',
    )
    assert clause(root, "vii") == 1


def test_clause_viii_a_future_verified_at_is_red(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path, merge_config(), record_overrides={"verified_at": "2099-01-01T00:00:00Z"}
    )
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_second_queue_rule_is_red(tmp_path: Path) -> None:
    """The plan's named fixture: a second `queue_rule` => 1."""
    root = make_tree(tmp_path, merge_config())
    config = merge_config()
    first = config["queue_rules"][0]
    config["queue_rules"] = [first, dict(first, name="other")]
    _write(root / ".mergify.yml", _dump(config))
    assert clause(root, "viii_a") == 1


def test_clause_viii_a_allow_queue_branch_edit_changes_digest(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config())
    before = mcg.gate_digest(root)
    _write(root / ".mergify.yml", _dump(merge_config(rule={"allow_queue_branch_edit": True})))
    assert mcg.gate_digest(root) != before
    assert clause(root, "viii_a") == 1


def test_clause_viii_b_absent_settings_with_consistent_flag_exits_2(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        merge_config(),
        settings=None,
        record_overrides={"settings_home_consistent": True},
    )
    assert mcg.run_static(root)[0] == 2


def test_settings_reader_basename_collision_is_not_exempt(tmp_path: Path) -> None:
    """Exemption is by exact repo-relative path, never by basename."""
    root = make_tree(tmp_path, merge_config())
    _write(root / "tests" / "e2e" / "test_mergify_config_guard.py", "# reads .github/settings.yml\n")
    assert clause(root, "viii_b") == 1


def test_static_unexpected_error_exits_2(tmp_path: Path) -> None:
    """A malformed record must be UNAVAILABLE (2), never a traceback with exit 1."""
    root = make_tree(
        tmp_path, merge_config(), record_overrides={"required_contexts": ["python-ci-gate", 5]}
    )
    assert mcg.run_static(root)[0] == 2


def test_live_lhs_uses_only_positive_check_success(tmp_path: Path) -> None:
    """I1's LHS is `check-success` only — a `check-pending` name is not a gate."""
    config = merge_config(
        rule={
            "queue_conditions": [
                "base=main",
                "-draft",
                "check-pending=extra",
                *[f"check-success={c}" for c in CHEAP_FIVE],
            ]
        }
    )
    root = make_tree(tmp_path, config)
    code, lines = mcg.run_live(
        root,
        root / mcg.RECORD_REL,
        slug="o/r",
        fetch=lambda _: {"required_status_checks": {"contexts": SIX}},
        write=False,
    )
    assert code == 0, lines


# --- cycle-2 review regressions ---------------------------------------------


def test_clause_vii_shadow_gate_workflow_is_red(tmp_path: Path) -> None:
    """A second workflow defining a job id `python-ci-gate` must not shadow the
    real gate: the required gate has to be unique, so the ambiguity is red."""
    shadow_step = {"run": "python3 tools/registry_integrity.py"}
    workflows = default_workflows(
        python_ci_jobs={
            "manifest-integrity": {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]},
            "python-ci-gate": {
                "runs-on": "ubuntu-latest",
                "needs": ["manifest-integrity"],
                "steps": [{"run": "true"}],
            },
        },
        extra={
            "aaa-decoy.yml": {
                "on": "pull_request",
                "jobs": {
                    "python-ci-gate": {
                        "if": "github.event.pull_request == null",
                        "runs-on": "ubuntu-latest",
                        "steps": [shadow_step],
                    }
                },
            }
        },
    )
    root = make_tree(
        tmp_path,
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_step_if_not_pull_request_is_red(tmp_path: Path) -> None:
    """`!= 'pull_request'` never runs on a PR — an earlier substring test admitted it."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        step_if="github.event_name != 'pull_request'",
    )
    assert clause(root, "vii") == 1


def test_clause_vii_step_if_event_pull_request_null_is_red(tmp_path: Path) -> None:
    """`github.event.pull_request == null` is false on a PR — must be rejected."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        step_if="github.event.pull_request == null",
    )
    assert clause(root, "vii") == 1


def test_clause_vii_step_if_not_push_is_red(tmp_path: Path) -> None:
    """`!= 'push'` can reach PR, but a conditionally-run validator is not a
    gate: the fail-closed allow-list requires unconditional (or a benign form)."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        step_if="github.event_name != 'push'",
    )
    assert clause(root, "vii") == 1


def test_clause_vii_fail_marker_inside_a_string_is_red(tmp_path: Path) -> None:
    """A no-op tool whose only 'assert' is a literal message is not a validator."""
    src = '''\
MSG = "assert config/ci-surfaces.yml is fine"
print(MSG)
'''
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src=src
    )
    assert clause(root, "vii") == 1


def test_clause_vii_real_failing_statement_is_green(tmp_path: Path) -> None:
    """A genuine `return 1` / `sys.exit(1)` satisfies the source check."""
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py")
    assert clause(root, "vii") == 0


def test_settings_reader_concatenated_name_is_not_exempt(tmp_path: Path) -> None:
    """`"settings" + ".yml"` builds the same path and must still be a reader."""
    root = make_tree(tmp_path, merge_config())
    _write(root / "tools" / "evil.py", 'P = "settings" + ".yml"\n')
    assert clause(root, "viii_b") == 1


def test_settings_reader_glob_pattern_is_not_exempt(tmp_path: Path) -> None:
    """A glob naming the stem must still be detected."""
    root = make_tree(tmp_path, merge_config())
    _write(root / "tools" / "evil.py", 'import glob\nG = glob.glob(".github/settings*")\n')
    assert clause(root, "viii_b") == 1


def test_settings_reader_stray_pyc_outside_cache_is_scanned(tmp_path: Path) -> None:
    """A compiled module checked into the tree (outside `__pycache__`) is scanned."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / "tools" / "legacy.pyc").write_bytes(b"\x00\x01settings.yml\x00")
    assert clause(root, "viii_b") == 1


# --- cycle-3 review regressions ---------------------------------------------


def test_clause_vii_step_if_pull_request_target_is_red(tmp_path: Path) -> None:
    """`pull_request_target` is a DIFFERENT event: the step never runs on a PR."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        step_if="github.event_name == 'pull_request_target'",
    )
    assert clause(root, "vii") == 1


def test_clause_vii_step_if_bare_event_name_is_red(tmp_path: Path) -> None:
    """A bare `github.event_name` is truthy everywhere, but the allow-list admits
    only the exact PR predicate or a benign form — fail-closed over-rejection."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        step_if="github.event_name",
    )
    assert clause(root, "vii") == 1


def test_clause_vii_exit_zero_variants_are_red(tmp_path: Path) -> None:
    """`sys.exit(0)`, `SystemExit(0)`, and `assert <truthy>` never fail."""
    for src in (
        'import sys\nsys.exit(0)\n',
        'raise SystemExit(0)\n',
        'assert True\n',
        'def helper():\n    return "registry"\nprint(helper())\n',
    ):
        root, _ = union_tree(
            tmp_path, "python3 tools/registry_integrity.py", validator_src=src
        )
        assert clause(root, "vii") == 1, src


def test_clause_vii_system_exit_nonzero_is_green(tmp_path: Path) -> None:
    src = 'REGISTRIES = ("config/ci-surfaces.yml",)\nraise SystemExit(1)\n'
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src=src
    )
    assert clause(root, "vii") == 0


def test_clause_vii_pythonoptimize_shadow_is_red(tmp_path: Path) -> None:
    """`PYTHONOPTIMIZE=1` strips `assert`, neutralising the validator."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        step_env={"PYTHONOPTIMIZE": "1"},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_job_level_env_shadow_is_red(tmp_path: Path) -> None:
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        job_env={"PYTHONPATH": "attacker"},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_workflow_defaults_shell_is_red(tmp_path: Path) -> None:
    """A workflow-level `defaults.run.shell` silences every run step."""
    root, _ = union_tree(
        tmp_path,
        "python3 tools/registry_integrity.py",
        workflow_shell="bash -c 'exit 0' {0}",
    )
    assert clause(root, "vii") == 1


def test_clause_vii_superstring_path_is_red(tmp_path: Path) -> None:
    """`backup/config/ci-surfaces.yml.bak` is not the unioned file."""
    src = 'REG = "backup/config/ci-surfaces.yml.bak"\nraise SystemExit(1)\n'
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src=src
    )
    assert clause(root, "vii") == 1


def test_settings_reader_non_utf8_bytes_is_red(tmp_path: Path) -> None:
    """A Latin-1 file storing the ASCII name literally must still be detected."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / "tools" / "latin.py").write_bytes(
        b"# caf\xe9\nPATH = '.github/settings.yml'\n"
    )
    assert clause(root, "viii_b") == 1


# --- cycle-4 review regressions ---------------------------------------------


def test_clause_vii_negated_pr_contains_is_red(tmp_path: Path) -> None:
    """`!contains(..., 'pull_request')` runs on every event EXCEPT a PR."""
    for expr in (
        "!contains(github.event_name, 'pull_request')",
        "!(github.event_name == 'pull_request')",
        "contains(github.event_name, 'pull_request') == false",
        "github.event_name == 'pull_request' && false",
    ):
        root, _ = union_tree(
            tmp_path, "python3 tools/registry_integrity.py", step_if=expr
        )
        assert clause(root, "vii") == 1, expr


def test_clause_vii_dot_slash_path_is_green(tmp_path: Path) -> None:
    """`./config/ci-surfaces.yml` names the real file — the earlier lookbehind
    rejected it because it blocked a preceding `/`."""
    src = 'REG = "./config/ci-surfaces.yml"\nraise SystemExit(1)\n'
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src=src
    )
    assert clause(root, "vii") == 0


def test_clause_vii_unreferenced_function_failure_is_red(tmp_path: Path) -> None:
    """A fail statement in a never-called function is not an exit status."""
    for src in (
        'import sys\nREG = "config/ci-surfaces.yml"\ndef unused():\n    sys.exit(1)\n',
        'REG = "config/ci-surfaces.yml"\ndef remainder():\n    return 1\n',
    ):
        root, _ = union_tree(
            tmp_path, "python3 tools/registry_integrity.py", validator_src=src
        )
        assert clause(root, "vii") == 1, src


def test_live_non_dict_record_does_not_raise(tmp_path: Path) -> None:
    """A JSON array as the record must return a code, not raise TypeError."""
    root = make_tree(tmp_path, merge_config())
    (root / mcg.RECORD_REL).write_text("[]", encoding="utf-8")
    code, _ = mcg.run_live(
        root,
        root / mcg.RECORD_REL,
        slug="o/r",
        fetch=lambda _: {"required_status_checks": {"contexts": ["wrong"]}},
        write=True,
    )
    assert code == 1


# --- cycle-5 review regressions ---------------------------------------------


def test_clause_vii_conditional_ancestor_job_is_red(tmp_path: Path) -> None:
    """A skip cascades down `needs`: a conditional ancestor skips the validator."""
    step = {"run": "python3 tools/registry_integrity.py"}
    workflows = default_workflows(
        python_ci_jobs={
            "ancestor": {"runs-on": "ubuntu-latest", "if": "false", "steps": [{"run": "true"}]},
            "manifest-integrity": {
                "runs-on": "ubuntu-latest",
                "needs": ["ancestor"],
                "steps": [step],
            },
            "python-ci-gate": {
                "runs-on": "ubuntu-latest",
                "needs": ["manifest-integrity"],
                "steps": [{"run": "true"}],
            },
        }
    )
    root = make_tree(
        tmp_path,
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 1


def test_clause_vii_glob_union_pattern_expands(tmp_path: Path) -> None:
    """`.gitattributes` `config/** merge=union` names the real file."""
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py")
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "ci-surfaces.yml").write_text("x: 1\n", encoding="utf-8")
    (root / ".gitattributes").write_text("config/** merge=union\n", encoding="utf-8")
    assert clause(root, "vii") == 0


def test_clause_vii_lambda_failure_is_red(tmp_path: Path) -> None:
    """A lambda body's `sys.exit` is not a module exit status."""
    src = (
        'import sys\nREG = "config/ci-surfaces.yml"\n'
        "f = lambda: sys.exit(1)\n"
    )
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src=src
    )
    assert clause(root, "vii") == 1


# --- cycle-6 review regressions ---------------------------------------------


def test_clause_vii_falsy_if_is_red(tmp_path: Path) -> None:
    """`if: 0` / `if: ""` are falsy in Actions: the step is SKIPPED, not unconditional."""
    for expr in (0, "0", ""):
        root, _ = union_tree(
            tmp_path, "python3 tools/registry_integrity.py", step_if=expr
        )
        assert clause(root, "vii") == 1, expr


def test_clause_vii_sibling_conditional_does_not_red(tmp_path: Path) -> None:
    """Only the validator's OWN ancestor path matters: a sibling job with a
    path-filter `if:` that the gate needs cannot skip `manifest-integrity`."""
    workflows = default_workflows(
        python_ci_jobs={
            "manifest-integrity": {
                "runs-on": "ubuntu-latest",
                "steps": [{"run": "python3 tools/registry_integrity.py"}],
            },
            "test": {
                "runs-on": "ubuntu-latest",
                "if": "needs.changes.outputs.python == 'true'",
                "steps": [{"run": "true"}],
            },
            "python-ci-gate": {
                "runs-on": "ubuntu-latest",
                "if": "always()",
                "needs": ["manifest-integrity", "test"],
                "steps": [{"run": "true"}],
            },
        }
    )
    root = make_tree(
        tmp_path,
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root, "vii") == 0


def test_clause_vii_recursive_glob_requires_nested_files(tmp_path: Path) -> None:
    """`config/**` unions nested files too, so naming only the direct child reds."""
    root, _ = union_tree(tmp_path, "python3 tools/registry_integrity.py")
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "ci-surfaces.yml").write_text("x: 1\n", encoding="utf-8")
    (root / "config" / "sub").mkdir(parents=True, exist_ok=True)
    (root / "config" / "sub" / "extra.yml").write_text("y: 1\n", encoding="utf-8")
    (root / ".gitattributes").write_text("config/** merge=union\n", encoding="utf-8")
    assert clause(root, "vii") == 1


def test_nested_gitattributes_is_anchored(tmp_path: Path) -> None:
    """`docs/.gitattributes` with `*.yml` unions `docs/*.yml`, not the root's."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / ".gitattributes").write_text("*.yml merge=union\n", encoding="utf-8")
    (root / "docs" / "x.yml").write_text("a: 1\n", encoding="utf-8")
    (root / "root.yml").write_text("b: 1\n", encoding="utf-8")
    assert mcg._unioned_files(root) == {"docs/x.yml"}


def test_attr_macro_is_expanded(tmp_path: Path) -> None:
    """`[attr]m merge=union` + `config/ci-surfaces.yml m` unions the real file."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "ci-surfaces.yml").write_text("x: 1\n", encoding="utf-8")
    (root / ".gitattributes").write_text(
        "[attr]myunion merge=union\nconfig/ci-surfaces.yml myunion\n", encoding="utf-8"
    )
    assert mcg._unioned_files(root) == {"config/ci-surfaces.yml"}


def test_clause_vii_indirect_helper_failure_is_red(tmp_path: Path) -> None:
    """Reachability is transitive: a helper called only by a never-called function
    is not an exit status."""
    src = (
        'REG = "config/ci-surfaces.yml"\n'
        "def unused():\n    helper()\n"
        "def helper():\n    return 1\n"
        'print("ok")\n'
    )
    root, _ = union_tree(
        tmp_path, "python3 tools/registry_integrity.py", validator_src=src
    )
    assert clause(root, "vii") == 1


# --- cycle-7 review regressions ---------------------------------------------


def test_gitattributes_no_slash_matches_any_depth(tmp_path: Path) -> None:
    """A no-slash pattern matches at any depth (gitignore semantics)."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / ".gitattributes").write_text("*.yml merge=union\n", encoding="utf-8")
    (root / "top.yml").write_text("a: 1\n", encoding="utf-8")
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "sub" / "deep.yml").write_text("b: 1\n", encoding="utf-8")
    unioned = mcg._unioned_files(root)
    assert {"top.yml", "sub/deep.yml"} <= unioned


def test_gitattributes_nested_leading_slash_is_dir_anchored(tmp_path: Path) -> None:
    """`/x.yml` in `nest/.gitattributes` anchors to `nest/`, not the repo root."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / "nest").mkdir(parents=True, exist_ok=True)
    (root / "nest" / ".gitattributes").write_text("/x.yml merge=union\n", encoding="utf-8")
    (root / "nest" / "x.yml").write_text("a: 1\n", encoding="utf-8")
    (root / "x.yml").write_text("b: 1\n", encoding="utf-8")
    assert mcg._unioned_files(root) == {"nest/x.yml"}


def test_attr_macro_expands_transitively(tmp_path: Path) -> None:
    """`[attr]b a` where `a` is a macro must resolve to `merge=union`."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    (root / ".gitattributes").write_text(
        "[attr]a merge=union\n[attr]b a\nf.txt b\n", encoding="utf-8"
    )
    assert mcg._unioned_files(root) == {"f.txt"}


def test_attr_macro_is_inherited_by_nested_file(tmp_path: Path) -> None:
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / ".gitattributes").write_text("[attr]m merge=union\n", encoding="utf-8")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / ".gitattributes").write_text("*.yml m\n", encoding="utf-8")
    (root / "docs" / "x.yml").write_text("a: 1\n", encoding="utf-8")
    assert mcg._unioned_files(root) == {"docs/x.yml"}


def test_gitattributes_bracket_negation(tmp_path: Path) -> None:
    """`[!r]` is a NEGATED class in git; Python `re` needs `[^r]`."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / ".gitattributes").write_text("cfg/[!r]eg.yml merge=union\n", encoding="utf-8")
    (root / "cfg").mkdir(parents=True, exist_ok=True)
    (root / "cfg" / "!eg.yml").write_text("a: 1\n", encoding="utf-8")
    (root / "cfg" / "seg.yml").write_text("b: 1\n", encoding="utf-8")
    (root / "cfg" / "reg.yml").write_text("c: 1\n", encoding="utf-8")
    assert mcg._unioned_files(root) == {"cfg/!eg.yml", "cfg/seg.yml"}


def test_gitattributes_empty_class_does_not_raise(tmp_path: Path) -> None:
    """`[]` is git-legal (matches nothing) and must not raise `re.error`."""
    root = make_tree(tmp_path, merge_config(), files={"tools/keep.py": "x = 1\n"})
    (root / ".gitattributes").write_text("[] merge=union\n", encoding="utf-8")
    assert mcg._unioned_files(root) == set()


def test_clause_vii_explicit_null_if_is_red(tmp_path: Path) -> None:
    """A bare `if:` parses to null (falsy) — the step is skipped."""
    _root, workflows = union_tree(tmp_path, "python3 tools/registry_integrity.py")
    workflows["python-ci.yml"]["jobs"]["manifest-integrity"]["steps"][0]["if"] = None
    root2 = make_tree(
        tmp_path / "b",
        merge_config(),
        workflows=workflows,
        attrs=UNION_ATTRS,
        files={"tools/registry_integrity.py": VALIDATOR_SRC},
    )
    assert clause(root2, "vii") == 1


def test_static_reds_on_a_recorded_live_divergence(tmp_path: Path) -> None:
    """A stored `live_result: DIVERGED` (I1's whole point) must red `--static`."""
    root = make_tree(
        tmp_path, merge_config(), record_overrides={"live_result": "DIVERGED"}
    )
    assert mcg.run_static(root)[0] == 1
