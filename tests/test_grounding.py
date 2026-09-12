"""Grounding tests — the control that enforces "the agent may not change treatment"."""

from __future__ import annotations

import pytest

from core.grounding import (
    Allowlist,
    check,
    describe,
    generate_grounded_discharge,
    render_template,
)
from core.models import DischargeInstructions, Medication, Patient, TreatmentPlan
from data.seed import PATIENT


@pytest.fixture
def patient() -> Patient:
    return Patient(**PATIENT)


@pytest.fixture
def plan() -> TreatmentPlan:
    """Bella's approved plan, as the live model actually produced it."""
    return TreatmentPlan(
        medications=[Medication(name="Carprofen", dose="75 mg", frequency="twice daily")],
        activity_restrictions=["No running for 10 days."],
        wound_care=["Keep incision dry."],
        monitoring=["Monitor for mild swelling, which is expected."],
        recheck="10-14 days",
    )


def _text(*paragraphs: str) -> DischargeInstructions:
    return DischargeInstructions(paragraphs=list(paragraphs))


# ------------------------------------------------------------------- allowlist


def test_allowlist_extracts_plan_facts(plan):
    allow = Allowlist(plan)
    assert "carprofen" in allow.medications
    assert ("75", "mg") in allow.dose_pairs
    assert {"75", "10", "14"} <= allow.numbers


def test_compound_drug_name_licenses_each_component():
    plan = TreatmentPlan(
        medications=[
            Medication(name="Amoxicillin-Clavulanate", dose="250 mg", frequency="BID")
        ]
    )
    allow = Allowlist(plan)
    assert "amoxicillin" in allow.medications
    assert "clavulanate" in allow.medications


# ----------------------------------------------------------------- clean output


def test_grounded_text_passes(plan):
    instructions = _text(
        "Bella had a mass removed today. She did well.",
        "Bella has been prescribed carprofen 75 mg, given twice daily. Follow the "
        "label and our instructions.",
        "Bella must not run for 10 days. Keep her calm and quiet indoors.",
        "Keep Bella's incision dry.",
        "Bella needs a recheck in 10 to 14 days.",
    )
    assert check(instructions, plan) == []


def test_reformatted_calendar_date_is_not_flagged(plan):
    """Regression: the live model rendered 2026-09-12 as '12 September 2026'.
    A date carries no time unit and must not read as an unverified duration."""
    instructions = _text("Bella had a mass removed on 12 September 2026.")
    assert check(instructions, plan) == []


def test_reworded_duration_is_not_flagged(plan):
    """Plan says '10-14 days'; the model wrote '10 to 14 days'. Same numbers."""
    instructions = _text("Please book a recheck in 10 to 14 days.")
    assert check(instructions, plan) == []


def test_number_without_a_unit_is_ignored(plan):
    instructions = _text("Call us on 555 0123 if you are worried.")
    assert check(instructions, plan) == []


# -------------------------------------------------------------- the real failures


def test_unprescribed_medication_is_rejected(plan):
    instructions = _text(
        "Give Bella her carprofen 75 mg twice daily.",
        "You can also give her Gabapentin if she seems sore.",
    )
    violations = check(instructions, plan)
    kinds = {(v.kind, v.term.lower()) for v in violations}
    assert ("medication", "gabapentin") in kinds
    assert violations[0].paragraph_index == 1


def test_toxic_human_drug_suggestion_is_rejected(plan):
    """The failure mode with real consequences: ibuprofen is toxic to dogs."""
    instructions = _text("If Bella seems painful you could try ibuprofen.")
    violations = check(instructions, plan)
    assert any(v.kind == "medication" and v.term.lower() == "ibuprofen" for v in violations)


def test_altered_dose_is_rejected(plan):
    instructions = _text("Give Bella carprofen 150 mg twice daily.")
    violations = check(instructions, plan)
    assert any(v.kind == "dose" and "150" in v.term for v in violations)


