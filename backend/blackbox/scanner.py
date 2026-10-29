"""Rule-based scan of untrusted text for common prompt-injection phrasing.

This is a tripwire, not a defence: paraphrases, other languages and encoded text get through.
Blackbox's real protection is limiting what a hijacked agent may do (policy + approvals).
"""
import re

LEVELS = {"none": 0, "low": 1, "medium": 2, "high": 3}

RULES = [
    ("high", "tries to override the agent's instructions",
     r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,30}\b(previous|prior|above|earlier|all|your|any|the|system|original)\b"
     r".{0,20}\b(instructions?|rules|prompts?|guidelines|directions|polic(?:y|ies)|guardrails)\b"),
    ("high", "tries to give the agent a new role",
     r"\byou are (now|no longer)\b|\bfrom now on,? you\b|\b(act|behave) as (an? )?(unrestricted|unfiltered|different|new)\b"
     r"|\bdeveloper mode\b|\bjailbreak|\bDAN mode\b"),
    ("high", "asks the agent to reveal its hidden instructions",
     r"\b(reveal|print|show|repeat|output|tell me)\b.{0,30}\b(system prompt|hidden instructions|your instructions|initial prompt|your rules)\b"),
    ("high", "asks to send sensitive data somewhere",
     r"\b(send|forward|email|share|upload|post|transfer|give)\b.{0,80}\b(passwords?|credentials?|api[ _-]?keys?|tokens?|bank|account (?:details|numbers?)"
     r"|ifsc|card numbers?|price ?list|customer (?:list|data|details)|database|invoices)\b.{0,80}\b(to|at)\b"),
    ("high", "pretends to be a system or admin message",
     r"(^|\n|<!--|\[)\s*(system|admin|administrator|assistant|developer)\s*(note|message|override|instruction|notice)?[^\n:\]]{0,40}[:\]]"),
    ("medium", "asks the agent to keep something secret",
     r"\b(do not|don't|never)\b.{0,20}\b(tell|mention|inform|notify|reveal|report)\b.{0,25}\b(anyone|the user|the owner|your (?:user|owner|boss)|them|this)\b"),
    ("medium", "contains hidden text (HTML comment or invisible characters)", r"<!--.*?-->|[\u200b-\u200f\u2060\ufeff]"),
    ("medium", "announces new instructions", r"\b(new|updated|real|actual|secret) (instructions?|task|rules|orders)\s*[:\-]"),
    ("medium", "tells the agent which tool to call",
     r"\b(call|use|run|execute|invoke|trigger)\b.{0,20}\b(the )?(tool|function|command|api)\b"),
    ("low", "creates urgency", r"\b(immediately|right away|urgent(?:ly)?|asap|without delay)\b"),
]
_COMPILED = [(sev, label, re.compile(rx, re.I | re.S)) for sev, label, rx in RULES]


def scan(text: str | None) -> dict:
    """Return {"severity": none|low|medium|high, "flagged": bool, "hits": [...]} for a piece of untrusted text."""
    hits = []
    for sev, label, rx in _COMPILED:
        m = rx.search(text or "")
        if m:
            hits.append({"severity": sev, "rule": label, "match": m.group(0).strip()[:120]})
    severity = max((h["severity"] for h in hits), key=LEVELS.get, default="none")
    return {"severity": severity, "flagged": LEVELS[severity] >= LEVELS["medium"], "hits": hits}


def worse(a: str, b: str) -> str:
    return a if LEVELS.get(a, 0) >= LEVELS.get(b, 0) else b
