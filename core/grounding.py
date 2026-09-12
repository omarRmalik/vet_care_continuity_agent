"""Verify that owner-facing text says nothing the approved plan does not.

The brief asserts "the AI should not independently create new treatments or change
medication instructions" but provides no mechanism. A prompt instruction is not a
control: it is unverified and fails silently. This module is the control.

It is deliberately deterministic — no model reviews the model. Three checks:

* **Medication** — a drug from the formulary that is not in the approved plan.
* **Dose** — a (number, unit) pair that does not appear in the plan.
* **Unverified number** — a number in a duration/frequency context that the plan
  never states.

Scope is honest about its limits: the formulary below is a demo-sized list, and a real
deployment would check against a veterinary formulary and the practice's own drug
database. What matters architecturally is that the check exists, runs on every
generation, and can reject output.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from core.models import (
    DischargeInstructions,
    GroundingReport,
    GroundingViolation,
    Patient,
    TreatmentPlan,
)

# Common small-animal drugs, including brand names and the human analgesics that are
# actively dangerous to suggest for a dog. Not exhaustive — see module docstring.
FORMULARY: frozenset[str] = frozenset(
    {
        "carprofen", "rimadyl", "meloxicam", "metacam", "firocoxib", "previcox",
        "deracoxib", "robenacoxib", "onsior", "grapiprant", "galliprant",
        "gabapentin", "tramadol", "amantadine", "buprenorphine", "butorphanol",
        "methadone", "fentanyl", "codeine", "ketamine", "acepromazine", "trazodone",
        "amoxicillin", "clavulanate", "clavamox", "cephalexin", "cefazolin",
        "cefpodoxime", "enrofloxacin", "baytril", "marbofloxacin", "metronidazole",
        "doxycycline", "clindamycin", "gentamicin", "neomycin", "mupirocin",
        "prednisone", "prednisolone", "dexamethasone", "maropitant", "cerenia",
        "ondansetron", "famotidine", "omeprazole", "sucralfate", "furosemide",
        "phenobarbital", "diphenhydramine", "benadryl", "chlorhexidine",
        "oclacitinib", "apoquel", "cytopoint", "ivermectin", "milbemycin",
        "praziquantel", "fenbendazole", "pyrantel", "ketoconazole", "itraconazole",
        "fluconazole", "terbinafine",
        # Toxic or contraindicated in dogs — flagging these is the point.
        "ibuprofen", "acetaminophen", "paracetamol", "aspirin", "naproxen",
        "xylitol", "advil", "tylenol",
    }
)

_DOSE_UNITS = r"mg|mcg|µg|g|ml|cc|iu|units?|tablets?|tabs?|capsules?|caps?|pills?"
_TIME_UNITS = r"days?|weeks?|months?|hours?|times?|x"

_DOSE_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*({_DOSE_UNITS})\b", re.IGNORECASE)
_DURATION_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*({_TIME_UNITS})\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-z][a-z\-]+", re.IGNORECASE)


class Allowlist:
    """Everything the approved plan permits the owner text to say."""

    def __init__(self, plan: TreatmentPlan):
        self.plan = plan
        self.medications: set[str] = set()
        for med in plan.medications:
            # "amoxicillin-clavulanate" also licenses each component.
            for part in re.split(r"[\s/\-]+", med.name.lower()):
                if part:
                    self.medications.add(part)

        plan_text = self._plan_text(plan)
        self.dose_pairs: set[tuple[str, str]] = {
            (n, u.lower()) for n, u in _DOSE_RE.findall(plan_text)
        }
        self.numbers: set[str] = set(_NUMBER_RE.findall(plan_text))

    @staticmethod
    def _plan_text(plan: TreatmentPlan) -> str:
        parts: list[str] = [m.as_text() for m in plan.medications]
        parts += plan.activity_restrictions
        parts += plan.wound_care
        parts += plan.monitoring
        if plan.recheck:
            parts.append(plan.recheck)
        return "\n".join(parts)


def _normalise_number(value: str) -> str:
    """So '10' and '10.0' compare equal."""
    try:
        f = float(value)
    except ValueError:
        return value
    return str(int(f)) if f.is_integer() else str(f)


def check(
    instructions: DischargeInstructions, plan: TreatmentPlan
) -> list[GroundingViolation]:
    """Return every term in ``instructions`` that the approved plan does not support."""
    allow = Allowlist(plan)
    allowed_numbers = {_normalise_number(n) for n in allow.numbers}
    allowed_doses = {(_normalise_number(n), u) for n, u in allow.dose_pairs}

    violations: list[GroundingViolation] = []

    for index, paragraph in enumerate(instructions.paragraphs):
        # --- medications -------------------------------------------------------
        for word in _WORD_RE.findall(paragraph):
            lowered = word.lower()
            if lowered in FORMULARY and lowered not in allow.medications:
                violations.append(
                    GroundingViolation(
                        kind="medication", term=word, paragraph_index=index
                    )
                )

        # --- doses -------------------------------------------------------------
        for number, unit in _DOSE_RE.findall(paragraph):
            pair = (_normalise_number(number), unit.lower())
            if pair not in allowed_doses:
                violations.append(
                    GroundingViolation(
                        kind="dose",
                        term=f"{number} {unit}",
                        paragraph_index=index,
                    )
                )

        # --- durations and frequencies ----------------------------------------
        # Scoped to numbers adjacent to a time unit. A reformatted calendar date
        # ("12 September 2026") carries no time unit and is correctly ignored.
        for number, unit in _DURATION_RE.findall(paragraph):
            if _normalise_number(number) not in allowed_numbers:
                violations.append(
                    GroundingViolation(
                        kind="unverified_number",
                        term=f"{number} {unit}",
                        paragraph_index=index,
                    )
                )

    return _dedupe(violations)


def _dedupe(violations: Iterable[GroundingViolation]) -> list[GroundingViolation]:
    seen: set[tuple[str, str, int]] = set()
    out: list[GroundingViolation] = []
    for v in violations:
        key = (v.kind, v.term.lower(), v.paragraph_index)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def describe(violations: Iterable[GroundingViolation]) -> str:
    """Feedback text for the regeneration attempt."""
    lines = []
    for v in violations:
        label = {
            "medication": "medication not in the approved plan",
            "dose": "dose not in the approved plan",
            "unverified_number": "duration/frequency not in the approved plan",
        }[v.kind]
        lines.append(f"- \"{v.term}\" ({label})")
    return "\n".join(lines)


# ------------------------------------------------------------------ safe fallback


def render_template(plan: TreatmentPlan, patient: Patient) -> DischargeInstructions:
    """Deterministic rendering of the approved plan.

    Grounded by construction — it contains nothing but the plan. Plainer than the
    model's prose, which is the correct trade when the model has failed twice.
    """
    name = patient.name
    paragraphs = [
        f"{name} had a {patient.procedure.lower()} on {patient.procedure_date}. "
        f"Please follow the instructions below and contact the practice with any "
        f"concerns."
    ]

    if plan.medications:
        meds = "; ".join(m.as_text() for m in plan.medications)
        paragraphs.append(
            f"Medication for {name}: {meds}. Give this exactly as labelled. If you are "
            f"unsure, call us before giving it."
        )
    for item in plan.activity_restrictions:
        paragraphs.append(f"Activity: {item}")
    for item in plan.wound_care:
        paragraphs.append(f"Wound care: {item}")
    if plan.monitoring:
        paragraphs.append(
            "Please watch for the following and contact the practice if you see them: "
            + "; ".join(plan.monitoring)
        )
    if plan.recheck:
        paragraphs.append(f"Recheck appointment: {plan.recheck}. Please call to book.")

    return DischargeInstructions(paragraphs=paragraphs)


# -------------------------------------------------------------------- orchestration

#: Signature of the discharge writer, injected so the retry logic is testable.
Writer = Callable[..., DischargeInstructions]


def generate_grounded_discharge(
    plan: TreatmentPlan,
    patient: Patient,
    writer: Writer | None = None,
) -> tuple[DischargeInstructions, GroundingReport]:
    """Generate owner instructions that are verified against the approved plan.

    One regeneration attempt with the violations fed back, then the template. The
    returned report describes the *delivered* text, so ``passed`` is always true when
    we fall back — the template cannot be ungrounded — while
    ``fell_back_to_template`` records that the model's output was rejected.
    """
    if writer is None:
        from llm.discharge import write_discharge

        writer = write_discharge

    instructions = writer(plan, patient)
    violations = check(instructions, plan)
    if not violations:
        return instructions, GroundingReport(passed=True, attempts=1)

    retry = writer(plan, patient, violation_feedback=describe(violations))
    retry_violations = check(retry, plan)
    if not retry_violations:
        return retry, GroundingReport(
            passed=True, attempts=2, violations=violations
        )

    return render_template(plan, patient), GroundingReport(
        passed=True,
        attempts=2,
        violations=retry_violations,
        fell_back_to_template=True,
    )
