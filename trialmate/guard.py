"""Redact identifiers and credentials before text reaches the model or the database.

Design carried over from the fail-closed guard in the earlier Hermes
personal-Telegram connector: an LLM never needs a Social Security number,
a card number or a password. Health details are kept on purpose, because
reporting them is the reason the agent exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
SSN_LABELED = re.compile(r"(?i)\b(?:ssn|social\s+security(?:\s+(?:number|no\.?|#))?)\b\W{0,6}(\d[\d -]{7,10}\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
CREDENTIAL = re.compile(
    r"(?i)\b(password|passcode|passwd|pwd|pin|login code|verification code|security code|one[- ]time code|cvv|cvc)"
    r"\b(\s*(?:is|was|=|:)\s*|\s+)([^\s,;]{3,})"
)
API_KEY = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|pho_[A-Za-z0-9_]{20,})\b"
)
OPAQUE = re.compile(r"(?<![\w/+=-])(?=[A-Za-z0-9_+/=-]*\d)(?=[A-Za-z0-9_+/=-]*[A-Za-z])[A-Za-z0-9_+/=-]{32,}(?![\w/+=-])")
BANK = re.compile(r"(?i)\b(routing|account|acct)\s*(?:number|no\.?|#)?\s*(?:is|:)?\s*(\d{6,17})\b")
MRN = re.compile(r"(?i)\b(mrn|medical\s+record(?:\s+(?:number|no\.?|#))?)\b\W{0,6}([A-Z0-9-]{5,})")


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class Redaction:
    text: str
    kinds: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.kinds)


def redact(text: str) -> Redaction:
    if not isinstance(text, str):
        return Redaction("", ["unreadable"])
    kinds: list[str] = []
    value = text[:8000]

    def mark(kind: str) -> str:
        if kind not in kinds:
            kinds.append(kind)
        return f"[{kind.upper()} REMOVED]"

    value = API_KEY.sub(lambda m: mark("api_key"), value)
    value = SSN_LABELED.sub(lambda m: m.group(0).replace(m.group(1), mark("ssn")), value)
    value = SSN.sub(lambda m: mark("ssn"), value)

    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return mark("card_number")
        return m.group(0)

    value = CARD.sub(card, value)
    value = BANK.sub(lambda m: f"{m.group(1)} number {mark('bank_account')}", value)
    value = MRN.sub(lambda m: f"{m.group(1)} {mark('record_number')}", value)
    value = CREDENTIAL.sub(lambda m: f"{m.group(1)}{m.group(2)}{mark('credential')}", value)
    value = OPAQUE.sub(lambda m: mark("token"), value)
    return Redaction(value, kinds)
