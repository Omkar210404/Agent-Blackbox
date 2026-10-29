"""Blackbox Python SDK: record what an agent does and ask permission before tool calls.

    bb = Blackbox("http://127.0.0.1:8377", api_key)

    @bb.tool("send_email")
    def send_email(to, subject, body): ...

    with bb.run("reply to customer") as run:
        run.untrusted_input(email_text, source="customer_email")
        run.model_call("my-model", prompt=..., response=...)
        send_email("a@b.com", "Hi", "...")   # checked, recorded, then run
"""
import contextvars
import functools
import inspect
import json
import time
import warnings

import httpx

__all__ = ["Blackbox", "Run", "BlockedByPolicy", "BlackboxError"]
__version__ = "0.1.0"

_current_run: contextvars.ContextVar = contextvars.ContextVar("blackbox_run", default=None)


class BlackboxError(Exception):
    pass


class BlockedByPolicy(BlackboxError):
    """Raised when Blackbox denies a tool call (or no human approved it in time)."""

    def __init__(self, tool: str, reason: str, decision: dict | None = None):
        super().__init__(f"{tool} blocked: {reason}")
        self.tool, self.reason, self.decision = tool, reason, decision or {}


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)[:2000]


class Blackbox:
    def __init__(self, url: str, api_key: str, *, timeout: float = 10.0, approval_timeout: float = 300.0,
                 poll_interval: float = 2.0, fail_open: bool = False, client: httpx.Client | None = None):
        self.approval_timeout, self.poll_interval, self.fail_open = approval_timeout, poll_interval, fail_open
        self._http = client or httpx.Client(base_url=url.rstrip("/"), timeout=timeout)
        self._http.headers["Authorization"] = f"Bearer {api_key}"

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def _event(self, run_id, type_: str, payload: dict):
        """Best effort: a recorder outage must not crash the agent, but it is never silent."""
        try:
            body = {"run_id": run_id, "type": type_, "payload": {k: _jsonable(v) for k, v in payload.items()}}
            return self._post("/v1/events", body)
        except httpx.HTTPError as exc:
            warnings.warn(f"Blackbox: could not record {type_} event ({type(exc).__name__})")

    def run(self, name: str, parent_run_id: str | None = None) -> "Run":
        return Run(self, name, parent_run_id)

    def check(self, tool: str, args: dict | None = None, run: "Run | None" = None,
              untrusted_input: str | None = None) -> dict:
        """Ask Blackbox before a tool call. Returns the decision or raises BlockedByPolicy."""
        run = run or _current_run.get()
        body = {"run_id": run.id if run else None, "tool": tool, "args": args or {}}
        text = untrusted_input or (run.untrusted if run else None)
        if text:
            body["untrusted_input"] = text[-20000:]
        try:
            decision = self._post("/v1/check", body)
        except httpx.HTTPError as exc:
            if self.fail_open:
                warnings.warn(f"Blackbox unreachable, allowing {tool} because fail_open=True")
                return {"decision": "allow", "reason": "Blackbox unreachable (fail_open)"}
            raise BlockedByPolicy(tool, f"Blackbox could not be reached ({type(exc).__name__}), failing closed") from exc
        if decision["decision"] == "needs_approval":
            decision = self._wait_for_approval(decision)
        if decision["decision"] != "allow":
            raise BlockedByPolicy(tool, decision.get("reason", "denied"), decision)
        return decision

    def _wait_for_approval(self, decision: dict) -> dict:
        deadline = time.monotonic() + self.approval_timeout
        while time.monotonic() < deadline:
            try:
                r = self._http.get(f"/v1/approvals/{decision['approval_id']}")
                if r.status_code == 200:
                    status = r.json().get("status")
                    if status == "approved":
                        return {**decision, "decision": "allow", "reason": "approved by a human"}
                    if status in ("denied", "expired"):
                        return {**decision, "decision": "deny", "reason": f"approval {status}"}
            except httpx.HTTPError:
                pass
            time.sleep(self.poll_interval)
        return {**decision, "decision": "deny",
                "reason": f"no human decision within {self.approval_timeout:.0f}s, denied by default"}

    def tool(self, name: str | None = None):
        """Decorator: check before the call, then record the call and its result."""
        def decorate(fn):
            tool_name, sig = name or fn.__name__, inspect.signature(fn)

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                call_args = {k: _jsonable(v) for k, v in bound.arguments.items()}
                run = _current_run.get()
                run_id = run.id if run else None
                decision = self.check(tool_name, call_args, run)
                self._event(run_id, "tool_call", {"tool": tool_name, "args": call_args,
                                                  "check_id": decision.get("check_id")})
                start = time.perf_counter()
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    self._event(run_id, "tool_result", {"tool": tool_name, "ok": False, "error": repr(exc)[:2000],
                                                        "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
                    raise
                self._event(run_id, "tool_result", {"tool": tool_name, "ok": True, "result": _jsonable(result),
                                                    "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
                return result
            return wrapper
        return decorate

    def close(self):
        self._http.close()


class Run:
    def __init__(self, bb: Blackbox, name: str, parent_run_id: str | None = None):
        self.bb, self.name, self.parent_run_id = bb, name, parent_run_id
        self.id: str | None = None
        self.untrusted: str | None = None  # last untrusted text seen, sent along with tool checks
        self._token = None

    def __enter__(self):
        try:
            self.id = self.bb._post("/v1/runs", {"name": self.name, "parent_run_id": self.parent_run_id})["id"]
        except httpx.HTTPError as exc:
            warnings.warn(f"Blackbox: could not start run ({type(exc).__name__}), events will have no run")
        self._token = _current_run.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        _current_run.reset(self._token)
        if exc is not None and not isinstance(exc, BlockedByPolicy):
            self.error(repr(exc)[:2000])
        if self.id:
            status = "completed" if exc is None else "blocked" if isinstance(exc, BlockedByPolicy) else "failed"
            try:
                self.bb._post(f"/v1/runs/{self.id}/end", {"status": status})
            except httpx.HTTPError:
                warnings.warn("Blackbox: could not close run")
        return False

    def event(self, type_: str, **payload):
        return self.bb._event(self.id, type_, payload)

    def model_call(self, model: str, prompt=None, response=None, **extra):
        return self.event("model_call", model=model, prompt=prompt, response=response, **extra)

    def memory_write(self, key: str, value, **extra):
        return self.event("memory_write", key=key, value=value, **extra)

    def note(self, text: str, **extra):
        return self.event("note", text=text, **extra)

    def untrusted_input(self, text: str, source: str = "unknown"):
        """Record text the agent did not write (emails, web pages, user messages)."""
        self.untrusted = text
        return self.event("note", kind="input", trusted=False, source=source, text=text)

    def error(self, message: str, **extra):
        return self.event("error", message=message, **extra)

    def child(self, name: str, bb: Blackbox | None = None) -> "Run":
        """A sub-agent run linked to this one (optionally recorded under another agent's key)."""
        return Run(bb or self.bb, name, parent_run_id=self.id)
