"""Veterinary Care Continuity Agent — HTTP surface.

The orchestrator lives here and it is ordinary code: a fixed sequence with a human
approval gate. The model is called at named seams (draft the SOAP, rewrite the plan,
read an owner reply); it never decides what happens next and never decides whether a
case escalates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.followup import FollowupSession, start_session, submit_reply
from core.grounding import generate_grounded_discharge
from core.models import Alert, Patient, SoapNote, TreatmentPlan
from core.state import CaseStore
from data.seed import DEMO_REPLIES, FOLLOWUP_DAYS, PATIENT, VET_NOTES
from llm.client import LLMUnavailable, is_offline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vet_agent.app")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATE_PATH = BASE_DIR / "state.json"

app = FastAPI(title="Veterinary Care Continuity Agent")

PATIENT_MODEL = Patient(**PATIENT)


def _new_store() -> CaseStore:
    if STATE_PATH.exists():
        try:
            return CaseStore.load(STATE_PATH)
        except Exception:  # a malformed snapshot must not brick the demo
            log.warning("could not load %s; starting fresh", STATE_PATH)
    return CaseStore(patient=PATIENT_MODEL)


store = _new_store()

#: The in-flight check-in. Completed ones live in the event log; this is only the
#: conversation currently on screen, so a restart mid-conversation simply drops it.
session: FollowupSession | None = None


def _persist() -> None:
    store.save(STATE_PATH)


def _approved_plan() -> TreatmentPlan:
    soap = store.last_payload("SOAP_APPROVED").get("soap")
    if not soap:
        raise HTTPException(
            status_code=409,
            detail="The treatment plan has not been approved yet.",
        )
    return TreatmentPlan(**soap["plan"])


def _next_followup_day(current: int) -> int | None:
    for day in FOLLOWUP_DAYS:
        if day > current:
            return day
    return None


def _snapshot() -> dict[str, Any]:
    """Everything the UI needs, in one poll."""
    state = store.state
    return {
        "patient": store.patient.model_dump(),
        "case": state.model_dump(),
        "date": store.patient.date_for_day(state.day),
        "offline": is_offline(),
        "notes": store.last_payload("VISIT_RECORDED").get("notes"),
        "soap": store.last_payload("SOAP_DRAFTED").get("soap"),
        "approved_soap": store.last_payload("SOAP_APPROVED").get("soap"),
        "discharge": store.last_payload("INSTRUCTIONS_SENT").get("instructions"),
        "grounding": store.last_payload("INSTRUCTIONS_SENT").get("grounding"),
        "followup_days": FOLLOWUP_DAYS,
        "timeline": [e.model_dump() for e in store.timeline()],
        "alerts": [a.model_dump() for a in state.open_alerts],
        "session": session.model_dump() if session else None,
        "demo_replies": DEMO_REPLIES.get(state.day, []),
    }


# --------------------------------------------------------------------------- read


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "patient": store.patient.name, "offline": is_offline()}


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _snapshot()


@app.get("/api/seed-notes")
def seed_notes() -> dict[str, str]:
    """The demo's starting notes, so the operator doesn't have to type them on stage."""
    return {"notes": VET_NOTES}


# -------------------------------------------------------------------- visit flow


@app.post("/api/visit/notes")
def submit_notes(payload: dict = Body(...)) -> dict[str, Any]:
    """Seam 1: the vet's free text becomes a structured draft SOAP note."""
    from llm.soap import draft_soap

    notes = (payload.get("text") or "").strip()
    if not notes:
        raise HTTPException(status_code=400, detail="Clinical notes are required.")

    store.append("VISIT_RECORDED", {"notes": notes})
    try:
        note = draft_soap(notes, store.patient)
    except LLMUnavailable as exc:
        # Roll the visit event back so a retry doesn't stack duplicates.
        store.events.pop()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    store.append("SOAP_DRAFTED", {"soap": note.model_dump()})
    _persist()
    return _snapshot()


