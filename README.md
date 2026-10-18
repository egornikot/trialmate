# TrialMate

A text-message agent for clinical trials. Participants text it like a person. It logs side effects as
graded reports, records dose check-ins, answers protocol questions, sets reminders and pre-screens new
people for trials. A human coordinator approves every medical reply before it goes out, and
**Factory's Droid sets up each new trial inside the app** from its ClinicalTrials.gov record.

Built at HackHarvard 2026 for **Reinvent Clinical Trials** (Regeneron), **Build a Messaging Agent with
Photon 2.0** and **Best Use of the Factory Droid SDK**. The agent runs on Claude (Anthropic API).

## Why

The hard part of a trial is people, not chemistry: finding participants, keeping them in the study,
and hearing about side effects quickly and accurately. Participants already live in their text
messages. TrialMate meets them there and gives the study team structured, auditable data, with a human
in the loop for anything medical.

## What it does

| For participants (texting) | For the study team (dashboard) |
|---|---|
| Report a side effect in plain words → logged with a severity grade (1–4) | Approval queue: edit or approve medical replies before they're sent |
| "Took my dose, 8/10" → adherence check-in | Escalations, sorted emergency → urgent → routine |
| "When's my next visit?", "Do I get paid for parking?" → answers from the protocol | Side-effect log with grades and "serious" flags |
| "Remind me at 9pm" → scheduled text | Dose adherence per participant, one-click check-in texts |
| Emergency words → immediate 911/988 reply + emergency escalation | Pre-screen referrals from people looking for trials |
| New people: "diabetes trials near Boston?" → live ClinicalTrials.gov results + yes/no screening | **Studies: enter an NCT ID and Droid builds the study** |

## Where Factory Droid is used (Study Builder)

Setting up a trial on a texting agent normally means someone turns a long protocol into an FAQ, a visit
schedule, screening questions and safety rules. In TrialMate that's Droid's job, at runtime:

1. A coordinator types an NCT ID in **Studies → Build study** on the dashboard.
2. TrialMate downloads the official record from ClinicalTrials.gov into a private folder
   (`data/studies/<NCT>/trial.json`) next to `SCHEMA.md`, the format and rules for a study.
3. `trialmate/study_builder.py` starts a Droid session with the **Droid Python SDK**:
   - `Session(cwd=<that folder>, config=SessionConfig(autonomy=Autonomy.LOW, mcp_servers=[...]))`.
     With low autonomy and that working folder, Droid can only edit files there.
   - It registers a **custom in-process tool, `check_study`**, built with `droid_sdk.mcp.create_sdk_mcp_server`.
     The tool validates `study.json` and runs its test messages against its own red-flag rules.
   - It answers Droid's **permission requests** itself (`InteractionHandlers(on_permission=...)`): file edits
     inside the study folder are approved, shell commands and any path outside the folder are denied, and
     every decision is shown in the build log.
4. Droid writes `study.json`: a plain-language summary and FAQ, the visit schedule, yes/no screening
   questions built from the eligibility criteria, **study-specific red-flag symptoms** (regex rules with
   urgency) and test messages. It calls `check_study`, fixes what fails and repeats until everything passes.
5. Every Droid step streams to the dashboard live (`session.stream()` events).
6. TrialMate re-validates the file itself and loads it as a **draft** study. A coordinator reviews it and
   clicks **Activate**.

From then on, Droid's output runs the product:

- Prospects who find that trial are pre-screened with Droid's questions, and the dashboard labels it
  "run by our study team".
- Participants enrolled in it get Droid's visit schedule.
- Their texts are checked against Droid's red flags, for example "shaky and sweaty" in a diabetes
  study escalates as possible low blood sugar.

If `FACTORY_API_KEY`, the `droid` CLI or `droid-sdk` is missing, a basic rule-based builder runs instead so
the flow works in development. The dashboard labels which engine built each study, and its Droid chip says
exactly what is missing.

### The Droid code, condensed (`trialmate/study_builder.py`, `StudyBuilder._droid_session`)

