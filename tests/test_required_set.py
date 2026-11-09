"""Pins for `tools/required_set` — the credential-free REQUIRED-set derivation.

WHY THESE ARE GATE PINS AND NOT UNIT TESTS
    `tools/required_set.py` decides WHAT COUNTS AS REQUIRED, and every readiness verdict
    is a statement about that set. A defect here is not "a bug in a helper": it is a
    fail-OPEN in the thing that certifies a merge, which this repo classes as the one
    failure a gate may never have. So each case below is written to RED if the protected
    behaviour is reverted, and the ones that matter say what to revert.

Hermetic: no DB, no network, no live `gh`. Two cases read THIS REPO's own tracked
`.mergify.yml` and `docs/ci/required-contexts.json` (the live set must be pinned against
what this repo actually declares); every other case builds its own `tmp_path` tree.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mergify_config_guard import (  # noqa: E402
    MERGIFY_REL,
    RECORD_REL,
    GuardUnreadable,
)
from tools.required_set import (  # noqa: E402
    RequiredSet,
    RequiredSetConfigError,
    RequiredSetUnreadable,
    derive_from_config,
    derive_from_ref,
)

#: The cheap entry checks this repo actually gates entry on.
ENTRY = ("docs", "test-isolation", "pricing-artifact")

#: The heavy suite, deliberately in `merge_conditions` — the shape #10991 is about.
HEAVY = "python-ci-gate"


def _rule(
    *, mode: str = "merge", queue: tuple[str, ...] = ENTRY, merge: tuple[str, ...] = (HEAVY,)
) -> dict[str, Any]:
    return {
        "name": "main",
        "queue_conditions": ["base=main", "-draft", *[f"check-success={c}" for c in queue]],
        "branch_protection_injection_mode": mode,
        "merge_conditions": [f"check-success={c}" for c in merge],
    }


#: Sentinel for "the caller said nothing", which is NOT the same as `None` ("write an
#: explicit null"). `live_observed_contexts` needs both: absent, explicit-null and
#: "defaults to `required_contexts`" are three different records.
_UNSET: Any = object()


def _tree(
    root: Path,
    *,
    rules: list[dict[str, Any]] | None = None,
    required_contexts: list[str] | None = None,
    emitters: dict[str, Any] | None = None,
    write_record: bool = True,
    live_result: str | None = "SATISFIED",
    live_observed_contexts: Any = _UNSET,
    live_verified_at: str | None = "2026-09-29T02:35:26Z",
) -> Path:
    """A minimal tree carrying `.mergify.yml` + the committed snapshot.

    `required_contexts` defaults to the union of the rules, so a case only names it when
    it is deliberately testing a DISAGREEMENT between the two sources.

    The LIVE VERDICT defaults to today's tracked shape — `live_result: SATISFIED`, an
    `live_observed_contexts` equal to `required_contexts`, and a dated
    `live_verified_at` — because that is what `mergify_config_guard.--live` actually
    writes on a clean read (`_record_live_attempt`), and a fixture that omitted it would
    model a state the repo cannot be in. Pass `live_result=None` (and
    `live_observed_contexts=None`) for a record PREDATING the I1 read, which is the one
    absence that must still derive.
    """
    rules = rules if rules is not None else [_rule()]
    root.mkdir(parents=True, exist_ok=True)
    (root / MERGIFY_REL).write_text(
        yaml.safe_dump({"queue_rules": rules, "merge_protections_settings": {}}), encoding="utf-8"
    )
    if write_record:
        if required_contexts is None:
            required_contexts = sorted(
                {
                    name
                    for rule in rules
                    for key in ("queue_conditions", "merge_conditions")
                    for cond in rule.get(key) or []
                    if isinstance(cond, str) and cond.startswith("check-success=")
                    for name in (cond.split("=", 1)[1],)
                }
            )
        record = root / RECORD_REL
        record.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"required_contexts": required_contexts}
        # `emitters` is written by `mergify_config_guard --recut`; a case that omits it is
        # modelling a record that predates that projection, which must DEGRADE (see
        # `test_a_record_without_emitters_degrades_to_unattributed`).
        if emitters is not None:
            payload["emitters"] = emitters
        # THE RECORD'S OWN LIVE VERDICT — the second fact a consumer must read, not just
        # the payload. `None` omits the key entirely (a pre-I1 record).
        if live_result is not None:
            payload["live_result"] = live_result
        if live_observed_contexts is _UNSET:
            payload["live_observed_contexts"] = list(required_contexts)
        elif live_observed_contexts is not None:
            payload["live_observed_contexts"] = live_observed_contexts
        if live_verified_at is not None:
            payload["live_verified_at"] = live_verified_at
        record.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return root


# ── the split this repo actually has ────────────────────────────────────────


def test_derives_this_repo_split_from_the_tracked_config() -> None:
    """The live tree: entry and merge-time are SEPARATE lists, and the record agrees.

    Revert the split (return one flat `names` list as `queue_names` too) and the
    assertion on `merge_names` reds.
    """
    required = derive_from_config(REPO_ROOT)
    assert required.mode == "merge"
    assert HEAVY not in required.queue_names
    assert HEAVY in required.merge_names
    assert set(required.names) == set(required.queue_names) | set(required.merge_names)
    assert required.snapshot_agrees is True


def test_a_merge_time_check_is_not_an_entry_check(tmp_path: Path) -> None:
    """The whole point: `merge_conditions` names must not be reported as entry gates.

    A consumer that treats them as entry reports NOT-READY on every PR head, because
    Mergify evaluates them on `mergify/merge-queue/<sha>` — not at the author's head.

    ⛔ THE EVIDENCE FOR THIS SPLIT IS NOT "python-ci-gate is never seen at a PR head".
    That datum ("0 of 6 open PR heads", 2026-11-04) was measured while the matrix was
    still running and is RETRACTED — the aggregate DOES report on author heads
    (`queued` 11097, `success` 11100, `failure` 11096). The split stands on a different
    and stronger ground: the check's ABSENCE at an author head is not self-explanatory,
    which is why `ready_for_owner._merge_time_absence` attributes it (a PR-triggered run
    of the emitting workflow on the head ⇒ FAIL; no such run ⇒ DEFERRED). Quoting the
    retracted count here would hand the next reader the one datum this PR exists to
    correct — see `tools/required_set`'s own docstring retraction.
    """
    required = derive_from_config(_tree(tmp_path))
    assert required.queue_names == ENTRY
    assert required.merge_names == (HEAVY,)


# ── the injection mode is READ, and it CHANGES THE ANSWER ──────────────────


def test_injection_mode_queue_folds_every_check_into_entry(tmp_path: Path) -> None:
    """`queue` injection means NOTHING is deferred: every required context gates entry.

    ⛔ THE DISCRIMINATING CASE FOR A FALSE PASS. Under `queue` the protected contexts are
    injected for queuing AND merging (`.mergify.yml` clause 4), so a consumer that
    deferred `merge_conditions` would say READY on a head the queue refuses at ENTRY.
    Revert the fold (`q, m = ..., ()` under mode == "queue") and this reds.
    """
    required = derive_from_config(_tree(tmp_path, rules=[_rule(mode="queue")]))
    assert required.mode == "queue"
    assert required.merge_names == ()
    assert set(required.queue_names) == set(ENTRY) | {HEAVY}


def test_an_overlap_is_legal_under_queue_and_an_error_under_merge(tmp_path: Path) -> None:
    """The mode decides whether "named in both lists" is a violation.

    Under `merge` clause 1 requires EXACTLY ONE list; under `queue` the guard REQUIRES
    `merge_conditions` ⊆ `queue_conditions`, so the overlap is the legal shape.
    """
    with pytest.raises(RequiredSetConfigError):
        derive_from_config(_tree(tmp_path / "a", rules=[_rule(queue=ENTRY, merge=(ENTRY[0],))]))
    legal = derive_from_config(
        _tree(tmp_path / "b", rules=[_rule(mode="queue", queue=ENTRY, merge=(ENTRY[0],))])
    )
    assert legal.mode == "queue"
    assert legal.merge_names == ()


def test_an_unknown_injection_mode_is_refused(tmp_path: Path) -> None:
    """A mode this module has not seen is not "probably merge"."""
    with pytest.raises(RequiredSetUnreadable):
        derive_from_config(_tree(tmp_path, rules=[_rule(mode="sometimes")]))


# ── the snapshot is a witness, and losing it must not be a shortcut ────────


def test_a_missing_record_is_a_refusal_not_an_empty_pass(tmp_path: Path) -> None:
    """Deleting the record must never be the cheapest route to a green set."""
    with pytest.raises(RequiredSetUnreadable):
        derive_from_config(_tree(tmp_path, write_record=False))


def test_an_empty_record_is_a_refusal(tmp_path: Path) -> None:
    with pytest.raises(RequiredSetUnreadable):
        derive_from_config(_tree(tmp_path, required_contexts=[]))


# ── the record's OWN live verdict is part of the witness ───────────────────


def test_a_diverged_live_read_is_refused_not_certified(tmp_path: Path) -> None:
    """⛔ A RECORD THAT SAYS THE LIVE READ DIVERGED MUST NOT CERTIFY READY.

    `mergify_config_guard._record_live_attempt` re-attests `required_contexts` ONLY on a
    `SATISFIED` read, so a `DIVERGED` record keeps the OLD payload — which still equals
    the config union, because the config is what the divergence is measured against.
    A consumer that checks only `set(derived) == set(required_contexts)` therefore sees
    PERFECT agreement and reports `snapshot_agrees: True` over a set the record itself
    says live protection no longer matches: the "silent deadlock" clause 2 exists to
    prevent, and the reviewer's exact reproduction.

    Measured on the PRE-FIX code at `58124337`: this fixture derives
    `('docs', 'test-isolation', 'python-ci-gate')` with `snapshot_agrees: True` and NO
    exception, while `live_observed_contexts` names `brand-new-required-check` — a
    required context the tool would never evaluate. `_live_attestation` reads
    `live_result` and this REDs; delete that call and it is green again.
    """
    with pytest.raises(RequiredSetConfigError) as excinfo:
        derive_from_config(
            _tree(
                tmp_path,
                live_result="DIVERGED",
                live_observed_contexts=["docs", "brand-new-required-check"],
            )
        )
    message = str(excinfo.value)
    assert "DIVERGED" in message
    # The remedy must be one that WORKS: `--recut` rewrites only gate_digest/emitters/
    # verified_at, so it can never re-attest a live read. Naming it only to rule it out is
    # the point, and the real remedy (`--live`) must be there.
    assert "--live" in message
    assert "does NOT fix this" in message


def test_an_unavailable_live_read_is_refused(tmp_path: Path) -> None:
    """`UNAVAILABLE` is NON-CLEAN, never "satisfied by default".

    `run_live` records `UNAVAILABLE` when no admin credential could make the read at all.
    That is `EXIT_UNAVAILABLE`, never `EXIT_OK`, and it leaves `required_contexts` stale
    exactly like `DIVERGED` — so the payload witness is no more trustworthy here. Same
    refusal, same remedy; the token is different and the message must still carry it.
    """
    with pytest.raises(RequiredSetConfigError) as excinfo:
        derive_from_config(_tree(tmp_path, live_result="UNAVAILABLE"))
    message = str(excinfo.value)
    assert "UNAVAILABLE" in message
    assert "--live" in message


def test_a_self_contradictory_record_is_refused(tmp_path: Path) -> None:
    """`SATISFIED` is not enough on its own: the two recorded sets must also AGREE.

    A `SATISFIED` read writes `required_contexts = live_observed_contexts` in the same
    write (`_record_live_attempt`), so a record where they differ was hand-edited or
    half-written. Neither list is then a witness, and picking one silently would let the
    edit that created the difference decide the gate. Revert the comparison arm and this
    reds with `snapshot_agrees: True`.
    """
    with pytest.raises(RequiredSetConfigError) as excinfo:
        derive_from_config(_tree(tmp_path, live_observed_contexts=[*ENTRY, "smuggled-in"]))
    message = str(excinfo.value)
    assert "smuggled-in" in message
    assert "required_contexts" in message


def test_a_record_predating_the_live_read_still_derives(tmp_path: Path) -> None:
    """⛔ THE COUNTERPART PIN: tolerance must not collapse into a blanket refusal.

    A record written before the I1 read has NO `live_result` at all — and there are real
    ones (`tests/test_ready_for_owner.py` builds such a record, and so does any tree that
    predates the field). Refusing there would demand an ADMIN credential to produce any
    verdict, which is the very 403 this module exists to remove, so absence is tolerated
    and DISCLOSED. Delete the `live is None` arm (falling through to the non-clean refusal)
    and this reds — which is why the pair is needed: without it, "refuse everything" would
    pass every other case here.
    """
    required = derive_from_config(
        _tree(tmp_path, live_result=None, live_observed_contexts=None, live_verified_at=None)
    )
    assert required.names == (*ENTRY, HEAVY)
    assert required.snapshot_agrees is True
    assert "no recorded live verdict" in required.source


def test_a_non_string_live_verdict_is_unreadable(tmp_path: Path) -> None:
    """A present-but-unreadable verdict is NOT the tolerated absence. Fail closed.

    An explicit `null` (or a number) is the shape a half-written or hand-edited record
    produces. Reading it as "no verdict recorded" would make a corrupt field
    indistinguishable from a clean pre-I1 record, which is the wrong direction for a
    gate — so it is UNREADABLE ("I could not read this"), never a PASS.
    """
    _tree(tmp_path)
    record = tmp_path / RECORD_REL
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["live_result"] = None
    record.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(RequiredSetUnreadable) as excinfo:
        derive_from_config(tmp_path)
    assert "live_result" in str(excinfo.value)


def test_the_live_verdict_is_disclosed_with_its_age() -> None:
    """⛔ THIS REPO'S TRACKED RECORD MUST STILL DERIVE — and must say what it rests on.

    `snapshot_agrees: True` reads to a human as "the required set was verified". It is not:
    it says the CONFIG agrees with the record's payload, and the attestation behind that
    payload has an age. So the age is DISCLOSED against the guard's own window (never
    enforced — enforcing it would put an admin credential in the gate's critical path) and
    the verdict is named, so a 38-day-old read cannot be mistaken for a fresh one.

    This is also the pin that keeps the refusal from eating today's tree: if the tracked
    record ever stops deriving, this REDs and names it, instead of the whole instrument
    silently refusing every PR.
    """
    required = derive_from_config(REPO_ROOT)
    record = json.loads((REPO_ROOT / RECORD_REL).read_text(encoding="utf-8"))
    assert record["live_result"] == "SATISFIED"
    assert sorted(record["live_observed_contexts"]) == sorted(record["required_contexts"])
    assert "live read SATISFIED at " in required.source
    assert record["live_verified_at"] in required.source
    assert re.search(r"\(\d+d old, window \d+d", required.source), required.source


def test_the_record_is_read_once_per_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The payload, the emitters and the live verdict must come from the SAME bytes.

    Three fields of ONE attestation read by three separate `read_text` calls can pair the
    PAYLOAD of one record with the VERDICT of another — and the verdict is now
    load-bearing (`_live_attestation`), so a torn read is a real fail-open rather than a
    cosmetic inconsistency. Revert `_read_record` to per-field reads and the count below
    becomes 3.
    """
    _tree(tmp_path, emitters={HEAVY: [["python-ci.yml", HEAVY]]})
    record = tmp_path / RECORD_REL
    reads: list[str] = []
    real_read_text = Path.read_text

    def counting_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == record:
            reads.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    required = derive_from_config(tmp_path)
    # Both projections still resolved, so the single read is a real one.
    assert required.emit_workflows(HEAVY) == ("python-ci.yml",)
    assert required.snapshot_agrees is True
    assert len(reads) == 1, f"the committed record was read {len(reads)} times, not once"


