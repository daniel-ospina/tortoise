"""LongMemEval external comparability runner (issue #1144, axis 2).

Ingests LongMemEval chat histories into the Tortoise graph, runs hybrid
retrieval per question, answers via a reader LLM, and scores with the official
GPT-4o answer-check judge. Full methodology recorded in a provenance JSON —
no "#1" claims, honest numbers only (design-locked 2026-08-15).

Submodules:
    dataset   — fetch/load the official LongMemEval-S split (HF or local JSONL)
    ingest    — haystack transcripts → graph points (sessions, turns, raw text)
    retrieve  — hybrid retrieval per question + recall@k + context tokens
    reader    — reader LLM (provider-config via env; offline mock seam)
    judge     — official answer-check judge prompts + judge LLM wrapper
    report    — aggregate metrics + methodology provenance JSON
    run       — CLI entry point (``python -m tools.longmem_eval.run``)
"""
