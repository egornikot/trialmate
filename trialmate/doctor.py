"""Connection checker: `py run.py --check` (or double-click check.bat).

Tests every key and outside service one by one and prints what works,
without ever printing a key. Paste the output to a teammate or to Claude.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .config import ROOT, Settings

RESULTS: list[tuple[str, str, str]] = []
LINES: list[str] = []


def _mask(value: str) -> str:
    return f"set ({len(value)} chars)" if value else "missing"


def check(name: str, fn: Callable[[], str]) -> None:
    started = time.time()
    try:
        detail = fn()
        status = "SKIP" if detail.startswith("SKIP") else "OK"
        detail = detail[5:] if status == "SKIP" else detail
    except Exception as e:  # report every failure, keep going
        status, detail = "FAIL", f"{type(e).__name__}: {str(e)[:300]}"
    RESULTS.append((status, name, detail))
    line = f"  [{status:<4}] {name:<26} {detail}  ({time.time() - started:.1f}s)"
    LINES.append(line)
    print(line, flush=True)


def run(settings: Settings, droid: bool = True) -> int:
    print("\n  TrialMate connection check (keys are never printed)\n")
    env_file = ROOT / ".env"

    check("Python version", lambda: f"{sys.version.split()[0]}" + (
        "" if sys.version_info >= (3, 10) else "  (works, but Droid needs 3.10+)"))
    check(".env file", lambda: "found" if env_file.is_file() else
          "SKIP not found: copy .env.example to .env and add your keys")

    def tz() -> str:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(settings.tz)
            return f"{settings.tz} available"
        except Exception:
            return "SKIP time zone data missing, using local time (fix: py -m pip install tzdata)"
    check("Time zones", tz)

    def keys() -> str:
        return ", ".join([
            f"Chalk router key {_mask(settings.chalk_router_key)}", f"Chalk env id {_mask(settings.chalk_env_id)}",
            f"Anthropic key {_mask(settings.anthropic_key)}", f"Photon key {_mask(settings.photon_key)}",
            f"Photon project {_mask(settings.photon_project)}", f"Factory key {_mask(os.environ.get('FACTORY_API_KEY', ''))}"])
    check("Keys in .env", keys)

    def model() -> str:
        from .app import make_llm
        if settings.llm_mode == "mock":
            return "SKIP no AI key, running the mock model (add a Chalk router key + env id, or an Anthropic key)"
        llm = make_llm(settings)
        try:
            resp = llm.create("Reply with exactly: OK", [{"role": "user", "content": "ping"}], [])
        except Exception as e:
            hint = ""
            if settings.llm_mode == "chalk":
                hint = (" | Chalk: for the HackHarvard key use CHALK_ENV_ID=clkdcdaf84e and CHALK_MODEL=openai/gpt-6.1-sol. "
                        "For your own environment, add a provider connection under Agents > Model Gateway > Routing.")
            raise RuntimeError(f"{e}{hint}")
        text = " ".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
        if not text and not any(b.get("type") == "tool_use" for b in resp.get("content", [])):
            raise RuntimeError(f"{getattr(llm, 'label', 'model')} ({llm.model}) answered with no text")
        return f"{getattr(llm, 'label', 'Claude')} ({llm.model}) answered: {text[:40]!r}"
    check("AI model", model)

    def trials() -> str:
        from .ctgov import ClinicalTrials
        found = ClinicalTrials().search("asthma", "Boston", 3)
        if not found:
            return "reachable, but no results for asthma in Boston"
        return f"{len(found)} recruiting asthma studies near Boston, e.g. {found[0]['nct_id']}"
    check("ClinicalTrials.gov", trials)

    def photon() -> str:
        if not settings.photon_enabled:
            return "SKIP not configured (PHOTON_API_KEY + PHOTON_PROJECT_ID)"
        from .channels import PhotonChannel
        ch = PhotonChannel(settings)
        query = urllib.parse.urlencode({"platform": settings.photon_platform, "direction": "received", "pageSize": "5",
                                        "order": "desc"})
        status, page = ch._request("GET", f"{ch.endpoint}?{query}")
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {json.dumps(page)[:200]} (check key permissions project:read/write and the project ID)")
        items = page.get("items", []) if isinstance(page, dict) else []
        texts = [i for i in items if ch.parse(i)]
        return (f"connected to {settings.photon_platform}; {len(items)} recent received messages "
                f"({len(texts)} readable texts). Text your line, then run this again to see it counted.")
    check("Photon", photon)

    def droid_setup() -> str:
        parts = []
        cli = shutil.which("droid")
        parts.append("droid CLI found" if cli else "droid CLI missing (PowerShell: irm https://app.factory.ai/cli/windows | iex)")
        try:
            import droid_sdk  # noqa: F401
            parts.append("droid-sdk installed")
        except Exception:
            parts.append('droid-sdk missing (py -m pip install "droid-sdk[mcp]")')
        try:
            import droid_sdk.mcp  # noqa: F401
            parts.append("mcp extra installed")
        except Exception:
            parts.append('mcp extra missing (py -m pip install "droid-sdk[mcp]")')
        parts.append("FACTORY_API_KEY set" if os.environ.get("FACTORY_API_KEY") else "FACTORY_API_KEY missing")
        ok = cli and all("missing" not in p for p in parts)
        if not ok:
            raise RuntimeError("; ".join(parts))
        return "; ".join(parts)
    check("Droid setup", droid_setup)

    def droid_live() -> str:
        if not droid:
            return "SKIP skipped (--no-droid)"
        if any(r[1] == "Droid setup" and r[0] != "OK" for r in RESULTS):
            return "SKIP fix Droid setup first"
        import asyncio
        from droid_sdk import Autonomy, SessionConfig, run as droid_run  # type: ignore

        async def go() -> Any:
            with tempfile.TemporaryDirectory() as tmp:
                return await droid_run("Reply with exactly the word OK and nothing else. Do not use any tools.",
                                       cwd=tmp, config=SessionConfig(autonomy=Autonomy.LOW))
        result = asyncio.run(go())
        if getattr(result, "success", True) is False:
            raise RuntimeError(f"Droid run failed: {getattr(result, 'subtype', '')}")
        return f"Droid answered: {str(getattr(result, 'text', ''))[:40]!r}"
    check("Droid live run", droid_live)

    def database() -> str:
        from .db import Database
        Database(settings.db_path).one("SELECT 1 AS ok")
        return f"writable at {settings.db_path}"
    check("Database", database)

    def port() -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", settings.port)) == 0:
                return f"SKIP port {settings.port} is in use (TrialMate may already be running, or use --port 8001)"
        return f"{settings.port} is free"
    check("Dashboard port", port)

    fails = [r for r in RESULTS if r[0] == "FAIL"]
    skips = [r for r in RESULTS if r[0] == "SKIP"]
    summary = f"  {len(RESULTS) - len(fails) - len(skips)} OK, {len(skips)} skipped, {len(fails)} failed."
    print("\n" + summary)
    if fails:
        print("  Fix the FAIL lines first. Copy this whole output if you need help.")
    print()
    try:  # saved so a teammate (or Claude, with folder access) can read it; contains no keys
        (ROOT / "check-result.txt").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "\n".join(LINES) + "\n" + summary + "\n",
                                               encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0
