"""Drive the pipeline from the terminal — useful before the UI exists, and the way
fixtures get recorded.

    python cli.py soap          draft the SOAP note from the seed notes
    python cli.py discharge     draft SOAP, treat it as approved, write owner text
    python cli.py status        show key / offline configuration
"""

from __future__ import annotations

import logging
import sys

from core.models import Patient
from data.seed import PATIENT, VET_NOTES
from llm.client import LLMUnavailable, has_api_key, is_offline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def cmd_status() -> None:
    print(f"offline mode : {is_offline()}")
    print(f"api key set  : {has_api_key()}")


def cmd_soap() -> Patient:
    from llm.soap import draft_soap

    patient = Patient(**PATIENT)
    _rule("VETERINARIAN'S NOTES")
    print(VET_NOTES)

    note = draft_soap(VET_NOTES, patient)
    _rule("DRAFT SOAP NOTE")
    print(note.as_text())

    _rule("STRUCTURED PLAN (what gets frozen on approval)")
    print(note.plan.model_dump_json(indent=2))
    return note


def cmd_discharge() -> None:
    from core.grounding import generate_grounded_discharge

    patient = Patient(**PATIENT)
    note = cmd_soap()

    # Stands in for the veterinarian pressing Approve.
    instructions, report = generate_grounded_discharge(note.plan, patient)

    _rule("OWNER DISCHARGE INSTRUCTIONS")
    print(instructions.as_text())

    _rule("GROUNDING CHECK")
    if report.fell_back_to_template:
        print(f"REJECTED after {report.attempts} attempts — showing the plan verbatim.")
    else:
        print(f"PASSED on attempt {report.attempts} — 0 unverified clinical claims.")
    for v in report.violations:
        print(f"  violation: {v.kind} '{v.term}' (paragraph {v.paragraph_index})")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "discharge"
    try:
        if command == "status":
            cmd_status()
        elif command == "soap":
            cmd_soap()
        elif command == "discharge":
            cmd_discharge()
        else:
            print(__doc__)
            return 2
    except LLMUnavailable as exc:
        print(f"\nLLM unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
