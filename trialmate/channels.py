"""Messaging channels.

LocalChannel: the dashboard phone simulator and terminal chat (no network).
PhotonChannel: Photon's HTTP API (api.photon.codes/v1) for iMessage, SMS,
email and more. It polls for received messages and replies with an
Idempotency-Key so retries are safe.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .net import USER_AGENT, ssl_context
from .outbox import ChannelError, PermanentChannelError


class LocalChannel:
    name = "sim"

    def send(self, patient: dict, body: str, idempotency_key: str, parent_external_id: Optional[str]) -> Optional[str]:
        return None  # stored in messages by the outbox; the simulator reads it from there


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class PhotonChannel:
    name = "photon"

    def __init__(self, settings: Any) -> None:
        self.key = settings.photon_key
        self.project = settings.photon_project
        self.platform = settings.photon_platform
        self.origin = settings.photon_origin
        self.thread_replies = settings.photon_thread_replies
        self.endpoint = f"{self.origin}/v1/projects/{urllib.parse.quote(self.project, safe='')}/messages"

    def _request(self, method: str, url: str, body: Optional[dict] = None, idempotency_key: str = "") -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20, context=ssl_context()) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()[:800].decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    # ---------------------------------------------------------------- send
    def send(self, patient: dict, body: str, idempotency_key: str, parent_external_id: Optional[str]) -> Optional[str]:
        if not patient.get("space_id") or not patient.get("agent_id"):
            raise PermanentChannelError("this person hasn't texted the Photon line yet, so there is no conversation to reply in")
        payload: dict[str, Any] = {"spaceId": patient["space_id"], "senderId": patient["agent_id"],
                                   "content": {"type": "text", "format": "plain", "text": body[:3000]}}
        if self.thread_replies and parent_external_id:
            payload["parentMessageId"] = parent_external_id
        try:
            status, data = self._request("POST", self.endpoint, payload, idempotency_key)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ChannelError(f"photon unreachable: {e}")
        if status in (200, 201, 202):
            return (data or {}).get("id") if isinstance(data, dict) else None
        if status in (400, 401, 403, 404, 422):
            raise PermanentChannelError(f"photon {status}: {json.dumps(data)[:300]}")
        raise ChannelError(f"photon {status}: {json.dumps(data)[:300]}")

    # ---------------------------------------------------------------- receive
    def poll(self, since_iso: str) -> tuple[list[dict], str]:
        """Return (text messages from humans to the agent, newest createdAt seen)."""
        query = {"createdSince": since_iso, "platform": self.platform, "direction": "received",
                 "order": "asc", "pageSize": "100"}
        out, newest, token = [], since_iso, None
        for _ in range(10):
            if token:
                query["pageToken"] = token
            status, page = self._request("GET", f"{self.endpoint}?{urllib.parse.urlencode(query)}")
            if status != 200:
                raise ChannelError(f"photon list {status}: {json.dumps(page)[:300]}")
            for m in page.get("items", []):
                newest = max(newest, m.get("createdAt") or newest)
                parsed = self.parse(m)
                if parsed:
                    out.append(parsed)
            token = page.get("nextPageToken")
            if not token:
                break
        return out, newest

    def probe(self, limit: int = 5) -> dict:
        """Latest messages in the project with no filters, summarized for diagnosis (no text, masked handles)."""
        status, page = self._request("GET", f"{self.endpoint}?{urllib.parse.urlencode({'order': 'desc', 'pageSize': str(limit)})}")
        if status != 200:
            return {"http": status, "error": json.dumps(page)[:300]}

        def mask(handle: Any) -> str:
            h = str(handle or "")
            if "@" in h:
                name, _, domain = h.partition("@")
                return f"{name[:1]}…@{domain}"
            return f"…{h[-4:]}" if len(h) > 4 else h

        out = []
        for m in (page.get("items") or [])[:limit]:
            sb, rb, sp, ct = (m.get("sentBy") or {}), (m.get("receivedBy") or {}), (m.get("space") or {}), (m.get("content") or {})
            out.append({"createdAt": m.get("createdAt"), "platform": m.get("platform"), "status": m.get("status"),
                        "from": f"{sb.get('kind')} {mask(sb.get('handle'))}", "to": f"{rb.get('kind')} {mask(rb.get('handle'))}",
                        "space": sp.get("kind"), "content": ct.get("type"), "would_accept": bool(self.parse(m))})
        return {"http": 200, "latest": out, "checked_at": _iso(datetime.now(timezone.utc))}

    def parse(self, m: dict) -> Optional[dict]:
        sender, receiver = m.get("sentBy") or {}, m.get("receivedBy") or {}
        space, content = m.get("space") or {}, m.get("content") or {}
        if sender.get("kind") != "human" or receiver.get("kind") != "agent" or sender.get("id") == receiver.get("id"):
            return None
        if self.platform == "imessage" and space.get("kind") not in (None, "direct"):
            return None  # ignore group chats
        text = content.get("text") if content.get("type") == "text" else None
        if not text and isinstance(content.get("parts"), list):
            text = " ".join(p.get("text", "") for p in content["parts"] if isinstance(p, dict)).strip()
        if not text or not sender.get("handle") or not space.get("id"):
            return None
        return {"external_id": m.get("id"), "handle": sender["handle"], "text": text, "space_id": space["id"],
                "agent_id": receiver.get("id")}


class PhotonPoller(threading.Thread):
    """Background loop: fetch new Photon messages, store them, hand them to the agent."""

    def __init__(self, app: Any, on_message: Callable[[dict], None]) -> None:
        super().__init__(name="photon-poller", daemon=True)
        self.app = app
        self.channel: PhotonChannel = app.channels["photon"]
        self.on_message = on_message
        self.stop_event = threading.Event()

    def run(self) -> None:
        db = self.app.db
        cursor = db.get_kv("photon_cursor") or _iso(datetime.now(timezone.utc) - timedelta(seconds=120))
        interval = self.app.settings.photon_poll_seconds
        last_state = None  # log only when the connection state changes, not on every poll
        polls = 0
        while not self.stop_event.is_set():
            if polls % 15 == 0 and not self.app.status.get("photon", {}).get("received_total"):
                try:  # diagnosis while nothing has come in yet: what does Photon hold for this project?
                    self.app.status["photon_probe"] = self.channel.probe()
                except Exception as e:
                    self.app.status["photon_probe"] = {"error": str(e)[:300]}
            polls += 1
            try:
                items, newest = self.channel.poll(cursor)
                for item in items:
                    self.on_message(item)
                if newest != cursor:
                    cursor = newest
                    db.set_kv("photon_cursor", cursor)
                self.app.status["photon"] = {"ok": True, "last_poll": time.time(), "cursor": cursor,
                                             "received_total": self.app.status.get("photon", {}).get("received_total", 0)
                                             + len(items)}
                if last_state != "ok":
                    db.audit(None, "photon:ok", f"polling {self.channel.platform} messages")
                    last_state = "ok"
            except Exception as e:
                error = str(e)[:300]
                self.app.status["photon"] = {"ok": False, "last_poll": time.time(), "error": error}
                if last_state != error:
                    db.audit(None, "photon:error", error)
                    last_state = error
            self.stop_event.wait(interval)
