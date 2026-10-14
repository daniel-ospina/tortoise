"""#8496 — the append-only raw layer's redaction reconciliation must be STATED.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.3 gap G4: `#4911`'s
redaction (closed `COMPLETED` 2026-09-26) and `§2.4.2`'s append-only ruling
(2026-09-23) were designed in **different issues, at different times**, so
neither could see the other's constraint. `§2.4.2` makes the raw-fact layer
immutable *by design* — a raw fact that can be rewritten is not evidence — and a
control that removes a credential is, read naively, a rewrite. A secret captured
once would be retained forever. `#8496` asked for the reconciliation to be
**stated, not implied**.

A prose-only repair re-rots the way the gap arose: the ruling lives in a document
whose claims are only as durable as the next lane's reading, and a future edit can
silently drop *"redact BEFORE the append"* and leave the tombstone alternative —
which **keeps the secret** — looking equally defensible. So this file pins BOTH
halves:

  1. **the design's four decisions (R1-R4)** are present, with R1 named as
     redact-before-append and the tombstone form named as REFUSED; and
  2. **the code ground the ruling rests on** — the write-path scrub already
     emits the visible marker, is idempotent, and the stored-turn definition
     stores the marker rather than the secret. If the control is removed or
     reverted, this reds instead of the document silently becoming false.

Non-vacuity: the pre-fix document carries no `#### 2.4.4` at all, so check 1 is
red on it. The code assertions are red against a pre-`#4911` tree.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The reconciliation's own heading, and the heading that ends its section —
#: scoping every prose read to the section keeps a `[REDACTED:` elsewhere in the
#: document (e.g. §4.3 G4's row) from reding a guard that pins §2.4.4.
SECTION_HEADING = "#### 2.4.4"
SECTION_END = "## 3. Step 0 is not the only source-varying step"


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _section() -> str:
    text = _document()
    assert SECTION_HEADING in text, (
        f"{SECTION_HEADING!r} not found in {DOC.name} — the G4 reconciliation "
        "(#8496) was removed or renamed, which re-opens the gap"
    )
    start = text.index(SECTION_HEADING)
    end = text.index(SECTION_END, start)
    return text[start:end]


# ── the design half ──────────────────────────────────────────────────────


def test_the_reconciliation_section_exists_and_carries_all_four_decisions():
    section = _section()
    for rid in ("R1", "R2", "R3", "R4"):
        assert f"**{rid}**" in section, (
            f"{rid} is missing from §2.4.4 — #8496 asked for all four items to be "
            "stated, and a partial table leaves the rest implied"
        )


def test_r1_is_redact_before_the_append_and_the_tombstone_form_is_refused():
    section = _section()
    assert "Redact BEFORE the append" in section, (
        "§2.4.4 must state R1 as redact-before-append; if the choice is not "
        "stated the tombstone form (which retains the secret) reads as "
        "equally defensible (#8496)"
    )
    assert "superseding redaction record" in section and "REFUSED" in section, (
        "§2.4.4 must name the superseding-redaction-record option as REFUSED, "
        "with the reason: it keeps the bytes the control exists to remove"
    )


def test_r2_states_the_already_persisted_procedure():
    section = _section()
    assert "retention erasure" in section, (
        "R2 (already-persisted) must be stated as a retention erasure, not left "
        "implied — §2.4.2 rule 3 names GDPR/retention as the only in-place class"
    )
    assert "in place" in section and "same" in section, (
        "R2 must say the erasure is in place and reuses the SAME marker the "
        "write path emits, so a late redaction cannot look like a second class"
    )
    assert "journal" in section, (
        "R2 must require the erasure to be journalled (span id + kind, never "
        "the value) — an unlogged mutation of the truth layer breaks "
        "`derived = fold(truth)`"
    )


def test_r3_states_the_coverage_rule_and_the_discharged_full_scan():
    section = _section()
    assert "(scanned, total)" in section, (
        "R3 must state the coverage rule in the (scanned, total) form — a clean "
        "claim without its denominator is the partial-scan caveat re-created "
        "silently (#4911, #8496)"
    )
    assert "hasMore=false" in section and "include_retracted=True" in section, (
        "R3 must name the mechanism: page to `hasMore=false` and include "
        "retracted rows, or a default-capped read is a partial scan wearing a "
        "clean verdict"
    )
    assert "21,281 of 21,281" in section, (
        "R3 must record the DISCHARGED full scan (`#4911`'s posted re-scan, "
        "21,281 of 21,281 nodes, 0 anchored matches) — the issue body's "
        "10,000-of-19,960 premise is stale and must not be inherited"
    )


def test_r4_states_the_layer_home_and_cites_the_shipped_scrub():
    section = _section()
    assert "Postgres truth store" in section, (
        "R4 must state the raw-fact layer's home (the Postgres truth store, not "
        "the graph and not the object bucket) so the residency/retention answer "
        "is explicit (#8496 item 4)"
    )
    assert "redact_secrets" in section and "CREDENTIAL_KINDS" in section, (
        "§2.4.4 must name the shipped pattern table (`redact_secrets` / "
        "`CREDENTIAL_KINDS`) as the ONE table the scan and the write share — a "
        "parallel list is a second source of truth for what a credential is"
    )


# ── the code half — the ground the ruling stands on ───────────────────────


def test_the_write_path_scrub_emits_a_visible_marker_and_is_idempotent():
    from tortoise.security import CREDENTIAL_KINDS, redact_secrets

    assert CREDENTIAL_KINDS, "the credential table is empty — R1 has nothing to run"

    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    redacted, counts = redact_secrets(f"key is {secret} ok")
    assert secret not in redacted, redacted
    assert "[REDACTED:" in redacted, redacted
    assert counts, "the redaction count is what makes it visible (#4911)"

    # R1's "already-redacted text is never rewritten" rests on this: no rule's
    # anchor survives inside the marker, so a re-scan is a no-op. If it is not,
    # R2's marker would be re-derived on every pass and the count inflated.
    again, second_counts = redact_secrets(redacted)
    assert again == redacted, again
    assert second_counts == {}, second_counts


def test_the_stored_turn_definition_stores_the_marker_not_the_secret():
    """`_capture_turn_texts` is the ONE stored-text definition the writer calls.

    This is the code fact R1 names: the scrub sits BEFORE the write, so the
    append-only layer never receives an unredacted credential in the first
    place. Reverting the chokepoint reds here rather than the document
    becoming quietly false.
    """
    from tortoise.sdk import _capture_turn_texts

    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    stored = _capture_turn_texts([{"role": "user", "content": f"key is {secret}"}])
    assert len(stored) == 1, stored
    assert secret not in stored[0], stored
    assert "[REDACTED:" in stored[0], stored
