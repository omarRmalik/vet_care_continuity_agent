# Demo & recording guide

A ~4 minute walkthrough of the Veterinary Care Continuity Agent, written to be read
while you drive. Everything below runs **offline from recorded fixtures** — no network,
no API key, no latency surprises.

The one thing to remember: **click the suggested-reply chips, don't type.**

---

## 1. Pre-flight (do this ~5 minutes before recording)

### Start the server

```bash
cd C:\Users\omar\localhost2\vet-agent
$env:VET_AGENT_OFFLINE = "1"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Leave that terminal running. Confirm you see `Application startup complete.`

### Reset to a clean case

Open http://127.0.0.1:8000 and press **Reset** (top right) → confirm.

State persists in `state.json` across restarts, so without this you will start
mid-case. The header should read **Day 0 · 2026-09-12**, stage **VISIT**, risk
**NORMAL**, and you should see an `offline` chip.

### Set up the window

- Maximise the browser. The two-pane layout needs more than 900px wide; at your
  1536×1024 screen a maximised window is fine.
- **Zoom to 110–125%** (`Ctrl` + `+`). Default 100% is hard to read in a recorded
  video, especially the rule ids, which are the point of step 5.
- Close other tabs. The tab strip is in frame the whole time.
- Turn off notifications: **Win+N** → Focus, or Settings → System → Notifications → off.
  A Teams popup mid-take means starting again.

### Checklist

- [ ] Server running, `offline` chip visible
- [ ] Reset pressed, header reads Day 0
- [ ] Browser maximised, zoom 110–125%
- [ ] Notifications silenced
- [ ] Read §3 once before rolling

---

## 2. Recording

You have three options installed. **Use Xbox Game Bar** unless you need to show
anything outside the browser.

### Option A — Xbox Game Bar (recommended)

| Action | Key |
|---|---|
| Open the overlay | `Win` + `G` |
| Start / stop recording | `Win` + `Alt` + `R` |
| Toggle microphone | `Win` + `Alt` + `M` |

1. Click once inside the browser window so it has focus.
2. Press `Win` + `Alt` + `M` if you want narration — check the mic indicator before
   the real take.
3. Press `Win` + `Alt` + `R`. A small timer appears in the corner.
4. Do the walkthrough in §3.
5. Press `Win` + `Alt` + `R` to stop.

Output lands in **`C:\Users\omar\Videos\Captures`** as an `.mp4` named after the window.

Three things that catch people out:

- Game Bar records **one window**. It captures the browser you had focused when you
  started, and keeps recording it even if you alt-tab away. That's usually what you
  want — your terminal and notifications stay out of frame.
- It **cannot** record the desktop or File Explorer. If you want to show the terminal
  or the fixture files, use Option B.
- If `Win` + `Alt` + `R` does nothing, open `Win` + `G` once and allow capture, or
  check Settings → Gaming → Captures.

### Option B — Snipping Tool (region recording)

Use this if you want the terminal and the browser in one frame — e.g. to show the
server log printing `replaying fixture …` as proof it's offline.

1. `Win` + `Shift` + `S`, or open **Snipping Tool** from Start.
2. Switch to the **video camera** icon (not the still-capture one).
3. **New**, drag a rectangle around the region, press **Start**.
4. Stop from the toolbar. Save as `.mp4`.

### Trimming afterwards

**Clipchamp** is installed. Import the `.mp4`, drag the handles to cut dead air at the
start and end, export at 1080p. That's all most submissions need.

---

## 3. The walkthrough

Timings are a guide. Total ≈ 4 minutes. **Pause about a second after each click** —
the UI updates fast and a video that jumps is hard to follow.

### Step 0 — Open on the problem (~20s)

Say, roughly:

> An animal goes home after surgery and the practice loses sight of it. The owner
> notices something at day two, isn't sure if it matters, and doesn't call. This agent
> keeps that thread connected — but it never makes the clinical decision.

Nothing to click. Let the empty case sit on screen.

### Step 1 — Notes → SOAP (~35s)

1. Click **Load example notes**. The vet's terse dictation fills the box.
2. Click **Generate draft SOAP**.

While it renders:

> These are the notes a vet actually types — fragments, no structure. The model
> restructures them into a SOAP note and pulls the plan into machine-readable fields.
> Note it did *not* invent a duration for the carprofen, because the vet never gave one.

Point at the **medications table** — `Carprofen / 75 mg / twice daily / (blank)`.

### Step 2 — The human gate (~30s)

1. Click into the **Assessment** box and add a few words — e.g. ` Margins grossly
   complete.`
2. Click **Approve & generate discharge**.

> This is a draft until a vet approves it. The edit I just made is what gets frozen —
> the discharge instructions are generated from the approved note, not the draft. The
> agent has no path to the owner that doesn't go through this button.

### Step 3 — Grounded discharge (~35s)

The right pane fills in. Point at the green badge: **✓ Grounded — 0 unverified
clinical claims**.

> The plan says the model must not invent treatment. That's unenforceable as a prompt
> instruction, so it's checked in code. We build an allowlist from the approved plan —
> drug names, dose pairs, durations — and scan the generated text against it. If it
> mentions a drug that wasn't prescribed, or changes 75mg to 150mg, or stretches ten
> days to thirty, it's rejected and regenerated. Twice rejected and the owner gets the
> plan rendered verbatim instead.

*(Optional, if asked how you know it works: see §5.)*

### Step 4 — Day 1, uneventful (~30s)

1. Click **Start check-in**.
2. Click each suggested reply chip in order — three of them. Wait for the agent's
   reply between clicks.

> Day one is unremarkable and should feel that way. Three exchanges, then it stops.
> It isn't reading from a fixed questionnaire — it asked about bleeding first because
> that's the highest-priority thing it didn't know yet.

Header risk chip stays **NORMAL**. Timeline gains *Follow-up completed — No concerning
findings*.

### Step 5 — Day 2, the actual pitch (~60s)

**Slow down here. This is the step that wins or loses it.**

1. Click **Advance one day**.
2. Click **Start check-in**.
3. Click the first chip: *"It looks a little weird."*

> That's what a real owner says. It's clinically meaningless, and it's the hardest
> input in the system.

4. Click the remaining chips one at a time. Read the agent's questions aloud as they
   appear — what can you actually see → has it changed since yesterday → any
   discharge or bleeding.

> It's not accepting the vague answer. It's converting it into fields.

5. When the conversation closes, point at the outcome block: **REVIEW**, the two
   reasons, and `R-REV-01` `R-REV-02`, then the slot chips underneath.

> Here's the part I'd defend hardest. The model never decided this was serious. All it
> did was turn "it looks a little weird" into `incision_swelling: increasing` and
> `incision_discharge: yellow`. A deterministic rules engine in a YAML file made the
> call, and the alert cites the exact rule ids so a vet can audit why.

6. Point at the **VETERINARY REVIEW REQUIRED** card in the left pane, and the header
   stage now reading **REVIEW REQUIRED**.

### Step 6 — Urgent stops the conversation (~35s)

1. Click **Advance one day** twice (day 3, then day 4).
2. Click **Start check-in**.
3. Click the single chip: *"There's blood soaking through the bandage."*

> One reply. It doesn't ask the remaining questions — an emergency terminates the
> conversation immediately instead of working through a checklist. That exit wasn't in
> the original spec and it's the one I'd least want to get wrong.

Point at **URGENT**, `R-URG-01`, and the red escalation card.

### Step 7 — Close on the timeline (~25s)

Scroll to the timeline at the bottom.

> Everything is an append-only event log. The case state and this timeline are both
> derived from it, so nothing is overwritten and you can always answer what the agent
> knew and when. That's the record a practice would need if anyone ever asked.

Stop recording.

---

## 4. If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| *"Offline mode has no recorded response for that exact reply"* | You typed instead of clicking a chip | Click the chip. The session stays open — just click it and carry on. |
| Panes stacked vertically | Window under 900px wide | Maximise, or reduce zoom. |
| Starts mid-case | `state.json` persisted | Press **Reset**. |
| No suggested chips | Not on day 1, 2 or 4 | **Advance one day** to the next follow-up day. |
| Nothing responds | Server stopped | Restart it (§1) and press **Reset**. |
| `Win`+`Alt`+`R` does nothing | Game Bar capture off | `Win`+`G`, allow capture, or Settings → Gaming → Captures. |

Recovering on camera is fine — press Reset and pick up from Step 1. It costs 30
seconds and a trim in Clipchamp.

---

## 5. Optional extras

Only if you have time or get asked. Don't bolt these onto the main take.

### Prove the grounding check isn't decorative

In a second terminal:

```bash
cd C:\Users\omar\localhost2\vet-agent
$env:PYTHONPATH = "C:\Users\omar\localhost2\vet-agent"
.venv\Scripts\python.exe -m pytest tests/test_grounding.py -v
```

Or show the tamper results from the README: the real generated text passes with zero
violations, while injecting `gabapentin`, `150 mg`, `30 days`, or `ibuprofen` each
gets caught.

### Prove it's really offline

Record with Option B so the terminal is in frame — the server log prints
`replaying fixture …` on every call and never contacts `api.anthropic.com`. Or just
disconnect wifi before the take; the demo is unaffected.

### The honest weak point

If someone probes the safety story, say this rather than let them find it:

> The drug formulary is a hardcoded list of about seventy names. It catches a model
> naming a real drug that wasn't prescribed — including the human painkillers that are
> toxic to dogs. It would not catch a plausible invented name. A real deployment checks
> against a veterinary formulary. What holds architecturally is that the check is
> deterministic, runs on every generation, and can reject output.

Same for the rules: `core/rules.yaml` is illustrative. Real escalation rules have to be
written and validated by veterinary professionals. Saying so first is stronger than
being asked.
