"""Model access: the Anthropic Messages API over plain HTTPS, plus an offline mock.

No SDK needed. The mock follows simple keyword rules so the whole pipeline
(tools, approvals, dashboard, evals) can run without an API key.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from .net import USER_AGENT, ssl_context
from .tz import get_tz


class LLMError(Exception):
    pass


def _post_json(url: str, body: dict, headers: dict, timeout: float, label: str) -> dict:
    """POST with retries on rate limits, server errors and network hiccups."""
    data = json.dumps(body).encode("utf-8")
    last_error = ""
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method="POST", headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read()[:600].decode("utf-8", "replace")
            last_error = f"{label} {e.code}: {detail}"
            if e.code in (408, 429, 500, 502, 503, 504, 529) and attempt < 3:
                time.sleep(1.5 * 2 ** attempt)
                continue
            raise LLMError(last_error)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = f"{label} unreachable: {e}"
            if attempt < 3:
                time.sleep(1.5 * 2 ** attempt)
                continue
    raise LLMError(last_error)


class OpenAIChatLLM:
    """Any OpenAI-compatible chat endpoint, e.g. Chalk's AI Router with the HackHarvard key.

    The agent speaks Anthropic-style messages and tool blocks internally; this adapter
    translates them to OpenAI function calling and back, so nothing else changes.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, extra_headers: Optional[dict] = None,
                 max_tokens: int = 1024, timeout: float = 90.0, label: str = "") -> None:
        if not api_key:
            raise LLMError("API key is not set")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.extra_headers = extra_headers or {}
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.label = label or "OpenAI-compatible model"

    @staticmethod
    def to_openai(system: str, messages: list[dict], tools: list[dict]) -> tuple[list[dict], list[dict]]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                out.append({"role": m["role"], "content": content})
                continue
            if m["role"] == "assistant":
                text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                         for b in content if b.get("type") == "tool_use"]
                msg: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                texts = []
                for b in content:
                    if b.get("type") == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content", ""))})
                    elif b.get("type") == "text":
                        texts.append(b.get("text", ""))
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        fns = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
               for t in tools]
        return out, fns

    @staticmethod
    def from_openai(resp: dict) -> dict:
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        blocks: list[dict] = []
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            blocks.append({"type": "tool_use", "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                           "name": fn.get("name", ""), "input": args})
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return {"content": blocks, "stop_reason": stop, "usage": resp.get("usage")}

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        msgs, fns = self.to_openai(system, messages, tools)
        body: dict[str, Any] = {"model": self.model, "messages": msgs, "max_tokens": self.max_tokens}
        if fns:
            body["tools"] = fns
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.extra_headers}
        return self.from_openai(_post_json(self.url, body, headers, self.timeout, self.label))


class AnthropicLLM:
    """Claude via the Messages API, either directly or through Chalk's AI Router (same request format)."""

    URL = "https://api.anthropic.com/v1/messages"
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int = 1024, timeout: float = 90.0,
                 url: str = "", extra_headers: Optional[dict] = None, label: str = "",
                 model_fallbacks: Optional[list] = None) -> None:
        if not api_key:
            raise LLMError("API key is not set")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.url = url or self.URL
        self.extra_headers = extra_headers or {}
        self.label = label or "Claude"
        # Alternative spellings of the model id to try once if the gateway rejects the first one
        # (e.g. Chalk's router may want "anthropic/claude-..." instead of "claude-...").
        self.model_fallbacks = [m for m in (model_fallbacks or []) if m and m != model]

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        try:
            return self._create(self.model, system, messages, tools)
        except LLMError as first:
            text = str(first).lower()
            if not self.model_fallbacks or not any(c in text for c in (" 400:", " 404:", " 422:")) or "model" not in text:
                raise
            for alt in list(self.model_fallbacks):
                try:
                    result = self._create(alt, system, messages, tools)
                except LLMError:
                    continue
                self.model, self.model_fallbacks = alt, []  # remember the spelling that works
                return result
            raise first

    def _create(self, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:
        body: dict[str, Any] = {"model": model, "max_tokens": self.max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        data = json.dumps(body).encode("utf-8")
        last_error = ""
        for attempt in range(4):
            req = urllib.request.Request(self.url, data=data, method="POST", headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "user-agent": USER_AGENT,
                **self.extra_headers,
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=ssl_context()) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                detail = e.read()[:600].decode("utf-8", "replace")
                last_error = f"Anthropic API {e.code}: {detail}"
                if e.code in (408, 429, 500, 502, 503, 504, 529) and attempt < 3:
                    time.sleep(1.5 * 2 ** attempt)
                    continue
                raise LLMError(last_error)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = f"Anthropic API unreachable: {e}"
                if attempt < 3:
                    time.sleep(1.5 * 2 ** attempt)
                    continue
        raise LLMError(last_error)


# --------------------------------------------------------------------------- mock

SYMPTOMS = ["headache", "nausea", "nauseous", "vomit", "threw up", "throwing up", "rash", "itch", "dizzy", "dizziness",
            "fever", "chills", "tired", "fatigue", "exhausted", "cough", "sore throat", "runny nose", "diarrhea",
            "swelling", "swollen", "redness", "pain", "ache", "hurts", "wheez", "short of breath", "blood sugar",
            "palpitation", "insomnia", "can't sleep", "cramp", "bruise", "faint", "chest"]
SEVERE = ["severe", "unbearable", "worst", "emergency room", " er ", "hospital", "couldn't stop", "can't stop",
          "stay in bed", "in bed", "ambulance", "urgent care"]
MODERATE = ["really", "very", "a lot", "spreading", "keeping me up", "twice", "all day", "every time", "250", "bad"]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


class MockLLM:
    """Deterministic keyword agent for offline demos and pipeline tests. Not a substitute for Claude."""

    name = "mock"
    model = "mock-rules"

    def __init__(self, tz: str = "America/New_York") -> None:
        self.tz = get_tz(tz)
        self.calls: list[dict] = []

    def _tool(self, name: str, args: dict) -> dict:
        return {"content": [{"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:12]}", "name": name, "input": args}],
                "stop_reason": "tool_use"}

    @staticmethod
    def _say(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages))})
        names = {t["name"] for t in tools}
        last = messages[-1]
        if isinstance(last["content"], list) and any(b.get("type") == "tool_result" for b in last["content"]):
            return self._after_tool(messages)
        latest = _text_of(last["content"]).strip().split("\n")[-1]
        text = " " + latest.lower() + " "
        decision = self._decide(text, names)
        return decision if decision else self._say(
            "Thanks for the message! I'm here for anything about the study: visits, reminders, side effects or questions.")

    def _decide(self, text: str, names: set) -> Optional[dict]:
        if "log_symptom" in names:
            hit = next((s for s in SYMPTOMS if s in text), None)
            if hit and not re.search(r"\b(is gone|went away|feel(ing)? (fine|good|great))\b", text):
                grade = 3 if any(s in text for s in SEVERE) else 2 if any(s in text for s in MODERATE) else 1
                serious = any(s in text for s in ("emergency room", " er ", "hospital", "ambulance"))
                return self._tool("log_symptom", {"symptom": hit.strip(), "description": text.strip()[:200],
                                                  "grade": grade, "serious": serious, "onset": "reported today",
                                                  "ongoing": True})
        if "escalate_to_coordinator" in names and re.search(
                r"(real person|human|talk to (someone|somebody|a person)|quit|withdraw|leave the study|drop out|call me)", text):
            return self._tool("escalate_to_coordinator", {"reason": text.strip()[:200], "urgency": "routine"})
        if "schedule_reminder" in names and "remind" in text:
            return self._tool("schedule_reminder", {"text": "Time to take your study medication.",
                                                    "when": self._parse_time(text)})
        if "record_checkin" in names and re.search(r"\b(took|taken|forgot|missed|skipped)\b", text):
            took = not re.search(r"\b(forgot|missed|skipped)\b", text)
            m = re.search(r"\b(\d{1,2})\s*/\s*10\b", text)
            return self._tool("record_checkin", {"took_dose": took, "wellbeing": int(m.group(1)) if m else None,
                                                 "notes": text.strip()[:200]})
        nct = re.search(r"\bnct\d{8}\b", text)
        if nct and "get_trial_details" in names:
            return self._tool("get_trial_details", {"nct_id": nct.group(0).upper()})
        if "search_trials" in names and re.search(r"\b(trials?|stud(y|ies)|research)\b", text) and not text.strip().startswith("what is"):
            loc = re.search(r"\b(?:in|near|around)\s+([a-z .'-]+?)[?.!]*\s*$", text)
            cond = re.search(r"(?:for|with)\s+([a-z0-9' -]+?)(?:\s+(?:in|near|around)\b|[?.!]|\s*$)", text) or \
                re.search(r"any\s+([a-z0-9' -]+?)\s+(?:trials?|stud)", text)
            return self._tool("search_trials", {"condition": (cond.group(1) if cond else "asthma").strip(),
                                                "location": loc.group(1).strip() if loc else ""})
        if "get_my_study_info" in names and re.search(r"\b(visit|appointment|schedule|when is|next)\b", text):
            return self._tool("get_my_study_info", {})
        if "search_protocol" in names and "?" in text:
            return self._tool("search_protocol", {"question": text.strip()})
        return None

    def _parse_time(self, text: str) -> str:
        now = datetime.now(self.tz)
        m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if not m:
            return (now + timedelta(hours=1)).replace(second=0, microsecond=0).isoformat(timespec="minutes")
        hour, minute = int(m.group(1)) % 24, int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when.isoformat(timespec="minutes")

    def _after_tool(self, messages: list[dict]) -> dict:
        prev = messages[-2]["content"]
        use = next((b for b in prev if isinstance(b, dict) and b.get("type") == "tool_use"), {})
        result_block = next(b for b in messages[-1]["content"] if b.get("type") == "tool_result")
        try:
            result = json.loads(result_block.get("content") or "{}")
        except ValueError:
            result = {}
        name = use.get("name")
        if result.get("error"):
            return self._say("Sorry, I couldn't do that just now. I'll ask the study team to follow up.")
        if name == "log_symptom":
            return self._say("I'm sorry you're dealing with that. I've logged it for your study team and they'll "
                             "follow up soon. If it gets worse, call the clinic, or 911 in an emergency.")
        if name == "escalate_to_coordinator":
            return self._say("I've asked your study coordinator to reach out to you directly.")
        if name == "schedule_reminder":
            return self._say(f"Done! I'll text you a reminder {result.get('when_local', 'then')}.")
        if name == "record_checkin":
            return self._say("Thanks for checking in! I've logged it for the study team.")
        if name == "search_trials":
            trials = result.get("trials", [])
            if not trials:
                return self._say("I couldn't find recruiting studies for that. Want me to try a nearby city?")
            lines = [f"{t['nct_id']}: {t['title'][:90]}" for t in trials[:3]]
            return self._say("Here are recruiting studies that may be a match:\n" + "\n".join(lines) +
                             "\nWant me to check the main requirements for one of them?")
        if name == "get_trial_details":
            return self._say(f"{result.get('title', 'That study')} is {result.get('status', '').lower()}. "
                             "I can walk you through the main requirements. Are you 18 or older?")
        if name == "get_my_study_info":
            visits = result.get("upcoming_visits", [])
            if visits:
                return self._say(f"Your next visit is the {visits[0]['title']} on {visits[0]['when_local']}.")
            return self._say("You don't have any upcoming visits scheduled. I'll ask the team.")
        if name == "search_protocol":
            passages = result.get("passages", [])
            if passages:
                return self._say(passages[0][:300])
            return self._say("I'm not sure about that one, so I'll ask the study team.")
        return self._say("Done.")
