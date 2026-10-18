# TrialMate video narration (about 1 min 45 s)

Read it yourself or paste it into an AI voice tool (for example ElevenLabs or Clipchamp text-to-speech).
Each block matches one scene of the video. The video also shows these lines as captions, so it works muted.

---

**1. Intro (0:00–0:10)**
Clinical trials rarely fail because of the science. They fail because of people: finding participants,
keeping them enrolled, and hearing about side effects in time. TrialMate puts the study team one text away.

**2. Texting (0:10–0:27)**
Participants text TrialMate like a person. Ask "when is my next visit?" and Claude answers from the study
protocol. Describe a symptom in your own words, and it is logged with a severity grade automatically.

**3. Safety (0:27–0:45)**
But the AI never practices medicine. Every medical reply waits as a draft until a coordinator approves it.
Emergencies don't wait for AI at all: plain code spots them and sends 911 or 988 instructions instantly.
And social security numbers or card numbers are removed before the model ever sees them.

**4. Factory Droid (0:45–1:08)**
Setting up a new trial is Factory Droid's job. A coordinator types a ClinicalTrials.gov ID, and TrialMate
starts a Droid session through the Droid Python SDK, locked to one folder. Droid reads the official record
and writes the FAQ, visit schedule, screening questions and danger signs for that drug. It checks its own
work with a custom tool we built, fixes what fails, and the coordinator activates the study.

**5. Finding trials and Photon (1:08–1:22)**
People looking for a trial get live, recruiting studies from ClinicalTrials.gov and a quick pre-screen.
And it's made for real phones: TrialMate connects to Photon's iMessage API, so the same agent works over
iMessage.

**6. Evals and models (1:22–1:37)**
We measure it. Thirty-five labeled test messages check emergency detection, side-effect logging, and that
nothing unsafe goes out without review. The agent runs on Claude, and one setting routes it through
Chalk's AI Router instead.

**7. Close (1:37–1:45)**
TrialMate. Answers any time for participants, and a human for every medical message.
