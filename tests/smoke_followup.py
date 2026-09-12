"""End-to-end follow-up walk against a running server. Needs a live server.

    python tests/smoke_followup.py

Runs the demo's two follow-up threads: the day-1 normal check-in, and the day-2
thread where "it looks a little weird" has to become structured findings before the
rules engine can act. Optionally runs the URGENT closer with --urgent.
"""

from __future__ import annotations

import sys

from smoke_api import call, rule  # same directory


def show_transcript(session: dict) -> None:
    for turn in session["transcript"]:
        who = "agent" if turn["role"] == "agent" else "OWNER"
        print(f"  {who:>5}: {turn['text']}")


def show_outcome(session: dict) -> None:
    t = session["triage"]
    slots = {
        k: v for k, v in session["observation"].items()
        if v not in ("unknown", None)
    }
    print(f"\n  stop_reason = {session['stop_reason']}")
    print(f"  TRIAGE      = {t['level']}  rules={t['fired_rule_ids']}")
    for reason in t["reasons"]:
        print(f"                - {reason}")
    print(f"  slots filled= {slots}")


def run_thread(day: int, replies: list[str], expect_level: str) -> dict:
    rule(f"day {day} check-in")
    state = call("POST", "/api/followup/start", {"day": day})
    print(f"  agent: {state['session']['transcript'][0]['text']}")

    for reply in replies:
        state = call("POST", "/api/followup/reply", {"text": reply})
        session = state["session"]
        print(f"  OWNER: {reply}")
        print(f"  agent: {session['transcript'][-1]['text']}")
        if session["complete"]:
            break

    session = state["session"]
    show_outcome(session)

    assert session["complete"], (
        f"conversation did not terminate after {len(replies)} replies "
        f"(asked {session['questions_asked']})"
    )
    actual = session["triage"]["level"]
    assert actual == expect_level, f"expected {expect_level}, got {actual}"
    print(f"\n  OK — {expect_level} as expected")
    return state


def main() -> int:
    rule("setting up: notes -> approve")
    call("POST", "/api/reset")
    notes = call("GET", "/api/seed-notes")["notes"]
    state = call("POST", "/api/visit/notes", {"text": notes})
    state = call("POST", "/api/visit/approve", {"soap": state["soap"]})
    print(f"  approved; grounding passed={state['grounding']['passed']}")

    call("POST", "/api/clock/advance", {"day": 1})
    from data.seed import DEMO_REPLIES

    run_thread(1, DEMO_REPLIES[1], expect_level="NORMAL")

    call("POST", "/api/clock/advance", {"day": 2})
    state = run_thread(2, DEMO_REPLIES[2], expect_level="REVIEW")

    rule("alert raised for veterinary staff")
    alerts = state["alerts"]
    assert alerts, "no alert was raised for a REVIEW case"
    for a in alerts:
        print(f"  [{a['level']}] day {a['day']}  rules={a['fired_rule_ids']}")
        for reason in a["reasons"]:
            print(f"      - {reason}")
    assert state["case"]["stage"] == "REVIEW_REQUIRED", state["case"]["stage"]
    print(f"  case stage = {state['case']['stage']}")

    if "--urgent" in sys.argv:
        call("POST", "/api/clock/advance", {"day": 4})
        state = run_thread(4, ["There's blood soaking through the bandage."],
                           expect_level="URGENT")
        session = state["session"]
        assert session["stop_reason"] == "urgent", session["stop_reason"]
        assert session["questions_asked"] == 1, "agent kept asking through an emergency"
        print("  questioning halted on the first reply")

    rule("timeline")
    for e in call("GET", "/api/state")["timeline"]:
        detail = f"  — {e['detail']}" if e["detail"] else ""
        print(f"  {e['date']}  day {e['day']}  {e['label']}{detail}")

    print("\nFOLLOW-UP SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
