"""Wiring: database, model, trial data, channels and background workers."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import agent, outbox, seed
from .channels import LocalChannel, PhotonChannel, PhotonPoller
from .config import Settings
from .ctgov import ClinicalTrials
from .db import Database, now
from .llm import AnthropicLLM, MockLLM, OpenAIChatLLM
from .study_builder import StudyBuilder


def make_llm(settings: Settings) -> Any:
    if settings.llm_mode == "chalk" and settings.chalk_api_style == "openai":
        return OpenAIChatLLM(
            settings.chalk_router_key, settings.chalk_model, base_url=f"{settings.chalk_api_host}/v1/router",
            extra_headers={"X-Chalk-Env-Id": settings.chalk_env_id}, label="Chalk AI Router")
    if settings.llm_mode == "chalk":
        return AnthropicLLM(
            settings.chalk_router_key, settings.chalk_model,
            url=f"{settings.chalk_api_host}/v1/router/v1/messages",
            extra_headers={"Authorization": f"Bearer {settings.chalk_router_key}", "X-Chalk-Env-Id": settings.chalk_env_id},
            label="Claude via Chalk AI Router",
            model_fallbacks=[settings.chalk_model.split("/", 1)[1] if "/" in settings.chalk_model
                             else f"anthropic/{settings.chalk_model}"])
    if settings.llm_mode == "anthropic":
        return AnthropicLLM(settings.anthropic_key, settings.model, discover_models=True)
    return MockLLM(settings.tz)


class App:
    def __init__(self, settings: Settings, db: Optional[Database] = None, llm: Any = None, trials: Any = None) -> None:
        self.settings = settings
        self.db = db or Database(settings.db_path)
        seed.ensure(self.db, settings.tz)
        self.llm = llm or make_llm(settings)
        self.trials = trials or ClinicalTrials()
        self.channels: dict[str, Any] = {"sim": LocalChannel()}
        if settings.photon_enabled:
            self.channels["photon"] = PhotonChannel(settings)
        self.status: dict[str, Any] = {}
        self._locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
        self.builder = StudyBuilder(self)
        self.db.run("UPDATE builds SET status='failed', error='interrupted by restart' WHERE status='running'")

    def builder_engine(self) -> str:
        from .study_builder import droid_available
        return "droid" if droid_available() else "fallback"

    def droid_missing(self) -> list[str]:
        from .study_builder import droid_missing
        return droid_missing()

    def save_status(self) -> None:
        """Write data/status.json so problems can be diagnosed without opening the dashboard."""
        if str(self.settings.db_path) == ":memory:":
            return
        try:
            path = Path(self.settings.db_path).parent / "status.json"
            snapshot = {
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "llm": {"mode": self.settings.llm_mode, "provider": getattr(self.llm, "name", ""),
                        "model": getattr(self.llm, "model", ""), **self.status.get("llm", {})},
                "photon": {"enabled": "photon" in self.channels, **self.status.get("photon", {}),
                           "probe": self.status.get("photon_probe")},
                "droid": {"engine": self.builder_engine(), "missing": self.droid_missing()},
                "outbox_failed": [dict(r) for r in self.db.all(
                    "SELECT id, kind, attempts, last_error FROM outbox WHERE status='failed' ORDER BY id DESC LIMIT 5")],
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            pass

    def patient_lock(self, patient_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks[patient_id]

    # ------------------------------------------------------------------ inbound from channels
    def on_channel_message(self, item: dict) -> None:
        mid = agent.ingest(self, item["handle"], item["text"], channel="photon", external_id=item.get("external_id"),
                           space_id=item.get("space_id"), agent_id=item.get("agent_id"))
        if mid is not None:
            self.pool.submit(self._process_safe, mid)

    def _process_safe(self, mid: int) -> None:
        try:
            agent.process(self, mid)
        except Exception as e:
            self.db.audit(None, "agent:crash", f"message {mid}: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ workers
    def start_workers(self) -> None:
        recovered = outbox.recover(self.db)
        if recovered:
            self.db.audit(None, "outbox:recovered", str(recovered))
        for row in self.db.all("SELECT id FROM messages WHERE direction='in' AND processed=0 ORDER BY id"):
            self.pool.submit(self._process_safe, row["id"])  # finish work interrupted by a restart
        self._spawn("outbox", self._outbox_loop)
        self._spawn("scheduler", self._scheduler_loop)
        self.save_status()
        if "photon" in self.channels:
            poller = PhotonPoller(self, self.on_channel_message)
            poller.start()
            self._threads.append(poller)

    def _spawn(self, name: str, target: Any) -> None:
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _outbox_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not outbox.deliver_one(self):
                    self._stop.wait(0.5)
            except Exception as e:
                self.db.audit(None, "outbox:crash", f"{type(e).__name__}: {e}")
                self._stop.wait(2)

    def scheduler_tick(self) -> int:
        queued = 0
        for r in self.db.all("SELECT r.*, p.opted_out FROM reminders r JOIN patients p ON p.id=r.patient_id"
                             " WHERE r.status='scheduled' AND r.due_at<=?", (now(),)):
            if r["opted_out"]:
                self.db.run("UPDATE reminders SET status='cancelled' WHERE id=?", (r["id"],))
                continue
            outbox.enqueue(self.db, r["patient_id"], r["body"], "queued", f"reminder:{r['id']}", kind="reminder")
            self.db.run("UPDATE reminders SET status='sent' WHERE id=?", (r["id"],))
            queued += 1
        return queued

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scheduler_tick()
            except Exception as e:
                self.db.audit(None, "scheduler:crash", f"{type(e).__name__}: {e}")
            self.save_status()
            self._stop.wait(10)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            if isinstance(t, PhotonPoller):
                t.stop_event.set()
        self.pool.shutdown(wait=False)
        time.sleep(0.1)
