# Veterinary Care Continuity Agent

Maintains continuity of care between a veterinary visit and an animal's recovery at
home: clinical notes become a draft SOAP note, the vet approves it, the approved plan
becomes owner-friendly discharge instructions, and an agent conducts post-op follow-ups
that escalate to staff on deterministic rules.

The agent does not diagnose, prescribe, or modify treatment. The veterinarian is the
clinical decision-maker.

> **The triage rules in `core/rules.yaml` are illustrative only.** Escalation rules for a
> real system must be written and validated by veterinary professionals. All patient data
> in this repo is synthetic.

## Run

```bash
python -m venv .venv
.venv/Scripts/python -m pip install anthropic fastapi "uvicorn[standard]" pyyaml pydantic pytest python-dotenv
.venv/Scripts/python -m uvicorn app:app --reload
```

Open http://127.0.0.1:8000

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. `llm/client.py` loads `.env`
on import — the SDK itself only reads the process environment, so without that load the
file would be inert. A real environment variable always wins over `.env`.

Drive the pipeline from the terminal:

```bash
.venv/Scripts/python cli.py status      # key / offline configuration
.venv/Scripts/python cli.py soap        # notes -> draft SOAP
.venv/Scripts/python cli.py discharge   # notes -> SOAP -> owner instructions
```

Every live call records a fixture into `data/fixtures/`. For the demo, replay them
instead of calling the API:

```bash
VET_AGENT_OFFLINE=1 .venv/Scripts/python -m uvicorn app:app
```

## Tests

```bash
.venv/Scripts/python -m pytest          # unit suite, no server or key needed
.venv/Scripts/python tests/smoke_api.py # end-to-end, needs a running server
```

## Demo

Run it offline. Every step below is recorded in `data/fixtures/` and replays with no
network:

```bash
VET_AGENT_OFFLINE=1 .venv/Scripts/python -m uvicorn app:app
```

Press **Reset** first — state persists across restarts in `state.json`.

1. **Load example notes** → **Generate draft SOAP**. Edit a line in the note to show
   the vet is the author of record.
2. **Approve & generate discharge.** Owner instructions appear with the
   *Grounded ✓ — 0 unverified clinical claims* badge.
3. **Start check-in** (day 1). Click the suggested replies. Closes in three exchanges,
   NORMAL, timeline updates.
4. **Advance one day** → **Start check-in** (day 2). First reply is
   *"It looks a little weird."* The agent asks what can actually be seen, whether the
   swelling has changed, and about discharge — turning that into
   `{incision_swelling: increasing, incision_discharge: yellow}`, which fires
   `R-REV-01` and `R-REV-02`. A **VETERINARY REVIEW REQUIRED** card appears in the vet
   console citing both rule ids. *This step is the pitch.*
5. **Advance twice** → **Start check-in** (day 4) → *"There's blood soaking through
   the bandage."* URGENT on `R-URG-01`, and questioning stops on the first reply
   instead of continuing the questionnaire.

Use the suggested-reply chips. Typing a free-form reply offline has no recording and
returns a clear "no recorded response" error — by design, since replaying a near-miss
could inject another conversation's answer. Run with `VET_AGENT_OFFLINE=0` to improvise.

### Fixtures

`data/fixtures/` holds the 11 recordings the demo walk uses. `_superseded/` holds
earlier recordings from development, kept only so they can be restored if a prompt is
reverted; nothing reads them. Delete a fixture and the next live run re-records it.

## Build status

- [x] Scaffold
- [x] `core/` — models, event log, rules engine
- [x] SOAP drafting + discharge rewriting
- [x] Grounding check
- [x] Web UI (vet-side path)
- [x] Follow-up intake loop
- [x] Clock, timeline, alerts
