"""Follow-up loop tests. The model is injected out — this is about termination."""

from __future__ import annotations

import pytest

from core.followup import MAX_QUESTIONS, start_session, submit_reply
from core.models import Medication, Observation, Patient, TreatmentPlan
from data.seed import PATIENT
from llm.intake import IntakeTurn


@pytest.fixture
def patient() -> Patient:
    return Patient(**PATIENT)


@pytest.fixture
def plan() -> TreatmentPlan:
    return TreatmentPlan(
        medications=[Medication(name="Carprofen", dose="75 mg", frequency="twice daily")],
        activity_restrictions=["No running for 10 days."],
    )


def turn_fn(updates=None, question="And how is she eating?", done=False):
    """Build a fake model turn."""
    def _fn(**kwargs):
        return IntakeTurn(
            observation_updates=Observation(**(updates or {})),
            next_question=question,
            done=done,
            rationale="test",
        )
    return _fn


# ------------------------------------------------------------------------ opening


def test_session_opens_with_a_fixed_question(patient):
    s = start_session(day=1, patient=patient)
    assert s.questions_asked == 1
    assert len(s.transcript) == 1
    assert s.transcript[0].role == "agent"
    assert "Bella" in s.transcript[0].text
    assert not s.complete


# ------------------------------------------------- the exit the brief omits


def test_urgent_stops_questioning_immediately(patient, plan):
    """The model still wants to ask something; the rules engine says URGENT. The
    question must not be asked."""
    s = start_session(day=2, patient=patient)
    submit_reply(
        s, "There's blood soaking through the bandage.", patient, plan,
        turn_fn=turn_fn({"incision_bleeding": "heavy"}, question="Is she eating?"),
    )

    assert s.complete is True
    assert s.stop_reason == "urgent"
    assert s.triage.level == "URGENT"
    assert "Is she eating?" not in [t.text for t in s.transcript]


def test_urgent_closing_message_directs_the_owner_to_the_practice(patient, plan):
    s = start_session(day=2, patient=patient)
    submit_reply(
        s, "Blood everywhere.", patient, plan,
        turn_fn=turn_fn({"incision_bleeding": "heavy"}),
    )
    closing = s.transcript[-1].text
    assert s.transcript[-1].role == "agent"
    assert "contact the practice" in closing.lower()


def test_urgent_wins_even_when_the_model_says_done(patient, plan):
    s = start_session(day=2, patient=patient)
    submit_reply(
        s, "She can't breathe properly.", patient, plan,
        turn_fn=turn_fn({"breathing": "labored"}, done=True),
    )
    assert s.stop_reason == "urgent"
    assert s.triage.level == "URGENT"


# ---------------------------------------------------------------- other exits


def test_model_can_end_the_conversation(patient, plan):
    s = start_session(day=1, patient=patient)
    submit_reply(
        s, "All good, she ate breakfast.", patient, plan,
        turn_fn=turn_fn({"appetite": "normal"}, question=None, done=True),
    )
    assert s.complete and s.stop_reason == "model_done"
    assert s.triage.level == "NORMAL"


def test_null_question_ends_the_conversation(patient, plan):
    s = start_session(day=1, patient=patient)
    submit_reply(
        s, "Fine.", patient, plan,
        turn_fn=turn_fn({"appetite": "normal"}, question=None, done=False),
    )
    assert s.complete and s.stop_reason == "model_done"


def test_budget_exhaustion_terminates_an_evasive_owner(patient, plan):
    """A model that always wants one more question must still be stopped."""
    s = start_session(day=1, patient=patient)
    fn = turn_fn({}, question="But how does it look?")
    for _ in range(MAX_QUESTIONS + 2):
        if s.complete:
            break
        submit_reply(s, "dunno", patient, plan, turn_fn=fn)

    assert s.complete is True
    assert s.stop_reason == "budget"
    assert s.questions_asked <= MAX_QUESTIONS


