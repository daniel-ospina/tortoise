"""#2513 (delta-review P1): the re-injection total cap gates RESUME through
the REAL run path.

The rules tests in ``test_session_reinjection_rules.py`` exercise
``_resolve_reinjection_total_cap`` / ``_build_fingerprint`` /
``_save_checkpoint`` / ``_load_checkpoint`` directly. None of them calls
``run_evaluation``, so none of them exercised the SEAM between the resolver
and the fingerprint: deleting the threading line
(``reinjection_total_cap=reinjection_total_cap`` at the
``_build_fingerprint(...)`` call in ``run.py``) leaves ``fp10`` and ``fp15``
both WITHOUT the key, the key-union in ``_fingerprint_diffs`` finds no
difference, and every rules test STAYS GREEN while the defect reopens.

This module closes that seam end to end: a cap is recorded on a checkpoint
by a real ``run_evaluation``, and a real second run at a different cap must
REFUSE the stale checkpoint (``CheckpointStaleError``). The legitimate
same-cap resume stays green.

Runs fully offline (embedded FalkorDBLite graph, mocked reader/judge). The
per-question ingest outcome is deliberately NOT asserted here — the seam
under test is the resume gate, which runs before the question loop, and a
loaded CI box can fail a question's embedded-server start into a recorded
failure entry without touching the fingerprint contract (the #1988 class).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import run as runner
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _run(instances, tmp_path, checkpoint, *, cap):
    """One real run path at a given cap (arm ON), returning (outcomes, report).

    Every results-affecting knob but the cap is held identical, so the only
    fingerprint difference between the runs in a test is the cap itself.
    """
    return runner.run_evaluation(
        instances, reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        checkpoint=str(checkpoint), session_reinjection=True,
        reinjection_total_cap=cap)


def test_run_path_refuses_a_cap_change_on_resume(tmp_path):
    """The resolved cap the run path stamps GATES resume.

    Mutation (the reason this test exists): delete the
    ``reinjection_total_cap=reinjection_total_cap`` line from the
    ``_build_fingerprint(...)`` call in ``run_evaluation``. Both runs then
    build a fingerprint WITHOUT the key, the cap-15 run is silently accepted
    against the cap-10 checkpoint, and this test REDs on the missing
    ``CheckpointStaleError`` — before any key-existence assertion, so the
    RED is the semantic silent-acceptance, not an incidental KeyError.
    """
    instances = _mini()[:1]
    cp = tmp_path / "cp.json"

    # 1. arm ON, cap 10, THROUGH THE RUN PATH — the run writes its checkpoint
    #    through its own save path, stamped with the resolved cap.
    _outcomes, report = _run(instances, tmp_path, cp, cap=10)
    assert cp.is_file()
    # 2. THE DEFECT, asserted FIRST: a cap-15 resume must REFUSE this
    #    checkpoint. Two injection volumes must never blend into one
    #    artifact that declares one config. Under the mutation this call
    #    returns normally and the assertion below fails.
    with pytest.raises(runner.CheckpointStaleError) as ei:
        _run(instances, tmp_path, cp, cap=15)
    assert "session_reinjection_total_cap" in str(ei.value)

    # 3. the cap really rode the artifact the run wrote — checkpoint and
    #    report carry the SAME key name (the key-for-key cross-check that
    #    verifies this class by hand).
    on_disk = json.loads(cp.read_text(encoding="utf-8"))
    assert on_disk["fingerprint"]["session_reinjection_total_cap"] == 10
    assert report["methodology"]["session_reinjection_total_cap"] == 10

    # 4. the LEGITIMATE form stays GREEN: the same cap resumes the
    #    checkpoint (no refusal — the completed question, if any, is reused).
    _resumed, resumed_report = _run(instances, tmp_path, cp, cap=10)
    assert resumed_report["methodology"]["session_reinjection_total_cap"] == 10
