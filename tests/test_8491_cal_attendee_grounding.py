"""#8491 (in-repo read path): calendar attendees promoted into CRM identity
ungrounded by the meeting under process.

The issue's writer is OUT-OF-REPO — the ``minutes`` CLI's ``attribution`` step
emits ``attendees`` / ``speaker_map`` / ``calendar_people`` into
``~/meetings/*.md``, and no module in this repo writes those fields (measured:
``git grep -n -e speaker_map -e calendar_people -e diarization_collapsed``
returns nothing). What IS in-repo is the same defect **on the read path**:
``bridge.py::get_calendar_attendees()`` read
``~/.minutes/cal-trigger-state.json`` — a SINGLE-VALUED store that cal-trigger.py
overwrites on every recording start and that carries no ``meeting_id`` — and
``process_meeting`` auto-created one ``Person`` and one "First call"
``Opportunity`` per entry. Any re-run, backfill or ``--watch`` pass over an
older meeting therefore promoted *another* meeting's calendar participants into
this meeting's CRM, at machine confidence, with no grounding against evidence
already in hand: #8491's fix direction 3 ("scope the speaker-resolution store by
``meeting_id`` on the read path"), and its own stated harm — wrong people
attributed with machine confidence on a structured identity surface.

These tests pin the grounding contract (artifact evidence only; refuse rather
than guess) and the end-to-end consequence (no contact, no opportunity, no
week-old attendees attached to an unrelated call).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Repo root on sys.path so `import integrations` works under any pytest
# invocation (console script does not add CWD; `python -m pytest` does) — the
# same construction as tests/test_bridge_mcp.py. Deliberately NOT inserting
# `integrations/crm/twenty/` itself: that directory holds `review.py`, a name
# generic enough to shadow another module for the rest of the session.
sys.path.insert(0, str(REPO_ROOT))

import integrations.crm.twenty.bridge as bridge  # noqa: E402

# The contaminated artifact shape from the issue: one remote participant, a
# state file left behind by a *different* call recorded the same week.
MEETING_DATE = "2026-10-09T11:03:58-05:00"
MEETING_TITLE = "Preston Younger Intro for GTM Packaging"

ATTENDEES = [
    {"name": "Contact One", "email": "one@example.com"},
    {"name": "Contact Two", "email": "two@example.com"},
]


def _state_file(tmp_path: Path, **overrides) -> Path:
    state = {
        "recording_pid": 4242,
        "current_event_uid": "uid-previous-call",
        "started_at": "2026-10-07T09:00:00",
        "event_title": "Web3 GTM Shifts and Infrastructure Focus",
        "attendees": list(ATTENDEES),
    }
    state.update(overrides)
    path = tmp_path / "cal-trigger-state.json"
    path.write_text(json.dumps(state))
    return path


def _meeting_md(tmp_path: Path, *, quote_date: bool = True,
                date_value: str = MEETING_DATE, title: str = MEETING_TITLE) -> Path:
    """The contaminated artifact's markdown.

    ``quote_date=False`` writes the ``date:`` UNQUOTED, which PyYAML parses into
    a ``datetime`` — the shape ``_as_calendar_day`` was extended to accept and
    the shape real ``~/meetings/*.md`` files produce when the writer does not
    quote (a ``datetime`` is not subscriptable, so the opportunity label must go
    through the helper rather than slice the value).
    """
    date_line = f'date: "{date_value}"\n' if quote_date else f"date: {date_value}\n"
    path = tmp_path / "2026-10-09-preston-younger-intro.md"
    path.write_text(
        "---\n"
        'id: "2026-10-09-preston-younger-intro"\n'
        f"{date_line}"
        f'title: "{title}"\n'
        "type: meeting\n"
        "speakers:\n"
        '  - name: "SPEAKER_1"\n'
        "    segments: 12\n"
        "---\n"
        "## Transcript\n"
        "[SPEAKER_1 0:04] Let's talk packaging.\n"
    )
    return path


# ── grounding contract ─────────────────────────────────────────────────────

def test_no_grounding_evidence_refuses(tmp_path, monkeypatch):
    """No evidence supplied → refuse. An unscoped read is the defect."""
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(_state_file(tmp_path)))
    assert bridge.get_calendar_attendees() == []


def test_stale_neighbouring_state_is_not_applied(tmp_path, monkeypatch):
    """A state file from a different call (day AND title differ) → []."""
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(_state_file(tmp_path)))
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


def test_state_from_the_same_day_is_applied_when_the_title_cannot_speak(
        tmp_path, monkeypatch):
    """Same local day grounds it when the state carries NO title to contradict.

    The day arm exists for exactly this case: the state cannot name the event
    (``stop_recording()``/an older writer left no ``event_title``), so the day
    is the only evidence either side has.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T10:55:00",
                        event_title=None)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == ATTENDEES


def test_same_day_state_naming_a_different_event_is_refused(
        tmp_path, monkeypatch, capsys):
    """#8491 residual: a same-day NEIGHBOUR must not be rescued by the day arm.

    The state's own ``event_title`` is affirmatively a different event, which is
    positive evidence that it describes another call — the day coincidence is
    not enough. This is the arm that reachable in practice: ``watchdog.sh``
    re-enters on failure, so a retried run meets whatever call recorded most
    recently that day. A contradicting title is disqualifying, not "not
    evidence" (pre-fix, this returned ``ATTENDEES``).
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T14:00:00",
                        event_title="Afternoon Sync")),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []
    assert "does not describe this meeting" in capsys.readouterr().err


def test_long_calendar_title_grounds_on_the_writers_truncation(
        tmp_path, monkeypatch):
    """The title arm must survive cal-trigger.py's ``event_title[:50]`` cap.

    ``cal-trigger.py::start_recording`` stores ``event.get("title")[:50]`` while
    the artifact carries the full calendar title, so exact equality is
    unreachable above 50 characters — the arm the docstring designates for the
    across-midnight case was dead for every auto-generated Zoom/Meet invite.
    Day is deliberately mismatched here so ONLY the title arm can ground it.
    """
    long_title = ("Quarterly GTM Packaging Review with the Entire "
                  "Infrastructure and Pricing Team")
    # Must exceed the writer's cap, or the stored value is not a truncation.
    assert len(long_title) > bridge._TITLE_WRITER_CAP
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T23:52:00",
                        event_title=long_title[:bridge._TITLE_WRITER_CAP])),
    )
    assert bridge.get_calendar_attendees(
        MEETING_DATE, long_title) == ATTENDEES


def test_a_nested_title_that_is_not_the_writers_truncation_is_refused(
        tmp_path, monkeypatch):
    """Nesting alone is not agreement — a series variant is another event.

    ``Weekly Team Standup`` nests in ``Weekly Team Standup (Engineering)`` and
    both are short enough that the writer's ``[:50]`` cap cannot explain the
    difference, so the title arm is silent and a differing day refuses. (Across
    days, grounding on the bare prefix would seed the Tuesday call's CRM from
    Monday's state — #8491 again, through the door the truncation fix opened.)
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T09:00:00",
                        event_title="Weekly Team Standup")),
    )
    assert bridge.get_calendar_attendees(
        MEETING_DATE, "Weekly Team Standup (Engineering)") == []

    # Same day: the title cannot speak either way, so the day arm grounds it.
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T09:00:00",
                        event_title="Weekly Team Standup")),
    )
    assert bridge.get_calendar_attendees(
        MEETING_DATE, "Weekly Team Standup (Engineering)") == ATTENDEES


def test_truncated_title_ending_in_whitespace_still_grounds(tmp_path, monkeypatch):
    """The cap counts RAW characters: a 50th-character space must not unmask it.

    ``cal-trigger.py`` slices the raw title (``[:50]``), so a title whose 50th
    character is whitespace stores ``...\u0020`` and strips to 49 normalised
    characters. Testing the cap on the normalised length would refuse a state that
    genuinely describes this meeting (the arm exists precisely for the
    across-midnight case), so ``have_raw_len`` carries the raw length.
    """
    calendar_title = "A" * 49 + " " + "B" * 20
    stored = calendar_title[:bridge._TITLE_WRITER_CAP]
    assert stored.endswith(" ")
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T23:52:00",
                        event_title=stored)),
    )
    assert bridge.get_calendar_attendees(
        MEETING_DATE, calendar_title) == ATTENDEES


def test_a_store_longer_than_the_cap_is_not_read_as_its_truncation(
        tmp_path, monkeypatch):
    """A stored title ABOVE the cap cannot be the capped writer's truncation.

    The in-repo writer stores at most 50 characters, so a longer store came from
    a foreign/older writer and the cap explains nothing about it: nesting is not
    evidence, and a differing day refuses rather than seeding the CRM from
    whatever that store describes.
    """
    stored = "Weekly Engineering Sync Roadmap Planning Extended Session Q3"
    assert len(stored) > bridge._TITLE_WRITER_CAP
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T09:00:00",
                        event_title=stored)),
    )
    assert bridge.get_calendar_attendees(
        MEETING_DATE, stored + " (Engineering)") == []


def test_a_tiny_stored_title_cannot_ground_but_does_not_disqualify(
        tmp_path, monkeypatch):
    """A short prefix is not the writer's truncation, so it cannot ground.

    ``1:1`` nests in ``1:1 with Alice`` only because it is 3 characters; treating
    that as a match would ground any short stored title. The title therefore
    cannot speak — on a differing day it refuses, on the same day the day arm
    still decides.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T09:00:00",
                        event_title="1:1")),
    )
    # Different day, title cannot ground -> refuse.
    assert bridge.get_calendar_attendees(
        MEETING_DATE, "1:1 with Alice") == []

    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T09:00:00",
                        event_title="1:1")),
    )
    # Same day, title cannot disqualify -> the day arm grounds it.
    assert bridge.get_calendar_attendees(
        MEETING_DATE, "1:1 with Alice") == ATTENDEES


def test_cleared_state_is_silent_absence(tmp_path, monkeypatch, capsys):
    """The cleared state ``stop_recording()`` writes is absence, not a mismatch.

    It is written on every normal stop and read seconds later by ``watchdog.sh``,
    so a warning here would fire on every ordinary meeting and cost the warning
    its signal.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, recording_pid=None,
                        current_event_uid=None, started_at=None,
                        event_title=None, attendees=[])),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []
    assert capsys.readouterr().err == ""


def test_state_from_the_same_event_title_is_applied(tmp_path, monkeypatch):
    """Same event title grounds it even across a midnight boundary."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T23:52:00",
                        event_title=MEETING_TITLE)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == ATTENDEES


def test_same_title_from_a_previous_occurrence_is_refused(
        tmp_path, monkeypatch, capsys):
    """A recurring title is NOT evidence across occurrences — only ages ≤ 1 day.

    ``Intro Call`` / ``1:1`` / ``Weekly Sync`` are the same string every week, so
    an unbounded title arm promotes the PREVIOUS occurrence's participants into
    this meeting's CRM at machine confidence — #8491's stated harm, on the arm
    the grounding fix adds (pre-fix, this returned ``ATTENDEES``).
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-04T09:00:00",
                        event_title=MEETING_TITLE)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []
    assert "does not describe this meeting" in capsys.readouterr().err


def test_a_state_from_the_day_after_the_meeting_is_refused(
        tmp_path, monkeypatch):
    """The bound is one-directional: a LATER occurrence must not ground.

    ``started_at`` is the recording start and the artifact's ``date`` is the
    meeting's date, so a state dated after the artifact is today's same-titled
    call while yesterday's still-unprocessed artifact is re-run (only an
    already-created note makes the re-run skip). ``abs()`` here would admit it —
    pre-fix, this returned ``ATTENDEES``.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-10T00:05:00",
                        event_title=MEETING_TITLE)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


def test_a_title_literally_meeting_still_grounds_across_midnight(
        tmp_path, monkeypatch):
    """No over-refusal: a genuine across-midnight recording titled "Meeting".

    ``cal-trigger.py::get_upcoming_events`` always sets the ``title`` key, so its
    ``"Meeting"`` fallback is unreachable and such a title is a real calendar
    title, decided by the same one-day bound as any other.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T23:52:00",
                        event_title="Meeting")),
    )
    assert bridge.get_calendar_attendees(
        MEETING_DATE, "Meeting") == ATTENDEES


def test_a_daily_recurring_title_one_day_earlier_is_indistinguishable(
        tmp_path, monkeypatch):
    """Documented RESIDUAL, pinned so it is never mistaken for a guarantee.

    The bound is calendar-day granular: a daily recurrence ("Office Hours") one
    day earlier has the same gap as an across-midnight recording of one event, so
    it still grounds. This test is the honest statement of that limit — if the
    arm is ever made finer, it should fail and be updated deliberately.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T11:03:00",
                        event_title=MEETING_TITLE)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == ATTENDEES


def test_an_equally_titled_state_of_unknown_age_is_refused(
        tmp_path, monkeypatch):
    """The title arm's bound needs a readable ``started_at``; unknown → refuse."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="not-a-timestamp",
                        event_title=MEETING_TITLE)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


def test_unparseable_dates_do_not_ground(tmp_path, monkeypatch):
    """A timestamp nobody can read is evidence of nothing → refuse."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="not-a-timestamp")),
    )
    assert bridge.get_calendar_attendees(
        "also-not-a-date", "Some Other Title") == []


def test_no_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(tmp_path / "absent.json"))
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


def test_corrupt_state_file(tmp_path, monkeypatch):
    path = tmp_path / "cal-trigger-state.json"
    path.write_text("{not json")
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(path))
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


# ── end-to-end consequence on process_meeting ──────────────────────────────

@pytest.fixture
def crm_calls(monkeypatch):
    """Stub the Twenty REST surface and record what process_meeting asks for."""
    calls = {"created_people": [], "created_opportunities": []}
    created: set[str] = set()
    monkeypatch.setattr(bridge, "load_resolved_speakers", lambda: [])
    monkeypatch.setattr(bridge, "find_note_by_external_id", lambda *a, **k: None)

    def _find_person_by_email(email):
        # No CRM contact exists for any attendee yet: `process_meeting` then
        # creates one (and, on the re-read, an opportunity). This is what makes
        # the stale-state test NON-VACUOUS — if the bridge grounds an attendee,
        # a Person appears.
        return {"id": f"person-{email}", "name": email} if email in created else None

    monkeypatch.setattr(bridge, "find_person_by_email", _find_person_by_email)

    def _create_person(name, email):
        created.add(email)
        calls["created_people"].append((name, email))
        return {"id": f"person-{email}"}

    def _create_opportunity(person_id, name):
        calls["created_opportunities"].append((person_id, name))
        return {"id": "opp-1"}

    monkeypatch.setattr(bridge, "create_person", _create_person)
    monkeypatch.setattr(bridge, "create_opportunity", _create_opportunity)
    monkeypatch.setattr(bridge, "queue_unmatched_speaker", lambda *a, **k: None)
    monkeypatch.setattr(
        bridge, "create_note", lambda *a, **k: {"id": "note-1"})
    monkeypatch.setattr(
        bridge, "push_to_tortoise", lambda fm: {"status": "skipped"})
    return calls


def test_process_meeting_does_not_seed_crm_from_a_stale_state(
        tmp_path, monkeypatch, crm_calls):
    """The #8491 harm: an unrelated call's attendees must not become contacts.

    ``find_person_by_email`` returns a contact for every email, so any attendee
    the bridge decides to ground becomes a Person — this asserts the count is
    ZERO, not merely that the call "did not fail".
    """
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(_state_file(tmp_path)))
    result = bridge.process_meeting(str(_meeting_md(tmp_path)))
    assert crm_calls["created_people"] == []
    assert crm_calls["created_opportunities"] == []
    assert result["status"] in {"ok", "partial"}


def test_process_meeting_does_not_seed_crm_from_a_same_day_neighbour(
        tmp_path, monkeypatch, crm_calls):
    """The residual #8491 harm end to end: a same-day NEIGHBOUR seeds nothing.

    ``process_meeting`` auto-creates one Person + one "First call" Opportunity
    per grounded attendee, so a state written by the 14:00 call must contribute
    nothing to the 09:03 artifact — even though the calendar day matches.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T14:00:00",
                        event_title="Afternoon Sync")),
    )
    result = bridge.process_meeting(str(_meeting_md(tmp_path)))
    assert crm_calls["created_people"] == []
    assert crm_calls["created_opportunities"] == []
    assert result["status"] in {"ok", "partial"}


def test_process_meeting_accepts_an_unquoted_yaml_date(
        tmp_path, monkeypatch, crm_calls):
    """An UNQUOTED ``date:`` parses into a ``datetime`` — the flow must survive.

    ``datetime`` is not subscriptable, so the opportunity label slicing
    ``frontmatter['date'][:10]`` raised ``TypeError`` AFTER ``create_person`` had
    already written the Person (no opportunity, no note, no idempotency marker,
    and ``main()`` propagates). Grounded by the same day, so the run must reach
    both creates.
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T10:55:00",
                        event_title=MEETING_TITLE)),
    )
    result = bridge.process_meeting(str(_meeting_md(tmp_path, quote_date=False)))
    assert crm_calls["created_people"] == [
        (a["name"], a["email"]) for a in ATTENDEES]
    assert crm_calls["created_opportunities"] == [
        (f"person-{a['email']}", f"{a['name']} — First call 2026-10-09")
        for a in ATTENDEES]
    assert result["status"] in {"ok", "partial"}


def test_process_meeting_still_seeds_crm_when_grounded(
        tmp_path, monkeypatch, crm_calls):
    """Positive control: the same meeting with its OWN cal-trigger state."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(
            tmp_path,
            started_at="2026-10-09T10:55:00",
            event_title=MEETING_TITLE,
        )),
    )
    bridge.process_meeting(str(_meeting_md(tmp_path)))
    assert crm_calls["created_people"] == [
        (a["name"], a["email"]) for a in ATTENDEES]
    assert crm_calls["created_opportunities"] == [
        (f"person-{a['email']}", f"{a['name']} — First call 2026-10-09")
        for a in ATTENDEES]


def test_process_meeting_does_not_seed_crm_from_a_previous_occurrence(
        tmp_path, monkeypatch, crm_calls):
    """End to end: last week's same-titled call must contribute NO contacts.

    The weekly-recurring shape of #8491: the state's ``event_title`` equals the
    artifact's, the recording is a week old, and ``process_meeting`` must still
    create zero Persons and zero "First call" Opportunities (pre-fix it created
    both, for both attendees).
    """
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-02T11:03:00",
                        event_title=MEETING_TITLE)),
    )
    result = bridge.process_meeting(str(_meeting_md(tmp_path)))
    assert crm_calls["created_people"] == []
    assert crm_calls["created_opportunities"] == []
    assert result["status"] in {"ok", "partial"}


# ── the wiring that keeps this file running on its own subject ──────────────

def test_bridge_change_selects_core_so_this_guard_runs():
    """A bridge.py-only diff must select `core`, or this file never runs.

    Measured before the CORE_ALSO entry: `select(['integrations/crm/twenty/
    bridge.py'])` → ``surfaces=[]`` (45 tier-1 files) — `integrations/` is a flat
    NON_PYTHON_PREFIXES entry, so the path was dropped before the "unmatched →
    core" fallback. Mutation check: removing the CORE_ALSO entry fails this.
    """
    import tools.ci_selection as cs

    selected = cs.select(["integrations/crm/twenty/bridge.py"],
                         "pull_request", cs.load_manifest())
    assert "core" in selected["surfaces"]
    assert "test_8491_cal_attendee_grounding.py" in selected["test_files"]
    # And the row is registered exactly once (an unregistered file reds
    # `ci_selection.py --integrity`).
    manifest = cs.load_manifest()
    assert cs.classify_test_file(
        "test_8491_cal_attendee_grounding.py", manifest) == "core"
