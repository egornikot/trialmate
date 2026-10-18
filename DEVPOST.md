# Devpost submission: TrialMate

> Repo: https://github.com/egornikot/trialmate (make it **public** before submitting)
> Tracks to tick: Regeneron Healthcare: Reinvent Clinical Trials & Drug Development ·
> Build a Messaging Agent with Photon 2.0 · Best Use of Factory Droid SDK

## Elevator pitch

A texting companion for clinical trials: it finds trials, answers participants and logs side effects, while a coordinator approves every medical reply.

## About the project

## Inspiration

Clinical trials rarely fail because of the science. They fail because of people. Many sites struggle to enroll anyone, participants drop out, and side effects get reported late or not at all, because the only way to reach the study team is a phone call during office hours. Meanwhile, every participant carries a phone and texts all day. We wanted the study team to be one text away, without letting an AI give medical advice.

## What it does

TrialMate is a text-message agent for clinical trials, with a console for the study team.

**For participants**, it works like texting a person:
- *"When is my next visit?"* is answered from the participant's own schedule and the study protocol.
- *"Since my shot yesterday I've had a headache and the spot is red"* is logged as two side-effect reports, each with a severity grade, and the reply waits for a coordinator.
- *"Took my dose, feeling 8/10"* becomes a dose check-in, and *"Remind me at 9pm"* becomes a scheduled text.
- *"My throat feels like it's closing"* gets the 911 message instantly, from plain code, before any AI runs, and opens an emergency escalation.

**For people looking for a trial:** *"Any type 2 diabetes trials near Boston?"* returns live, recruiting studies from ClinicalTrials.gov in plain language, followed by a yes/no pre-screen that becomes a referral for the team.

**For the study team:** one console with an approval queue (every medical reply waits for a human, who can edit it), escalations sorted by urgency, graded side-effect reports, dose adherence, referrals and a trace of every tool call.

**Setting up a new trial is Factory Droid's job.** A coordinator types a ClinicalTrials.gov ID. TrialMate saves the official record to its own folder and starts a Droid session through the Droid Python SDK. Droid writes the study's FAQ, visit schedule, screening questions and study-specific danger signs, checks its work with a tool we built, and fixes what fails. The study stays a draft until a coordinator activates it, and from then on Droid's output drives screening, visit reminders and red-flag escalation in the live product.

## How we built it

- **Agent:** Claude through the Anthropic API, with 10 role-gated tools: trial search, eligibility details, pre-screen referral, symptom logging, check-ins, reminders, protocol search, escalation and memory. Identity comes from the messaging channel, never from the model, so tools can only touch the sender's own record.
- **Factory Droid SDK:** `trialmate/study_builder.py` runs a Droid `Session` at low autonomy, locked to one study folder, with a custom in-process `check_study` tool (`create_sdk_mcp_server`) and a permission handler that approves file edits inside that folder and denies shell commands. Every Droid event streams to the dashboard.
- **Photon 2.0:** TrialMate connects to Photon's iMessage API. It polls for new messages and replies with idempotency keys, so a retry can never double-text a patient.
- **ClinicalTrials.gov API v2** for live, recruiting studies.
- **Safety outside the model:** secrets (SSNs, card numbers, passwords) are removed before the AI sees a message; a deterministic emergency detector with negation handling; an approval gate for anything that looks like dosing advice, a diagnosis or unblinding; STOP/HELP handling; a write-before-send outbox.
- **Stack:** Python standard library only (no dependencies for the core), SQLite and a vanilla JavaScript dashboard, with a labeled eval suite and 30 automated tests.

## Challenges we ran into

- **Letting an AI talk to patients without letting it practice medicine.** We didn't want to block every reply, so the participant gets an instant, pre-written acknowledgement while the real reply waits for a coordinator. Emergencies skip the queue entirely.
- **Trusting Droid's output.** A study setup changes what people are asked and which symptoms escalate, so Droid has to prove its work: every red-flag rule ships with test messages that must match and routine messages that must not, `check_study` runs them, and TrialMate re-validates the file before it can load.
- **Droid's permission requests.** At low autonomy, Droid asks before creating files, and with no handler the request is cancelled, so our first real builds ended without writing anything. We now answer each request in code and show every decision in the build log.
- **Emergency detection that isn't trigger-happy.** "My chest feels tight" must fire; "no chest pain, just tired" must not.
- **Messaging rules.** A shared iMessage line can't start a conversation, and it only accepts texts from the registered sender, so the participant has to text first.

## Accomplishments that we're proud of

- No medical reply reaches a participant without a human, and emergencies are still answered immediately, before the AI even starts.
- Droid does real work inside the running product, sandboxed to one folder, and its output has to pass tests before a coordinator can activate it.
- The whole thing runs end to end on a laptop with one click, and the dashboard's phone simulator goes through exactly the same agent, safety layer and approval queue as iMessage.

## What we learned

- Guardrails belong outside the model. The most important safety rules in TrialMate are plain code that runs before and after Claude, so they behave the same every time.
- A labeled test set changes how you build: every prompt or rule change got re-checked, which caught regressions we would otherwise have demoed by accident.
- Coding agents are useful inside products, not just for writing them, as long as they work in a sandbox, check their own output and hand the result to a human.

## What's next for TrialMate

- A one-site pilot with a coordinator approving every medical reply, measuring time to answer, time to report a side effect, missed visits and dropout
- Voice check-ins on the Photon number for participants who prefer calling
- Spanish and other languages, with reading-level checks on everything the agent sends
- EHR and EDC integration so side-effect reports flow into the trial's official records
- HIPAA-ready hosting and an IRB review of all participant-facing text

## Built with

python · sqlite · claude · anthropic-api · factory-droid-sdk · photon · imessage · clinicaltrials.gov · javascript