def test_a_context_the_record_names_and_the_config_dropped_is_a_config_error(
    tmp_path: Path,
) -> None:
    """THE #3016 REGRESSION. Delete one `check-success=` line and the derived set silently
    shrinks; the record is the only witness that it is no longer the required set.

    The dangerous direction is the RECORD naming a context the CONFIG does not derive —
    a name the tool knows is required and would never evaluate. Treat this as a note (or
    drop the refusal) and the tool reports READY over a red required check.
    """
    dropped = ["docs", "test-isolation", "pricing-artifact", HEAVY, "retired-but-required"]
    with pytest.raises(RequiredSetConfigError) as excinfo:
        derive_from_config(_tree(tmp_path, required_contexts=dropped))
    assert "retired-but-required" in str(excinfo.value)


def test_an_unreadable_config_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / MERGIFY_REL).write_text("queue_rules: [[[\n", encoding="utf-8")
    with pytest.raises(GuardUnreadable):
        derive_from_config(tmp_path)


# ── the protected ref, not the tree being judged ───────────────────────────


def _git_env() -> dict[str, str]:
    """Host-independent env for the fixtures' own git calls.

    ⛔ THE THROWAWAY REPOSITORIES ARE NOT HERMETIC BY DEFAULT. Every `git` call here
    would otherwise inherit the caller's environment and global config, and BOTH
    failure modes are real on this fleet:

      1. a global `commit.gpgsign = true` (a routine developer setting) or a global
         `core.hooksPath` (this harness sets one) makes `git commit` exit non-zero,
         so `check=True` turns it into `CalledProcessError` and these tests read as
         an ERROR rather than as a finding;
      2. an inherited `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` retargets every
         call at a REAL repository — `git init -b main` reinitializes it, `git add -A`
         stages the fixture's files into ITS index and `git commit` writes them onto a
         live branch, with `check=True` reporting nothing but success.

    So: every inherited `GIT_*` is dropped and both config files are pointed at
    `os.devnull` — the same shape as `tests/test_measure_ask_distilled.py::_git_env`,
    `tests/test_who_is_on.py::_git_env` and `tests/test_lane_stash.py::_env`.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env=_git_env(),
    )


def _git_out(cwd: Path, *args: str) -> str:
    """Read from a fixture repository under the same hermetic env as `_git`."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    ).stdout.strip()


