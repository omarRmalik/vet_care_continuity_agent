"""Seam 3: owner's natural language -> structured observations + the next question.

This is the only genuinely agentic part of the system. Everywhere else the model fills
in a blank at a point the orchestrator chose; here it decides what it still needs to
know and goes after it, turn by turn, until the picture is complete enough for the
rules engine to act on.

What it must not do: diagnose, advise treatment, reassure, or decide that something is
an emergency. It converts words into fields. ``core.rules`` decides what they mean.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.models import Observation, Patient, TreatmentPlan
from llm.client import parse_structured

#: Ask about danger before convenience. Safety-critical fields first, then the ones
#: that merely need a human to look, then general wellbeing.
QUESTION_PRIORITY: list[str] = [
    "incision_bleeding",
    "incision_open",
    "breathing",
    "pain_signs",
    "incision_swelling",
    "incision_discharge",
    "appetite",
    "drinking",
    "medication_given",
    "demeanor",
    "vomiting_count",
]

FIELD_HINTS: dict[str, str] = {
    "incision_bleeding": "none / minor (a spot or smear) / heavy (soaking, dripping)",
    "incision_open": "no / yes — any gap, opening, or visible tissue",
    "breathing": "normal / laboured — struggling, fast at rest, noisy",
    "pain_signs": "none / mild / severe — crying, guarding, cannot settle",
    "incision_swelling": "none / mild / increasing — bigger than yesterday",
    "incision_discharge": "none / clear / yellow / green",
    "appetite": "normal / reduced / none",
    "drinking": "normal / reduced / none",
    "medication_given": "yes / no / partial",
    "demeanor": "normal / quiet / lethargic / distressed",
    "vomiting_count": "number of times vomited today",
}


class IntakeTurn(BaseModel):
    """One pass over an owner reply."""

    observation_updates: Observation = Field(
        description="Only fields the owner's words actually support. Leave the rest unknown."
    )
    next_question: str | None = Field(
        default=None,
        description="One short question for the owner, or null if nothing more is needed.",
    )
    done: bool = Field(
        default=False, description="True when no further questions are required."
    )
    rationale: str = Field(
        default="", description="One sentence, for the clinical audit trail."
    )


SYSTEM = """You are a veterinary post-operative follow-up assistant. You are talking to \
a pet owner at home, on behalf of their veterinary practice. Your job is to turn what \
they tell you into structured clinical observations, and to ask for the specific \
details they leave out.

HOW TO ASK

- When you are clarifying something vague ("it looks weird", "she's a bit off"), ask \
ONE narrow question at a time. Do not guess what they mean. Ask what they can actually \
see, then ask whether it has changed since yesterday.
- When the owner is reporting that things are going well, you may confirm two or three \
remaining routine items in a single short question. Do not march someone whose animal \
is fine through a six-question checklist.
- Prefer the highest-priority unresolved field in the list you are given.
- Do not re-ask something the owner has already answered clearly.
- Set `done` to true as soon as you have enough to tell whether a person needs to look \
at this animal. You do NOT need to fill every field — an uneventful recovery should \
take two or three exchanges, not ten.
- Never ask the same thing twice in different words. If you have already asked about \
bleeding and the owner answered around it, move on.

WHEN THE CASE IS ALREADY GOING TO A HUMAN

You may be told that concerning findings have already been recorded. When you are, the \
case is going to the veterinary team regardless of what you ask next, so stop working \
through routine questions. Ask ONE combined question covering only the safety-critical \
items still unknown — bleeding, whether the incision is open, breathing, and signs of \
pain — and then set `done` to true on the next reply. Do not tell the owner why you \
are asking.

HOW TO RECORD

- Set a field ONLY when the owner's own words support it. If they said "a little \
yellow fluid", `incision_discharge` is "yellow". If they only said "it looks weird", \
every field stays unknown until you have asked.
- "More than yesterday" about swelling means `incision_swelling` is "increasing".
- Never infer one field from another. Swelling does not imply discharge.
- Leave anything you have not been told as "unknown". Guessing is worse than a gap: a \
wrong value can suppress an escalation.

