"""Seam 2: approved treatment plan -> owner-friendly discharge instructions.

A translation task with a hard constraint: the output may not contain any medication,
dose, or instruction that is not in the approved plan. The prompt says so, and
``core.grounding`` then *verifies* it — because a prompt instruction is not a control.
"""

from __future__ import annotations

from core.models import DischargeInstructions, Patient, TreatmentPlan
from llm.client import parse_structured

SYSTEM = """You rewrite an approved veterinary treatment plan into discharge \
instructions for a pet owner with no medical training.

Absolute constraints:

- You may ONLY restate what is in the approved plan. Never introduce a medication, a \
dose, a frequency, a duration, or a care instruction that is not explicitly present.
- Never change a number. Durations, dose amounts and frequencies must match the plan \
exactly.
- Do not give dosing detail beyond what the plan states, and do not explain how to \
adjust a dose. Where the plan prescribes medication, refer the owner to the label and \
the veterinarian's instructions.
- Do not diagnose, predict outcomes, or speculate about what a symptom might mean.
- Do not invent an emergency-contact number, clinic name, or address.

Style:

- Warm, plain language at roughly a sixth-grade reading level. Short sentences.
- Address the owner directly and use the animal's name.
- One idea per paragraph. Aim for 4-6 short paragraphs.
- Where the plan says to monitor something, say plainly what to look for and tell the \
owner to contact the practice if they see it."""


def write_discharge(
    plan: TreatmentPlan,
    patient: Patient,
    *,
    violation_feedback: str | None = None,
) -> DischargeInstructions:
    """Rewrite the approved plan for the owner.

    ``violation_feedback`` is supplied by the grounding check on a retry: it names the
    exact terms that were not in the approved plan so the second attempt can drop them.
    """
    plan_lines: list[str] = []
    for med in plan.medications:
        plan_lines.append(f"- Medication: {med.as_text()}")
    for item in plan.activity_restrictions:
        plan_lines.append(f"- Activity restriction: {item}")
    for item in plan.wound_care:
        plan_lines.append(f"- Wound care: {item}")
    for item in plan.monitoring:
        plan_lines.append(f"- Monitor: {item}")
    if plan.recheck:
        plan_lines.append(f"- Recheck: {plan.recheck}")

    user = f"""Patient: {patient.name}, a {patient.age}-year-old {patient.breed}.
Procedure: {patient.procedure}, performed {patient.procedure_date}.
Owner: {patient.owner}

APPROVED PLAN (the complete and only source of clinical content):
{chr(10).join(plan_lines)}

Write the discharge instructions."""

    if violation_feedback:
        user += f"""

IMPORTANT — your previous attempt was rejected by an automated safety check. It \
contained clinical terms that do not appear in the approved plan above:
{violation_feedback}

Rewrite it. Use only medications, doses and numbers that appear in the approved plan."""

    kind = "discharge_retry" if violation_feedback else "discharge"
    return parse_structured(kind, SYSTEM, user, DischargeInstructions)