@app.post("/api/visit/approve")
def approve(payload: dict = Body(...)) -> dict[str, Any]:
    """The human gate. Nothing downstream may run until this has happened.

    The vet's edits are authoritative: whatever SOAP note arrives here is what gets
    frozen, and the discharge text is generated from *that*, not from the draft.
    """
    try:
        note = SoapNote(**payload.get("soap", {}))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid SOAP note: {exc}") from exc

    if not store.last("SOAP_DRAFTED"):
        raise HTTPException(status_code=409, detail="No SOAP note has been drafted yet.")

    store.append("SOAP_APPROVED", {"soap": note.model_dump()})

    # Seam 2, with the grounding check wrapped around it.
    try:
        instructions, report = generate_grounded_discharge(note.plan, store.patient)
    except LLMUnavailable as exc:
        store.events.pop()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    store.append(
        "INSTRUCTIONS_SENT",
        {"instructions": instructions.model_dump(), "grounding": report.model_dump()},
    )

    first = _next_followup_day(store.state.day)
    if first is not None:
        store.append(
            "FOLLOWUP_SCHEDULED",
            {"day": first, "date": store.patient.date_for_day(first)},
        )

    _persist()
    return _snapshot()


# --------------------------------------------------------------------- follow-up


@app.post("/api/followup/start")
def followup_start(payload: dict = Body(default={})) -> dict[str, Any]:
    """Open a check-in for the current simulated day."""
    global session
    _approved_plan()  # 409 if the vet has not approved yet

    day = int(payload.get("day", store.state.day))
    session = start_session(day, store.patient)
    store.append("FOLLOWUP_STARTED", {"day": day}, day=day)
    _persist()
    return _snapshot()


@app.post("/api/followup/reply")
def followup_reply(payload: dict = Body(...)) -> dict[str, Any]:
    """Seam 3, wrapped in the bounded loop.

    The model reads the reply and proposes the next question; ``core.rules`` — not the
    model — decides the triage level, and an URGENT result ends the conversation here
    rather than after the remaining questions.
    """
    global session
    if session is None or session.complete:
        raise HTTPException(status_code=409, detail="No check-in is in progress.")

    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="A reply is required.")

    plan = _approved_plan()
    next_day = _next_followup_day(session.day)

    try:
        submit_reply(
            session,
            text,
            store.patient,
            plan,
            next_date=store.patient.date_for_day(next_day) if next_day else None,
        )
    except LLMUnavailable as exc:
        session.transcript.pop()  # drop the un-processed reply so it can be retried
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if session.complete:
        _record_completed_followup(session, next_day)

    _persist()
    return _snapshot()


def _record_completed_followup(finished: FollowupSession, next_day: int | None) -> None:
    """Commit a finished check-in to the event log, raising an alert if warranted."""
    triage = finished.triage
    store.append(
        "FOLLOWUP_RECORDED",
        {
            "day": finished.day,
            "level": triage.level,
            "reasons": triage.reasons,
            "fired_rule_ids": triage.fired_rule_ids,
            "observation": finished.observation.model_dump(),
            "stop_reason": finished.stop_reason,
            "rationale": finished.rationale,
            "transcript": [t.model_dump() for t in finished.transcript],
        },
        day=finished.day,
    )

    if triage.needs_attention:
        alert = Alert(
            alert_id=f"A{len(store.events)}",
            day=finished.day,
            level=triage.level,
            fired_rule_ids=triage.fired_rule_ids,
            reasons=triage.reasons,
            observation=finished.observation,
        )
        store.append("ALERT_RAISED", alert.model_dump(), day=finished.day)

    if next_day is not None:
        store.append(
            "FOLLOWUP_SCHEDULED",
            {"day": next_day, "date": store.patient.date_for_day(next_day)},
            day=finished.day,
        )


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str) -> dict[str, Any]:
    """Vet acknowledges an alert. Keeps the stage honest rather than letting an
    unresolved escalation sit behind a green header."""
    store.append("ALERT_RESOLVED", {"alert_id": alert_id})
    _persist()
    return _snapshot()


# ------------------------------------------------------------------------- clock


@app.post("/api/clock/advance")
def advance_clock(payload: dict = Body(default={})) -> dict[str, Any]:
    """Simulated time. A real deployment would drive this from the practice's
    follow-up protocol; for the demo the days are advanced by hand."""
    global session
    day = payload.get("day")
    store.advance_clock(day=int(day) if day is not None else None)
    session = None  # a new day starts a new conversation
    _persist()
    return _snapshot()


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    """Clean slate, so the demo can be run twice."""
    global session
    session = None
    store.reset()
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    return _snapshot()


# -------------------------------------------------------------------------- pages


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
