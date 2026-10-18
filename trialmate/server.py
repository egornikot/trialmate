"""HTTP server: coordinator dashboard, phone simulator and JSON API (stdlib only)."""
from __future__ import annotations

import json
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from . import agent, outbox, seed
from .db import now

STATIC = Path(__file__).resolve().parent / "static"


def mask(handle: str) -> str:
    if handle.startswith("sim:"):
        return handle
    if "@" in handle:
        user, _, domain = handle.partition("@")
        return f"{user[:1]}***@{domain}"
    digits = re.sub(r"\D", "", handle)
    return f"•••{digits[-4:]}" if len(digits) >= 4 else "•••"


def state(app: Any) -> dict:
    db, t = app.db, now()
    day, week = t - 86400, t - 7 * 86400
    patients = db.all("SELECT * FROM patients ORDER BY CASE role WHEN 'participant' THEN 0 ELSE 1 END, COALESCE(last_seen_at, created_at) DESC")
    out_patients = []
    for p in patients:
        checks = db.all("SELECT took_dose FROM checkins WHERE patient_id=? AND created_at>=? AND took_dose IS NOT NULL",
                        (p["id"], week))
        last = db.one("SELECT body, direction, created_at FROM messages WHERE patient_id=? ORDER BY id DESC LIMIT 1", (p["id"],))
        out_patients.append({
            "id": p["id"], "name": p["name"] or "Unknown", "handle": mask(p["handle"]), "role": p["role"],
            "channel": p["channel"], "opted_out": bool(p["opted_out"]), "demo": bool(p["demo"]),
            "last_seen_at": p["last_seen_at"], "facts": json.loads(p["facts"] or "[]"),
            "adherence": (round(100 * sum(1 for c in checks if c["took_dose"]) / len(checks)) if checks else None),
            "last_message": last,
            "open_escalations": db.one("SELECT COUNT(*) n FROM escalations WHERE patient_id=? AND status='open'", (p["id"],))["n"],
        })
    names = {p["id"]: p["name"] or mask(p["handle"]) for p in patients}

    approvals = db.all("SELECT * FROM outbox WHERE status='needs_approval' ORDER BY id")
    for a in approvals:
        a["patient"] = names.get(a["patient_id"])
        m = re.match(r"reply:(\d+)$", a["idempotency_key"])
        trigger = db.one("SELECT body, created_at FROM messages WHERE id=?", (int(m.group(1)),)) if m else None
        a["in_reply_to"] = trigger["body"] if trigger else None
    escalations = db.all("SELECT * FROM escalations WHERE status='open' ORDER BY CASE urgency WHEN 'emergency' THEN 0 "
                         "WHEN 'urgent' THEN 1 ELSE 2 END, id DESC")
    events = db.all("SELECT * FROM events ORDER BY id DESC LIMIT 25")
    referrals = db.all("SELECT * FROM referrals ORDER BY id DESC LIMIT 20")
    for rows in (escalations, events, referrals):
        for r in rows:
            r["patient"] = names.get(r["patient_id"])
    week_checks = db.all("SELECT took_dose FROM checkins WHERE created_at>=? AND took_dose IS NOT NULL", (week,))
    trace = db.all("SELECT * FROM audit ORDER BY id DESC LIMIT 30")
    for r in trace:
        r["patient"] = names.get(r["patient_id"])
    s = app.settings
    studies = []
    for st in db.all("SELECT * FROM studies ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, id DESC"):
        cfg = json.loads(st.get("config") or "{}")
        studies.append({
            "id": st["id"], "code": st["code"], "title": st["title"], "nct_id": st["nct_id"], "status": st["status"],
            "source": st["source"], "condition": st["condition"],
            "participants": db.one("SELECT COUNT(*) n FROM patients WHERE study_id=? AND role='participant'", (st["id"],))["n"],
            "summary": cfg.get("summary_plain"),
            "counts": {k: len(cfg.get(k) or []) for k in ("faq", "visits", "prescreen_questions", "red_flags", "test_messages")},
            "prescreen_questions": [q.get("question") for q in cfg.get("prescreen_questions") or []],
            "red_flags": [{"name": r.get("name"), "urgency": r.get("urgency"), "why": r.get("why")} for r in cfg.get("red_flags") or []],
            "visits": cfg.get("visits") or [],
        })
    build = db.one("SELECT * FROM builds ORDER BY id DESC LIMIT 1")
    if build:
        build["log"] = json.loads(build["log"] or "[]")
    return {
        "now": t,
        "config": {"llm": app.llm.name, "llm_label": getattr(app.llm, "label", ""), "model": getattr(app.llm, "model", ""),
                   "photon": "photon" in app.channels, "platform": s.photon_platform, "approval_mode": s.approval_mode,
                   "tz": s.tz, "droid": app.builder_engine(), "droid_missing": app.droid_missing()},
        "studies": studies,
        "build": build,
        "status": app.status,
        "stats": {
            "awaiting_approval": len(approvals),
            "open_escalations": len(escalations),
            "emergencies": sum(1 for e in escalations if e["urgency"] == "emergency"),
            "events_7d": db.one("SELECT COUNT(*) n FROM events WHERE created_at>=?", (week,))["n"],
            "severe_7d": db.one("SELECT COUNT(*) n FROM events WHERE created_at>=? AND (grade>=3 OR serious=1)", (week,))["n"],
            "adherence_7d": round(100 * sum(1 for c in week_checks if c["took_dose"]) / len(week_checks)) if week_checks else None,
            "referrals_new": db.one("SELECT COUNT(*) n FROM referrals WHERE status='new'")["n"],
            "messages_today": db.one("SELECT COUNT(*) n FROM messages WHERE created_at>=?", (day,))["n"],
            "participants": sum(1 for p in patients if p["role"] == "participant"),
            "prospects": sum(1 for p in patients if p["role"] != "participant"),
        },
        "approvals": approvals,
        "escalations": escalations,
        "events": events,
        "referrals": referrals,
        "patients": out_patients,
        "outbox_recent": db.all("SELECT id, patient_id, status, kind, attempts, last_error, created_at, sent_at FROM outbox "
                                "WHERE status IN ('failed','sending','queued') ORDER BY id DESC LIMIT 10"),
        "trace": trace,
    }


