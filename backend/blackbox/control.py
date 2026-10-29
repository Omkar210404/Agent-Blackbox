"""Decisions, approvals and anomaly flags. The API routes call into this; it is the "control" half of Blackbox."""
import hashlib
from datetime import datetime, timedelta, timezone

from .config import Settings
from .db import Store, now
from .policy import Context, PolicyError, evaluate, parse_policy
from .redact import redact
from .scanner import LEVELS, scan, worse

BURST_PER_MIN = 15
DENIALS_FOR_FLAG = 3


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def describe(agent_name: str, tool: str, args: dict) -> str:
    """Plain-language sentence for what the agent wants to do (templates, no LLM)."""
    a = args or {}
    to = a.get("to") or a.get("recipient") or a.get("email")
    if "email" in tool and to:
        subject = f" with the subject \"{a['subject']}\"" if a.get("subject") else ""
        return f"{agent_name} wants to send an email to {to}{subject}."
    if a.get("url"):
        return f"{agent_name} wants to open the web page {a['url']}."
    amount = next((a[k] for k in a if "amount" in k and isinstance(a[k], (int, float))), None)
    if amount is not None:
        who = a.get("vendor") or a.get("payee") or a.get("customer") or ""
        return f"{agent_name} wants to run {tool} for {amount:g}" + (f" ({who})." if who else ".")
    shown = ", ".join(f"{k} = {str(v)[:40]}" for k, v in list(a.items())[:3])
    return f"{agent_name} wants to run {tool}" + (f" ({shown})." if shown else ".")


