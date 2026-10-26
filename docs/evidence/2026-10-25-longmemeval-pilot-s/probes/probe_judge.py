"""Probe candidate judges with the OFFICIAL judge call shape (max_tokens=10,
no system message, temperature 0) before spending a run on one.

Why: the official shape is unforgiving — a thinking-mode model can spend the
whole 10-token budget on reasoning and return empty content, which the
harness would grade as "no". That failure mode is invisible in the report
(it looks like a wrong answer), so it must be measured BEFORE the run.
"""
from __future__ import annotations

import json
import pathlib
import sys

# Run from the repo root: `python probes/<name>`; the repo root is
# resolved from this file so the probe always tests THIS tree.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from tools.longmem_eval.judge import build_judge

# (question_type, question, gold, hypothesis, abstention, expected_label)
CASES = [
    ("single-session-user", "What did I buy for my sister's birthday?",
     "a silver necklace", "You bought a silver necklace for your sister.", False, True),
    ("single-session-user", "What did I buy for my sister's birthday?",
     "a silver necklace", "You bought a bicycle.", False, False),
    ("knowledge-update", "Where do I work now?",
     "Stripe", "You work at Google.", False, False),
    ("_abs", "What is my cat's name?", "unanswerable",
     "I don't have that information in our conversation.", True, True),
]

SPECS = [
    "deepseek:deepseek-flash",
    "deepseek:deepseek-v4-pro",
    "gemini:gemini-2.5-flash",
    "gemini:gemini-2.5-pro",
]

results = {}
for spec in SPECS:
    rows = []
    try:
        j = build_judge(spec)
    except Exception as e:
        results[spec] = [{"error": f"{type(e).__name__}: {e}"}]
        continue
    for qt, q, gold, hyp, abst, expected in CASES:
        rec = {"q": q, "gold": gold, "hyp": hyp, "expected": expected}
        try:
            ok = j.judge(question_type=qt, question=q, answer=gold,
                         hypothesis=hyp, abstention=abst)
            rec.update({"label": ok})
        except Exception as e:
            rec.update({"error": f"{type(e).__name__}: {e}"})
        rows.append(rec)
    results[spec] = rows

print(json.dumps(results, indent=2))