def conversation(app: Any, patient_id: int) -> dict:
    db = app.db
    p = db.one("SELECT * FROM patients WHERE id=?", (patient_id,))
    if not p:
        return {"messages": []}
    msgs = db.all("SELECT id, direction, body, redactions, created_at FROM messages WHERE patient_id=?"
                  " ORDER BY created_at DESC, id DESC LIMIT 80", (patient_id,))
    msgs.reverse()
    pending = db.all("SELECT id, body, reason, created_at FROM outbox WHERE patient_id=? AND status='needs_approval' ORDER BY id",
                     (patient_id,))
    queued = db.all("SELECT id, body, created_at FROM outbox WHERE patient_id=? AND status IN ('queued','sending') ORDER BY id",
                    (patient_id,))
    return {"patient": {"id": p["id"], "name": p["name"], "role": p["role"], "handle": mask(p["handle"])},
            "messages": msgs, "pending": pending, "queued": queued}


def make_handler(app: Any):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TrialMate/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
            pass

        # ---------------------------------------------------------------- helpers
        def _send(self, code: int, payload: Any, ctype: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(min(n, 1_000_000)))
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}

        def _authorized(self, query: dict) -> bool:
            token = app.settings.dashboard_token
            if not token:
                return True
            return self.headers.get("X-Token") == token or query.get("token", [""])[0] == token

        # ---------------------------------------------------------------- routes
        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
                if url.path == "/api/health":
                    return self._send(200, {"ok": True, "llm": app.llm.name, "photon": "photon" in app.channels})
                if not self._authorized(query):
                    return self._send(401, {"error": "unauthorized"})
                if url.path == "/api/state":
                    return self._send(200, state(app))
                if url.path == "/api/conversation":
                    return self._send(200, conversation(app, int(query.get("patient_id", ["0"])[0] or 0)))
                return self._send(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self) -> None:
            url = urlparse(self.path)
            if not self._authorized(parse_qs(url.query)):
                return self._send(401, {"error": "unauthorized"})
            body = self._body()
            path = url.path
            try:
                if path == "/api/simulate":
                    handle = str(body.get("handle") or "sim:visitor")[:80]
                    if not handle.startswith("sim:"):
                        handle = "sim:" + handle
                    text = str(body.get("text") or "").strip()
                    if not text:
                        return self._send(400, {"error": "text is required"})
                    result = agent.handle_inbound(app, handle, text, channel="sim", name=body.get("name") or None)
                    return self._send(200, result)
                m = re.match(r"^/api/outbox/(\d+)/(approve|reject)$", path)
                if m:
                    oid = int(m.group(1))
                    ok = outbox.approve(app.db, oid, body.get("body")) if m.group(2) == "approve" else outbox.reject(app.db, oid)
                    return self._send(200 if ok else 409, {"ok": ok})
                m = re.match(r"^/api/escalations/(\d+)/resolve$", path)
                if m:
                    app.db.run("UPDATE escalations SET status='resolved', resolved_at=? WHERE id=?", (now(), int(m.group(1))))
                    return self._send(200, {"ok": True})
                m = re.match(r"^/api/events/(\d+)/review$", path)
                if m:
                    app.db.run("UPDATE events SET status='reviewed' WHERE id=?", (int(m.group(1)),))
                    return self._send(200, {"ok": True})
                m = re.match(r"^/api/referrals/(\d+)/status$", path)
                if m:
                    status = str(body.get("status") or "contacted")
                    if status not in {"new", "contacted", "declined", "enrolled"}:
                        return self._send(400, {"error": "bad status"})
                    app.db.run("UPDATE referrals SET status=? WHERE id=?", (status, int(m.group(1))))
                    return self._send(200, {"ok": True})
                m = re.match(r"^/api/patients/(\d+)/(checkin|enroll)$", path)
                if m:
                    pid = int(m.group(1))
                    if m.group(2) == "checkin":
                        return self._send(200, {"ok": agent.send_checkin(app, pid) is not None})
                    ok = seed.enroll(app.db, pid, str(body.get("name") or ""), app.settings.tz, int(body.get("study_id") or 0))
                    return self._send(200 if ok else 409, {"ok": ok})
                if path == "/api/studies/build":
                    result = app.builder.start(str(body.get("nct_id") or ""))
                    return self._send(400 if "error" in result else 200, result)
                m = re.match(r"^/api/studies/(\d+)/(activate|archive)$", path)
                if m:
                    status = "active" if m.group(2) == "activate" else "archived"
                    app.db.run("UPDATE studies SET status=? WHERE id=?", (status, int(m.group(1))))
                    app.db.audit(None, f"study:{status}", m.group(1))
                    return self._send(200, {"ok": True})
                if path == "/api/reminders/run":
                    app.db.run("UPDATE reminders SET due_at=? WHERE status='scheduled' AND patient_id=?",
                               (now(), int(body.get("patient_id") or 0)))
                    return self._send(200, {"queued": app.scheduler_tick()})
                return self._send(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def serve(app: Any, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
