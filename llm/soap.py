"""Seam 1: free-text clinical notes -> structured draft SOAP note.

This is a *restructuring* task, not a clinical one. The model reorganises what the
veterinarian already wrote into S/O/A/P and pulls the plan into machine-readable
fields. It must not add findings, infer diagnoses, or invent doses — the output is a
draft that a human approves before anything downstream can use it.
"""

from __future__ import annotations

from core.models import Patient, SoapNote
from llm.client import parse_structured

SYSTEM = """You are a veterinary clinical documentation assistant. You convert a \
veterinarian's rough notes into a structured draft SOAP note.

Rules you must follow:

- Restructure ONLY what the veterinarian wrote. Never add clinical findings, \
diagnoses, medications, doses, or follow-up intervals that are not present in the \
notes.
- If a SOAP section has no supporting content in the notes, write a brief factual \
statement of what is known rather than inventing detail. Do not speculate.
- Copy medication names, doses and frequencies EXACTLY as written. Do not convert \
units, do not normalise "twice daily" into "BID" or vice versa, do not add a duration \
that was not stated.
- `activity_restrictions`, `wound_care` and `monitoring` are short imperative lines \
taken from the notes. Keep clinical register here — this is the vet's record, not the \
owner's handout.
- `recheck` is the follow-up interval exactly as stated, or null if none was given.
- This is a DRAFT for veterinary review. Accuracy to the source notes matters more \
than completeness."""


def draft_soap(notes: str, patient: Patient) -> SoapNote:
    user = f"""Patient: {patient.name}, {patient.age}y {patient.breed} \
({patient.species}), {patient.weight_kg} kg
Procedure: {patient.procedure} on {patient.procedure_date}

Veterinarian's notes:
---
{notes}
---

Produce the draft SOAP note."""

    return parse_structured("soap", SYSTEM, user, SoapNote)
