"""#10146 — the agent-finding form must produce a body the provenance gate accepts.

The form (``.github/ISSUE_TEMPLATE/agent_finding.yml``) and the gate
(``tools/finding_provenance.py``) are two halves of ONE contract, and neither
read the other:

* the form asked for a bare short sha in a field that was ``required: false``;
* the gate needs a line matching ``PROVENANCE_RE``
  (``Measured at: <ref>@<40-hex-sha> on <YYYY-MM-DD>``).

So a finding filed **through the intended form** could not satisfy the gate.
These tests pin the two halves to ONE source of truth: the gate's own
module-level regexes, imported — never copied. A test written against a copy of
the regex would drift exactly as the form did (the owner's implementation
constraint on the issue).

⛔ WHICH PARSER DECIDES WHICH SURFACE — the part the first reading of #10146
got wrong, corrected here because a docstring that asserts a consequence the
code does not produce is worse than no docstring. ``claims_finding()`` /
``PROVENANCE_CLAIM_RE`` is reached ONLY from ``--is-finding``, which the
workflow called ONLY for the COMMENT arm. The ISSUE-BODY arm classified with
``grep -qE '^Measured at:'`` — a column-0 shell copy of the predicate — and
``--validate`` uses ``PROVENANCE_RE`` alone. So the claim parser never decided
an issue body, and a rendered heading claim did **not** become
``FAIL UNKNOWN_PROVENANCE`` on the issue path: the grep did not see it, the
``bug``/``beta-feedback`` label fallbacks did not match (the form declared an
``internal`` label GitHub dropped, so the issue carried NO label at all), and
the body was **silently skipped** — never gated at all.
That fail-open is the real residual of #10146 and is closed in the workflow's
``decide`` step, which now classifies BOTH arms with ``--is-finding`` and
REFUSES a form-filed body carrying no parseable claim instead of skipping it
(pinned in ``tests/test_finding_provenance.py``).

⛔ THE KEY THAT REFUSAL USES — corrected twice (#10146 F1). The first repair
keyed the refusal on the label this form declared (``labels: ["internal"]``).
That label does **not exist** in this repo (measured 2026-11-05: 78 labels, none
named ``internal``), and GitHub **silently drops** a template label that does
not exist — so a form-filed issue arrived with NO label, the refusal never
fired, and the fail-open stayed open in production while the tests passed on an
injected label the form cannot deliver.

The second repair keyed it on a ``<!-- finding -->`` marker placed in a
``type: markdown`` element. GitHub documents a ``markdown`` element as
*“displayed in the form … but is not submitted”* — it is **UI text**, so that
marker reached no real issue body either, and the two pins that asserted it
were green because the renderer appended ``markdown`` values itself.

So the key is a **SUBMITTED, REQUIRED field whose label the gate's own claim
parser selects**: ``measured_at`` is labelled ``Measured at (paste the `--emit`
line)``, so GitHub renders ``### Measured at …`` into every filing and
``claims_finding()`` / ``PROVENANCE_CLAIM_RE`` selects the body. It cannot be
dropped by repo state (unlike a label), cannot be dropped by the platform
(unlike a ``markdown`` element), and survives a filer who rewrites the
pre-populated title (the ``title: "[finding]: "`` prefix is kept as a weaker
second key).

``test_the_form_delivers_the_claim_the_gate_selects`` below is the anti-rot pin
on this half of the contract.

⛔ AND THE SAME RULE ONE TEMPLATE OVER: a field label that reads as a claim
gates EVERY filing of that form. That is right for this form (its only required
substantive answer IS the provenance line) and wrong for ``bug_report.yml``,
whose ``beta-feedback`` exemption depends on a blank report NOT claiming —
``test_no_field_label_of_the_bug_form_reads_as_a_provenance_claim`` pins that.

The rendering below mirrors what GitHub puts in the created issue body — from
the PLATFORM's rules, not from what would make the assertions pass: a
``markdown`` element is *“displayed in the form … but is not submitted”*, so its
``value`` is never in the body, and an unanswered field renders as
``_No response_`` (not its placeholder). For each SUBMITTED field GitHub writes
a ``### <label>`` heading followed by the answer. That is the whole model, and
getting it wrong is what let two repairs key on tokens no real issue carries.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.finding_provenance import (
    PROVENANCE_CLAIM_RE,
    PROVENANCE_RE,
    claims_finding,
)

FORM = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE" / "agent_finding.yml"
BUG_FORM = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"


def _load(path: Path = FORM):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _render_body(form) -> str:
    """What GitHub puts in the issue body when the form is used, unanswered.

    ``markdown`` elements are NOT submitted (platform rule), so they are
    excluded; an unanswered field renders as ``_No response_``, not as its
    placeholder. A renderer that appends ``markdown`` values models the
    platform wrong — that is how the ``<!-- finding -->`` key looked delivered
    while reaching no real issue (the blocking finding on this PR).
    """
    lines: list[str] = []
    for item in form["body"]:
        kind = item.get("type")
        attrs = item.get("attributes") or {}
        if kind == "markdown":
            continue
        lines.append(f"### {attrs.get('label', '')}")
        lines.append("")
        if kind == "checkboxes":
            for opt in attrs.get("options") or []:
                lines.append(f"- [ ] {opt.get('label', '')}")
        else:
            lines.append("_No response_")
        lines.append("")
    return "\n".join(lines)


def _submitted(form):
    return [item for item in form["body"] if item.get("type") != "markdown"]


def test_the_form_delivers_the_claim_the_gate_selects():
    """#10146 F1 — the refusal key must be delivered BY THE PLATFORM, not by a
    label and not by a ``markdown`` element.

    The gate's ``decide`` step classifies every issue body with the repo's own
    claim parser and REFUSES a form-filed body carrying no parseable claim, so
    the body this form produces must SELECT there. The two keys that did not:
    ``labels: ["internal"]`` (a repo label that does not exist — GitHub drops
    it) and a ``<!-- finding -->`` marker in a ``type: markdown`` element
    (documented as not submitted — GitHub drops it too).

    The delivered key is a submitted, required field's ``### <label>`` heading.
    """
    form = _load()
    body = _render_body(form)
    assert claims_finding(body), (
        "the body this form produces is not selected by the gate's own "
        "claims_finding() — a form-filed finding with a malformed provenance "
        "field would be SKIPPED instead of refused (#10146 F1)")
    # …and the claim must not come from a `markdown` element, which GitHub never
    # submits: a key there is dead code that reads as delivered.
    markdown_claim = "\n".join(
        str((item.get("attributes") or {}).get("value", ""))
        for item in form["body"] if item.get("type") == "markdown")
    assert not claims_finding(markdown_claim), (
        "this form puts a finding claim inside a `type: markdown` element. "
        "GitHub documents markdown elements as displayed-but-not-submitted, so "
        "it can never reach the gate — the exact defect that made the previous "
        "repair dead code (#10146 F1)")
    carrying = [item for item in _submitted(form)
                if claims_finding("### " + str(
                    (item.get("attributes") or {}).get("label", "")))]
    assert carrying, (
        "no SUBMITTED field of this form carries a claim, so a form-filed body "
        "is only gated if the filer leaves the pre-populated title alone")
    assert any((item.get("validations") or {}).get("required")
               for item in carrying), (
        "the field carrying the gate's claim is not required, so a filing can "
        "arrive without it")
    assert _load().get("title") == "[finding]: ", (
        "the form's title prefix is the second (weaker) delivered key the gate "
        "reads — it is gone")
    assert _load().get("labels") is None, (
        "this form declares labels again. A label is repo STATE: GitHub drops "
        "one that does not exist, silently, which is exactly how the #10146 "
        "refusal became dead code. If a label is ever needed here, pin that it "
        "EXISTS in the repo.")


def test_no_field_label_of_the_bug_form_reads_as_a_provenance_claim():
    """The same rule, one template over — and the sign is INVERTED.

    ``bug_report.yml`` stamps ``beta-feedback``, and the workflow exempts an
    external report with no measurement. A field's ``### <label>`` heading IS
    part of the submitted body, so a label reading ``Measured at …`` makes every
    blank-filed beta report a claim — it takes the `0` arm ahead of the
    exemption and is labelled ``provenance-stale``, with a reply telling a
    hosted reporter to re-verify against ``origin/main``.

    So here (unlike ``agent_finding.yml``) the label must stay NEUTRAL, and the
    shape is taught by ``description``/``placeholder``, which are UI-only.
    """
    form = _load(BUG_FORM)
    hits = [str((item.get("attributes") or {}).get("label", ""))
            for item in _submitted(form)
            if any(PROVENANCE_CLAIM_RE.match(ln) for ln in str(
                (item.get("attributes") or {}).get("label", "")).splitlines())]
    assert not hits, (
        "bug_report.yml field labels read as provenance claims, so every "
        f"blank-filed beta report is gated instead of exempted: {hits}")
    assert not claims_finding(_render_body(form)), (
        "the body a blank-filed beta report arrives with claims to be a "
        "finding, making the `beta-feedback` exemption unreachable")


def test_the_old_label_claim_is_selected_by_one_parser_and_refused_by_the_other():
    """The mechanism this module is about, in one assertion pair.

    ``### Measured at (commit / sha you read)`` — the label the form used to
    ship, and the line the pre-fix red run reported — is a CLAIM (the claim
    parser tolerates the ``###`` prefix) and is never a valid provenance line.
    That split is exactly what the two halves must never disagree about; and it
    is why the ISSUE-BODY arm's column-0 ``grep`` was the deeper defect, since
    it reads neither parser and so saw no claim here at all.
    """
    line = "### Measured at (commit / sha you read)"
    assert PROVENANCE_CLAIM_RE.match(line), (
        "the label the form used to render is a claim the claim parser selects")
    assert not PROVENANCE_RE.search(line), (
        "…and it is never a valid provenance line — the strict parser refuses it")


def test_form_tells_the_filer_the_command_that_emits_the_line():
    """The filer must be told to paste the gate's own output (`--emit`).

    A form that demands a shape without naming the command that produces it
    trades one failed gate for a hand-typed sha — which is what broke in the
    first place (3/3 programmatic filings flagged, 2026-10-26).
    """
    text = FORM.read_text(encoding="utf-8")
    assert "finding_provenance.py --emit" in text, (
        "the form never names `tools/finding_provenance.py --emit`, so the "
        "filer has no mechanical way to produce the line it demands")
    # …and it must state the shape the STRICT parser accepts, in prose, so a
    # careful filer can self-check without running anything.
    assert "<ref>@<40-hex-sha> on <YYYY-MM-DD>" in text, (
        "the form does not state the exact shape PROVENANCE_RE accepts")


def test_measured_at_is_required():
    """``required: false`` is the second half of the mismatch."""
    fields = {item.get("id"): item for item in _load()["body"] if item.get("id")}
    field = fields["measured_at"]
    assert (field.get("validations") or {}).get("required") is True, (
        "the measured_at field is optional, so the form ships findings with no "
        "provenance line at all — the exact body the gate refuses")


def test_the_shape_the_form_asks_for_is_the_shape_the_gate_accepts():
    """The two halves are pinned by ONE regex each, imported — never copied.

    A value the filer types per the form's instruction must satisfy the strict
    parser, and must NOT be selected by the claim parser while being rejected
    by it (i.e. when the strict parser accepts the line, the claim parser also
    selects it — they agree on a VALID line).
    """
    line = "Measured at: origin/main@" + "a" * 40 + " on 2026-10-27"
    assert PROVENANCE_RE.search(line), "the pinned sample line is not accepted"
    assert PROVENANCE_CLAIM_RE.match(line), (
        "a VALID provenance line must also be selected by the claim parser — "
        "the two regexes must agree on the good case, or the gate's selector "
        "and its validator disagree about what a finding looks like")


@pytest.mark.parametrize("bad", [
    "Measured at: 98ab138c9",                       # the form's old placeholder
    "Measured at: origin/main@98ab138c9",           # short sha
    "Measured at: origin/main@" + "a" * 40,         # no date
])
def test_the_old_form_shape_is_refused(bad):
    """Pin the defect: the shape the form used to ask for fails the gate.

    Selected-then-refused is the pair that makes it ``UNKNOWN_PROVENANCE``
    rather than silence — on the arm that uses the claim parser at all.
    """
    assert not PROVENANCE_RE.search(bad)
    assert PROVENANCE_CLAIM_RE.match(bad), (
        "a malformed provenance claim must still be SELECTED by the claim "
        f"parser (that is what makes it UNKNOWN_PROVENANCE, not discussion): {bad!r}")
