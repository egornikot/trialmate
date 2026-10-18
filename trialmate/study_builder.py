"""Study Builder: Factory's Droid turns a ClinicalTrials.gov record into a ready-to-run study.

A coordinator enters an NCT ID on the dashboard. TrialMate downloads the official
record into a private workspace, then Droid (via the Droid SDK) writes `study.json`:
plain-language FAQ, visit schedule, pre-screen questions, study-specific red-flag
symptoms and test messages. Droid checks its own work with our `check_study` tool
and fixes errors until the checks pass. TrialMate validates the file again,
then loads it as a draft study that a coordinator must activate.

If the Droid SDK or Factory API key is missing, a basic rule-based builder runs
instead so the flow still works in development. The dashboard shows which engine built it.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .ctgov import TrialsError
from .db import now
from .safety import outbound_review_reason

NCT = re.compile(r"^NCT\d{8}$")

SCHEMA_DOC = """\
study.json must be one JSON object with exactly these keys:

{
  "nct_id": "NCT01234567",                       // same as trial.json
  "code": "short label, max 24 chars",
  "title": "plain-language title, max 120 chars",
  "condition": "main condition in plain words",
  "summary_plain": "2-3 sentences a 12-year-old understands: what the study tests and what joining involves",
  "faq": [ {"topic": "Visit schedule", "answer": "..."} ],          // 5-12 items
  "visits": [ {"title": "Screening visit", "day": 0} ],              // 2-20 items, day = days after enrollment, ascending
  "prescreen_questions": [ {"question": "Are you 18 or older?", "criterion": "Age 18 or older", "disqualify_if": "no"} ],  // 3-8 items
  "red_flags": [ {"name": "liver problems", "pattern": "yellow(ing)? (skin|eyes)|dark urine", "urgency": "urgent", "why": "..."} ],  // 2-8 items
  "test_messages": [ {"text": "my eyes look yellow", "expect": "red_flag", "red_flag": "liver problems"},
                     {"text": "when is my next visit", "expect": "routine"} ]   // 6-20 items
}

Rules:
- Use only facts in trial.json. If the record doesn't say (payment, parking, exact visit days), the FAQ answer must say
  the study team will confirm, and visit days should be a reasonable schedule marked "(estimated)" in the title.
- Plain language, short sentences, no jargon. Never give medical advice, dosing instructions or diagnoses.
- FAQ must include: what the study is testing, what joining involves, leaving the study anytime, who to contact,
  and what to do in an emergency (call 911).
- prescreen_questions are yes/no questions built from the most important inclusion/exclusion criteria.
  disqualify_if is "yes" or "no": the answer that would rule the person out.
- red_flags are symptoms that need urgent attention from the study team for THIS intervention and condition.
  pattern is a case-insensitive Python regular expression. urgency is "urgent" or "emergency".
- Every red flag needs at least one test message with expect "red_flag" naming it, which its pattern must match.
  Include at least 3 test messages with expect "routine" that must NOT match any red flag.
"""

TASK = """You are setting up a clinical trial for TrialMate, a text-message assistant for trial participants.

