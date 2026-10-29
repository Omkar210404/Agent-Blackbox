"""Per-agent YAML policy: validation with clear messages, and evaluation of a tool call."""
import fnmatch
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .scanner import LEVELS

Action = Literal["allow", "needs_approval", "deny"]
RANK = {"allow": 0, "needs_approval": 1, "deny": 2}
RECIPIENT_KEYS = re.compile(r"^(to|cc|bcc|recipients?|email|emails|address|send_to)$", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
URL = re.compile(r"https?://([^/\s:?#]+)", re.I)
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRule(Strict):
    require_approval: bool = False
    allowed_recipient_domains: list[str] | None = None
    allowed_recipients: list[str] | None = None
    allowed_url_domains: list[str] | None = None
    amount_field: str = "amount"
    max_amount: float | None = Field(None, ge=0)
    approval_above_amount: float | None = Field(None, ge=0)
    forbidden_patterns: list[str] = Field(default_factory=list)
    max_per_hour: int | None = Field(None, ge=0)
    max_per_day: int | None = Field(None, ge=0)

    @field_validator("forbidden_patterns")
    @classmethod
    def _regex_ok(cls, v):
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"'{p}' is not a valid pattern ({exc})")
        return v


class QuietHours(Strict):
    start: str
    end: str
    utc_offset: str = "+00:00"
    action: Literal["needs_approval", "deny"] = "needs_approval"

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v):
        if not HHMM.match(v):
            raise ValueError("use 24-hour HH:MM, for example 22:00")
        return v

    @field_validator("utc_offset")
    @classmethod
    def _offset(cls, v):
        if not re.match(r"^[+-]([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("use +HH:MM or -HH:MM, for example +05:30")
        return v


class Budgets(Strict):
    calls_per_day: int | None = Field(None, ge=0)
    spend_per_day: float | None = Field(None, ge=0)


class UntrustedRule(Strict):
    high: Action = "needs_approval"
    medium: Action = "allow"


class Policy(Strict):
    allowed_tools: list[str] | None = None
    denied_tools: list[str] = Field(default_factory=list)
    require_approval: list[str] = Field(default_factory=list)
    tools: dict[str, ToolRule] = Field(default_factory=dict)
    budgets: Budgets = Field(default_factory=Budgets)
    quiet_hours: QuietHours | None = None
    untrusted_input: UntrustedRule = Field(default_factory=UntrustedRule)


class PolicyError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_policy(text: str) -> Policy:
    """Parse and validate YAML. Raises PolicyError with one readable message per problem."""
    try:
        data = yaml.safe_load(text or "") or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        raise PolicyError([f"YAML syntax error at {where}{getattr(exc, 'problem', None) or exc}"])
    if not isinstance(data, dict):
        raise PolicyError(["the policy must be a YAML mapping (key: value pairs)"])
    try:
        return Policy.model_validate(data)
    except ValidationError as exc:
        msgs = []
        for e in exc.errors():
            path = ".".join(str(p) for p in e["loc"]) or "policy"
            msg = e["msg"].replace("Value error, ", "")
            if e["type"] == "extra_forbidden":
                msg = "unknown setting (check the spelling)"
            msgs.append(f"{path}: {msg}")
        raise PolicyError(msgs)


@dataclass
class Context:
    """Facts the policy needs about the world, injected so evaluation is easy to test."""
    agent_name: str = "The agent"
    paused: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    count: Callable[[str | None, datetime], int] = lambda tool, since: 0     # allowed calls since
    spent: Callable[[datetime], float] = lambda since: 0.0                   # allowed spend since
    untrusted_severity: str = "none"
    untrusted_reason: str = ""


def _matches(tool: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(tool, p) for p in patterns)


def _domain_ok(host: str, allowed: list[str]) -> bool:
    host = host.lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def _walk(value, key=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    else:
        yield key, value


def _in_quiet_hours(q: QuietHours, now: datetime) -> bool:
    sign = 1 if q.utc_offset[0] == "+" else -1
    local = now + sign * timedelta(hours=int(q.utc_offset[1:3]), minutes=int(q.utc_offset[4:6]))
    cur, start, end = local.strftime("%H:%M"), q.start, q.end
    return start <= cur < end if start <= end else (cur >= start or cur < end)


def evaluate(policy: Policy | None, tool: str, args: dict, ctx: Context) -> dict:
    """Decide allow / needs_approval / deny. Every rule that fires adds a plain-language reason."""
    reasons: list[tuple[str, str]] = []

    def add(level: Action, text: str):
        reasons.append((level, text))

    if ctx.paused:
        add("deny", f"{ctx.agent_name} is paused by its owner.")
    sev = ctx.untrusted_severity
    if policy is None:
        if LEVELS.get(sev, 0) >= LEVELS["high"]:
            add("needs_approval", f"This run read untrusted text that looks like a prompt injection ({sev}): {ctx.untrusted_reason}.")
        return _result(reasons, None, "No policy is set for this agent, so the call is allowed and recorded.")

    rule = policy.tools.get(tool) or next((r for p, r in policy.tools.items() if _matches(tool, [p])), ToolRule())
    if _matches(tool, policy.denied_tools):
        add("deny", f"{tool} is on this agent's denied list.")
    if policy.allowed_tools is not None and not _matches(tool, policy.allowed_tools):
        add("deny", f"{tool} is not in this agent's allowed tools.")

    fields = list(_walk(args))
    if rule.allowed_recipient_domains is not None or rule.allowed_recipients is not None:
        for key, val in fields:
            if not (isinstance(val, str) and RECIPIENT_KEYS.match(key)):
                continue
            for m in EMAIL.finditer(val):
                addr, domain = m.group(0).lower(), m.group(1).lower()
                ok_addr = rule.allowed_recipients is not None and addr in [a.lower() for a in rule.allowed_recipients]
                ok_dom = rule.allowed_recipient_domains is not None and _domain_ok(domain, rule.allowed_recipient_domains)
                if not (ok_addr or ok_dom):
                    allowed = ", ".join((rule.allowed_recipient_domains or []) + (rule.allowed_recipients or [])) or "none"
                    add("deny", f"Recipient domain {domain} is not allowed (allowed: {allowed}).")
    if rule.allowed_url_domains is not None:
        for _, val in fields:
            for m in URL.finditer(val if isinstance(val, str) else ""):
                if not _domain_ok(m.group(1), rule.allowed_url_domains):
                    add("deny", f"Website {m.group(1)} is not on the allowed list ({', '.join(rule.allowed_url_domains) or 'none'}).")
    text = json.dumps(args, ensure_ascii=False, default=str)
    for p in rule.forbidden_patterns:
        if re.search(p, text, re.I):
            add("deny", f"The call contains forbidden content (rule: '{p}').")

    amount = args.get(rule.amount_field) if isinstance(args, dict) else None
    amount = float(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None
    if amount is not None:
        if rule.max_amount is not None and amount > rule.max_amount:
            add("deny", f"Amount {amount:g} is above the limit of {rule.max_amount:g}.")
        elif rule.approval_above_amount is not None and amount > rule.approval_above_amount:
            add("needs_approval", f"Amount {amount:g} is above {rule.approval_above_amount:g}, so a human must approve it.")

    day_start = ctx.now.replace(hour=0, minute=0, second=0, microsecond=0)
    if rule.max_per_hour is not None and ctx.count(tool, ctx.now - timedelta(hours=1)) >= rule.max_per_hour:
        add("deny", f"{tool} already ran {rule.max_per_hour} times in the last hour (the limit).")
    if rule.max_per_day is not None and ctx.count(tool, day_start) >= rule.max_per_day:
        add("deny", f"{tool} already ran {rule.max_per_day} times today (the limit).")
    if policy.budgets.calls_per_day is not None and ctx.count(None, day_start) >= policy.budgets.calls_per_day:
        add("deny", f"The daily budget of {policy.budgets.calls_per_day} tool calls is used up.")
    if policy.budgets.spend_per_day is not None and amount is not None and \
            ctx.spent(day_start) + amount > policy.budgets.spend_per_day:
        add("deny", f"This would take today's spend over the budget of {policy.budgets.spend_per_day:g}.")

    if rule.require_approval or _matches(tool, policy.require_approval):
        add("needs_approval", f"{tool} always needs a human's approval.")
    if policy.quiet_hours and _in_quiet_hours(policy.quiet_hours, ctx.now):
        q = policy.quiet_hours
        add(q.action, f"It is quiet hours ({q.start}-{q.end}, UTC{q.utc_offset}).")
    if sev in ("high", "medium"):
        action = getattr(policy.untrusted_input, sev)
        if action != "allow":
            add(action, f"This run read untrusted text that looks like a prompt injection ({sev}): {ctx.untrusted_reason}.")
    return _result(reasons, amount, "Allowed: the call matches this agent's policy.")


def _result(reasons, amount, allow_text):
    decision = max((lvl for lvl, _ in reasons), key=RANK.get, default="allow")
    shown = [t for lvl, t in reasons if lvl == decision] if decision != "allow" else [allow_text]
    return {"decision": decision, "reason": " ".join(shown), "reasons": [{"level": l, "text": t} for l, t in reasons],
            "amount": amount}