```python
from droid_sdk import Autonomy, InteractionHandlers, Session, SessionConfig
from droid_sdk.mcp import create_sdk_mcp_server, tool

@tool("check_study", "Validate study.json in the workspace and run its test messages.")
async def check_study(path: str = "study.json") -> str:
    errors = validate(json.loads((workspace / "study.json").read_text()), nct)   # same checks TrialMate uses
    return "OK: all checks passed" if not errors else "ERRORS:\n- " + "\n- ".join(errors)

server = create_sdk_mcp_server(name="trialmate", tools=[check_study])
async with Session(cwd=str(workspace),                                         # one study folder only
                   config=SessionConfig(autonomy=Autonomy.LOW, mcp_servers=[server]),
                   interactions=InteractionHandlers(on_permission=approve_edits_in_folder_only)) as session:
    async with session.stream(TASK) as stream:                                 # TASK: read trial.json,
        async for event in stream:                                             # write study.json, call
            self.log(build_id, f"droid:{type(event).__name__}", ...)           # check_study until OK
```

After the session, TrialMate validates `study.json` again (`validate()`), stores it as a **draft** study and
waits for a human to click **Activate**. Where Droid's output is used afterwards:

| Droid wrote | Used by | File |
|---|---|---|
| `prescreen_questions` | prospects who pick the trial are screened with them | `tools.py` (`get_trial_details`, `save_prescreen_referral`) |
| `visits` | an enrolled participant's visit schedule and reminders | `seed.py` (`enroll`) |
| `red_flags` (regex + urgency) | every participant text is checked; a match escalates to the coordinator | `safety.py` (`study_red_flag`), `agent.py` |
| `faq` | stored as the study protocol; answers participants' questions | `study_builder.py`, `tools.py` (`search_protocol`) |
| `test_messages` | Droid's own self-test inside `check_study` | `study_builder.py` (`validate`) |

Test without a Factory account: `tests/test_core.py` (`test_droid_engine_with_sdk_shaped_fake`) drives this exact
code with a stand-in SDK, including a first draft that fails `check_study`, the fix, and the permission handler
approving in-folder edits while denying a shell command and a path outside the folder.

## Which model runs it

- **Claude via the Anthropic API** (default): set `ANTHROPIC_API_KEY` and `LLM_MODE=anthropic` in `.env`.
  If the configured `ANTHROPIC_MODEL` isn't available to the key, TrialMate asks `/v1/models` and switches
  to the newest Sonnet the key can use.
- **Chalk AI Router** (optional, supported): set `CHALK_ROUTER_API_KEY`, `CHALK_ENV_ID`, `CHALK_MODEL` and
  `LLM_MODE=chalk`. OpenAI-style models are translated by `OpenAIChatLLM` in `trialmate/llm.py`.
- **Mock** (no key): rule-based replies so the whole app, tests and evals run offline.
  A provider whose key is missing is skipped, so the app always starts. See `make_llm` in `trialmate/app.py`.
- **Evals ("prove it works").** `evals/run_evals.py` scores the agent on 35 labeled messages; see below.

## Safety design (outside the model)

The model is never the last line of defense. These rules run in plain code:

1. **Redaction before AI.** SSNs, card numbers (Luhn-checked), bank numbers, passwords and API keys are
   removed before text reaches the model or the database (`trialmate/guard.py`).
2. **Emergency detector.** Red-flag phrases (chest pain, can't breathe, swelling throat, fainting,
   self-harm and more, with negation handling) send a fixed 911/988 reply immediately and open an
   emergency escalation, without waiting for the LLM (`trialmate/safety.py`).
3. **Study red flags.** The ones Droid wrote and TrialMate validated add study-specific urgent escalations.
4. **Approval gate.** If the turn logged a symptom or escalated, or if the draft looks like dosing advice, a
   dose amount, a diagnosis or unblinding, the reply is held for a coordinator. The patient gets a fixed
   acknowledgment right away (`trialmate/agent.py`).
5. **Identity from the channel, not the model.** Tools act only on the person who sent the message, and
   each tool is gated by role. Disallowed calls fail closed (`trialmate/tools.py`).
6. **Write before send.** Every outgoing text is stored with a unique idempotency key before any network
   call, and Photon gets the same key on retry, so a crash can't double-text anyone (`trialmate/outbox.py`).
7. **STOP/START/HELP** handled before the model, as carriers expect.

## Try it in 2 minutes (no phone needed)

Photon connects TrialMate to a real iMessage line, but you don't need it to judge the project. The dashboard has a
**phone simulator** that goes through exactly the same agent, safety layer and approval queue.

1. `py run.py` (Windows: double-click `start.bat`). Without any keys it runs a rule-based mock model; with
   `ANTHROPIC_API_KEY` it runs Claude.
