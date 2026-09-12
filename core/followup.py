"""The follow-up conversation: a bounded slot-filling loop.

The brief describes an agent that asks clarifying questions but never says when it
stops. Left open, an evasive or confused owner loops forever. Four exits here:

* the rules engine returns URGENT — questioning stops **immediately**. Continuing down
  a questionnaire while someone is describing an emergency is the wrong behaviour, and
  it is the exit the brief misses entirely;
* the model reports it has what it needs;
* nothing rule-relevant is still unknown;
* the question budget runs out.

Triage runs after *every* reply, not once at the end.
"""

from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field

from core.models import Observation, Patient, TreatmentPlan, TriageResult
from core.rules import evaluate

#: Total questions per check-in, including the opener.
MAX_QUESTIONS = 6

#: The fields that separate "a human should look at this" from "call the clinic now".
SAFETY_CRITICAL = ("incision_bleeding", "incision_open", "breathing", "pain_signs")

StopReason = Literal["urgent", "model_done", "resolved", "budget"]


def safety_unknowns(observation: Observation) -> list[str]:
    return [f for f in SAFETY_CRITICAL if getattr(observation, f) == "unknown"]


class Turn(BaseModel):
    role: Literal["agent", "owner"]
    text: str


class FollowupSession(BaseModel):
    day: int
    observation: Observation = Field(default_factory=Observation)
    transcript: list[Turn] = Field(default_factory=list)
    questions_asked: int = 0
    complete: bool = False
    stop_reason: StopReason | None = None
    triage: TriageResult = Field(default_factory=TriageResult)
    rationale: str = ""


def opening_question(patient: Patient) -> str:
    return (
        f"Hello — I'm checking in on {patient.name} for the practice. "
        f"How has {patient.name} been today, and how does the incision look?"
    )


def start_session(day: int, patient: Patient) -> FollowupSession:
    """Open a check-in. The first question is fixed rather than generated: it costs
    nothing, it cannot drift, and it keeps the demo's opening beat identical."""
    return FollowupSession(
        day=day,
        transcript=[Turn(role="agent", text=opening_question(patient))],
        questions_asked=1,
    )


def _closing_message(level: str, patient: Patient, next_date: str | None) -> str:
    """Deterministic. The model is never allowed to decide how a check-in ends —
    what the owner is told at an escalation is a clinic policy question, not a
    generative one."""
    if level == "URGENT":
        return (
            f"Thank you for telling me. I've recorded this and notified the veterinary "
            f"team straight away. Please contact the practice now on the number on "
            f"{patient.name}'s discharge notes."
        )
    if level == "REVIEW":
        return (
            "Thank you — I've recorded that and passed it to the veterinary team to "
            "review. Someone from the practice will be in touch."
        )
    when = f" I'll check in again on {next_date}." if next_date else ""
    return f"Thank you, that's all recorded.{when}"


def submit_reply(
    session: FollowupSession,
    text: str,
    patient: Patient,
    plan: TreatmentPlan,
    *,
    next_date: str | None = None,
    turn_fn: Callable | None = None,
) -> FollowupSession:
    """Process one owner reply and decide whether to keep asking.

    ``turn_fn`` is injected so the loop can be tested without the network.
    """
    if session.complete:
        raise ValueError("This check-in has already finished.")

    if turn_fn is None:
        from llm.intake import next_turn

        turn_fn = next_turn

    session.transcript.append(Turn(role="owner", text=text))

    turn = turn_fn(
        patient=patient,
        plan=plan,
        observation=session.observation,
        transcript=[t.model_dump() for t in session.transcript],
        day=session.day,
        turns_remaining=max(0, MAX_QUESTIONS - session.questions_asked),
        # From the *previous* evaluation: the rules engine steers how the model
        # spends its remaining questions, without ceding the triage decision to it.
        escalation_pending=session.triage.needs_attention,
    )

    session.observation = session.observation.merge(turn.observation_updates)
    session.rationale = turn.rationale or session.rationale

    # Re-evaluated after every single reply — an emergency must not wait for the end
    # of the questionnaire.
    session.triage = evaluate(session.observation)

    if session.triage.is_urgent:
        return _finish(session, "urgent", patient, next_date)

    # The case is already going to a human and nothing dangerous is still unknown.
    # Further questions cannot change the outcome, so stop asking them — this is a
    # decision for the orchestrator, not something to negotiate with the model.
    if session.triage.needs_attention and not safety_unknowns(session.observation):
        return _finish(session, "resolved", patient, next_date)

    if turn.done or not turn.next_question:
        return _finish(session, "model_done", patient, next_date)

    if session.questions_asked >= MAX_QUESTIONS:
        return _finish(session, "budget", patient, next_date)

    session.transcript.append(Turn(role="agent", text=turn.next_question))
    session.questions_asked += 1
    return session


def _finish(
    session: FollowupSession,
    reason: StopReason,
    patient: Patient,
    next_date: str | None,
) -> FollowupSession:
    # Final evaluation knows the agent has stopped asking, which is what lets the
    # optional escalate_on_unknown policy see an unanswered safety question.
    session.triage = evaluate(session.observation, questioning_complete=True)
    session.complete = True
    session.stop_reason = reason
    session.transcript.append(
        Turn(
            role="agent",
            text=_closing_message(session.triage.level, patient, next_date),
        )
    )
    return session