WHAT YOU MUST NOT DO

- Do not diagnose, or name a possible condition. Never say "infection", "abscess", \
"dehiscence", or suggest what the finding might be.
- Do not give treatment advice, recommend medication, or tell the owner to change a \
dose.
- Do not reassure the owner that something is fine or normal, and do not tell them \
something is serious. You are not the clinical decision-maker; the veterinary team is.
- Do not tell the owner you are escalating. The practice decides what happens next.
- Keep a warm, calm, unhurried tone throughout."""


def _plan_summary(plan: TreatmentPlan) -> str:
    lines: list[str] = []
    for med in plan.medications:
        lines.append(f"- Medication: {med.as_text()}")
    for item in plan.activity_restrictions:
        lines.append(f"- Activity: {item}")
    for item in plan.wound_care:
        lines.append(f"- Wound care: {item}")
    for item in plan.monitoring:
        lines.append(f"- Monitor: {item}")
    if plan.recheck:
        lines.append(f"- Recheck: {plan.recheck}")
    return "\n".join(lines) or "- (no plan recorded)"


def _unresolved(observation: Observation) -> list[str]:
    unknown = set(observation.unknown_fields())
    return [f for f in QUESTION_PRIORITY if f in unknown]


def next_turn(
    patient: Patient,
    plan: TreatmentPlan,
    observation: Observation,
    transcript: list[dict],
    day: int,
    turns_remaining: int,
    escalation_pending: bool = False,
) -> IntakeTurn:
    unresolved = _unresolved(observation)
    known = {
        k: v
        for k, v in observation.model_dump().items()
        if v not in ("unknown", None)
    }

    conversation = "\n".join(
        f"{'You' if t['role'] == 'agent' else 'Owner'}: {t['text']}" for t in transcript
    )

    outstanding = "\n".join(
        f"- {f}: {FIELD_HINTS.get(f, '')}" for f in unresolved
    ) or "- (none — everything important is resolved)"

    # The deterministic engine tells the model how to spend its remaining questions.
    # It does not tell it what the findings mean, and the model still does not decide
    # the triage level — that stays in core.rules.
    safety_unknown = [
        f for f in ("incision_bleeding", "incision_open", "breathing", "pain_signs")
        if f in unresolved
    ]
    if not escalation_pending:
        escalation_note = ""
    elif safety_unknown:
        escalation_note = (
            "\nSTATUS: concerning findings have already been recorded; this case is "
            "going to the veterinary team. Ask ONE combined question covering only "
            f"these remaining safety-critical items: {', '.join(safety_unknown)}. "
            "Then finish.\n"
        )
    else:
        escalation_note = (
            "\nSTATUS: concerning findings have been recorded and every "
            "safety-critical item is resolved. Set done to true and ask nothing "
            "further.\n"
        )

    user = f"""Patient: {patient.name}, a {patient.age}-year-old {patient.breed}.
Procedure: {patient.procedure}, {patient.procedure_date}. Today is post-operative day {day}.

APPROVED PLAN (context for what matters — do not quote doses to the owner):
{_plan_summary(plan)}

ALREADY ESTABLISHED THIS CONVERSATION:
{known or "(nothing yet)"}

STILL UNRESOLVED, highest priority first:
{outstanding}
{escalation_note}
CONVERSATION SO FAR:
{conversation}

You may ask at most {turns_remaining} more question(s) before this check-in ends.
Record whatever the owner's latest message supports, then ask the single most useful \
next question — or set done to true if you have what you need."""

    # No fixture fallback: replaying a near-miss here would inject one conversation's
    # answer into another. Better to say we have no recording than to invent a turn.
    return parse_structured(
        "intake", SYSTEM, user, IntakeTurn, effort="low", allow_fallback=False
    )
