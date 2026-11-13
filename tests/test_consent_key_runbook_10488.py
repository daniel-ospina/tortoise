"""#10488 — the OPERATOR table for the consent-key check (`docs/infra-runbook.md` §4.4).

WHY THIS IS ITS OWN FILE AND NOT A TEST INSIDE THE FINGERPRINT SUITE
-------------------------------------------------------------------
`docs/infra-runbook.md` is a `NON_CODE_DOC_PATHS` member: it selects **no** surface, so a
runbook-only diff takes the docs-only early return and runs the `tier1` smoke only. A pin on it that
lives in a file registered solely in `api`/`core` is therefore ASLEEP on the one diff that can break
it — the #4309/#11064 silent-drop class, and the reason `tests/test_ci_selection.py` derives the
runbook-reader set and requires every reader to be `tier1`-registered.

So this file is deliberately SMALL and `tier1`-registered: one pin, on the operator-facing
restatement of the consent-key contract. Keeping it inside the fingerprint suite would pull fourteen
unrelated tests into the always-run smoke to guard one table.

⚠️ A file registered in `tier1` costs every PR. That is why it carries exactly one assertion set about
one table and nothing else — if this file grows, split it, do not let the smoke grow with it.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_the_OPERATOR_table_still_matches_the_published_contract() -> None:
    """`docs/infra-runbook.md` §4.4 is the table a person HOLDING THE ALERT reads.

    §2.5 above is the contract; §4.4 is its operator-facing restatement. Both were added by this
    change, and the two halves of one document drifting apart is exactly the "spec says X, the runbook
    says Y" defect this repo treats as a bug — so the restatement is pinned to the same facts §2.5 is
    pinned to, not to its own prose.

    ⛔ WHY THE RUNBOOK AND NOT ONLY THE SPEC: the alert's own text sends the operator to the runbook,
    and §4.4 was where the field table and the two misreads were missing (review finding C-4). A pin
    that only read §2.5 would leave the table the operator actually sees unguarded — and the runbook
    edit that removes it would select nothing at all (`docs/` is in `NON_PYTHON_PREFIXES`; measured
    `surfaces=[]` before this PR registered it).
    """
    doc = (REPO / "docs/infra-runbook.md").read_text(encoding="utf-8")
    assert "\n### 4.4 " in doc, "the consent-key operator section is gone from the runbook"
    section = doc.split("\n### 4.4 ", 1)[1].split("\n## ", 1)[0]

    # The three fields and the closed reason vocabulary, field for field with §2.5 and the code.
    for field in ("key_fingerprint", "configured", "reason"):
        assert field in section, f"§4.4 no longer documents `{field}`"
    for code in ("not_set", "padded", "reused_credential", "too_short", "not_utf8", "unreadable"):
        assert code in section, f"§4.4 no longer documents the `{code}` reason code"
    # ⛔ The two misreads, which are the whole point of restating the contract for an operator. Pinned
    # as the DISAMBIGUATING CLAUSE, not the noun: a rewrite of "`null` means the half has no key" keeps
    # every token and inverts the warning, and that mutation was measured GREEN against a
    # noun-only pin. So each assertion binds the corrective half of its own sentence.
    assert "never read it as `false`" in section, (
        "§4.4 no longer says `configured: null` must NOT be read as `false` — its opposite passes any "
        "pin that only looks for `null`"
    )
    assert "MISMATCH" in section and "is the rotation" in section, (
        "§4.4 no longer says a rotation window IS the rotation — the misread that sends an operator "
        "into re-rotating a key that is fine"
    )
    assert "RANDOM" in section or "random" in section, (
        "§4.4 no longer carries §8's precondition — the operator-facing table is where a hand-typed "
        "guessable key gets caught"
    )
    assert "reverts a hand-set Fly value" in section or "silently reverts" in section, (
        "§4.4 no longer warns that `fly secrets set` alone is reverted by the next deploy"
    )
    # The one-line reproduce, so an operator can check a half by hand rather than trusting the tool.
    assert "sha256sum" in section and "cut -c1-12" in section, "§4.4 lost the reproduce one-liner"
    # …and the off-switch that needs no PR (a schedule only fires from the default branch).
    assert "gh workflow disable consent-key-drift" in section
