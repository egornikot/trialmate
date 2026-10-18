"""Deterministic safety rules that run outside the model.

1. Emergency red flags in an incoming text trigger an immediate fixed reply
   (911 / 988) and an emergency escalation, without waiting for the LLM.
2. Outgoing drafts that look like dosing advice, a diagnosis or unblinding
   are held for a coordinator, whatever the model decided.
"""
from __future__ import annotations

import re
from typing import Optional

NEGATION = re.compile(r"(?i)\b(no|not|never|without|denies|deny|don'?t have|didn'?t have|isn'?t|wasn'?t)\b[\w\s,']{0,18}$")

EMERGENCY = [
    ("self_harm", re.compile(
        r"(?i)\b(kill(?:ing)? myself|suicid\w*|end(?:ing)? (?:my|it all)(?: life)?|want(?:ed)? to die|"
        r"(?:don'?t|do not) want to (?:live|be alive|be here)|hurt(?:ing)? myself|self[- ]harm\w*|"
        r"better off dead|no reason to live)")),
    ("cardiac", re.compile(
        r"(?i)\b(chest (?:pain|pressure|tightness|hurts?|feels? (?:tight|heavy))|heart attack|pain in my chest|"
        r"my chest (?:hurts|is tight|feels tight|feels heavy))")),
    ("breathing", re.compile(
        r"(?i)(\b(?:can'?t|cannot|can not|hard to|trouble|struggling to|difficulty|unable to) (?:breath\w*|catch my breath)|"
        r"\bshort(?:ness)? of breath|\bgasping\b|\bchoking\b)")),
    ("allergic", re.compile(
        r"(?i)\b((?:throat|tongue|lips?|face|mouth) (?:is |are |feels? |keeps? )?(?:swell\w*|swollen|closing|tight)|"
        r"anaphyla\w*|hives all over|epi ?pen)")),
    ("neuro", re.compile(
        r"(?i)\b(seizures?|convuls\w*|passed out|faint(?:ed|ing)?|unconscious|blacked out|slurred speech|"
        r"(?:face|mouth) (?:is )?droop\w*|numb(?:ness)? on one side|can'?t move my (?:arm|leg|face)|stroke)\b")),
    ("bleeding", re.compile(
        r"(?i)\b(bleeding (?:heavily|a lot|badly|won'?t stop|that won'?t stop)|(?:vomit\w*|throwing up|threw up|thrown up|coughing up|coughed up) blood|"
        r"blood in (?:my )?(?:stool|vomit|urine|pee|poop))")),
]

EMERGENCY_REPLY = (
    "This could be a medical emergency. If you have chest pain, trouble breathing, swelling of your face or "
    "throat, or feel like you might pass out, call 911 now or go to the nearest emergency room."
)
SELF_HARM_REPLY = (
    "I'm really sorry you're going through this, and I'm glad you told me. Please call or text 988 to reach "
    "the Suicide & Crisis Lifeline right now. If you're in immediate danger, call 911."
)
TEAM_ALERTED = " I've alerted your study team so a person can reach you."


def detect_emergency(text: str) -> Optional[str]:
    for category, pattern in EMERGENCY:
        for m in pattern.finditer(text or ""):
            if NEGATION.search(text[max(0, m.start() - 30):m.start()]):
                continue
            return category
    return None


def study_red_flag(text: str, red_flags: list) -> Optional[dict]:
    """Study-specific warning signs (written by the Study Builder Droid, validated by TrialMate)."""
    for rf in red_flags or []:
        try:
            m = re.search(rf["pattern"], text or "", re.I)
        except (re.error, KeyError, TypeError):
            continue
        if m and not NEGATION.search(text[max(0, m.start() - 30):m.start()]):
            return rf
    return None


def emergency_reply(category: str, has_team: bool) -> str:
    base = SELF_HARM_REPLY if category == "self_harm" else EMERGENCY_REPLY
    return base + (TEAM_ALERTED if has_team else "")


REVIEW = [
    ("dosing advice", re.compile(
        r"(?i)\b(stop|start|skip|double|increase|decrease|reduce|lower|raise|change|hold|pause|resume|take (?:an? )?(?:extra|another|two|2))"
        r"\w*\b[^.?!]{0,40}\b(doses?|dosing|medications?|medicines?|meds|pills?|tablets?|injections?|inhalers?|drugs?|shots?)\b")),
    ("dosage amount", re.compile(r"(?i)\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|ml|units|iu)\b")),
    ("diagnosis", re.compile(
        r"(?i)\b(you (?:probably |likely |may |might )?have|it'?s (?:probably|likely)|sounds like (?:you have|a|an))\b"
        r"[^.?!]{0,40}\b(infection|syndrome|disease|disorder|reaction|condition|pneumonia|flu|covid|allerg\w+|asthma attack)\b")),
    ("unblinding", re.compile(
        r"(?i)\b(you(?:'re| are) (?:on|in|getting|receiving) (?:the )?(?:placebo|active|real|treatment|study drug arm)|"
        r"your (?:arm|group) is|assigned to (?:the )?(?:placebo|active|treatment))")),
]


def outbound_review_reason(text: str) -> Optional[str]:
    for reason, pattern in REVIEW:
        if pattern.search(text or ""):
            return reason
    return None


OPT_OUT = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
OPT_IN = {"start", "unstop", "subscribe"}
HELP = {"help", "info"}


def keyword(text: str) -> Optional[str]:
    word = re.sub(r"[^a-z]", "", (text or "").strip().lower())
    if word in OPT_OUT:
        return "stop"
    if word in OPT_IN:
        return "start"
    if word in HELP:
        return "help"
    return None
