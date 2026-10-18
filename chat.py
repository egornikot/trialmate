#!/usr/bin/env python3
"""Text the agent from your terminal, no Photon or browser needed.

    py chat.py                 # as Maria, a demo participant      (Mac: python3 chat.py)
    py chat.py --as sam        # as Sam, another participant
    py chat.py --as visitor    # as a new person looking for trials
    py chat.py --mock          # without an API key
"""
from __future__ import annotations

import argparse
import sys

for stream in (sys.stdout, sys.stderr):  # Windows consoles can't print every character; never crash on one
    try:
        stream.reconfigure(errors="replace")
    except Exception:
        pass

from trialmate import agent, outbox
from trialmate.app import App
from trialmate.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as", dest="who", default="maria", help="sim handle: maria, sam, or any new name")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    if args.mock:
        settings.llm_mode = "mock"
    app = App(settings)
    handle = "sim:" + args.who.replace("sim:", "")
    print(f"Texting TrialMate as {handle} ({getattr(app.llm, 'label', 'model') + ' ' + app.llm.model if app.llm.name != 'mock' else 'mock model'}). "
          "Ctrl+C to quit.\n")
    while True:
        try:
            text = input("you  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        result = agent.handle_inbound(app, handle, text, channel="sim")
        for call in result.get("tools", []):
            print(f"       [tool] {call['name']} {call['input']}")
        if result.get("emergency"):
            print(f"       [safety] emergency detected: {result['emergency']}")
        if result.get("redactions"):
            print(f"       [guard] removed before AI: {result['redactions']}")
        pending = app.db.all("SELECT body FROM outbox WHERE patient_id=? AND status='queued' ORDER BY id", (result.get("patient_id", 0),))
        outbox.flush(app)
        for row in pending:
            print(f"agent> {row['body']}")
        if result.get("status") == "needs_approval":
            print(f"       [held for coordinator: {', '.join(result.get('reasons', []))}]\n       draft: {result.get('reply')}")
        print()


if __name__ == "__main__":
    main()