def test_the_git_fixture_ignores_a_hostile_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ A CONFIGURED HOST MUST NOT TURN THIS SUITE RED.

    `_git` builds a throwaway repository and commits into it. If it inherited the
    caller's global config, a routine developer setting — `commit.gpgsign = true`, or
    a `core.hooksPath` (this harness sets one) whose hook exits non-zero — makes
    `git commit` fail, and `check=True` reports that as `CalledProcessError`: an
    ERROR in this suite, which reads as a finding about `tools/required_set` when it
    is an artefact of the host. Both are set here at once; the fixture must be
    unaffected.

    Revert `_git` to `subprocess.run(...)` without `env=`/`commit.gpgsign=false` and
    this reds on any host that has either setting.
    """
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\necho 'host hook ran inside the fixture' >&2\nexit 1\n", encoding="utf-8"
    )
    hook.chmod(0o755)
    hostile = tmp_path / "hostile.gitconfig"
    hostile.write_text(
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tprogram = /bin/false\n"
        f"[core]\n\thooksPath = {hooks}\n"
        "[init]\n\tdefaultBranch = trunk\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))

    fixture = tmp_path / "fixture"
    _tree(fixture)
    _git(fixture, "init", "-b", "main")
    _git(fixture, "add", "-A")
    _git(fixture, "commit", "-m", "config")

    # The commit landed, with the hostile config never consulted and no host hook run.
    assert _git_out(fixture, "log", "--format=%s").splitlines() == ["config"]
    assert MERGIFY_REL in _git_out(fixture, "ls-files").split()


def test_derive_from_ref_ignores_a_branch_that_shrinks_its_own_set(tmp_path: Path) -> None:
    """⛔ A GATE THAT READS ITS OWN SUBJECT IS DEFEATABLE BY ITS SUBJECT.

    A PR can edit `.mergify.yml` AND `docs/ci/required-contexts.json` in one diff, so the
    two agree with each other and the witness certifies the shrunken set it was just
    changed to. Verified here by shrinking BOTH in the working tree: `derive_from_config`
    happily reports the smaller set (that is why it must not be the production read),
    while `derive_from_ref("HEAD")` still reports the committed one.

    Revert `_read_required_contexts` to read the workdir and this is the bypass.
    """
    (tmp_path / MERGIFY_REL).parent.mkdir(parents=True, exist_ok=True)
    _tree(tmp_path, required_contexts=sorted({*ENTRY, HEAVY}))
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")

    # Now the "PR": drop `docs` from the config AND from the record, in one change.
    shrunk = [*ENTRY[1:], HEAVY]
    _tree(tmp_path, rules=[_rule(queue=ENTRY[1:])], required_contexts=sorted(shrunk))

    assert "docs" not in derive_from_config(tmp_path).names  # the bypass, if used
    assert "docs" in derive_from_ref(tmp_path, "HEAD").names  # the committed truth


def test_derive_from_ref_refuses_an_unreadable_ref(tmp_path: Path) -> None:
    """A ref that cannot be read is a REFUSAL — never a fall back to the workdir copy."""
    _tree(tmp_path)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")
    with pytest.raises(RequiredSetUnreadable) as excinfo:
        derive_from_ref(tmp_path, "origin/does-not-exist")
    assert "origin/does-not-exist" in str(excinfo.value)


def test_the_git_fixture_cannot_write_into_an_inherited_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ THE DANGEROUS HALF: AN INHERITED `GIT_DIR` MUST NOT REDIRECT THE FIXTURE.

    With `GIT_DIR` set in the caller's environment, a non-hermetic `_git` sends
    `git init`/`add`/`commit` at a REAL repository: the fixture's `.mergify.yml` and
    `docs/ci/required-contexts.json` get staged into that checkout's index and
    committed onto its branch, and `check=True` reports success. This is the class of
    accident `tests/test_lane_stash.py::_env` was hardened against.

    Revert `_git` to inherit the environment and the decoy's HEAD moves.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, "init", "-b", "main")
    (decoy / "real.txt").write_text("real work\n", encoding="utf-8")
    _git(decoy, "add", "-A")
    _git(decoy, "commit", "-m", "real work")
    decoy_head = _git_out(decoy, "rev-parse", "HEAD")
    decoy_files = _git_out(decoy, "ls-files").split()

    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))

    fixture = tmp_path / "fixture"
    _tree(fixture)
    _git(fixture, "init", "-b", "main")
    _git(fixture, "add", "-A")
    _git(fixture, "commit", "-m", "config")

    # The fixture committed into ITSELF (its own git dir, its own files)…
    assert Path(_git_out(fixture, "rev-parse", "--show-toplevel")).resolve() == fixture.resolve()
    assert MERGIFY_REL in _git_out(fixture, "ls-files").split()
    assert _git_out(fixture, "log", "--oneline")
    # …and the real repository the environment pointed at is untouched.
    assert _git_out(decoy, "rev-parse", "HEAD") == decoy_head
    assert _git_out(decoy, "ls-files").split() == decoy_files


# ── an empty set is never a pass ───────────────────────────────────────────


def test_a_config_with_no_positive_check_condition_is_refused(tmp_path: Path) -> None:
    """A readable config that names no `check-success=` is refused — and the MESSAGE must
    be this arm's, not the record's.

    ⛔ This case previously passed `write_record=False`, which made the MISSING-RECORD arm
    raise the same exception class — so `if not union:` → `if False:` stayed GREEN (the
    exact non-discriminating shape round 3 caught). It now supplies a readable EMPTY record
    and matches the message, so the mutant reds on the record arm's different wording.
    """
    rule = _rule(queue=(), merge=())
    with pytest.raises(RequiredSetUnreadable, match="no positive"):
        derive_from_config(_tree(tmp_path, rules=[rule], required_contexts=[]))


def test_the_result_is_frozen() -> None:
    """A verdict input that a consumer can mutate is not a verdict input."""
    required: RequiredSet = derive_from_config(REPO_ROOT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        required.names = ()  # type: ignore[misc]


def test_a_context_the_config_derives_and_the_record_lacks_also_refuses(tmp_path: Path) -> None:
    """⛔ BOTH DIVERGENCE DIRECTIONS REFUSE. Clause 1 is an EQUALITY, and the CI guard
    (`mergify_config_guard._clause_iv`) compares the same two sets for that reason.

    This is the config-ONLY direction, so the record must be a strict SUBSET of the
    derived union. (An earlier version of this case passed a SUPERSET record, which made
    the snapshot-only arm fire first and left the config-only arm completely unpinned —
    the mutation `if config_only:` → `if False:` stayed GREEN.) Revert the config-only
    raise to a note and this reds on the derived name.
    """
    with pytest.raises(RequiredSetConfigError) as excinfo:
        derive_from_config(_tree(tmp_path, required_contexts=list(ENTRY)))
    message = str(excinfo.value)
    assert HEAVY in message
    # The remedy must be one that WORKS. `--recut` rewrites only gate_digest/emitters/
    # verified_at, so it must never be presented as the fix — naming it only to rule it
    # out is the point, and the real remedy (`--live`) must be there.
    assert "--live" in message
    assert "does NOT fix this" in message


def test_derive_from_ref_discloses_the_COMMIT_for_an_annotated_tag(tmp_path: Path) -> None:
    """`git rev-parse --verify <ref>` returns the TAG OBJECT for an annotated tag, so a
    disclosure built on it would name an object that is not a commit — and the provenance
    line exists to name the commit whose tree was read.

    (The blob reads are pinned to the resolved `sha`, and git peels `<rev>:<path>` itself,
    so the defect this pins is the WRONG NAME in the disclosure, not different bytes.)

    Remove `^{commit}` from the `rev-parse` call and this reds (the disclosed sha becomes
    the tag's).
    """
    _tree(tmp_path)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")
    _git(tmp_path, "-c", "user.email=t@example.com", "tag", "-a", "cfg4", "-m", "tagged")
    commit = _git_out(tmp_path, "rev-parse", "--verify", "HEAD")
    tag_object = _git_out(tmp_path, "rev-parse", "--verify", "cfg4")
    assert commit != tag_object, "an annotated tag must not point at itself"
    required = derive_from_ref(tmp_path, "cfg4")
    assert commit[:12] in required.source
    assert tag_object[:12] not in required.source


def test_derive_from_ref_discloses_the_sha_it_read(tmp_path: Path) -> None:
    """Resolving a ref (`git rev-parse`) does not fetch, so freshness cannot be asserted —
    it must be DISCLOSED. The sha in `source` is the only honest statement about what was
    read.

    Drop the `git rev-parse` and the `source` loses the sha; a stale protected ref then
    reads exactly like a fresh one.
    """
    _tree(tmp_path)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")
    expected = _git_out(tmp_path, "rev-parse", "--verify", "HEAD")
    required = derive_from_ref(tmp_path, "HEAD")
    assert expected[:12] in required.source
    assert "HEAD" in required.source


def test_derive_from_ref_refuses_a_non_utf8_blob(tmp_path: Path) -> None:
    """A blob that will not decode is a REFUSAL, not a traceback.

    `subprocess.run(text=True)` raises `UnicodeDecodeError` (a `ValueError`); it is not an
    `OSError`, so without `ValueError` in the handler it escaped a function documented to
    return a set or refuse.
    """
    _tree(tmp_path)
    raw = b"queue_rules: []\n\xff\xfe\x00"
    blob = tmp_path / MERGIFY_REL
    blob.write_bytes(raw)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")
    with pytest.raises((RequiredSetUnreadable, GuardUnreadable)):
        derive_from_ref(tmp_path, "HEAD")


# ── the emitter projection: WHICH WORKFLOW should have posted the check ─────


def test_emit_files_project_the_records_emitters_to_workflow_files(tmp_path: Path) -> None:
    """`emitters: name -> [[workflow_file, job_key]]` becomes `name -> (workflow_file,)`.

    A consumer needs the FILE, because that is the only handle an Actions RUN offers
    (`workflow_runs[].path` is `.github/workflows/<file>`). Revert the projection to return
    the `[file, job]` pairs unsplit and this reds.
    """
    _tree(
        tmp_path,
        emitters={
            HEAVY: [["python-ci.yml", HEAVY]],
            "docs": [["ci.yml", "docs"], ["docs-copy.yml", "docs"]],
        },
    )
    required = derive_from_config(tmp_path)
    assert required.emit_workflows(HEAVY) == ("python-ci.yml",)
    # Several workflows may emit one effective name — all of them are attribution.
    assert required.emit_workflows("docs") == ("ci.yml", "docs-copy.yml")
    # A name the record does not attribute is `()` — a real answer, never "nothing emits it".
    assert required.emit_workflows("never-heard-of-it") == ()


def test_a_record_without_emitters_degrades_to_unattributed(tmp_path: Path) -> None:
    """An older record must not become a REFUSAL — but it must not be read as attribution.

    `emitters` is not the witness the required set rests on (`required_contexts` is), so
    its absence cannot turn a readable record into an unreadable one. It must still be
    visibly EMPTY, because the consumer's rule is "no attribution => FAIL, never DEFER":
    a degrade that returned a guessed workflow file would make the fail-open invisible.
    """
    _tree(tmp_path)  # no `emitters` key at all
    required = derive_from_config(tmp_path)
    assert required.snapshot_agrees is True
    assert required.emit_files == ()
    assert required.emit_workflows(HEAVY) == ()


def test_emit_files_are_read_from_the_protected_ref(tmp_path: Path) -> None:
    """The attribution follows the PROTECTED ref, like every other byte in this module.

    Attribution is what decides DEFERRED (a PASS arm), so a branch that rewrote
    `emitters` to point at a workflow that never runs on a PR head would otherwise be
    able to switch the merge-time check's absence from FAIL to DEFERRED in the same diff
    it uses to judge itself.
    """
    (tmp_path / MERGIFY_REL).parent.mkdir(parents=True, exist_ok=True)
    _tree(tmp_path, emitters={HEAVY: [["python-ci.yml", HEAVY]]})
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")

    # The "PR": re-point the attribution at a workflow nothing runs.
    _tree(tmp_path, emitters={HEAVY: [["never-runs.yml", HEAVY]]})

    assert derive_from_config(tmp_path).emit_workflows(HEAVY) == ("never-runs.yml",)
    assert derive_from_ref(tmp_path, "HEAD").emit_workflows(HEAVY) == ("python-ci.yml",)


def test_the_protected_refs_workflow_listing_travels_with_the_set(tmp_path: Path) -> None:
    """⛔ THE `emitters` PROJECTION IS ONLY READABLE AGAINST THE FILES THAT EXIST (#11175).

    A consumer decides a merge-time context's ABSENCE by matching the record's recorded
    workflow FILE against the runs on a head. A workflow RENAME leaves the record naming a
    file that no longer exists — and a spelling that matches no run reads as "the emitter did
    not run here", i.e. DEFERRED (a PASS). So the set carries the workflow listing of the
    tree it was read from, and the consumer separates *exists-and-did-not-run* from
    *does-not-exist-any-more*.

    ⛔ THE LISTING MUST COME FROM THE PROTECTED REF, not from the temp root the derivation
    materialises: that root carries only `.mergify.yml` and the record, so listing it answers
    `()` — "this repo has no workflow files" — for EVERY repository, which would make every
    recorded emitter look stale and refuse the whole fleet. Revert
    `derive_from_ref(..., workflow_files=_workflow_files_at(...))` and the first assertion
    below reads `()`.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "python-ci.yml").write_text("name: ci\n", encoding="utf-8")
    _tree(tmp_path, emitters={HEAVY: [["python-ci.yml", HEAVY]]})
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "config")

    # The "PR": the emitting workflow is RENAMED in the workdir, uncommitted.
    (workflows / "python-ci.yml").rename(workflows / "python-suite.yml")

    committed = derive_from_ref(tmp_path, "HEAD")
    assert committed.workflow_files == ("python-ci.yml",)  # the ref's tree, not the workdir's
    assert committed.emit_workflows(HEAVY) == ("python-ci.yml",)  # the projection still resolves
    # …while the same derivation on the WORKDIR sees the rename, which is what makes the two
    # comparable: the consumer's question is "does this spelling exist at the ref I read?".
    assert derive_from_config(tmp_path).workflow_files == ("python-suite.yml",)


