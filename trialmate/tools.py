"""Agent tools.

Every tool acts on the patient bound to the incoming message by the channel,
never on an ID the model supplies, and each tool is gated by role. A call that
is not allowed fails closed. (Same rule as the earlier Hermes corporate-policy
plugin: identity comes from the transport, not from model arguments.)
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from datetime import tzinfo

from .ctgov import TrialsError
from .db import now
from .guard import redact
from .tz import get_tz

PARTICIPANT = "participant"
PROSPECT = "prospect"


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    app: Any
    patient: dict
    inbound_id: Optional[int] = None
    flags: set = field(default_factory=set)
    calls: list = field(default_factory=list)

    @property
    def db(self):
        return self.app.db

    @property
    def tz(self) -> tzinfo:
        return get_tz(self.app.settings.tz)


def fmt_local(ts: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _str(args: dict, key: str, required: bool = True, limit: int = 500) -> str:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolError(f"missing {key}")
        return ""
    return redact(str(value)).text.strip()[:limit]


def _int(args: dict, key: str, lo: int, hi: int, default: Optional[int] = None) -> Optional[int]:
    value = args.get(key, default)
    if value is None or value == "":
        return default
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a number")
    return max(lo, min(hi, n))


def _bool(args: dict, key: str, default: Optional[bool] = None) -> Optional[bool]:
    value = args.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    return default if value is None else bool(value)


# --------------------------------------------------------------------------- implementations

def search_trials(ctx: ToolContext, args: dict) -> dict:
    condition = _str(args, "condition", limit=120)
    location = _str(args, "location", required=False, limit=80)
    try:
        trials = ctx.app.trials.search(condition, location, _int(args, "max_results", 1, 5, 3) or 3)
    except TrialsError as e:
        return {"error": str(e)}
    ours = {r["nct_id"] for r in ctx.db.all("SELECT nct_id FROM studies WHERE status='active' AND nct_id IS NOT NULL")}
    for t in trials:
        t["run_by_our_study_team"] = t.get("nct_id") in ours
    return {"source": "ClinicalTrials.gov (live)", "query": {"condition": condition, "location": location},
            "trials": trials, "note": "Mention it when a trial is run by our study team: we can pre-screen and refer directly."}


def get_trial_details(ctx: ToolContext, args: dict) -> dict:
    nct = _str(args, "nct_id", limit=20).upper()
    try:
        record = ctx.app.trials.get(nct, _str(args, "near", required=False, limit=80)) or {}
    except TrialsError as e:
        record = {"error": str(e)}
    ours = ctx.db.one("SELECT config FROM studies WHERE nct_id=? AND status='active'", (nct,))
    if ours:
        config = json.loads(ours["config"] or "{}")
        record["run_by_our_study_team"] = True
        record["approved_prescreen_questions"] = [q["question"] for q in config.get("prescreen_questions", [])]
        record["plain_summary"] = config.get("summary_plain")
        record["note"] = "Ask the approved pre-screen questions one or two at a time, then save_prescreen_referral."
        record.pop("error", None)
    return record


def save_prescreen_referral(ctx: ToolContext, args: dict) -> dict:
    match = _str(args, "likely_match", limit=10).lower()
    if match not in {"likely", "unlikely", "unsure"}:
        raise ToolError("likely_match must be likely, unlikely or unsure")
    rid = ctx.db.insert(
        "INSERT INTO referrals(patient_id,nct_id,trial_title,likely_match,summary,created_at) VALUES(?,?,?,?,?,?)",
        (ctx.patient["id"], _str(args, "nct_id", limit=20).upper(), _str(args, "trial_title", required=False, limit=300),
         match, _str(args, "summary", limit=1500), now()))
    ctx.flags.add("referral")
    return {"saved": True, "referral_id": rid, "note": "The study team reviews every referral and decides eligibility."}


def get_my_study_info(ctx: ToolContext, args: dict) -> dict:
    p = ctx.patient
    study = ctx.db.one("SELECT code,title,site,coordinator,coordinator_phone FROM studies WHERE id=?", (p["study_id"],)) or {}
    t = now()
    visits = ctx.db.all("SELECT title,due_at,location FROM visits WHERE patient_id=? AND due_at>=? AND status='scheduled'"
                        " ORDER BY due_at LIMIT 3", (p["id"], t - 3600))
    reminders = ctx.db.all("SELECT body,due_at FROM reminders WHERE patient_id=? AND status='scheduled' ORDER BY due_at LIMIT 5",
                           (p["id"],))
    week = ctx.db.all("SELECT took_dose FROM checkins WHERE patient_id=? AND created_at>=? AND took_dose IS NOT NULL",
                      (p["id"], t - 7 * 86400))
    return {
        "study": study,
        "upcoming_visits": [{"title": v["title"], "when_local": fmt_local(v["due_at"], ctx.tz), "location": v["location"]}
                            for v in visits],
        "scheduled_reminders": [{"text": r["body"], "when_local": fmt_local(r["due_at"], ctx.tz)} for r in reminders],
        "doses_reported_last_7_days": {"taken": sum(1 for w in week if w["took_dose"]), "missed": sum(1 for w in week if not w["took_dose"])},
        "note": "Treatment assignment is blinded. Never guess it.",
    }


STOP = set("a an and are as at be but by can do does for from have how i if in is it me my of on or so that the this to "
           "what when where which who why will with you your am was were should would could get got im".split())
SYNONYMS = {"paid": "compensation", "pay": "compensation", "payment": "compensation", "money": "compensation",
            "reimburse": "travel", "uber": "travel", "lyft": "travel", "taxi": "travel", "ride": "travel",
            "ibuprofen": "medicine", "advil": "medicine", "tylenol": "medicine", "aspirin": "medicine",
            "supplement": "medicine", "vitamin": "medicine", "quit": "leave", "withdraw": "leave", "stop": "leave",
            "placebo": "blinding", "blind": "blinding", "pregnant": "pregnancy", "reschedule": "missed", "miss": "missed",
            "shot": "injection", "vaccine": "vaccine", "diary": "diary", "call": "contacts", "phone": "contacts",
            "weekend": "contacts", "night": "contacts", "doctor": "contacts", "blood": "blood"}


def _tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) < 3 or w in STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        out.append(SYNONYMS.get(w, w))
    return out


def search_protocol(ctx: ToolContext, args: dict) -> dict:
    question = _str(args, "question", limit=500)
    study = ctx.db.one("SELECT protocol FROM studies WHERE id=?", (ctx.patient["study_id"],))
    passages = [p.strip() for p in (study or {}).get("protocol", "").split("\n\n") if p.strip()]
    if not passages:
        return {"passages": [], "note": "No protocol on file."}
    docs = [set(_tokens(p)) for p in passages]
    heads = [set(_tokens(p.split(":", 1)[0])) for p in passages]
    q = set(_tokens(question))
    scores = []
    for i, d in enumerate(docs):
        s = sum(math.log(1 + len(docs) / (1 + sum(1 for o in docs if t in o))) for t in q & d)
        s += 2.0 * len(q & heads[i])
        scores.append((s, i))
    top = [passages[i] for s, i in sorted(scores, reverse=True)[:3] if s > 0]
    return {"passages": top, "note": "Answer only from these passages. If they don't answer it, say the team will follow up."}


def log_symptom(ctx: ToolContext, args: dict) -> dict:
    grade = _int(args, "grade", 1, 5, 1) or 1
    serious = bool(_bool(args, "serious", False))
    symptom = _str(args, "symptom", limit=120)
    eid = ctx.db.insert(
        "INSERT INTO events(patient_id,symptom,description,grade,serious,onset,ongoing,source_message_id,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (ctx.patient["id"], symptom, _str(args, "description", required=False, limit=1000), grade, int(serious),
         _str(args, "onset", required=False, limit=120), _bool(args, "ongoing"), ctx.inbound_id, now()))
    ctx.flags.add("medical")
    escalated = False
    if grade >= 3 or serious:
        escalated = open_escalation(ctx.db, ctx.patient["id"], f"Grade {grade}{' serious' if serious else ''} event: {symptom}",
                                    "urgent")
    return {"logged": True, "event_id": eid, "escalated_to_team": escalated or grade >= 3 or serious}


def record_checkin(ctx: ToolContext, args: dict) -> dict:
    cid = ctx.db.insert("INSERT INTO checkins(patient_id,took_dose,wellbeing,notes,created_at) VALUES(?,?,?,?,?)",
                        (ctx.patient["id"], _bool(args, "took_dose"), _int(args, "wellbeing", 1, 10),
                         _str(args, "notes", required=False, limit=500), now()))
    return {"recorded": True, "checkin_id": cid}


def _parse_when(value: str, tz: tzinfo) -> float:
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ToolError("when must be an ISO 8601 local date-time like 2026-10-17T20:00")
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    return when.timestamp()


def schedule_reminder(ctx: ToolContext, args: dict) -> dict:
    due = _parse_when(_str(args, "when", limit=40), ctx.tz)
    t = now()
    if due < t + 30:
        raise ToolError("reminder time must be in the future")
    if due > t + 60 * 86400:
        raise ToolError("reminders can be at most 60 days ahead")
    count = ctx.db.one("SELECT COUNT(*) AS n FROM reminders WHERE patient_id=? AND status='scheduled'", (ctx.patient["id"],))
    if count and count["n"] >= 10:
        raise ToolError("too many scheduled reminders (max 10)")
    rid = ctx.db.insert("INSERT INTO reminders(patient_id,body,due_at,created_by,created_at) VALUES(?,?,?,?,?)",
                        (ctx.patient["id"], _str(args, "text", limit=300), due, "agent", t))
    return {"scheduled": True, "reminder_id": rid, "when_local": fmt_local(due, ctx.tz)}


def open_escalation(db, patient_id: int, reason: str, urgency: str) -> bool:
    """Create an escalation unless an identical open one was raised in the last 10 minutes."""
    recent = db.one("SELECT id FROM escalations WHERE patient_id=? AND status='open' AND urgency=? AND created_at>=?",
                    (patient_id, urgency, now() - 600))
    if recent:
        return False
    db.insert("INSERT INTO escalations(patient_id,reason,urgency,created_at) VALUES(?,?,?,?)",
              (patient_id, reason[:500], urgency, now()))
    return True


def escalate_to_coordinator(ctx: ToolContext, args: dict) -> dict:
    urgency = (_str(args, "urgency", required=False, limit=10) or "routine").lower()
    if urgency not in {"routine", "urgent"}:
        urgency = "routine"
    open_escalation(ctx.db, ctx.patient["id"], _str(args, "reason", limit=500), urgency)
    ctx.flags.add("medical")
    return {"escalated": True, "urgency": urgency}


def remember_fact(ctx: ToolContext, args: dict) -> dict:
    fact = _str(args, "fact", limit=200)
    row = ctx.db.one("SELECT facts FROM patients WHERE id=?", (ctx.patient["id"],))
    facts = json.loads(row["facts"] or "[]") if row else []
    if fact not in facts:
        facts = (facts + [fact])[-30:]
        ctx.db.run("UPDATE patients SET facts=? WHERE id=?", (json.dumps(facts), ctx.patient["id"]))
    return {"remembered": True, "facts_on_file": len(facts)}


# --------------------------------------------------------------------------- registry

@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list
    roles: set
    fn: Callable[[ToolContext, dict], dict]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": self.properties, "required": self.required}}


BOTH = {PARTICIPANT, PROSPECT}
TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("search_trials", "Search ClinicalTrials.gov for recruiting studies by condition and optional city/state.",
         {"condition": {"type": "string", "description": "Condition or disease, e.g. 'type 2 diabetes'"},
          "location": {"type": "string", "description": "City, state or country, e.g. 'Boston'"},
          "max_results": {"type": "integer", "minimum": 1, "maximum": 5}},
         ["condition"], {PROSPECT}, search_trials),
    Tool("get_trial_details", "Get one study's eligibility criteria, ages, sites and summary from ClinicalTrials.gov.",
         {"nct_id": {"type": "string", "description": "NCT ID like NCT01234567"},
          "near": {"type": "string", "description": "Optional city to list nearby sites first"}},
         ["nct_id"], {PROSPECT}, get_trial_details),
    Tool("save_prescreen_referral",
         "Save a pre-screening summary for the study team after asking about key eligibility criteria. "
         "The team decides eligibility; this only flags interest.",
         {"nct_id": {"type": "string"}, "trial_title": {"type": "string"},
          "likely_match": {"type": "string", "enum": ["likely", "unlikely", "unsure"]},
          "summary": {"type": "string", "description": "Which criteria were asked and the person's answers"}},
         ["nct_id", "likely_match", "summary"], {PROSPECT}, save_prescreen_referral),
    Tool("get_my_study_info", "Get the participant's study, coordinator contact, upcoming visits, reminders and recent dose check-ins.",
         {}, [], {PARTICIPANT}, get_my_study_info),
    Tool("search_protocol", "Search the participant's study protocol and FAQ (visits, compensation, medicines, leaving the study).",
         {"question": {"type": "string"}}, ["question"], {PARTICIPANT}, search_protocol),
    Tool("log_symptom",
         "Record any symptom, side effect or health change the participant reports, even mild. Grade 1 mild, "
         "2 moderate (limits some activities), 3 severe (limits self-care or needed medical care), 4 life-threatening.",
         {"symptom": {"type": "string", "description": "Short name, e.g. 'headache'"},
          "description": {"type": "string", "description": "What they said, in plain words"},
          "grade": {"type": "integer", "minimum": 1, "maximum": 5},
          "serious": {"type": "boolean", "description": "True if hospitalization, ER visit or life-threatening"},
          "onset": {"type": "string", "description": "When it started, e.g. 'this morning, after injection'"},
          "ongoing": {"type": "boolean"}},
         ["symptom", "grade"], {PARTICIPANT}, log_symptom),
    Tool("record_checkin", "Record a daily check-in: whether they took/received their study dose and overall wellbeing 1-10.",
         {"took_dose": {"type": "boolean"}, "wellbeing": {"type": "integer", "minimum": 1, "maximum": 10},
          "notes": {"type": "string"}}, [], {PARTICIPANT}, record_checkin),
    Tool("schedule_reminder", "Schedule a text reminder for this person at a local date-time.",
         {"text": {"type": "string", "description": "Reminder text to send"},
          "when": {"type": "string", "description": "Local ISO 8601 date-time, e.g. 2026-10-17T20:00"}},
         ["text", "when"], {PARTICIPANT}, schedule_reminder),
    Tool("escalate_to_coordinator",
         "Flag the human study coordinator: person wants a human, wants to leave the study, has a question you can't "
         "answer from the protocol, or anything concerning.",
         {"reason": {"type": "string"}, "urgency": {"type": "string", "enum": ["routine", "urgent"]}},
         ["reason"], BOTH, escalate_to_coordinator),
    Tool("remember_fact", "Remember a lasting preference about this person (preferred name, best time to text, language).",
         {"fact": {"type": "string"}}, ["fact"], BOTH, remember_fact),
]}

MEDICAL_TOOLS = {"log_symptom", "escalate_to_coordinator"}


def schemas_for(role: str) -> list[dict]:
    return [t.schema() for t in TOOLS.values() if role in t.roles]


def execute(ctx: ToolContext, name: str, args: Any) -> dict:
    tool = TOOLS.get(name)
    if tool is None or ctx.patient.get("role") not in tool.roles:
        result = {"error": "tool_not_allowed"}
    elif not isinstance(args, dict):
        result = {"error": "arguments must be an object"}
    else:
        try:
            result = tool.fn(ctx, args)
        except ToolError as e:
            result = {"error": str(e)}
        except Exception as e:  # never let one tool crash the conversation
            result = {"error": f"internal_error: {type(e).__name__}"}
    ctx.calls.append({"name": name, "input": args, "output": result})
    ctx.db.audit(ctx.patient["id"], f"tool:{name}", json.dumps({"input": args, "output": result}, default=str)[:4000])
    return result
