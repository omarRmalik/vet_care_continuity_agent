"""Synthetic demo data. No real patient information appears anywhere in this project.

Held as plain dicts/strings so this module has no import dependency on core.models —
the models validate this data, not the other way round.
"""

# The simulated clock starts on the day of the procedure. Every "day N" in the
# follow-up schedule is an offset from this date, so the demo has exactly one clock.
PROCEDURE_DATE = "2026-09-12"

PATIENT = {
    "patient_id": "DOG001",
    "name": "Bella",
    "species": "Dog",
    "breed": "Golden Retriever",
    "age": 6,
    "weight_kg": 28.4,
    "owner": "Jane Smith",
    "procedure": "Mass removal",
    "procedure_date": PROCEDURE_DATE,
}

# What the veterinarian types/dictates at the end of the visit. Deliberately terse
# and unstructured — this is the input to the SOAP drafting step.
VET_NOTES = """Bella had a small mass removed from her left shoulder.
Procedure went normally. Mild swelling expected.

Carprofen 75 mg twice daily.

Keep incision dry.

No running for 10 days.

Recheck in 10-14 days."""

# The follow-up schedule. A real practice would define this; for the demo these
# are simulated instantly via POST /api/clock/advance.
FOLLOWUP_DAYS = [1, 2, 4, 7, 10]

# Canned owner replies for rehearsing the demo without typing. The day-2 thread is
# the one that matters: an ambiguous opener that the agent has to resolve into
# structured observations before the rules engine can act on it.
DEMO_REPLIES = {
    # Day 1 reads as an uneventful recovery and should close in two or three
    # exchanges. Each reply answers a cluster of fields the way a real owner would.
    1: [
        "She's doing well — ate all her breakfast, drinking normally, and I gave her "
        "the tablet this morning.",
        "The incision looks clean and fully closed. No bleeding, no swelling, and "
        "nothing coming out of it.",
        "She's her usual self, breathing normally, no vomiting and she seems comfortable.",
    ],
    # The thread the whole demo rests on. An ambiguous opener becomes structured
    # findings. The last reply rules out dehiscence — having found something
    # concerning, the agent checks the URGENT case before it stops asking.
    2: [
        "It looks a little weird.",
        "It seems swollen.",
        "Yes, more than yesterday.",
        "There is a little yellow fluid.",
        "No, it's still closed and there's no blood. She's breathing fine and doesn't "
        "seem to be in pain — just puffy and a bit red around it.",
    ],
    # The closer — demonstrates that URGENT halts questioning immediately. Keyed to
    # day 4 (the next scheduled follow-up after day 2) so the UI, which looks replies
    # up by the current simulated day, actually offers it as a suggestion.
    4: [
        "There's blood soaking through the bandage.",
    ],
}