2. In the phone simulator, as **Maria** (a participant), send: "When is my next visit?" → answered right away.
   Then "Since my shot yesterday I have a headache and the spot is red" → the symptom is logged with a grade and the
   reply waits in **Needs your approval**. Click **Approve & send**.
3. Send "my throat feels like it's closing" → the 911 message goes out instantly, before any AI runs, and an
   emergency escalation appears.
4. Click **New person** and send "Any type 2 diabetes trials near Boston?" → live results from ClinicalTrials.gov.
5. In **Studies**, type an NCT ID (e.g. `NCT07448974`) and click **Build study** → the Droid build log streams in.
6. `py -m unittest discover -s tests` runs the 29 tests; `py evals/run_evals.py` scores the 35 labeled messages.

## Quick start

The core app needs Python 3.9+ and **no packages**: it's all standard library. It runs on Windows, macOS
and Linux.

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**.
2. Unzip TrialMate and **double-click `start.bat`**. It creates `.env`, starts the server and opens the
   dashboard at http://localhost:8000. Without API keys it runs the offline stand-in model.
3. To add keys, close the window, run `notepad .env`, fill them in, save and double-click `start.bat` again.
4. Double-click **`check.bat`** to test every key and service (AI model, ClinicalTrials.gov, Photon, Droid).
   It never prints your keys.

| File | What it does |
|---|---|
| `start.bat` | Starts TrialMate and opens the dashboard |
| `check.bat` | Tests your keys and connections |
| `reset.bat` | Wipes the demo database and starts fresh |
| `install_droid.bat` | Installs the Factory droid CLI and Droid SDK (needed for the Droid study builder) |
| `chat.bat` | Text the agent from a plain window |
| `evals.bat` | Scores the agent on 35 labeled messages |
| `prepare_upload.bat` | Makes a clean copy for GitHub (no `.env`) |

Guides: [demo video script](docs/DEMO_VIDEO.md) · [judging day cheat sheet](docs/JUDGING_DAY.md) ·
[putting it on GitHub](docs/GITHUB.md) · [Devpost text](DEVPOST.md)

From PowerShell in the TrialMate folder:

```powershell
py run.py --mock              # run without API keys
py run.py                     # run with the keys in .env
py run.py --reset             # wipe the local database and re-seed the demo data
py chat.py                    # text the agent from the terminal as demo participant Maria (or: chat.bat)
py chat.py --as visitor       # ...or as a new person looking for a trial
py -m unittest discover tests # 23 offline tests (incl. the Droid integration against an SDK-shaped fake)
py evals\run_evals.py         # score the agent
py -m pip install -r requirements-optional.txt   # tzdata (real time zones), droid-sdk, certifi
```

Turning on Droid on Windows (Python 3.10+), in PowerShell:

```powershell
irm https://app.factory.ai/cli/windows | iex     # installs the droid CLI
py -m pip install "droid-sdk[mcp]" tzdata
# put FACTORY_API_KEY=fk-... in .env (from app.factory.ai/settings/api-keys)
```

### macOS / Linux

```bash
cp .env.example .env          # add your keys
python3 run.py --mock         # or python3 run.py with keys; opens http://localhost:8000
curl -fsSL https://app.factory.ai/cli | sh && pip3 install "droid-sdk[mcp]"   # optional: Droid
```

Other scripts are the same as on Windows with `python3` instead of `py`.

**Troubleshooting**
- *"py is not recognized"*: reinstall Python with "Add python.exe to PATH" ticked, or use `python` instead of `py`.
- *"Port 8000 is busy"*: another TrialMate window is open. Close it, or run `py run.py --port 8001`.
- *A time zone note at startup* on Windows: run `py -m pip install tzdata`. Until then TrialMate uses your laptop's local time.
- *`CERTIFICATE_VERIFY_FAILED`*: run `py -m pip install certifi` (macOS: run `Install Certificates.command` in the Python folder).

## Connecting Photon (iMessage, SMS and more)

TrialMate uses Photon's HTTP API (`api.photon.codes/v1`), so it needs no Node.js or SDK.

1. In the Photon dashboard, create a project and an API key with `project:read` and `project:write`.
2. Assign a line (iMessage number) and add your test phone as a sender.
3. Put `PHOTON_API_KEY`, `PHOTON_PROJECT_ID` and `PHOTON_PLATFORM=imessage` in `.env`, then restart.
4. **Text the line first.** A shared line can't start a conversation. The person shows up on the dashboard
   as a prospect; click **Enroll** to make them a participant.