def test_wrong_unit_on_a_right_number_is_rejected(plan):
    instructions = _text("Give Bella 75 ml of her medicine.")
    violations = check(instructions, plan)
    assert any(v.kind == "dose" and "ml" in v.term.lower() for v in violations)


def test_invented_duration_is_rejected(plan):
    instructions = _text("Keep Bella quiet for 30 days.")
    violations = check(instructions, plan)
    assert any(v.kind == "unverified_number" and "30" in v.term for v in violations)


def test_prescribed_drug_is_allowed_in_any_case(plan):
    instructions = _text("Give CARPROFEN 75 mg twice daily.")
    assert check(instructions, plan) == []


def test_violations_are_deduped(plan):
    instructions = _text("Gabapentin, gabapentin, gabapentin.")
    violations = check(instructions, plan)
    assert len([v for v in violations if v.kind == "medication"]) == 1


def test_describe_renders_feedback(plan):
    instructions = _text("Also give Gabapentin 100 mg.")
    text = describe(check(instructions, plan))
    assert "Gabapentin" in text
    assert "not in the approved plan" in text


# ------------------------------------------------------------- template fallback


def test_template_is_grounded_by_construction(plan, patient):
    instructions = render_template(plan, patient)
    assert check(instructions, plan) == []
    assert any("Carprofen" in p for p in instructions.paragraphs)
    assert any("10-14 days" in p for p in instructions.paragraphs)


def test_template_handles_an_empty_plan(patient):
    instructions = render_template(TreatmentPlan(), patient)
    assert len(instructions.paragraphs) == 1
    assert check(instructions, TreatmentPlan()) == []


# ----------------------------------------------------------------- orchestration


def test_clean_first_attempt_is_returned_as_is(plan, patient):
    def writer(p, pt, violation_feedback=None):
        return _text("Bella must rest for 10 days. Her carprofen is 75 mg twice daily.")

    instructions, report = generate_grounded_discharge(plan, patient, writer=writer)
    assert report.passed and report.attempts == 1
    assert report.fell_back_to_template is False
    assert report.violations == []


def test_retry_receives_feedback_and_can_succeed(plan, patient):
    seen: dict[str, str | None] = {}

    def writer(p, pt, violation_feedback=None):
        seen["feedback"] = violation_feedback
        if violation_feedback is None:
            return _text("Also give Gabapentin 100 mg.")
        return _text("Give carprofen 75 mg twice daily.")

    instructions, report = generate_grounded_discharge(plan, patient, writer=writer)
    assert report.attempts == 2
    assert report.passed is True
    assert report.fell_back_to_template is False
    assert "Gabapentin" in seen["feedback"]
    assert "Gabapentin" not in instructions.as_text()


def test_two_failures_fall_back_to_the_template(plan, patient):
    def writer(p, pt, violation_feedback=None):
        return _text("Give Bella Gabapentin 100 mg every 30 days.")

    instructions, report = generate_grounded_discharge(plan, patient, writer=writer)
    assert report.fell_back_to_template is True
    assert report.attempts == 2
    assert report.passed is True  # the delivered text is the template, which is safe
    assert report.violations  # but we still record why the model was rejected
    assert "Gabapentin" not in instructions.as_text()
    assert check(instructions, plan) == []


def test_delivered_text_is_always_grounded(plan, patient):
    """The invariant that matters: whatever path is taken, what reaches the owner
    contains nothing outside the approved plan."""
    writers = [
        lambda p, pt, violation_feedback=None: _text("carprofen 75 mg twice daily"),
        lambda p, pt, violation_feedback=None: _text("Gabapentin 100 mg")
        if violation_feedback is None
        else _text("carprofen 75 mg"),
        lambda p, pt, violation_feedback=None: _text("ibuprofen 200 mg for 30 days"),
    ]
    for writer in writers:
        instructions, _ = generate_grounded_discharge(plan, patient, writer=writer)
        assert check(instructions, plan) == []
