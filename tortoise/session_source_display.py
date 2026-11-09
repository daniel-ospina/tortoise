"""The session `:Source`'s DISPLAY descriptor — ONE home for its prefix (#10000).

#10000 replaced the session `:Source`'s prose derivation with a word LEXICON, and
tied the two derived columns to ONE display form:

    summary     = SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)
    _searchText = ", ".join(topics)

TWO doors read that shape, and they must not drift:

* the **capture** (`sdk._session_source_metadata` →
  `sdk._materialize_session_source`) WRITES it; and
* the **cache projection**
  (`cache_projection._episodic_source_search_text_is_the_lexicon`) VERIFIES it on
  a record row before projecting `_searchText` — because on a post-#10000 row that
  value is the topic lexicon rather than §9.1/D1 prose, and declining it by column
  NAME un-indexes every restored session on the recovery door (#10965).

⛔ WHY A LEAF MODULE, AND NOT A CONSTANT IN `sdk`. The projection may not import
the capture: `cache_projection` reads the RECORD and sits BELOW the SDK (`sdk`
imports the projection, never the reverse), so a decision that depends on a
constant living in `sdk` can only be expressed there as a layering inversion.
Nor may it be copied — a second literal is exactly how two doors come to disagree
about what a derived row looks like, the defect class the guard exists to close.
So the value lives here, this module imports NOTHING, and both sides import IT.

⭐ AND IT IS NOT MERELY AESTHETIC: the prefix is the WITNESS that separates the
two derivations of the pair (#10852 review, P2). A pre-#10000 capture wrote
`summary` = the transcript's first substantive utterance and derived
`_searchText` FROM that same value, so a legacy row holds
`summary == _search_text == <the utterance>`. When the utterance is itself a
comma-space list ("milk, eggs, bread, cheese, apples, oranges"), it coincides
with `", ".join(topics)` EXACTLY — the join alone cannot tell it apart, and the
prose would be re-indexed with nothing red. `summary == PREFIX + join` can (a
prose utterance carries no `PREFIX`).

⛔ AND THE PREFIX ITSELF MUST NEVER REACH `_searchText`. `_source_search_text`
returns the BARE lexicon: the Source retrieval leg OR-unions query terms
(`search_engine` → `sparse.build_or_query`), so a constant "session capture
topics" in the index makes EVERY captured session match any source query
containing "session", "capture" or "topics" — and none of the three is a
RediSearch stopword (its default list is function words) or a member of
`sdk._SESSION_SOURCE_STOPWORDS`. Measured on a two-session graph (one about a
migration plan, one about a fencing budget): the query "session capture topics"
returned BOTH, on the prefix alone.
"""

from __future__ import annotations

#: #10000: the DISPLAY prefix on a session Source's ``summary`` — a LABEL, not
#: vocabulary. It is kept out of ``_searchText`` (see the module docstring) and it
#: is the WITNESS the projection reads to tell a post-#10000 row's lexicon
#: ``_searchText`` from a pre-#10000 row's prose (see
#: ``cache_projection._episodic_source_search_text_is_the_lexicon``).
SESSION_SOURCE_SUMMARY_PREFIX = "session capture topics: "