class Control:
    def __init__(self, store: Store, settings: Settings, notifier=None):
        self.store, self.settings, self.notifier = store, settings, notifier

    def clean(self, value):
        return redact(value) if self.settings.redact else value

    # ---- policies -------------------------------------------------------------
    def policy_for(self, agent_id: str):
        row = self.store.get_policy(agent_id)
        if not row:
            return None
        try:
            return parse_policy(row["yaml"])
        except PolicyError:
            return None  # cannot happen through the API (validated on save); a hand-edited DB falls back to no policy

    def set_policy(self, agent: dict, text: str, by: str):
        parse_policy(text)  # raises PolicyError with readable messages
        old = self.store.get_policy(agent["id"])
        self.store.set_policy(agent["id"], text, by)
        digest = hashlib.sha256(text.encode()).hexdigest()
        e = self.store.add_event(agent["id"], None, "note", {
            "kind": "policy_change", "by": by, "policy_sha256": digest,
            "previous_sha256": hashlib.sha256(old["yaml"].encode()).hexdigest() if old else None,
            "text": f"Policy {'updated' if old else 'set'} by {by}."})
        self.store.add_flag(agent["id"], None, e["id"], "permission_change", "medium",
                            f"Policy for {agent['name']} was {'changed' if old else 'set'} by {by}.")

    def set_paused(self, agent: dict, paused: bool, by: str):
        self.store.set_paused(agent["id"], paused)
        verb = "paused" if paused else "resumed"
        e = self.store.add_event(agent["id"], None, "note", {"kind": "permission_change", "by": by,
                                                            "text": f"{agent['name']} was {verb} by {by}."})
        self.store.add_flag(agent["id"], None, e["id"], "permission_change", "medium", f"{agent['name']} was {verb} by {by}.")

    # ---- the check ------------------------------------------------------------
    def context(self, agent: dict, run_id, untrusted_text, now_dt=None):
        now_dt = now_dt or datetime.now(timezone.utc)
        sev, why = "none", ""
        if untrusted_text:
            s = scan(untrusted_text)
            if s["hits"]:
                sev, why = s["severity"], f"\"{s['hits'][0]['match']}\" ({s['hits'][0]['rule']})"
        if run_id:  # inputs recorded earlier in the run count too (n8n flows may not resend them)
            for inp in self.store.run_inputs(run_id, iso(now_dt - timedelta(hours=6))):
                s = inp.get("scan") or {}
                if LEVELS.get(s.get("severity"), 0) > LEVELS[sev]:
                    sev = s["severity"]
                    hit = (s.get("hits") or [{}])[0]
                    why = f"\"{hit.get('match', '')}\" ({hit.get('rule', '')}) in {inp.get('source', 'input')}"
        return Context(agent_name=agent["name"], paused=agent["paused"], now=now_dt,
                       count=lambda tool, since: self.store.count_decisions(agent["id"], iso(since), tool),
                       spent=lambda since: self.store.spent_since(agent["id"], iso(since)),
                       untrusted_severity=sev, untrusted_reason=why)

    def check(self, agent: dict, run_id, tool: str, args: dict, untrusted_text: str | None = None) -> dict:
        ctx = self.context(agent, run_id, untrusted_text)
        result = evaluate(self.policy_for(agent["id"]), tool, args, ctx)
        first_use = not self.store.tool_seen_before(agent["id"], tool)
        payload = {"tool": tool, "args": args, "decision": result["decision"], "reason": result["reason"],
                   "reasons": result["reasons"], "amount": result["amount"], "untrusted_severity": ctx.untrusted_severity}
        if untrusted_text:
            payload["untrusted_input"] = untrusted_text[:2000]
        approval = None
        if result["decision"] == "needs_approval":
            approval = self.store.create_approval(
                agent["id"], run_id, "tool_call", tool, self.clean(args), self.clean(describe(agent["name"], tool, args)),
                self.clean(result["reasons"]), self.settings.approval_timeout_s)
            payload["approval_id"] = approval["id"]
        e = self.store.add_event(agent["id"], run_id, "policy_decision", self.clean(payload))
        if approval:
            channel = self.notify(approval)
            self.store.add_event(agent["id"], run_id, "approval_request", {
                "approval_id": approval["id"], "tool": tool, "summary": approval["summary"],
                "reason": self.clean(result["reason"]), "notified": channel, "expires": approval["expires"]})
        self._anomalies_after_check(agent, run_id, e["id"], tool, result["decision"], first_use)
        return {"decision": result["decision"], "reason": result["reason"], "reasons": result["reasons"],
                "check_id": e["id"], "approval_id": approval["id"] if approval else None,
                "poll_url": f"/v1/approvals/{approval['id']}" if approval else None,
                "expires": approval["expires"] if approval else None}

    def notify(self, approval: dict) -> str:
        if self.notifier and self.notifier.enabled:
            try:
                return "telegram" if self.notifier.send_approval(approval) else "dashboard (telegram send failed)"
            except Exception:  # never let a notification problem break the check; never log the token-bearing URL
                return "dashboard (telegram send failed)"
        return "dashboard (telegram not configured: simulated)"

    # ---- events ---------------------------------------------------------------
    def record(self, agent: dict, run_id, type_: str, payload: dict) -> dict:
        flag = None
        if type_ == "note" and payload.get("kind") == "input" and payload.get("trusted") is False:
            s = scan(str(payload.get("text", "")))
            payload = {**payload, "scan": s}
            if s["hits"]:
                flag = ("untrusted_input", s["severity"] if s["flagged"] else "low",
                        f"Untrusted {payload.get('source', 'input')} matches injection rule: {s['hits'][0]['rule']}.")
        e = self.store.add_event(agent["id"], run_id, type_, self.clean(payload))
        if flag:
            self.store.add_flag(agent["id"], run_id, e["id"], *flag)
        if type_ == "memory_write" and run_id:
            inputs = self.store.run_inputs(run_id, iso(datetime.now(timezone.utc) - timedelta(minutes=10)))
            if inputs:
                sev = "low"
                for i in inputs:
                    sev = worse(sev, "high" if (i.get("scan") or {}).get("flagged") else "low")
                self.store.add_flag(agent["id"], run_id, e["id"], "memory_after_untrusted", sev,
                                    f"Memory '{payload.get('key', '?')}' was written shortly after untrusted input"
                                    + (" that looked like a prompt injection." if sev == "high" else "."))
        return e

    def _anomalies_after_check(self, agent, run_id, event_id, tool, decision, first_use):
        aid, t = agent["id"], datetime.now(timezone.utc)
        if first_use:
            self.store.add_flag(aid, run_id, event_id, "first_tool_use", "low", f"First time {agent['name']} used {tool}.")
        minute_ago = iso(t - timedelta(minutes=1))
        if self.store.count_decisions(aid, minute_ago, decision=None) > BURST_PER_MIN and \
                not self.store.recent_flag(aid, "burst", minute_ago):
            self.store.add_flag(aid, run_id, event_id, "burst", "medium",
                                f"Burst: more than {BURST_PER_MIN} tool calls in one minute.")
        ten_min = iso(t - timedelta(minutes=10))
        if decision == "deny" and self.store.count_decisions(aid, ten_min, decision="deny") >= DENIALS_FOR_FLAG and \
                not self.store.recent_flag(aid, "repeated_denials", ten_min):
            self.store.add_flag(aid, run_id, event_id, "repeated_denials", "high",
                                f"{DENIALS_FOR_FLAG} or more calls were denied in 10 minutes.")

    # ---- approvals ------------------------------------------------------------
    def poll(self, approval: dict) -> dict:
        """Expire a pending approval whose time is up (default: deny)."""
        if approval["status"] == "pending" and approval["expires"] < now():
            done = self.store.decide_approval(approval["id"], "expired", "system (timeout)",
                                              "No human decided in time, so it was denied by default.")
            if done:
                self._record_decision(done, via="timeout")
                return done
            return self.store.get_approval(approval["id"])
        return approval

    def decide(self, approval_id: str, approve: bool, by: str, note: str = "", via: str = "dashboard"):
        a = self.store.get_approval(approval_id)
        if not a:
            return None, "not found"
        a = self.poll(a)
        if a["status"] != "pending":
            return a, f"already {a['status']}"
        done = self.store.decide_approval(approval_id, "approved" if approve else "denied", by, note)
        if not done:
            return self.store.get_approval(approval_id), "already decided"
        self._record_decision(done, via)
        if done["kind"] == "pause_request" and approve:
            self.set_paused(self.store.get_agent(done["agent_id"]), True, f"{by} (owner's pause request)")
        return done, "ok"

    def _record_decision(self, a: dict, via: str):
        self.store.add_event(a["agent_id"], a["run_id"], "approval_decision", {
            "approval_id": a["id"], "tool": a["tool"], "kind": a["kind"], "decision": a["status"],
            "decided_by": a["decided_by"], "decided_at": a["decided_at"], "note": a["note"] or "", "via": via})
        if self.notifier and self.notifier.enabled and via != "telegram":
            try:
                self.notifier.mark_decided(a)
            except Exception:
                pass

    def pause_request(self, agent: dict, who: str = "client (owner page)") -> dict:
        a = self.store.create_approval(agent["id"], None, "pause_request", "", {},
                                       f"The owner asked to pause {agent['name']}.",
                                       [{"level": "needs_approval", "text": f"Requested by {who}."}],
                                       7 * 24 * 3600)
        channel = self.notify(a)
        self.store.add_event(agent["id"], None, "approval_request", {"approval_id": a["id"], "kind": "pause_request",
                                                                     "summary": a["summary"], "notified": channel})
        return a
