"""Event log / fold tests: state and timeline are derived, never mutated."""

from __future__ import annotations

import pytest

from core.models import Observation, Patient
from core.state import CaseStore
from data.seed import PATIENT


@pytest.fixture
def store() -> CaseStore:
    return CaseStore(patient=Patient(**PATIENT))


def _alert_payload(alert_id="A1", level="REVIEW", day=2):
    return {
        "alert_id": alert_id,
        "day": day,
        "level": level,
        "fired_rule_ids": ["R-REV-01"],
        "reasons": ["Increasing surgical-site swelling"],
        "observation": Observation(incision_swelling="increasing").model_dump(),
    }


def test_initial_state(store):
    state = store.state
    assert state.stage == "VISIT"
    assert state.day == 0
    assert state.soap_approved is False
    assert state.risk_status == "NORMAL"


def test_stage_progresses_through_the_workflow(store):
    store.append("VISIT_RECORDED")
    assert store.state.stage == "DOCUMENTATION_PENDING"

    store.append("SOAP_DRAFTED")
    assert store.state.stage == "PLAN_APPROVAL"

    store.append("SOAP_APPROVED")
    assert store.state.soap_approved is True

    store.append("INSTRUCTIONS_SENT")
    assert store.state.stage == "DISCHARGED"
    assert store.state.instructions_sent is True

    store.append("FOLLOWUP_SCHEDULED", {"date": "2026-09-13"})
    assert store.state.stage == "MONITORING"
    assert store.state.next_followup == "2026-09-13"


def test_clock_advances(store):
    assert store.state.day == 0
    store.advance_clock()
    assert store.state.day == 1
    store.advance_clock(day=4)
    assert store.state.day == 4


def test_events_inherit_the_current_day(store):
    store.advance_clock(day=2)
    event = store.append("FOLLOWUP_RECORDED", {"level": "NORMAL"})
    assert event.day == 2


def test_open_alert_drives_the_stage(store):
    store.append("INSTRUCTIONS_SENT")
    store.append("ALERT_RAISED", _alert_payload(level="REVIEW"))
    state = store.state
    assert state.stage == "REVIEW_REQUIRED"
    assert state.risk_status == "REVIEW"
    assert len(state.open_alerts) == 1


def test_urgent_alert_outranks_review(store):
    store.append("ALERT_RAISED", _alert_payload(alert_id="A1", level="REVIEW"))
    store.append("ALERT_RAISED", _alert_payload(alert_id="A2", level="URGENT"))
    state = store.state
    assert state.stage == "URGENT_ESCALATION"
    assert state.risk_status == "URGENT"


def test_a_later_normal_followup_does_not_clear_an_open_alert(store):
    """The case must not drift back to MONITORING while a human still owes a
    decision on an unresolved escalation."""
    store.append("ALERT_RAISED", _alert_payload(level="URGENT"))
    store.advance_clock(day=4)
    store.append("FOLLOWUP_RECORDED", {"level": "NORMAL"})
    store.append("FOLLOWUP_SCHEDULED", {"date": "2026-09-19"})

    state = store.state
    assert state.stage == "URGENT_ESCALATION"
    assert state.risk_status == "URGENT"


def test_resolving_an_alert_releases_the_stage(store):
    store.append("FOLLOWUP_SCHEDULED", {"date": "2026-09-14"})
    store.append("ALERT_RAISED", _alert_payload(alert_id="A1"))
    assert store.state.stage == "REVIEW_REQUIRED"

    store.append("ALERT_RESOLVED", {"alert_id": "A1"})
    state = store.state
    assert state.open_alerts == []
    assert state.stage == "MONITORING"


def test_completed_overrides_everything(store):
    store.append("ALERT_RAISED", _alert_payload(level="URGENT"))
    store.append("CASE_COMPLETED")
    assert store.state.stage == "COMPLETED"


# ------------------------------------------------------------------- timeline


def test_timeline_skips_mechanical_events(store):
    store.append("VISIT_RECORDED")
    store.advance_clock()
    store.append("FOLLOWUP_STARTED")
    labels = [e.label for e in store.timeline()]
    assert "Visit recorded" in labels
    assert all("Day" not in lbl for lbl in labels)
    assert len(store.timeline()) == 1


def test_timeline_dates_offset_from_the_procedure_date(store):
    store.advance_clock(day=2)
    store.append("FOLLOWUP_RECORDED", {"level": "NORMAL", "reasons": []})
    entry = store.timeline()[-1]
    assert entry.day == 2
    assert entry.date == "2026-09-14"  # procedure 2026-09-12 + 2


def test_alert_timeline_entry_cites_rule_ids(store):
    store.advance_clock(day=2)
    store.append("ALERT_RAISED", _alert_payload(level="REVIEW", day=2))
    entry = store.timeline()[-1]
    assert entry.level == "REVIEW"
    assert "R-REV-01" in entry.detail
    assert "swelling" in entry.detail.lower()


def test_urgent_alert_gets_its_own_label(store):
    store.append("ALERT_RAISED", _alert_payload(level="URGENT"))
    assert "URGENT" in store.timeline()[-1].label


def test_normal_followup_reads_as_no_concerns(store):
    store.append("FOLLOWUP_RECORDED", {"level": "NORMAL", "reasons": []})
    assert store.timeline()[-1].detail == "No concerning findings"


# ---------------------------------------------------------------- persistence


def test_round_trips_through_disk(store, tmp_path):
    store.append("VISIT_RECORDED")
    store.append("SOAP_APPROVED")
    store.advance_clock(day=2)
    store.append("ALERT_RAISED", _alert_payload())

    path = tmp_path / "state.json"
    store.save(path)
    reloaded = CaseStore.load(path)

    assert len(reloaded.events) == len(store.events)
    assert reloaded.state.model_dump() == store.state.model_dump()
    assert reloaded.patient.name == "Bella"


def test_sequence_numbers_are_contiguous(store):
    for _ in range(5):
        store.append("FOLLOWUP_STARTED")
    assert [e.seq for e in store.events] == [1, 2, 3, 4, 5]
