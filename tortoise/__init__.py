"""Tortoise — live epistemic-graph extraction from transcripts.

M0 spike: file → extractor → JSONL event log → projection → static grid render.
See ../BUILD_PLAN.md. This is the spine only; storage, streaming, idempotency,
and the eval loop are later milestones.

GAP-15 / #7003: Conversation mining pipeline (mining.py) now wired.
"""
