"""Offline tests (no API key, no network): py -m unittest discover tests   (Mac: python3 -m ...)"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trialmate import agent, outbox, safety  # noqa: E402
from trialmate.app import App  # noqa: E402
from trialmate.channels import PhotonChannel  # noqa: E402
from trialmate.config import Settings  # noqa: E402
from trialmate.ctgov import OfflineTrials, summarize  # noqa: E402
from trialmate.db import Database, now  # noqa: E402
from trialmate.guard import redact  # noqa: E402
from trialmate.outbox import PermanentChannelError  # noqa: E402
from trialmate.server import serve  # noqa: E402
from trialmate.tools import ToolContext, execute  # noqa: E402


def make_app() -> App:
    s = Settings()
    s.llm_mode = "mock"
    s.photon_key = s.photon_project = ""
    return App(s, db=Database(":memory:"), trials=OfflineTrials())


class GuardTests(unittest.TestCase):
    def test_redacts_identifiers(self):
        r = redact("ssn 123-45-6789, card 4111 1111 1111 1111, password: hunter22, key sk-ant-abcdefghijklmnop1234")
        self.assertNotIn("123-45-6789", r.text)
        self.assertNotIn("4111", r.text)
        self.assertNotIn("hunter22", r.text)
        self.assertNotIn("sk-ant", r.text)
        self.assertEqual(set(r.kinds), {"ssn", "card_number", "credential", "api_key"})

    def test_keeps_health_details_and_phone_numbers(self):
        text = "Headache since 2pm, took 2 pills, call me at 617-555-0142"
        self.assertEqual(redact(text).text, text)


class SafetyTests(unittest.TestCase):
    def test_emergencies(self):
        self.assertEqual(safety.detect_emergency("my chest feels tight"), "cardiac")
        self.assertEqual(safety.detect_emergency("I don't want to be alive anymore"), "self_harm")
        self.assertEqual(safety.detect_emergency("my throat is closing"), "allergic")
        self.assertIsNone(safety.detect_emergency("no chest pain, just tired"))
        self.assertIsNone(safety.detect_emergency("mild headache"))

    def test_outbound_review(self):
        self.assertEqual(safety.outbound_review_reason("You can skip the pill tonight"), "dosing advice")
        self.assertEqual(safety.outbound_review_reason("Take 400 mg of ibuprofen"), "dosage amount")
        self.assertEqual(safety.outbound_review_reason("You are on the placebo"), "unblinding")
        self.assertIsNone(safety.outbound_review_reason("Your next visit is Tuesday at 10am."))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_symptom_is_logged_and_held_with_ack(self):
        r = agent.handle_inbound(self.app, "sim:maria", "I've had a bad headache since my shot")
        self.assertEqual(r["status"], "needs_approval")
        self.assertIn("log_symptom", [t["name"] for t in r["tools"]])
        outbox.flush(self.app)
        sent = [m["body"] for m in self.app.db.all("SELECT body FROM messages WHERE patient_id=? AND direction='out'", (r["patient_id"],))]
        self.assertEqual(sent, [agent.ACK_PARTICIPANT])  # ack only; draft waits for a human
        draft = self.app.db.one("SELECT id FROM outbox WHERE status='needs_approval'")
        self.assertTrue(outbox.approve(self.app.db, draft["id"], "Jordan will call you shortly."))
        outbox.flush(self.app)
        last = self.app.db.one("SELECT body FROM messages WHERE direction='out' ORDER BY id DESC LIMIT 1")
        self.assertEqual(last["body"], "Jordan will call you shortly.")

    def test_emergency_sends_fixed_reply_immediately(self):
        r = agent.handle_inbound(self.app, "sim:maria", "my lips are swelling after the dose")
        self.assertEqual(r["emergency"], "allergic")
        row = self.app.db.one("SELECT body, status FROM outbox WHERE kind='safety'")
        self.assertEqual(row["status"], "queued")
        self.assertIn("911", row["body"])
        self.assertEqual(self.app.db.one("SELECT urgency FROM escalations")["urgency"], "emergency")

    def test_secrets_never_reach_model(self):
        agent.handle_inbound(self.app, "sim:maria", "my SSN is 123-45-6789")
        self.assertNotIn("123-45-6789", json.dumps(self.app.llm.calls))
        self.assertNotIn("123-45-6789", json.dumps(self.app.db.all("SELECT body FROM messages")))

    def test_duplicate_external_id_processed_once(self):
        a = agent.handle_inbound(self.app, "+15550001111", "hi", channel="sim", external_id="m1")
        b = agent.handle_inbound(self.app, "+15550001111", "hi", channel="sim", external_id="m1")
        self.assertNotIn("duplicate", a)
        self.assertTrue(b["duplicate"])

    def test_stop_opts_out_and_suppresses(self):
        r = agent.handle_inbound(self.app, "sim:sam", "STOP")
        self.assertEqual(r["action"], "opted_out")
        agent.send_checkin(self.app, r["patient_id"])
        outbox.flush(self.app)
        statuses = {row["kind"]: row["status"] for row in self.app.db.all("SELECT kind, status FROM outbox")}
        self.assertEqual(statuses.get("system"), "sent")

    def test_prospect_cannot_use_participant_tools(self):
        p = self.app.db.one("SELECT * FROM patients WHERE handle='sim:maria'")
        p["role"] = "prospect"
        out = execute(ToolContext(self.app, p), "log_symptom", {"symptom": "x", "grade": 1})
        self.assertEqual(out, {"error": "tool_not_allowed"})

    def test_reminder_sent_once(self):
        p = self.app.db.one("SELECT * FROM patients WHERE handle='sim:maria'")
        self.app.db.run("DELETE FROM reminders")
        self.app.db.insert("INSERT INTO reminders(patient_id,body,due_at,created_at) VALUES(?,?,?,?)",
                           (p["id"], "Diary time", now() - 1, now()))
        self.assertEqual(self.app.scheduler_tick(), 1)
        self.assertEqual(self.app.scheduler_tick(), 0)
        self.assertEqual(self.app.db.one("SELECT COUNT(*) n FROM outbox WHERE kind='reminder'")["n"], 1)

    def test_outbox_key_is_idempotent(self):
        self.assertIsNotNone(outbox.enqueue(self.app.db, 1, "a", "queued", "k1"))
        self.assertIsNone(outbox.enqueue(self.app.db, 1, "a", "queued", "k1"))


class PhotonTests(unittest.TestCase):
    def setUp(self):
        s = Settings()
        s.photon_key, s.photon_project = "test", "pho_prj_test"
        self.ch = PhotonChannel(s)

    def test_parse_inbound(self):
        item = {"id": "msg_1", "createdAt": "2026-10-16T20:00:00.000Z", "status": "received",
                "sentBy": {"id": "u1", "kind": "human", "handle": "+16175550101"},
                "receivedBy": {"id": "a1", "kind": "agent", "handle": "+16175550000"},
                "space": {"id": "sp1", "kind": "direct"},
                "content": {"type": "text", "format": "plain", "text": "hello"}}
        self.assertEqual(self.ch.parse(item), {"external_id": "msg_1", "handle": "+16175550101", "text": "hello",
                                               "space_id": "sp1", "agent_id": "a1"})
        item["sentBy"]["kind"] = "agent"
        self.assertIsNone(self.ch.parse(item))

    def test_send_needs_conversation(self):
        with self.assertRaises(PermanentChannelError):
            self.ch.send({"space_id": None, "agent_id": None}, "hi", "k", None)

    def test_probe_summarizes_without_text_and_masks_handles(self):
        from unittest import mock
        page = {"items": [{"id": "m1", "createdAt": "2026-10-18T06:00:00.000Z", "platform": "imessage", "status": "received",
                           "sentBy": {"id": "u1", "kind": "human", "handle": "egor@icloud.com"},
                           "receivedBy": {"id": "a1", "kind": "agent", "handle": "+16283166196"},
                           "space": {"id": "sp1", "kind": "direct"},
                           "content": {"type": "text", "format": "plain", "text": "secret words"}}]}
        with mock.patch.object(self.ch, "_request", return_value=(200, page)):
            probe = self.ch.probe()
        self.assertNotIn("secret", json.dumps(probe))
        self.assertNotIn("egor@", json.dumps(probe))
        self.assertEqual(probe["latest"][0]["from"], "human e…@icloud.com")
        self.assertEqual(probe["latest"][0]["to"], "agent …6196")
        self.assertTrue(probe["latest"][0]["would_accept"])


class TrialsTests(unittest.TestCase):
    def test_summarize_v2_shape(self):
        study = {"protocolSection": {
            "identificationModule": {"nctId": "NCT01234567", "briefTitle": "A study"},
            "statusModule": {"overallStatus": "RECRUITING"},
            "eligibilityModule": {"minimumAge": "18 Years", "eligibilityCriteria": "Inclusion: adults"},
            "contactsLocationsModule": {"locations": [{"facility": "MGH", "city": "Boston", "state": "Massachusetts"},
                                                      {"facility": "X", "city": "Chicago"}]}}}
        s = summarize(study, near="boston", detail=True)
        self.assertEqual(s["nct_id"], "NCT01234567")
        self.assertEqual(s["sites_near_query"], 1)
        self.assertIn("MGH", s["sites_shown"][0])
        self.assertEqual(s["eligibility_criteria"], "Inclusion: adults")


class StudyBuilderTests(unittest.TestCase):
    def _app(self):
        import tempfile
        s = Settings()
        s.llm_mode, s.photon_key, s.photon_project = "mock", "", ""
        s.db_path = Path(tempfile.mkdtemp()) / "t.db"
        return App(s, trials=OfflineTrials())

    def _wait(self, app):
        import time
        for _ in range(100):
            b = app.db.one("SELECT * FROM builds ORDER BY id DESC LIMIT 1")
            if b["status"] != "running":
                return b
            time.sleep(0.05)
        self.fail("build did not finish")

    def test_validation_catches_bad_regex_and_missing_tests(self):
        from trialmate.study_builder import fallback_config, validate
        good = fallback_config({"nct_id": "NCT01234567", "title": "Example"})
        self.assertEqual(validate(good, "NCT01234567"), [])
        good["red_flags"][0]["pattern"] = "(("
        self.assertTrue(any("not a valid regex" in e for e in validate(good, "NCT01234567")))

    def test_fallback_build_loads_draft_and_red_flags_work(self):
        app = self._app()
        self.assertEqual(app.builder.start("NCT01234567")["engine"], "fallback")
        b = self._wait(app)
        self.assertEqual(b["status"], "done", b["error"])
        study = app.db.one("SELECT * FROM studies WHERE id=?", (b["study_id"],))
        self.assertEqual(study["status"], "draft")
        app.db.run("UPDATE studies SET status='active' WHERE id=?", (study["id"],))
        from trialmate import seed
        r = agent.handle_inbound(app, "sim:new", "hello")
        self.assertTrue(seed.enroll(app.db, r["patient_id"], "New", app.settings.tz, study["id"]))
        r = agent.handle_inbound(app, "sim:new", "I have a fever of 104")
        self.assertEqual(r["red_flag"], "high fever")
        self.assertEqual(r["status"], "needs_approval")

    def test_droid_engine_with_sdk_shaped_fake(self):
        import os
        import tempfile
        from unittest import mock
        from trialmate.study_builder import droid_missing
        fakes = str(Path(__file__).resolve().parent / "fakes")
        sys.path.insert(0, fakes)
        os.environ["FACTORY_API_KEY"] = "fk-test"
        saved_path = os.environ.get("PATH", "")
        home = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(Path, "home", return_value=Path(home.name)), \
                    mock.patch("shutil.which", return_value=None):
                # Factory's Windows installer drops droid.exe in %USERPROFILE%\bin; until then Droid isn't ready
                self.assertTrue(any("droid CLI" in m for m in droid_missing()))
                (Path(home.name) / "bin").mkdir()
                (Path(home.name) / "bin" / "droid.exe").write_text("fake")
                self.assertEqual(droid_missing(), [])
                self.assertTrue(os.environ["PATH"].startswith(str(Path(home.name) / "bin")))
                app = self._app()
                self.assertEqual(app.builder.start("NCT07654321")["engine"], "droid")
            b = self._wait(app)
            self.assertEqual(b["status"], "done", b["error"])
            log = json.loads(b["log"])
            self.assertTrue(any(l["kind"] == "check" and l["text"].startswith("ERRORS") for l in log))
            self.assertTrue(any(l["kind"] == "droid:ToolCall" for l in log))
            perms = [l["text"] for l in log if l["kind"] == "permission"]
            self.assertEqual([x.split(":")[0] for x in perms], ["approved", "approved", "denied", "denied", "approved"], perms)
            self.assertEqual(app.db.one("SELECT source FROM studies WHERE id=?", (b["study_id"],))["source"], "droid")
        finally:
            os.environ.pop("FACTORY_API_KEY", None)
            os.environ["PATH"] = saved_path
            home.cleanup()
            sys.path.remove(fakes)
            for name in [m for m in sys.modules if m.startswith("droid_sdk")]:
                del sys.modules[name]


class WindowsTests(unittest.TestCase):
    def test_timezone_falls_back_when_tz_database_is_missing(self):
        import zoneinfo
        from unittest import mock
        from trialmate.tz import get_tz
        get_tz.cache_clear()
        try:
            with mock.patch.object(zoneinfo, "ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("missing")):
                tz = get_tz("America/New_York")
            self.assertIsNotNone(tz)
        finally:
            get_tz.cache_clear()

    def test_env_file_with_notepad_bom(self):
        import os
        import tempfile
        from trialmate.config import load_dotenv
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_bytes("﻿TM_TEST_KEY=abc\r\nOTHER=\"x y\"\r\n".encode("utf-8"))
        try:
            load_dotenv(path)
            self.assertEqual(os.environ.get("TM_TEST_KEY"), "abc")
            self.assertEqual(os.environ.get("OTHER"), "x y")
        finally:
            os.environ.pop("TM_TEST_KEY", None)
            os.environ.pop("OTHER", None)


class DoctorTests(unittest.TestCase):
    def test_check_reports_without_printing_keys(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest import mock
        from trialmate import doctor
        fakes = str(Path(__file__).resolve().parent / "fakes")
        sys.path.insert(0, fakes)
        os.environ["FACTORY_API_KEY"] = "fk-secret-value"
        s = Settings()
        s.llm_mode, s.photon_key, s.photon_project = "mock", "", ""
        doctor.RESULTS.clear()
        out = io.StringIO()
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/droid"), \
                    mock.patch("trialmate.ctgov.ClinicalTrials.search", return_value=[{"nct_id": "NCT01234567"}]), \
                    redirect_stdout(out):
                code = doctor.run(s)
        finally:
            os.environ.pop("FACTORY_API_KEY", None)
            sys.path.remove(fakes)
            for name in [m for m in sys.modules if m.startswith("droid_sdk")]:
                del sys.modules[name]
        text = out.getvalue()
        self.assertNotIn("fk-secret-value", text)
        self.assertIn("Droid answered", text)
        self.assertEqual(code, 0, text)


class ChalkRouterTests(unittest.TestCase):
    def test_router_request_shape(self):
        from trialmate.app import make_llm
        s = Settings()
        s.llm_mode, s.chalk_router_key, s.chalk_env_id = "chalk", "ck-test", "env-1"
        s.chalk_api_host, s.chalk_model, s.chalk_api_style = "https://api.chalk.ai", "claude-sonnet-5-5", "anthropic"
        llm = make_llm(s)
        self.assertEqual(llm.url, "https://api.chalk.ai/v1/router/v1/messages")
        self.assertEqual(llm.extra_headers["X-Chalk-Env-Id"], "env-1")
        self.assertEqual(llm.extra_headers["Authorization"], "Bearer ck-test")


class OpenAIRouterTests(unittest.TestCase):
    """The HackHarvard Chalk key speaks OpenAI chat completions; the agent must work through it unchanged."""

    def test_agent_turn_through_openai_compatible_router(self):
        import http.server
        from trialmate.app import make_llm
        seen = []

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append({"path": self.path, "env": self.headers.get("X-Chalk-Env-Id"),
                             "auth": self.headers.get("Authorization"), "body": body})
                if any(m["role"] == "tool" for m in body["messages"]):
                    msg = {"role": "assistant", "content": "Your next visit is the Week 4 visit."}
                else:
                    msg = {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "get_my_study_info", "arguments": "{}"}}]}
                out = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            s = Settings()
            s.llm_mode, s.chalk_router_key, s.chalk_env_id = "chalk", "ck-hack", "clkdcdaf84e"
            s.chalk_api_host, s.chalk_model, s.chalk_api_style = f"http://127.0.0.1:{srv.server_address[1]}", "openai/gpt-6.1-sol", "openai"
            s.photon_key = s.photon_project = ""
            app = App(s, db=Database(":memory:"), llm=make_llm(s), trials=OfflineTrials())
            r = agent.handle_inbound(app, "sim:maria", "When is my next visit?")
        finally:
            srv.shutdown()
        self.assertEqual(r["reply"], "Your next visit is the Week 4 visit.")
        self.assertEqual([t["name"] for t in r["tools"]], ["get_my_study_info"])
        first, second = seen
        self.assertEqual(first["path"], "/v1/router/chat/completions")
        self.assertEqual(first["env"], "clkdcdaf84e")
        self.assertEqual(first["auth"], "Bearer ck-hack")
        self.assertEqual(first["body"]["model"], "openai/gpt-6.1-sol")
        self.assertIn("max_tokens", first["body"])
        self.assertNotIn("temperature", first["body"])
        self.assertEqual(first["body"]["messages"][0]["role"], "system")
        self.assertTrue(any(t["function"]["name"] == "log_symptom" for t in first["body"]["tools"]))
        tool_msg = [m for m in second["body"]["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tool_msg["tool_call_id"], "call_1")


class AnthropicDirectTests(unittest.TestCase):
    """A fresh Claude key: unknown model ids are replaced by one the key can use, found via /v1/models."""

    def test_unknown_model_falls_back_to_listed_sonnet(self):
        import http.server
        from trialmate.llm import AnthropicLLM
        posts = []

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                out = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                assert self.path.startswith("/v1/models"), self.path
                self._send(200, {"data": [{"id": "claude-opus-x"}, {"id": "claude-haiku-x"}, {"id": "claude-sonnet-x"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                posts.append(body["model"])
                if body["model"] != "claude-sonnet-x":
                    return self._send(404, {"type": "error", "error": {"type": "not_found_error",
                                                                       "message": f"model: {body['model']}"}})
                self._send(200, {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"})

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            llm = AnthropicLLM("sk-test", "claude-made-up", url=f"http://127.0.0.1:{srv.server_address[1]}/v1/messages",
                               discover_models=True)
            resp = llm.create("sys", [{"role": "user", "content": "ping"}], [])
            again = llm.create("sys", [{"role": "user", "content": "ping"}], [])
        finally:
            srv.shutdown()
        self.assertEqual(resp["content"][0]["text"], "OK")
        self.assertEqual(again["content"][0]["text"], "OK")
        self.assertEqual(llm.model, "claude-sonnet-x")
        self.assertEqual(posts, ["claude-made-up", "claude-sonnet-x", "claude-sonnet-x"])

    def test_mode_never_picks_provider_without_credentials(self):
        keys = ["LLM_MODE", "ANTHROPIC_API_KEY", "CHALK_ROUTER_API_KEY", "CHALK_ENV_ID"]
        saved = {k: os.environ.get(k) for k in keys}
        try:
            def mode(**env):
                for k in keys:
                    os.environ[k] = env.get(k, "")
                return Settings().llm_mode
            self.assertEqual(mode(LLM_MODE="anthropic"), "mock")
            self.assertEqual(mode(LLM_MODE="anthropic", ANTHROPIC_API_KEY="sk"), "anthropic")
            self.assertEqual(mode(LLM_MODE="anthropic", ANTHROPIC_API_KEY="sk", CHALK_ROUTER_API_KEY="c", CHALK_ENV_ID="e"),
                             "anthropic")
            self.assertEqual(mode(CHALK_ROUTER_API_KEY="c", CHALK_ENV_ID="e", ANTHROPIC_API_KEY="sk"), "chalk")
            self.assertEqual(mode(LLM_MODE="chalk", ANTHROPIC_API_KEY="sk"), "anthropic")
            self.assertEqual(mode(), "mock")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class ChatOrderTests(unittest.TestCase):
    def test_quick_second_text_does_not_jump_above_the_first_answer(self):
        from trialmate.server import conversation
        app = make_app()
        agent.handle_inbound(app, "sim:sam", "Took my dose today, feeling 8/10")
        time.sleep(0.01)
        agent.handle_inbound(app, "sim:sam", "Thanks!")  # arrives before the worker sent the first answer
        while outbox.deliver_one(app):
            pass
        sam = app.db.one("SELECT id FROM patients WHERE handle='sim:sam'")["id"]
        directions = [m["direction"] for m in conversation(app, sam)["messages"]]
        self.assertEqual(directions, ["in", "out", "in", "out"])


class MemoryAndStatusTests(unittest.TestCase):
    def test_model_is_told_what_its_tools_already_did(self):
        app = make_app()
        agent.handle_inbound(app, "sim:maria", "I've had a headache since my shot this morning")
        maria = app.db.one("SELECT * FROM patients WHERE handle='sim:maria'")
        system = agent._system(app, maria)
        self.assertIn("log_symptom", system)
        self.assertIn("symptom=", system)
        fresh = app.db.one("SELECT * FROM patients WHERE handle='sim:sam'")
        self.assertIn("- nothing yet", agent._system(app, fresh))

    def test_status_file_written_next_to_database(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            s = Settings()
            s.llm_mode, s.photon_key, s.photon_project = "mock", "", ""
            s.db_path = Path(d) / "trialmate.db"
            app = App(s, trials=OfflineTrials())
            app.status["photon"] = {"ok": False, "error": "HTTP 403"}
            app.save_status()
            data = json.loads((Path(d) / "status.json").read_text(encoding="utf-8"))
            app.db.close() if hasattr(app.db, "close") else None
        self.assertEqual(data["llm"]["mode"], "mock")
        self.assertEqual(data["photon"]["error"], "HTTP 403")
        self.assertIn("missing", data["droid"])


class ServerTests(unittest.TestCase):
    def test_state_and_simulate(self):
        app = make_app()
        httpd = serve(app, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            req = urllib.request.Request(base + "/api/simulate", method="POST",
                                         data=json.dumps({"handle": "sim:maria", "text": "When is my next visit?"}).encode())
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(json.load(r)["status"], "queued")
            with urllib.request.urlopen(base + "/api/state", timeout=10) as r:
                state = json.load(r)
            self.assertEqual(state["stats"]["participants"], 2)
            with urllib.request.urlopen(base + "/", timeout=10) as r:
                self.assertIn(b"TrialMate", r.read())
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