def test_review_plus_resolved_safety_fields_stops_asking(patient, plan):
    """Once a human is already going to look at the case and nothing dangerous is
    still unknown, more questions cannot change the outcome — so we stop, whatever
    the model wanted to ask next."""
    s = start_session(day=2, patient=patient)
    submit_reply(
        s, "Swollen, yellow fluid, but closed and no blood — breathing fine, not sore.",
        patient, plan,
        turn_fn=turn_fn({
            "incision_swelling": "increasing",
            "incision_discharge": "yellow",
            "incision_bleeding": "none",
            "incision_open": "no",
            "breathing": "normal",
            "pain_signs": "none",
        }, question="And how is she eating?"),
    )
    assert s.complete is True
    assert s.stop_reason == "resolved"
    assert s.triage.level == "REVIEW"
    assert "eating" not in s.transcript[-1].text


def test_review_with_unresolved_safety_fields_keeps_asking(patient, plan):
    """The mirror case: a REVIEW finding with bleeding still unknown must NOT stop."""
    s = start_session(day=2, patient=patient)
    submit_reply(
        s, "There's yellow fluid.", patient, plan,
        turn_fn=turn_fn({"incision_discharge": "yellow"}, question="Any bleeding?"),
    )
    assert s.complete is False
    assert s.triage.level == "REVIEW"
    assert s.transcript[-1].text == "Any bleeding?"


def test_replying_to_a_finished_session_is_refused(patient, plan):
    s = start_session(day=1, patient=patient)
    submit_reply(s, "fine", patient, plan, turn_fn=turn_fn({}, done=True))
    with pytest.raises(ValueError, match="already finished"):
        submit_reply(s, "more", patient, plan, turn_fn=turn_fn({}))


# ------------------------------------------------------------ slot accumulation


def test_observations_accumulate_across_turns(patient, plan):
    """The demo's day-2 thread: vague -> swollen -> worse -> yellow fluid."""
    s = start_session(day=2, patient=patient)

    submit_reply(s, "It looks a little weird.", patient, plan,
                 turn_fn=turn_fn({}, question="What looks different?"))
    assert s.observation.incision_swelling == "unknown"
    assert s.triage.level == "NORMAL"
    assert not s.complete

    submit_reply(s, "It seems swollen.", patient, plan,
                 turn_fn=turn_fn({"incision_swelling": "mild"},
                                 question="Has it increased since yesterday?"))
    assert s.observation.incision_swelling == "mild"

    submit_reply(s, "Yes, more than yesterday.", patient, plan,
                 turn_fn=turn_fn({"incision_swelling": "increasing"},
                                 question="Any discharge or bleeding?"))
    assert s.triage.level == "REVIEW"
    assert not s.complete  # REVIEW keeps asking; only URGENT cuts it short

    submit_reply(s, "There is a little yellow fluid.", patient, plan,
                 turn_fn=turn_fn({"incision_discharge": "yellow"}, done=True))

    assert s.complete and s.stop_reason == "model_done"
    assert s.triage.level == "REVIEW"
    assert set(s.triage.fired_rule_ids) == {"R-REV-01", "R-REV-02"}
    assert s.observation.incision_swelling == "increasing"
    assert s.observation.incision_discharge == "yellow"


def test_an_unknown_update_never_erases_an_established_value(patient, plan):
    s = start_session(day=2, patient=patient)
    submit_reply(s, "She ate fine.", patient, plan,
                 turn_fn=turn_fn({"appetite": "normal"}, question="And the incision?"))
    submit_reply(s, "Looks swollen.", patient, plan,
                 turn_fn=turn_fn({"incision_swelling": "increasing"}, done=True))
    assert s.observation.appetite == "normal"


def test_review_closing_message_is_not_reassuring(patient, plan):
    s = start_session(day=2, patient=patient)
    submit_reply(s, "Yellow fluid.", patient, plan,
                 turn_fn=turn_fn({"incision_discharge": "yellow"}, done=True))
    closing = s.transcript[-1].text.lower()
    assert "review" in closing
    assert "fine" not in closing
    assert "normal" not in closing


def test_normal_closing_message_mentions_the_next_check_in(patient, plan):
    s = start_session(day=1, patient=patient)
    submit_reply(s, "All fine.", patient, plan, next_date="2026-09-14",
                 turn_fn=turn_fn({"appetite": "normal"}, done=True))
    assert "2026-09-14" in s.transcript[-1].text