The official ClinicalTrials.gov record is in trial.json in the current folder. Read it.
Write study.json in the current folder, following the format and rules in SCHEMA.md.
Then call the check_study tool. If it reports errors, fix study.json and call check_study again.
Repeat until check_study returns OK. Do not edit trial.json or SCHEMA.md and do not create other files.
Finish with one sentence summarizing the study you set up."""


# --------------------------------------------------------------------------- validation (shared by Droid and TrialMate)

def _str(v: Any, limit: int) -> bool:
    return isinstance(v, str) and 0 < len(v.strip()) <= limit


def validate(study: Any, nct_id: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(study, dict):
        return ["study.json must be a JSON object"]
    expected = {"nct_id", "code", "title", "condition", "summary_plain", "faq", "visits", "prescreen_questions",
                "red_flags", "test_messages"}
    missing, extra = expected - set(study), set(study) - expected
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected keys: {sorted(extra)}")
    if study.get("nct_id") != nct_id:
        errors.append(f"nct_id must be {nct_id}")
    for key, limit in (("code", 24), ("title", 120), ("condition", 120), ("summary_plain", 700)):
        if not _str(study.get(key), limit):
            errors.append(f"{key} must be a non-empty string of at most {limit} characters")

    def items(key: str, lo: int, hi: int) -> list:
        value = study.get(key)
        if not isinstance(value, list) or not lo <= len(value) <= hi:
            errors.append(f"{key} must be a list with {lo}-{hi} items")
            return []
        return [v for v in value if isinstance(v, dict)]

    for i, f in enumerate(items("faq", 5, 12)):
        if not _str(f.get("topic"), 60) or not _str(f.get("answer"), 600):
            errors.append(f"faq[{i}] needs topic (<=60 chars) and answer (<=600 chars)")
        elif outbound_review_reason(f["answer"]):
            errors.append(f"faq[{i}] answer looks like {outbound_review_reason(f['answer'])}; remove medical advice")
    topics = " ".join(str(f.get("topic", "")) + " " + str(f.get("answer", "")) for f in study.get("faq") or [] if isinstance(f, dict)).lower()
    for need, words in (("leaving the study", ("leave", "withdraw", "stop being")), ("emergency", ("911",)),
                        ("contact", ("contact", "call", "coordinator", "study team"))):
        if not any(w in topics for w in words):
            errors.append(f"faq must cover {need}")

    last_day = -1
    for i, v in enumerate(items("visits", 2, 20)):
        day = v.get("day")
        if not _str(v.get("title"), 80) or not isinstance(day, int) or not 0 <= day <= 730:
            errors.append(f"visits[{i}] needs title and integer day 0-730")
        elif day < last_day:
            errors.append("visits must be in ascending day order")
        else:
            last_day = day

    for i, q in enumerate(items("prescreen_questions", 3, 8)):
        if not _str(q.get("question"), 160) or not q["question"].strip().endswith("?"):
            errors.append(f"prescreen_questions[{i}].question must be a yes/no question ending with '?'")
        if not _str(q.get("criterion"), 300):
            errors.append(f"prescreen_questions[{i}].criterion is required")
        if q.get("disqualify_if") not in ("yes", "no"):
            errors.append(f"prescreen_questions[{i}].disqualify_if must be 'yes' or 'no'")

    flags: dict[str, re.Pattern] = {}
    for i, rf in enumerate(items("red_flags", 2, 8)):
        if not _str(rf.get("name"), 60) or not _str(rf.get("why"), 300) or rf.get("urgency") not in ("urgent", "emergency"):
            errors.append(f"red_flags[{i}] needs name, why and urgency 'urgent' or 'emergency'")
            continue
        try:
            pattern = rf.get("pattern")
            if not _str(pattern, 300):
                raise re.error("empty")
            flags[rf["name"]] = re.compile(pattern, re.I)
        except re.error as e:
            errors.append(f"red_flags[{i}].pattern is not a valid regex: {e}")

    tests = items("test_messages", 6, 20)
    covered: set[str] = set()
    routine = 0
    for i, t in enumerate(tests):
        text, expect = t.get("text"), t.get("expect")
        if not _str(text, 300) or expect not in ("red_flag", "routine"):
            errors.append(f"test_messages[{i}] needs text and expect 'red_flag' or 'routine'")
            continue
        hits = [name for name, p in flags.items() if p.search(text)]
        if expect == "red_flag":
            target = t.get("red_flag")
            if target not in flags:
                errors.append(f"test_messages[{i}].red_flag must name one of {sorted(flags)}")
            elif target not in hits:
                errors.append(f"test failed: red flag '{target}' pattern does not match test message {text!r}")
            else:
                covered.add(target)
        else:
            routine += 1
            if hits:
                errors.append(f"test failed: routine message {text!r} wrongly matches red flag(s) {hits}")
    for name in flags:
        if name not in covered:
            errors.append(f"red flag '{name}' has no passing test message")
    if routine < 3:
        errors.append("need at least 3 routine test messages")
    return errors


# --------------------------------------------------------------------------- loading

def study_from_config(db: Any, config: dict, source: str) -> int:
    protocol = "\n\n".join(f"{f['topic']}: {f['answer']}" for f in config["faq"])
    existing = db.one("SELECT id FROM studies WHERE nct_id=?", (config["nct_id"],))
    values = (config["code"], config["title"], config["condition"], "Partner site (demo)", "Study coordinator",
              "(617) 555-0142", protocol, config["nct_id"], source, json.dumps(config), now())
    if existing:
        db.run("UPDATE studies SET code=?, title=?, condition=?, site=?, coordinator=?, coordinator_phone=?, protocol=?,"
               " nct_id=?, source=?, config=?, created_at=?, status='draft' WHERE id=?", values + (existing["id"],))
        return existing["id"]
    return db.insert("INSERT INTO studies(code,title,condition,site,coordinator,coordinator_phone,protocol,nct_id,source,"
                     "config,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,'draft')", values)


# --------------------------------------------------------------------------- engines

def droid_available() -> bool:
    if not os.environ.get("FACTORY_API_KEY"):
        return False
    try:
        import droid_sdk  # noqa: F401
        return True
    except Exception:
        return False


def fallback_config(record: dict) -> dict:
    """Simple rule-based study setup, used only when Droid isn't configured."""
    nct = record["nct_id"]
    criteria = record.get("eligibility_criteria", "")
    inclusion = criteria.split("Exclusion")[0]
    lines = [re.sub(r"^[\s*\-•\d.)]+", "", ln).strip() for ln in inclusion.splitlines()]
    lines = [ln for ln in lines if 12 <= len(ln) <= 200 and not ln.lower().startswith("inclusion")][:4]
    questions = [{"question": f"Is this true for you: {ln.rstrip('.;')}?"[:160], "criterion": ln[:300], "disqualify_if": "no"}
                 for ln in lines]
    ages = record.get("ages", "")
    questions.insert(0, {"question": f"Is your age in the range {ages}?"[:160], "criterion": f"Age {ages}", "disqualify_if": "no"})
    while len(questions) < 3:
        questions.append({"question": "Are you willing to come to the study clinic for visits?", "criterion": "Able to attend visits",
                          "disqualify_if": "no"})
    title = record.get("title") or nct
    return {
        "nct_id": nct, "code": nct, "title": title[:120], "condition": ", ".join(record.get("conditions", [])[:2])[:120] or "see record",
        "summary_plain": (record.get("summary") or title)[:600],
        "faq": [
            {"topic": "What the study tests", "answer": f"This study is called: {title[:400]}. The study team will explain the details."},
            {"topic": "What joining involves", "answer": "The study team will explain the visits and tests before you decide to join."},
            {"topic": "Leaving the study", "answer": "You can leave the study at any time, for any reason, without affecting your regular care."},
            {"topic": "Contacts", "answer": "Text this number or call the study coordinator with any question."},
            {"topic": "Emergencies", "answer": "In an emergency, call 911 first, then tell the study team."},
        ],
        "visits": [{"title": "Screening visit (estimated)", "day": 0}, {"title": "Follow-up visit (estimated)", "day": 28}],
        "prescreen_questions": questions[:8],
        "red_flags": [
            {"name": "severe allergic reaction", "pattern": r"(face|lips?|tongue|throat).{0,15}(swell|swollen)|hives all over",
             "urgency": "emergency", "why": "Possible allergic reaction to a study treatment."},
            {"name": "high fever", "pattern": r"fever.{0,20}(10[3-9]|high)|(10[3-9])\s*(degrees|f\b)",
             "urgency": "urgent", "why": "High fever can signal infection."},
        ],
        "test_messages": [
            {"text": "my lips are swollen", "expect": "red_flag", "red_flag": "severe allergic reaction"},
            {"text": "I have a fever of 103", "expect": "red_flag", "red_flag": "high fever"},
            {"text": "when is my next visit?", "expect": "routine"},
            {"text": "do I get paid for parking?", "expect": "routine"},
            {"text": "I took my medicine today", "expect": "routine"},
            {"text": "can I bring my kid to the visit?", "expect": "routine"},
        ],
    }


