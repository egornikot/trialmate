"""Demo data: one fictional study and two fictional participants.

Everything here is made up for the hackathon demo. Real trial data comes live
from ClinicalTrials.gov through the search tools.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from datetime import tzinfo

from .db import Database, now
from .tz import get_tz

PROTOCOL = """\
Visit schedule: After screening, clinic visits happen at weeks 0, 2, 4, 8 and 12, with a final follow-up visit at week 16. Each visit takes about 2 hours and includes a breathing test, vital signs and a short questionnaire. Blood is drawn at weeks 0, 4 and 12. Please bring your study diary and your rescue inhaler.

Study medication: The study medication is one injection under the skin every 2 weeks, given at the clinic during your visit. Keep using your regular rescue inhaler exactly as your own doctor prescribed.

Missed visit or dose: Do not try to make up a missed dose on your own. Contact the study coordinator to reschedule; visits can usually be moved by up to 3 days.

Other medicines: Tell the study team before starting any new medicine, including over-the-counter medicines, vitamins and supplements, and before any vaccine. The team will tell you whether it is allowed in this study.

Side effects: Common side effects reported with injections include redness or soreness where the shot was given, headache and sore throat. Report every side effect to the study team, even mild ones, by text or phone. In an emergency call 911.

Compensation and travel: You receive $75 for each completed clinic visit, loaded onto a study payment card within 2 weeks. Parking in the clinic garage is validated at the front desk. Rideshare or transit costs are reimbursed up to $40 per visit with receipts.

Leaving the study: You can leave the study at any time, for any reason, and it will not affect your regular medical care. Tell the coordinator, who will schedule a final safety check.

Blinding: This is a double-blind study. Neither you nor the study team knows whether you receive the study medication or placebo. The team can unblind only in a medical emergency.

Study diary: Each evening, record your asthma symptoms and how many puffs of your rescue inhaler you used in the study diary app.

Pregnancy: If you become pregnant or think you might be, tell the study team right away.

Contacts: Study coordinator Jordan Lee, RN, (617) 555-0142, Monday to Friday 9am to 5pm. After hours, call the clinic line and choose option 2 for the on-call study doctor.
"""


def _at(tz: tzinfo, days: int, hour: int, minute: int = 0) -> float:
    base = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (base + timedelta(days=days)).timestamp()


def ensure(db: Database, tz_name: str = "America/New_York") -> None:
    if db.one("SELECT id FROM studies LIMIT 1"):
        return
    tz = get_tz(tz_name)
    t = now()
    study = db.insert(
        "INSERT INTO studies(code,title,condition,site,coordinator,coordinator_phone,protocol,status,source,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("TM-201 (demo)", "A 16-week study of an every-2-weeks injection for moderate asthma (fictional demo study)",
         "moderate asthma", "Cambridge Research Clinic (demo)", "Jordan Lee, RN", "(617) 555-0142", PROTOCOL,
         "active", "demo", t))

    def patient(handle: str, name: str, facts: list[str]) -> int:
        return db.insert(
            "INSERT INTO patients(handle,name,role,study_id,channel,facts,demo,created_at,last_seen_at)"
            " VALUES(?,?,?,?,?,?,1,?,?)",
            (handle, name, "participant", study, "sim", json.dumps(facts), t - 86400 * 20, t - 3600))

    maria = patient("sim:maria", "Maria Lopez", ["Prefers texts after 5pm", "Goes by Maria"])
    sam = patient("sim:sam", "Sam Patel", ["Works night shifts"])

    for pid, visits in ((maria, [("Week 4 visit", 2, 10), ("Week 8 visit", 30, 10)]),
                        (sam, [("Week 2 visit", 1, 14), ("Week 4 visit", 15, 14)])):
        for title, days, hour in visits:
            db.insert("INSERT INTO visits(patient_id,title,due_at,location) VALUES(?,?,?,?)",
                      (pid, title, _at(tz, days, hour), "Cambridge Research Clinic, 3rd floor"))

    for days_ago, took, wellbeing in ((6, 1, 7), (5, 1, 7), (4, 1, 6), (3, 0, 5), (2, 1, 7), (1, 1, 8)):
        db.insert("INSERT INTO checkins(patient_id,took_dose,wellbeing,notes,created_at) VALUES(?,?,?,?,?)",
                  (maria, took, wellbeing, "", t - 86400 * days_ago))
    for days_ago in (3, 2, 1):
        db.insert("INSERT INTO checkins(patient_id,took_dose,wellbeing,notes,created_at) VALUES(?,?,?,?,?)",
                  (sam, 1, 7, "", t - 86400 * days_ago))
    db.insert("INSERT INTO events(patient_id,symptom,description,grade,serious,onset,ongoing,status,created_at)"
              " VALUES(?,?,?,?,?,?,?,?,?)",
              (maria, "injection site redness", "Small red patch where the shot was given, gone in a day", 1, 0,
               "after week 2 injection", 0, "reviewed", t - 86400 * 4))
    db.insert("INSERT INTO reminders(patient_id,body,due_at,created_by,created_at) VALUES(?,?,?,?,?)",
              (maria, "Reminder: your Week 4 visit is in 2 days at 10:00 AM. Bring your study diary and rescue inhaler.",
               _at(tz, 1, 18), "seed", t))


def enroll(db: Database, patient_id: int, name: str = "", tz_name: str = "America/New_York",
           study_id: int = 0) -> bool:
    """Turn a prospect into a participant of an active study (dashboard 'Enroll' button)."""
    study = (db.one("SELECT * FROM studies WHERE id=? AND status='active'", (study_id,)) if study_id else None) or \
        db.one("SELECT * FROM studies WHERE status='active' ORDER BY id LIMIT 1")
    if not study:
        return False
    tz = get_tz(tz_name)
    db.run("UPDATE patients SET role='participant', study_id=?, name=COALESCE(NULLIF(?,''), name) WHERE id=?",
           (study["id"], name, patient_id))
    db.run("DELETE FROM visits WHERE patient_id=? AND status='scheduled'", (patient_id,))
    config = json.loads(study.get("config") or "{}")
    visits = config.get("visits") or [{"title": "Screening visit", "day": 3}]
    for v in visits[:20]:
        db.insert("INSERT INTO visits(patient_id,title,due_at,location) VALUES(?,?,?,?)",
                  (patient_id, v["title"], _at(tz, max(1, int(v["day"])), 9, 30), study.get("site") or "Study clinic"))
    return True
