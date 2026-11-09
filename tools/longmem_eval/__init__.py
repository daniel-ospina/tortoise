"""LongMemEval external comparability runner (issue #1144, axis 2).

Ingests LongMemEval chat histories into the Tortoise graph, runs hybrid
retrieval per question, answers via a reader LLM, and scores with the run's
pinned judge: the official `evaluate_qa.py` anscheck RUBRIC, applied by
`judge.DEFAULT_JUDGE_MODEL` — the official GPT-4o grader only when
`judge.OFFICIAL_JUDGE_MODEL` is named (#8486 owner ruling, 2026-10-27).
Full methodology recorded in a provenance JSON —
no "#1" claims, honest numbers only (design-locked 2026-08-15).

Submodules:
    dataset   — fetch/load the official LongMemEval-S split (HF or local JSONL)
    ingest    — haystack transcripts → graph points (sessions, turns, raw text)
    retrieve  — hybrid retrieval per question + recall@k + context tokens
    reader    — reader LLM (provider-config via env; offline mock seam)
    judge     — official answer-check judge prompts + judge LLM wrapper
    report    — aggregate metrics + methodology provenance JSON
    run       — CLI entry point (``uv run python -m tools.longmem_eval.run``)
"""
