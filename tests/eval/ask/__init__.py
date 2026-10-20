"""Sealed ask-lane benchmark corpus (issue #9097, epic #2281 Q3 exit bar).

The corpus tree for the ask lane's published measurement: ``fixtures/`` (the
ingest/read inputs the runner may see), ``gold/`` (**sealed** — the grading
key), ``_manifest.json`` (sha256 of every fixture AND gold file) and
``baselines/main.json`` (first-run-pending: no preset quality bar).

Layout rationale and the sealed-key discipline live in ``README.md``; the
generator that renders the corpus from its authored spec is
``generate_corpus.py``; the runner-facing API is ``corpus.py``.
"""
