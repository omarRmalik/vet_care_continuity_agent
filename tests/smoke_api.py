"""End-to-end smoke test against a running server.

    python -m uvicorn app:app          (in one terminal, VET_AGENT_OFFLINE=1)
    python tests/smoke_api.py          (in another)

Not part of the pytest suite — it needs a live server. It walks the vet-side path the
demo follows and asserts the things a judge will actually look at.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read() or b"{}").get("detail", exc.reason)
        raise SystemExit(f"HTTP {exc.code} on {method} {path}: {detail}")


def rule(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def main() -> int:
    rule("reset")
    state = call("POST", "/api/reset")
    assert state["case"]["stage"] == "VISIT", state["case"]["stage"]
    assert state["timeline"] == []
    print(f"stage={state['case']['stage']}  offline={state['offline']}")

    rule("seed notes")
    notes = call("GET", "/api/seed-notes")["notes"]
    print(notes.splitlines()[0] + " …")

    rule("POST /api/visit/notes  (seam 1: notes -> SOAP)")
    state = call("POST", "/api/visit/notes", {"text": notes})
    soap = state["soap"]
    assert state["case"]["stage"] == "PLAN_APPROVAL", state["case"]["stage"]
    assert soap["plan"]["medications"], "no medications extracted"
    print(f"stage={state['case']['stage']}")
    print(f"meds={[m['name'] + ' ' + m['dose'] for m in soap['plan']['medications']]}")
    print(f"recheck={soap['plan']['recheck']!r}")
    assert state["discharge"] is None, "discharge leaked before approval!"
    print("discharge before approval: None  <- the human gate holds")

    rule("vet edits the note, then approves")
    # Stand in for the vet correcting a line in the browser.
    soap["assessment"] = soap["assessment"].rstrip(".") + ". Margins grossly complete."
    state = call("POST", "/api/visit/approve", {"soap": soap})
    assert state["case"]["soap_approved"] is True
    assert state["approved_soap"]["assessment"].endswith("Margins grossly complete.")
    print("edit preserved through approval:", state["approved_soap"]["assessment"][-30:])

    rule("discharge + grounding")
    grounding = state["grounding"]
    print(f"paragraphs={len(state['discharge']['paragraphs'])}")
    print(f"grounding passed={grounding['passed']} attempts={grounding['attempts']} "
          f"fallback={grounding['fell_back_to_template']}")
    assert grounding["passed"] is True
    print(state["discharge"]["paragraphs"][0][:90] + " …")

    rule("scheduling + stage")
    print(f"stage={state['case']['stage']}  next_followup={state['case']['next_followup']}")
    assert state["case"]["next_followup"], "no follow-up scheduled after discharge"

    rule("clock")
    state = call("POST", "/api/clock/advance", {})
    print(f"day={state['case']['day']}  date={state['date']}")
    assert state["case"]["day"] == 1

    rule("rejects an invalid SOAP")
    try:
        call("POST", "/api/visit/approve", {"soap": {"subjective": "only this"}})
        print("FAIL: invalid SOAP was accepted")
        return 1
    except SystemExit as exc:
        assert "400" in str(exc), exc
        print("400 as expected")

    rule("timeline")
    for entry in state["timeline"]:
        detail = f"  — {entry['detail']}" if entry["detail"] else ""
        print(f"  {entry['date']}  day {entry['day']}  {entry['label']}{detail}")

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