class StudyBuilder:
    def __init__(self, app: Any, root: Optional[Path] = None) -> None:
        self.app = app
        self.root = root or Path(app.settings.db_path).parent / "studies"

    def start(self, nct_id: str) -> dict:
        nct = (nct_id or "").strip().upper()
        if not NCT.match(nct):
            return {"error": "Enter an NCT ID like NCT01234567"}
        if self.app.db.one("SELECT id FROM builds WHERE status='running'"):
            return {"error": "A study is already being built. Wait for it to finish."}
        engine = "droid" if droid_available() else "fallback"
        bid = self.app.db.insert("INSERT INTO builds(nct_id,engine,created_at) VALUES(?,?,?)", (nct, engine, now()))
        threading.Thread(target=self._run, args=(bid, nct, engine), name=f"build-{nct}", daemon=True).start()
        return {"build_id": bid, "engine": engine}

    # ---------------------------------------------------------------- helpers
    def log(self, bid: int, kind: str, text: str) -> None:
        with self.app.db.tx() as conn:
            row = conn.execute("SELECT log FROM builds WHERE id=?", (bid,)).fetchone()
            entries = json.loads(row["log"]) if row else []
            entries.append({"t": time.time(), "kind": kind, "text": text[:600]})
            conn.execute("UPDATE builds SET log=? WHERE id=?", (json.dumps(entries[-200:]), bid))

    def finish(self, bid: int, status: str, study_id: Optional[int] = None, error: Optional[str] = None) -> None:
        self.app.db.run("UPDATE builds SET status=?, study_id=?, error=?, finished_at=? WHERE id=?",
                        (status, study_id, error, now(), bid))

    def _run(self, bid: int, nct: str, engine: str) -> None:
        try:
            self.log(bid, "step", f"Downloading {nct} from ClinicalTrials.gov")
            try:
                record = self.app.trials.get(nct)
            except TrialsError as e:
                raise RuntimeError(f"Couldn't download the trial: {e}")
            workspace = self.root / nct
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "trial.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            (workspace / "SCHEMA.md").write_text(SCHEMA_DOC, encoding="utf-8")
            stale = workspace / "study.json"
            if stale.exists():
                stale.unlink()
            self.log(bid, "step", f"Saved the official record ({record.get('title', '')[:80]})")
            if engine == "droid":
                self._run_droid(bid, workspace, nct)
                config = json.loads((workspace / "study.json").read_text(encoding="utf-8-sig"))
            else:
                self.log(bid, "step", "Droid isn't configured (FACTORY_API_KEY + droid-sdk), using the basic builder")
                config = fallback_config(record)
                (workspace / "study.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
            errors = validate(config, nct)
            if errors:
                raise RuntimeError("Final check failed: " + "; ".join(errors[:5]))
            self.log(bid, "check", "TrialMate re-checked study.json: all checks passed")
            sid = study_from_config(self.app.db, config, engine)
            self.log(bid, "done", f"Loaded as a draft study with {len(config['faq'])} FAQ answers, {len(config['visits'])} visits, "
                                  f"{len(config['prescreen_questions'])} screening questions and {len(config['red_flags'])} red flags. "
                                  "Review and activate it.")
            self.finish(bid, "done", sid)
        except Exception as e:
            self.log(bid, "error", str(e))
            self.finish(bid, "failed", error=str(e)[:500])

    def _run_droid(self, bid: int, workspace: Path, nct: str) -> None:
        asyncio.run(self._droid_session(bid, workspace, nct))
        if not (workspace / "study.json").exists():
            raise RuntimeError("Droid finished without writing study.json")

    async def _droid_session(self, bid: int, workspace: Path, nct: str) -> None:
        from droid_sdk import Autonomy, Session, SessionConfig  # type: ignore
        from droid_sdk.mcp import create_sdk_mcp_server, tool  # type: ignore

        @tool("check_study", "Validate study.json in the workspace and run its test messages. Returns OK or a list of errors.")
        async def check_study(path: str = "study.json") -> str:
            target = workspace / "study.json"
            if not target.exists():
                result = "ERROR: study.json does not exist yet"
            else:
                try:
                    errors = validate(json.loads(target.read_text(encoding="utf-8-sig")), nct)
                    result = "OK: all checks passed" if not errors else "ERRORS:\n- " + "\n- ".join(errors)
                except ValueError as e:
                    result = f"ERROR: study.json is not valid JSON: {e}"
            self.log(bid, "check", result[:600])
            return result

        server = create_sdk_mcp_server(name="trialmate", tools=[check_study])
        kwargs: dict[str, Any] = {"cwd": str(workspace),
                                  "config": SessionConfig(autonomy=Autonomy.LOW, mcp_servers=[server])}
        model = os.environ.get("FACTORY_MODEL", "").strip()
        if model:
            kwargs["model"] = model
        self.log(bid, "step", "Starting Droid (autonomy: low, can only edit files in this study's folder)")
        async with Session(**kwargs) as session:
            async with session.stream(TASK) as stream:
                async for event in stream:
                    kind = type(event).__name__
                    if kind in ("TextDelta", "ThinkingDelta", "TokenUsageUpdate", "UserMessage"):
                        continue
                    text = (getattr(event, "text", None) or getattr(event, "name", None)
                            or getattr(event, "tool_name", None) or getattr(event, "message", None) or "")
                    if kind == "ToolCall":
                        args = getattr(event, "input", None) or getattr(event, "arguments", None) or ""
                        text = f"{text} {json.dumps(args, default=str)[:200] if args else ''}"
                    self.log(bid, f"droid:{kind}", str(text) or kind)
            result = stream.result
            summary = getattr(result, "text", None) or getattr(result, "subtype", "")
            self.log(bid, "droid:result", str(summary)[:600])
