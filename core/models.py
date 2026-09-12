"""Domain models.

Two deliberate departures from the original brief:

1. Every clinical field is an enum with an explicit ``unknown`` member. The brief mixed
   booleans and strings, which cannot express "minor vs heavy bleeding" — exactly the
   distinction that separates NORMAL from URGENT — and had no way to say "the owner has
   not answered yet".
2. The treatment plan is structured rather than prose. The grounding check builds its
   allowlist from ``TreatmentPlan.medications``, so drug names and doses have to be
   machine-readable, not buried in a paragraph.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- enums

TriageLevel = Literal["NORMAL", "REVIEW", "URGENT"]

#: Ordering for triage precedence. URGENT wins over REVIEW wins over NORMAL.
SEVERITY: dict[str, int] = {"NORMAL": 0, "REVIEW": 1, "URGENT": 2}

Stage = Literal[
    "VISIT",
    "DOCUMENTATION_PENDING",
    "PLAN_APPROVAL",
    "DISCHARGED",
    "MONITORING",
    "REVIEW_REQUIRED",
    "URGENT_ESCALATION",
    "COMPLETED",
]

EventType = Literal[
    "VISIT_RECORDED",
    "SOAP_DRAFTED",
    "SOAP_APPROVED",
    "INSTRUCTIONS_SENT",
    "FOLLOWUP_STARTED",
    "FOLLOWUP_RECORDED",
    "FOLLOWUP_SCHEDULED",
    "ALERT_RAISED",
    "ALERT_RESOLVED",
    "CLOCK_ADVANCED",
    "CASE_COMPLETED",
]

# ------------------------------------------------------------------------- patient


class Patient(BaseModel):
    patient_id: str
    name: str
    species: str
    breed: str
    age: int
    weight_kg: float
    owner: str
    procedure: str
    procedure_date: str  # ISO date; the single clock every "day N" offsets from

    def date_for_day(self, day: int) -> str:
        """Calendar date of post-operative day ``day``."""
        return (date.fromisoformat(self.procedure_date) + timedelta(days=day)).isoformat()


# ---------------------------------------------------------------------------- plan


class Medication(BaseModel):
    name: str
    dose: str
    frequency: str
    duration: str | None = None

    def as_text(self) -> str:
        parts = [self.name, self.dose, self.frequency]
        if self.duration:
            parts.append(f"for {self.duration}")
        return " ".join(p for p in parts if p)


class TreatmentPlan(BaseModel):
    """The clinical plan. Frozen at approval; the agent may never add to it."""

    medications: list[Medication] = Field(default_factory=list)
    activity_restrictions: list[str] = Field(default_factory=list)
    wound_care: list[str] = Field(default_factory=list)
    monitoring: list[str] = Field(default_factory=list)
    recheck: str | None = None


class SoapNote(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: TreatmentPlan

    def as_text(self) -> str:
        lines = [
            f"S:\n{self.subjective}",
            f"O:\n{self.objective}",
            f"A:\n{self.assessment}",
            "P:",
        ]
        for med in self.plan.medications:
            lines.append(f"  {med.as_text()}")
        for item in (
            self.plan.activity_restrictions
            + self.plan.wound_care
            + self.plan.monitoring
        ):
            lines.append(f"  {item}")
        if self.plan.recheck:
            lines.append(f"  Recheck: {self.plan.recheck}")
        return "\n".join(lines)


class DischargeInstructions(BaseModel):
    """Owner-facing rewrite of the approved plan. Paragraphs, not prose blob, so the
    UI can render them and the grounding check can report per-paragraph violations."""

    paragraphs: list[str] = Field(default_factory=list)

    def as_text(self) -> str:
        return "\n\n".join(self.paragraphs)


class GroundingViolation(BaseModel):
    kind: Literal["medication", "dose", "unverified_number"]
    term: str
    paragraph_index: int


class GroundingReport(BaseModel):
    passed: bool = True
    violations: list[GroundingViolation] = Field(default_factory=list)
    attempts: int = 1
    fell_back_to_template: bool = False


# --------------------------------------------------------------------- observation


class Observation(BaseModel):
    """Structured owner-reported findings for one follow-up.

    Everything defaults to ``unknown``: an unanswered question is a first-class state,
    not a missing key. ``unknown`` never matches a triage rule, so absent data can
    neither crash the evaluator nor raise a false alarm.
    """

    appetite: Literal["normal", "reduced", "none", "unknown"] = "unknown"
    drinking: Literal["normal", "reduced", "none", "unknown"] = "unknown"
    medication_given: Literal["yes", "no", "partial", "unknown"] = "unknown"
    incision_bleeding: Literal["none", "minor", "heavy", "unknown"] = "unknown"
    incision_swelling: Literal["none", "mild", "increasing", "unknown"] = "unknown"
    incision_discharge: Literal["none", "clear", "yellow", "green", "unknown"] = "unknown"
    incision_open: Literal["no", "yes", "unknown"] = "unknown"
    breathing: Literal["normal", "labored", "unknown"] = "unknown"
    demeanor: Literal["normal", "quiet", "lethargic", "distressed", "unknown"] = "unknown"
    pain_signs: Literal["none", "mild", "severe", "unknown"] = "unknown"
    vomiting_count: int | None = None

    def unknown_fields(self) -> list[str]:
        """Fields the owner has not yet resolved."""
        out = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value == "unknown" or value is None:
                out.append(name)
        return out

    def merge(self, updates: "Observation | dict[str, Any]") -> "Observation":
        """Return a copy with non-``unknown`` values from ``updates`` applied.

        Used by the intake loop: each owner reply refines the picture and must never
        blank out a field already established earlier in the conversation.
        """
        if isinstance(updates, Observation):
            updates = updates.model_dump()
        merged = self.model_dump()
        for key, value in updates.items():
            if key not in merged:
                continue
            if value is None or value == "unknown":
                continue
            merged[key] = value
        return Observation(**merged)


# ------------------------------------------------------------------------- triage


class TriageResult(BaseModel):
    level: TriageLevel = "NORMAL"
    fired_rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def is_urgent(self) -> bool:
        return self.level == "URGENT"

    @property
    def needs_attention(self) -> bool:
        return self.level in ("REVIEW", "URGENT")


class Alert(BaseModel):
    alert_id: str
    day: int
    level: TriageLevel
    fired_rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    observation: Observation = Field(default_factory=Observation)
    resolved: bool = False


# -------------------------------------------------------------------------- events


class Event(BaseModel):
    """One immutable fact. The case state and the timeline are folds over these."""

    seq: int
    type: EventType
    day: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class TimelineEntry(BaseModel):
    seq: int
    day: int
    date: str
    label: str
    detail: str | None = None
    level: TriageLevel | None = None


class CaseState(BaseModel):
    """Derived, never mutated directly."""

    patient_id: str
    stage: Stage = "VISIT"
    day: int = 0
    soap_approved: bool = False
    instructions_sent: bool = False
    next_followup: str | None = None
    risk_status: TriageLevel = "NORMAL"
    open_alerts: list[Alert] = Field(default_factory=list)