The poller fetches new messages every 2 seconds. Replies are sent with `Idempotency-Key`. If replies need
to be threaded, set `PHOTON_THREAD_REPLIES=1`.

## Evals: proving it works

`evals/cases.jsonl` holds 35 labeled messages: mild-to-serious side effects, emergencies (including
negated ones such as "no chest pain"), dosing questions, unblinding and prompt-injection attempts,
secrets, opt-out and trial searches. `evals/run_evals.py` runs each one in a fresh database and reports:

- emergency detection recall and false alarms
- side-effect logging recall and false positives, and whether the severity grade is in range
- urgent cases escalated to a human
- medical replies held for review vs. routine replies sent immediately
- right tool chosen
- unsafe text sent without review (target 0) and secrets that reached the model (target 0)
- latency per message

Results are written to `evals/results/latest.md` and `latest.json`. Add `--offline` to stub the trial
search, or `--mock` to check the pipeline without an API key. Each Droid-built study also carries its own
test messages, which must pass before the study can load.

## Architecture

```
iPhone ── iMessage/SMS ──> Photon ──poll──> PhotonPoller ─┐
Dashboard phone simulator ──────────────── /api/simulate ─┤
                                                          v
                 guard.redact ─> safety (911/988 now · study red flags) ─> outbox
                                                          │
                         Claude (Anthropic API) + role-gated tools    
                         ClinicalTrials.gov · protocol search · SQLite records
                                                          │
                         approval policy ─ held ─> coordinator dashboard ─ approve ─> outbox ─> Photon

Dashboard "Build study" ─> ClinicalTrials.gov record ─> Factory Droid (SDK, low autonomy, check_study tool)
                         ─> study.json ─> TrialMate validation ─> draft study ─> Activate
```

| File | Role |
|---|---|
| `trialmate/agent.py` | Conversation loop, system prompt, approval policy |
| `trialmate/tools.py` | 10 tools (trial search, symptom log, check-ins, reminders, escalation, ...) |
| `trialmate/study_builder.py` | **Factory Droid** study setup, `check_study` tool, validation |
| `trialmate/ctgov.py` | ClinicalTrials.gov API v2 client |
| `trialmate/llm.py`, `app.py` | Claude (Anthropic Messages API), optional Chalk AI Router, offline mock |
| `trialmate/guard.py`, `safety.py` | Redaction, emergency detection, study red flags, outbound review |
| `trialmate/outbox.py` | Write-before-send queue with approvals and retries |
| `trialmate/channels.py` | Photon poll/send, local simulator channel |
| `trialmate/server.py`, `static/dashboard.html` | JSON API and coordinator console |
| `trialmate/db.py`, `seed.py` | SQLite schema, fictional demo study |

## Demo script (3 minutes)

1. **Droid:** type an NCT ID and click **Build study**. Droid's steps stream in, including a failed check it
   fixes itself. Review the screening questions and red flags, then click **Activate**.
2. **Photon:** a judge texts the number: "Any type 2 diabetes trials near Boston?" Our study is listed first.
   They pick it and answer Droid's screening questions, and a referral appears on the dashboard.
3. Click **Enroll**. They text "I've felt shaky and sweaty since lunch." Droid's low-blood-sugar red flag
   escalates it as urgent, the symptom is logged and the reply waits for approval. Approve it, and it lands on
   their phone.
4. "My throat feels like it's closing" gets the 911 message instantly.
5. **Evals:** show `evals/results/latest.md`: 35 labeled messages, zero unsafe texts sent without review.

## How this was built

Written at HackHarvard 2026 with AI coding help (Claude). The safety patterns (fail-closed tool policy,
redaction before the model, write-before-send outbox with idempotency keys, human approval for sensitive
actions) are adapted from an earlier messaging-assistant project by a team member. No code from it is used
here as a dependency.

## Limits

This is a hackathon prototype, not a medical device. It is not HIPAA-compliant as built (no BAA, local
SQLite, no encryption at rest), and the demo study, protocol and participants are fictional. Severity
grades and Droid-built study content are drafts for a clinician to review; studies stay in draft until a
coordinator activates them. A real deployment needs IRB approval of all patient-facing text.
