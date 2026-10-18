# Demo video script (about 2.5 minutes)

Record on Windows with **Win + Alt + R** (Xbox Game Bar records the active window) or Clipchamp's screen
recorder. Record the dashboard full screen; hold your phone up to the webcam or add a phone screen
recording for the texting parts. Upload to YouTube (unlisted) and paste the link in Devpost.

**Before recording:** run `py run.py --reset`, and make sure check.bat shows OK for the AI model and Photon
(and Droid, if installed). Keep the texts below in a note so you can copy them.

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Dashboard | "Clinical trials don't usually fail on the chemistry. They fail on people: finding them, keeping them, and hearing about side effects in time. TrialMate puts the study team one text away, without letting AI practice medicine." |
| 0:15–0:45 | Studies panel: type an NCT ID, click **Build study**; the Droid log streams | "To set up a new trial, a coordinator types its ID. Factory's Droid reads the official ClinicalTrials.gov record and writes the FAQ, visit schedule, screening questions and the danger signs for this drug. It tests its own work, fixes what fails, and we re-check it." Click **Review**, then **Activate**. |
| 0:45–1:15 | Phone texts the Photon number: "Any type 2 diabetes trials near Boston?", then "the first one", then answers yes/no | "Anyone can text our number on iMessage through Photon. They get real recruiting trials, ours first, and Droid's screening questions." Show the referral appear on the dashboard. Click **Enroll**. |
| 1:15–1:50 | Phone: "I've felt shaky and sweaty since lunch" | "Now they're a participant. They describe a symptom in their own words. It's logged with a severity grade, it matched a red flag Droid wrote for this study, so it's escalated as urgent, and Claude's reply waits for a nurse." Edit the draft a little, click **Approve & send**, and show it arrive on the phone. |
| 1:50–2:10 | Phone: "my throat feels like it's closing" | "Emergencies never wait. Plain code, not the AI, sends the 911 message instantly and alerts the team." |
| 2:10–2:30 | `evals/results/latest.md` | "We score the agent on 35 labeled messages: emergency recall **[x]**, side effects logged **[x]**, and zero unsafe messages sent without review." |
| 2:30–2:40 | Dashboard | "TrialMate: a texting nurse for clinical trials, with a human in the loop for anything medical." |
