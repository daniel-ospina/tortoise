"""#8488 scope 1 — context-enriched turn embeddings (the half #8978 did not ship).

Why this module exists. The lexical arm (Okapi BM25) landed in #8978, gated off.
This is the OTHER half of #8488: before a captured turn is embedded, rewrite it
into a SELF-CONTAINED segment using its neighbour window — resolving pronouns
and implicit references — so a turn that reads "they said it blocks the
release" is retrievable by what it MEANS, not only by the words it happens to
contain. The owner ruling is recorded on #8932 (CLOSED) and its shape is A′:

  * the node keeps ``content`` (raw, unchanged, still keyword-searchable) and
    gains ``embeddingText`` (the enriched text);
  * ONE vector is computed from ``embeddingText`` (not a second vector);
  * ``embedding_text_hash`` attests ``embeddingText`` — the text the node
    holds — so the #4194 identity rule stays true as written ("the vector can
    never describe different text than the node holds").

The gate (:func:`enrich_enabled`) is fail-safe OFF: unset/blank/garbage → False,
the same posture as ``TORTOISE_BM25_LEXICAL``. When off, the write path embeds
the raw stored text and stores NO ``embeddingText`` — byte-identical to
pre-#8488, which is the retrieval-equivalence property the suite pins.

The rewrite seam (:func:`enrich_turn_texts`) takes a ``complete_fn`` with the
repo's ``complete(*, system, user) -> str`` contract (``model_adapters``'
``RoutingModel.complete``). It is the CALLER's job to build the model; this
module only threads it. When ``complete_fn`` is ``None`` — or a rewrite fails,
returns blank, or raises — the turn falls back to its raw text, so a capture
can never store a HALF-enriched batch (#4194: the encoded text, the stored text
and the hash must be one string).

What this module deliberately does NOT decide:

* **The prompt-caching wiring.** The production caller builds the model and the
  per-turn prompts below share the session window as a fixed prefix, which is
  exactly what DeepSeek's automatic context caching (and Anthropic's documented
  prompt-cache band) keys on. That is a provider property, not something this
  module can own.
* **Whether the flag is on.** :func:`enrich_enabled` reads
  ``TORTOISE_CONTEXT_ENRICH`` and the write path only calls enrichment when it
  is explicitly enabled. The rewrite is an ingest-time LLM pass (owner-approved
  on #8932, ~$1.02/M doc tokens with prompt caching); it is a retrieval-behaviour
  change and #8486 has not yet produced a baseline to measure it against.
* **The numeric/identifier policy.** The rewrite prompt asks the model to keep
  exact identifiers verbatim; that is a prompt instruction, not a tokenizer
  decision (#8933 owns the tokenizer half).
"""
from __future__ import annotations

import os
from collections.abc import Callable

from .env_truthy import is_truthy

#: The gate. Fail-safe OFF: enrichment is only applied when an operator asks
#: for it AND (#8486) has a baseline to judge it against — the same posture as
#: ``TORTOISE_BM25_LEXICAL``.
ENRICH_ENV = "TORTOISE_CONTEXT_ENRICH"

#: The rewrite instruction. Keep it single-purpose: self-containment, identity
#: preservation, and a bare-text output contract (no preamble/quotes/markdown),
#: because the output IS the text that will be embedded and stored.
_SYSTEM_PROMPT = (
    "You rewrite one conversation turn into a self-contained statement for "
    "embedding-based retrieval. Rewrite the TARGET turn so it is understandable "
    "on its own: resolve pronouns and implicit references using the previous "
    "and next turns as context. Preserve the original meaning exactly, and keep "
    "any exact identifiers (IDs, ticket numbers, error codes, names, URLs) "
    "verbatim. Output ONLY the rewritten turn text — no preamble, no quotes, no "
    "markdown, no labels."
)


def enrich_enabled() -> bool:
    """Is context-enriched turn embedding enabled for this process?

    Fail-safe OFF (unset/blank/garbage → False) via the recorded truthy
    contract in :mod:`tortoise.env_truthy` — the same spellings every other
    lever in this tree accepts.
    """
    return is_truthy(os.environ.get(ENRICH_ENV))


def enrich_turn_texts(
    windowed_texts: list[str],
    *,
    complete_fn: Callable[..., str] | None = None,
) -> list[str]:
    """The ``embeddingText`` for each turn in a stored-text window.

    ``windowed_texts`` is the SAME stored-text list the writer embeds
    (``_capture_turn_texts``'s output, already scrubbed). Returns a list of the
    same length: each element is the enriched text for that turn, or the raw
    text when there is nothing to rewrite with.

    ``complete_fn`` is ``complete(*, system, user) -> str`` (the
    ``model_adapters`` contract). ``None`` — or a callable that raises, returns
    ``None``, or returns whitespace for a turn — yields that turn's raw text,
    never an error and never an empty embedding target. This is the fail-soft
    posture: a capture must not fail (or store a junk vector) because the
    enrichment model was unavailable for one turn of five hundred.
    """
    if not windowed_texts:
        return []
    if complete_fn is None:
        return list(windowed_texts)
    out: list[str] = []
    for i, text in enumerate(windowed_texts):
        out.append(_rewrite_one(windowed_texts, i, text, complete_fn))
    return out


def _rewrite_one(
    windowed_texts: list[str],
    index: int,
    text: str,
    complete_fn: Callable[..., str],
) -> str:
    """Rewrite ONE turn; the raw text is the fallback on every failure path."""
    prev_text = windowed_texts[index - 1] if index > 0 else ""
    next_text = windowed_texts[index + 1] if index + 1 < len(windowed_texts) else ""
    user = (
        f"Previous turn:\n{prev_text}\n\n"
        f"TARGET turn (rewrite this one):\n{text}\n\n"
        f"Next turn:\n{next_text}"
    )
    try:
        rewritten = complete_fn(system=_SYSTEM_PROMPT, user=user)
    except Exception:
        return text
    if not isinstance(rewritten, str) or not rewritten.strip():
        return text
    return rewritten.strip()
