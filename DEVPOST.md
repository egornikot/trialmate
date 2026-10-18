# Devpost submission draft: TrialMate

> Paste each section into Devpost. Replace every **[fill in]** before submitting.
> Team: **[fill in names]** · Repo: **[fill in GitHub link]** · Video: **[fill in YouTube/Drive link]**
> Challenges to select: Reinvent Clinical Trials (Regeneron) · Build a Messaging Agent with Photon 2.0 ·
> Best Use of the Factory Droid SDK · Best Data-Driven Agent Built on Chalk

## Tagline

A texting nurse for clinical trials: participants report side effects by iMessage, and a human approves every medical reply.

## Inspiration

Clinical trials don't usually fail on the chemistry. They fail on people. Sites struggle to find
participants, participants drop out, and side effects get reported late or not at all because the
only channel is a phone call during office hours. Meanwhile every participant carries a phone and texts
all day. We wanted the study team to be one text away, without letting an AI give medical advice.

## What it does

**For participants:** TrialMate is a phone number you text like a person.
- "I've had a headache since my shot" → logged as a side effect with a severity grade, and the study team is notified
- "Took my dose, feeling 8/10" → recorded as a check-in
- "Do I get paid for parking?" → answered only from the trial's own protocol
- "Remind me at 9pm" → a scheduled text
- "My throat is closing" → an instant 911 message and an emergency alert to the team, before any AI runs

**For people looking for a trial:** "Any type 2 diabetes trials near Boston?" returns live
ClinicalTrials.gov results. The agent walks through yes/no screening questions and sends a referral to
the study team.

**For the study team:** a live console with an approval queue (every medical reply waits for a human,
who can edit it), escalations sorted by urgency, a graded side-effect log, dose adherence, referrals and a
full trace of every tool call.

**Setting up a new trial takes minutes, not days.** Type an NCT ID and Factory's Droid reads the official
record and writes the study's FAQ, visit schedule, screening questions, study-specific danger signs and
test messages. It checks its work with our validator and fixes what fails. The study then goes live once a
coordinator approves it.

## How we built it

- **Agent:** Claude with 10 role-gated tools (trial search, eligibility details, pre-screen referral, symptom
  logging, check-ins, reminders, protocol search, escalation, memory). Tools act only on the person who
  sent the message; identity comes from the channel, never from the model.
- **Photon 2.0:** the agent lives on a real iMessage line through Photon's API. Messages are polled, and
  replies go out with idempotency keys so a retry can never double-text a patient.
- **Factory Droid SDK:** `study_builder.py` runs a Droid session with low autonomy, scoped to one study's
  folder, plus a custom in-process `check_study` tool. Droid's steps stream to the dashboard live. Droid's
  output drives screening, visit schedules and red-flag escalation in the running product.
- **Chalk:** every Claude call can run through Chalk's AI Router (budgets, fallbacks, usage per key).
  Our 35-case labeled eval suite measures whether the agent works. **[fill in what you used in Chalk after
  talking to their team]**
- **Safety outside the model:** redaction of SSNs, card numbers and passwords before the AI sees them; a
  deterministic emergency detector with negation handling; an approval gate for anything that looks like
  dosing advice, a diagnosis or unblinding; STOP/HELP handling; a write-before-send outbox.
- **Stack:** Python standard library (zero dependencies for the core), SQLite and a vanilla JS dashboard.

## Challenges we ran into

- **Letting an AI talk to patients without letting it practice medicine.** We didn't want to block every
  reply, so the patient gets an instant, pre-written acknowledgment while the real reply waits for a nurse.
  Emergencies skip the queue entirely.
- **Trusting Droid's output.** A study setup that Droid writes changes what patients are asked and which
  symptoms escalate. We made Droid prove its own work: each red-flag rule ships with test messages that must
  match, routine messages that must not, and a validator Droid calls until it passes. TrialMate re-checks it,
  and it stays a draft until a coordinator activates it.
- **Emergency detection that isn't trigger-happy.** "My chest feels tight" must fire, "no chest pain, just
  tired" must not. Negation handling and a labeled eval set kept us honest.
- **Messaging rules.** A shared iMessage line can't start a conversation, so the first text has to come
  from the participant; reminders and check-ins work after that. Every send carries an idempotency key so
  retries never double-text anyone.
- **[fill in anything that went wrong on-site, e.g. Photon setup or Wi-Fi]**

## Accomplishments that we're proud of

- A judge can text the number from their own phone and watch it hit the dashboard.
- No medical reply reaches a patient without a human, and emergencies still get an instant answer.
- Droid's study setup must pass its own tests before it can load, and TrialMate re-checks it.
- Eval results on 35 labeled messages: **[fill in from evals/results/latest.md: emergency recall, side
  effects logged, medical replies held, secrets that reached the model]**

## What we learned

- Guardrails belong outside the model: the most important safety rules in TrialMate are plain code that
  runs before and after Claude, so they behave the same every time.
- A labeled eval set changes how you build. Every prompt or rule change got re-scored, which caught
  regressions we would have demoed by accident.
- Coding agents are useful inside products, not just for writing them, as long as they work in a sandboxed
  folder, check their own output and hand the result to a human for approval.
- **[fill in a personal lesson]**

## What's next

- Voice check-ins on the Photon number for participants who prefer calling
- Spanish and other languages, with reading-level checks on everything the agent sends
- EHR and EDC integration so side-effect reports flow into the trial's official records
- HIPAA-ready hosting (BAA, encryption at rest) and an IRB-ready review of all patient-facing text

## Built with

python · sqlite · claude · anthropic · photon · imessage · factory-droid · chalk · clinicaltrials.gov · javascript
