"""Append-only event log; case state and timeline are folds over it.

The brief modelled the case as a mutable dict, which throws away the audit trail its
own patient-timeline section requires. Here every change appends an immutable
:class:`Event`, and both the workflow stage and the timeline are *derived*. Nothing
overwrites history, so "what did the agent know, and when" is always answerable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.models import (
    SEVERITY,
    Alert,
    CaseState,
    Event,
    EventType,
    Patient,
    TimelineEntry,
    TriageLevel,
)

#: Mechanical events that carry no clinical meaning for the owner-facing timeline.
_TIMELINE_SKIP = {"CLOCK_ADVANCED", "FOLLOWUP_STARTED"}

_LABELS: dict[str, str] = {
    "VISIT_RECORDED": "Visit recorded",
    "SOAP_DRAFTED": "SOAP note drafted",
    "SOAP_APPROVED": "SOAP note approved by veterinarian",
    "INSTRUCTIONS_SENT": "Discharge instructions provided",
    "FOLLOWUP_RECORDED": "Follow-up completed",
    "FOLLOWUP_SCHEDULED": "Next follow-up scheduled",
    "ALERT_RAISED": "Veterinary attention requested",
    "ALERT_RESOLVED": "Alert resolved",
    "CASE_COMPLETED": "Case closed",
}


def fold(patient: Patient, events: list[Event]) -> CaseState:
    """Derive the current case state from the full event history."""
    state = CaseState(patient_id=patient.patient_id)
    alerts: dict[str, Alert] = {}
    last_followup_level: TriageLevel = "NORMAL"
    completed = False

    for event in events:
        payload = event.payload
        match event.type:
            case "VISIT_RECORDED":
                state.stage = "DOCUMENTATION_PENDING"
            case "SOAP_DRAFTED":
                state.stage = "PLAN_APPROVAL"
            case "SOAP_APPROVED":
                state.soap_approved = True
            case "INSTRUCTIONS_SENT":
                state.instructions_sent = True
                state.stage = "DISCHARGED"
            case "FOLLOWUP_STARTED":
                state.stage = "MONITORING"
            case "FOLLOWUP_RECORDED":
                last_followup_level = payload.get("level", "NORMAL")
            case "FOLLOWUP_SCHEDULED":
                state.next_followup = payload.get("date")
                state.stage = "MONITORING"
            case "ALERT_RAISED":
                alert = Alert(**payload)
                alerts[alert.alert_id] = alert
            case "ALERT_RESOLVED":
                alert_id = payload.get("alert_id")
                if alert_id in alerts:
                    alerts[alert_id].resolved = True
            case "CLOCK_ADVANCED":
                state.day = payload.get("day", state.day)
            case "CASE_COMPLETED":
                completed = True

    open_alerts = [a for a in alerts.values() if not a.resolved]
    state.open_alerts = open_alerts

    # An open alert outranks the monitoring stage: a case with an unresolved URGENT
    # does not quietly drift back to MONITORING because a later day passed normally.
    if open_alerts:
        worst = max(open_alerts, key=lambda a: SEVERITY[a.level])
        state.risk_status = worst.level
        state.stage = (
            "URGENT_ESCALATION" if worst.level == "URGENT" else "REVIEW_REQUIRED"
        )
    else:
        state.risk_status = last_followup_level

    if completed:
        state.stage = "COMPLETED"

    return state


def build_timeline(patient: Patient, events: list[Event]) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for event in events:
        if event.type in _TIMELINE_SKIP:
            continue
        label = _LABELS.get(event.type, event.type)
        detail: str | None = None
        level: TriageLevel | None = None

        if event.type == "FOLLOWUP_RECORDED":
            level = event.payload.get("level")
            reasons = event.payload.get("reasons") or []
            detail = "; ".join(reasons) if reasons else "No concerning findings"
        elif event.type == "ALERT_RAISED":
            level = event.payload.get("level")
            label = (
                "URGENT — immediate escalation"
                if level == "URGENT"
                else "Veterinary review requested"
            )
            reasons = event.payload.get("reasons") or []
            rule_ids = event.payload.get("fired_rule_ids") or []
            detail = "; ".join(reasons)
            if rule_ids:
                detail += f"  [{', '.join(rule_ids)}]"
        elif event.type == "FOLLOWUP_SCHEDULED":
            detail = event.payload.get("date")

        entries.append(
            TimelineEntry(
                seq=event.seq,
                day=event.day,
                date=patient.date_for_day(event.day),
                label=label,
                detail=detail,
                level=level,
            )
        )
    return entries


class CaseStore:
    """In-process event log with a JSON snapshot. One patient, one case — enough
    for the MVP, and a restart gives a clean demo."""

    def __init__(self, patient: Patient, events: list[Event] | None = None):
        self.patient = patient
        self.events: list[Event] = events or []

    # ------------------------------------------------------------------ mutation

    def append(
        self,
        type: EventType,
        payload: dict[str, Any] | None = None,
        day: int | None = None,
    ) -> Event:
        event = Event(
            seq=len(self.events) + 1,
            type=type,
            day=self.state.day if day is None else day,
            payload=payload or {},
        )
        self.events.append(event)
        return event

    def advance_clock(self, day: int | None = None) -> Event:
        target = self.state.day + 1 if day is None else day
        return self.append("CLOCK_ADVANCED", {"day": target}, day=target)

    # -------------------------------------------------------------------- derived

    @property
    def state(self) -> CaseState:
        return fold(self.patient, self.events)

    def timeline(self) -> list[TimelineEntry]:
        return build_timeline(self.patient, self.events)

    def last(self, type: EventType) -> Event | None:
        """Most recent event of a type, or None. Documents are read back out of the
        log rather than cached alongside it, so the log stays the only source."""
        for event in reversed(self.events):
            if event.type == type:
                return event
        return None

    def last_payload(self, type: EventType) -> dict[str, Any]:
        event = self.last(type)
        return event.payload if event else {}

    def reset(self) -> None:
        self.events = []

    # ---------------------------------------------------------------- persistence

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient": self.patient.model_dump(),
            "events": [e.model_dump(mode="json") for e in self.events],
        }

    def save(self, path: Path | str) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path | str) -> "CaseStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            patient=Patient(**data["patient"]),
            events=[Event(**e) for e in data["events"]],
        )