def test_a_tree_without_workflows_is_an_EMPTY_measurement_not_an_unreadable_one(
    tmp_path: Path,
) -> None:
    """⛔ `()` AND `None` ARE DIFFERENT ANSWERS. A tree that carries no workflow files cannot
    emit any check-run, so a recorded emitter there is stale — a MEASUREMENT. An unreadable
    directory is a read failure and must not be graded as that measurement.

    (The `None` arm is exercised by the consumer pins in `tests/test_ready_for_owner.py`,
    which assert a `None` listing is UNATTRIBUTABLE rather than a deferral.)
    """
    _tree(tmp_path)
    assert derive_from_config(tmp_path).workflow_files == ()


# ── the SECOND derivation in this repo, and the pin between them ────────────
#
# `.github/scripts/check-required-set.py` (#6144) already parses `.mergify.yml` for the
# queue's two condition lists, and `tools/required_set.py` now parses the same file for the
# same purpose. Two independent parsers of one config is a drift surface: nothing about the
# first makes the second right, and a divergence would answer "what is required?" two ways
# with no test to say which. This pin compares them on THIS REPO's own config — the one
# tree where both are known to have a verdict.

GUARD_SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-required-set.py"


def _load_required_set_guard() -> Any:
    """The #6144 guard module, imported by path — it is a script, not an importable module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_required_set_pin", GUARD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_two_derivations_of_the_required_set_agree_on_this_repo() -> None:
    """★ TWO PARSERS OF `.mergify.yml` must answer the same question the same way.

    `tools.required_set` (this PR) and `.github/scripts/check-required-set.py` (#6144) each
    read `queue_rules[].queue_conditions` / `merge_conditions` for the `check-success=`
    names, and neither is derived from the other. Agreement is therefore a MEASUREMENT, not
    a tautology — and it is the measurement the PR body cited while no test made it.

    ⛔ NOT VACUOUS: this reds if either parser's notion of "a check-success condition"
    diverges — e.g. `required_set` de-duplicating, dropping a rule's second list, or
    matching the prefix differently from the guard. It compares the two parsers, not one
    parser against itself.
    """
    guard = _load_required_set_guard()
    ours = derive_from_config(REPO_ROOT)
    # ⛔ THE TRACKED FILE, NAMED EXPLICITLY. `load_mergify()` resolves its path from the
    # `MERGIFY_CONFIG` env seam at CALL time, so an exported `MERGIFY_CONFIG` would decide
    # what this pin compares — a false RED on a correct tree (measured: a copy of the file
    # with one `check-success=` line removed reds this pin). `test_required_set_sync.py`
    # clears that seam for the same reason; here the path is simply supplied, which is the
    # stricter form.
    theirs = guard.load_mergify(REPO_ROOT / ".mergify.yml")
    # `derive_from_config` FOLDS `merge` into `entry` under mode `queue`, so state the mode
    # the comparison is valid under rather than assuming one.
    assert ours.mode == "merge", (
        "this pin compares the two flat lists, which is only the derived shape under "
        f"mode=merge; this repo is now mode={ours.mode!r} — extend the pin, do not delete it"
    )
    assert set(ours.queue_names) == theirs["queue"]
    assert set(ours.merge_names) == theirs["merge"]
