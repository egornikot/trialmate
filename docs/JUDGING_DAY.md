# Judging day cheat sheet (Sunday)

## By 8:00 AM
- Devpost submitted: all four challenges ticked, repo link, video link, team members.

## 30 minutes before judging
1. Plug in the laptop. Turn off sleep: Settings → System → Power → Screen and sleep → Never.
2. Run **check.bat**. AI model, ClinicalTrials.gov, Photon and Droid should all say OK.
3. Run `py run.py --reset` (fresh demo data). Leave the window open; closing it stops the agent.
4. Pre-build one study with Droid and **Activate** it, so a slow Droid run can't eat into judging time.
   Keep a second NCT ID ready to build live if the judge wants to watch.
5. Text the Photon number once from your own phone to confirm replies arrive.
6. Open `evals/results/latest.md` in a second tab.

## 30-second pitch
"Trials struggle to find people, keep them, and hear about side effects in time. TrialMate is a number
participants text like a nurse. It logs side effects with a severity grade, tracks doses, answers from the
protocol and matches new people to real trials. Every medical reply waits for a human, and emergencies get
an instant 911 message. Setting up a new trial takes minutes because Factory's Droid builds it from the
official record and tests its own work."

## Texts to use
| Goal | Text |
|---|---|
| Find a trial | `Any type 2 diabetes trials near Boston?` then `the first one`, then answer the questions |
| Side effect | `I've felt shaky and sweaty since lunch` (diabetes study) or `I've had a headache since my shot` |
| Check-in | `Took my dose today, feeling 8/10` |
| Reminder | `Remind me to fill out my diary at 9pm` |
| Protocol | `Do I get paid for parking?` |
| Blinding | `Am I on the placebo?` (it refuses to guess) |
| Privacy | `my SSN is 123-45-6789` (shows "removed before AI") |
| Emergency | `my throat feels like it's closing` |

## Show the judge's own phone
Ask them to text the number. Their first message shows up under **People** as a prospect. Click
**Enroll**, then let them report a symptom and watch your approval queue.

## If something breaks
- **Photon down or slow:** use the phone simulator on the dashboard. Same agent, same rules.
- **Droid slow:** show the study you pre-built and its build log (Studies → Review).
- **AI key or network problem:** restart with `py run.py --mock` and say the replies come from the offline
  stand-in. The safety rules, approval queue and dashboard behave the same.

## Likely questions
- **"Is this HIPAA-compliant?"** "Not yet. It's a prototype. A real deployment needs a BAA with every
  vendor, encrypted storage and IRB approval of every patient-facing message. The design already keeps
  identifiers out of the model and keeps a human on every medical reply."
- **"What if the AI says something wrong?"** "Anything medical is held for a nurse. Plain-code rules also
  catch dosing advice, diagnoses and unblinding in any draft, whatever the model decided."
- **"Why Droid and not just a prompt?"** "Setting up a study is a multi-step job: read the record, write a
  structured file, test it, fix it. Droid runs in a locked folder with low autonomy and must pass our
  checker, and a human activates the result."
- **"How do you know it works?"** Show `latest.md`: 35 labeled messages scored every time we change something.
