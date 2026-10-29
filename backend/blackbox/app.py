"""Blackbox HTTP API (versioned under /v1) plus the built dashboard."""
import hashlib
import hmac
import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import __version__, views
from .config import ROOT, Settings
from .control import Control, describe
from .db import EVENT_TYPES, Store
from .policy import PolicyError, evaluate, parse_policy
from .telegram import Telegram

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:\-]{1,64}$")]
Id = Annotated[str, StringConstraints(pattern=r"^[a-z]{3}_[0-9a-f]{16}$")]
Day = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
DIST = ROOT / "dashboard" / "dist"

POLICY_TEMPLATE = """# Policy for this agent. Every key is optional.
# allowed_tools: [lookup_order, send_email]     # anything else is denied
denied_tools: []
require_approval: []                            # tools that always need a human
tools: {}
#  send_email:
#    allowed_recipient_domains: [yourshop.example]
#    forbidden_patterns: ["bank", "IFSC", "password"]
#    max_per_hour: 20
#  refund:
#    amount_field: amount
#    approval_above_amount: 1000
#    max_amount: 5000
budgets: {}
#  calls_per_day: 500
#  spend_per_day: 20000
# quiet_hours: {start: "22:00", end: "07:00", utc_offset: "+05:30", action: needs_approval}
untrusted_input: {high: needs_approval, medium: allow}
"""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Manifest(Strict):
    tools: list[Name] = Field(default_factory=list, max_length=200)
    limits: dict[Name, int | float | str] = Field(default_factory=dict)


class AgentIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    owner: str = Field("", max_length=120)
    manifest: Manifest = Field(default_factory=Manifest)


class RunIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    parent_run_id: Id | None = None


class RunEnd(Strict):
    status: Literal["completed", "failed", "blocked", "cancelled"] = "completed"


class EventIn(Strict):
    run_id: Id | None = None
    type: Literal[EVENT_TYPES]  # type: ignore[valid-type]
    payload: dict = Field(default_factory=dict)


class CheckIn(Strict):
    run_id: Id | None = None
    tool: Name
    args: dict = Field(default_factory=dict)
    untrusted_input: str | None = Field(None, max_length=20000)


class PolicyIn(Strict):
    yaml: str = Field(max_length=50_000)
    by: str = Field("admin (dashboard)", max_length=80)


class PolicyTest(Strict):
    agent_id: str
    yaml: str = Field(max_length=50_000)
    tool: Name
    args: dict = Field(default_factory=dict)
    untrusted_input: str | None = Field(None, max_length=20000)


class DecisionIn(Strict):
    approve: bool
    by: str = Field("admin (dashboard)", min_length=1, max_length=80)
    note: str = Field("", max_length=500)


class PauseIn(Strict):
    paused: bool
    by: str = Field("admin (dashboard)", max_length=80)


class RateLimiter:
    """Fixed one-minute window per caller (token hash or IP). In memory, single process."""

    def __init__(self, per_min: int):
        self.per_min, self.hits, self.lock = per_min, {}, threading.Lock()

    def allow(self, key: str) -> bool:
        window = int(time.time() // 60)
        with self.lock:
            w, n = self.hits.get(key, (window, 0))
            n = n + 1 if w == window else 1
            self.hits[key] = (window, n)
            if len(self.hits) > 10_000:
                self.hits = {k: v for k, v in self.hits.items() if v[0] == window}
            return n <= self.per_min


_scheme = HTTPBearer(auto_error=False, description="Admin token (from .env) or an agent API key")
Creds = HTTPAuthorizationCredentials | None


def _bearer(creds: Creds) -> str:
    if creds and creds.credentials:
        return creds.credentials.strip()
    raise HTTPException(401, "missing bearer token")


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    store = Store(settings.db_path)
    control = Control(store, settings)
    telegram = Telegram(settings.telegram_bot_token, settings.telegram_chat_id,
                        on_decision=lambda aid, ok, by: control.decide(aid, ok, by, via="telegram"))
    control.notifier = telegram
    limiter = RateLimiter(settings.rate_limit_per_min)

    @asynccontextmanager
    async def lifespan(_app):
        telegram.start()
        yield
        telegram.stop()

    app = FastAPI(title="Blackbox", version=__version__, lifespan=lifespan)
    app.state.store, app.state.settings, app.state.control = store, settings, control

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is None and "chunked" in request.headers.get("transfer-encoding", "").lower():
                return JSONResponse({"detail": "Content-Length header required (chunked bodies are not accepted)"}, 411)
            if length is not None and (not length.isdigit() or int(length) > settings.max_body_kb * 1024):
                return JSONResponse({"detail": f"body larger than {settings.max_body_kb} KB"}, 413)
        if request.url.path.startswith("/v1/"):
            who = request.headers.get("authorization") or (request.client.host if request.client else "?")
            if not limiter.allow(hashlib.sha256(who.encode()).hexdigest()[:16]):
                return JSONResponse({"detail": "rate limit exceeded, slow down"}, 429)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"  # client links carry a token in the path
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])

    def is_admin(token: str) -> bool:
        return bool(settings.admin_token) and hmac.compare_digest(token.encode(), settings.admin_token.encode())

    def admin(creds: Creds = Depends(_scheme)):
        if not is_admin(_bearer(creds)):
            raise HTTPException(401, "invalid admin token")

    def agent(creds: Creds = Depends(_scheme)) -> dict:
        found = store.agent_by_key(_bearer(creds))
        if not found:
            raise HTTPException(401, "invalid agent API key")
        return found

    def own_run(a: dict, run_id: str | None):
        if run_id is None:
            return None
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if run["agent_id"] != a["id"]:
            raise HTTPException(403, "run belongs to another agent")
        return run

    def get_agent_or_404(agent_id: str) -> dict:
        return store.get_agent(agent_id) or _404("agent")

    def client_agent(token: str) -> dict:
        if len(token) < 20:
            _404("page")
        return store.agent_by_client_token(token) or _404("page")

    def pending():
        return [p for p in (control.poll(a) for a in store.list_approvals(status="pending")) if p["status"] == "pending"]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    # ---- agents (admin) ---------------------------------------------------------
    @app.post("/v1/agents", dependencies=[Depends(admin)], status_code=201)
    def create_agent(body: AgentIn):
        created, api_key = store.create_agent(body.name, body.owner, body.manifest.model_dump())
        return {"agent": created, "api_key": api_key, "note": "The API key is shown once and stored only as a hash."}

    @app.get("/v1/agents", dependencies=[Depends(admin)])
    def list_agents():
        return store.list_agents()

    @app.get("/v1/agents/{agent_id}", dependencies=[Depends(admin)])
    def get_agent(agent_id: str):
        return get_agent_or_404(agent_id)

    @app.get("/v1/agents/{agent_id}/policy", dependencies=[Depends(admin)])
    def get_policy(agent_id: str):
        get_agent_or_404(agent_id)
        return store.get_policy(agent_id) or {"agent_id": agent_id, "yaml": POLICY_TEMPLATE, "updated": None,
                                              "updated_by": None, "template": True}

    @app.put("/v1/agents/{agent_id}/policy", dependencies=[Depends(admin)])
    def put_policy(agent_id: str, body: PolicyIn):
        try:
            control.set_policy(get_agent_or_404(agent_id), body.yaml, body.by)
        except PolicyError as exc:
            raise HTTPException(422, {"errors": exc.errors})
        return store.get_policy(agent_id)

    @app.post("/v1/agents/{agent_id}/pause", dependencies=[Depends(admin)])
    def pause(agent_id: str, body: PauseIn):
        control.set_paused(get_agent_or_404(agent_id), body.paused, body.by)
        return store.get_agent(agent_id)

    @app.post("/v1/agents/{agent_id}/client-link", dependencies=[Depends(admin)])
    def client_link(agent_id: str):
        get_agent_or_404(agent_id)
        token = store.new_client_token(agent_id)
        return {"path": f"/client/{token}", "note": "Shown once. Creating a new link disables the old one."}

    @app.post("/v1/policy/validate", dependencies=[Depends(admin)])
    def validate_policy(body: PolicyIn):
        try:
            parse_policy(body.yaml)
            return {"ok": True, "errors": []}
        except PolicyError as exc:
            return {"ok": False, "errors": exc.errors}

    @app.post("/v1/policy/test", dependencies=[Depends(admin)])
    def test_policy(body: PolicyTest):
        a = get_agent_or_404(body.agent_id)
        try:
            pol = parse_policy(body.yaml)
        except PolicyError as exc:
            return {"ok": False, "errors": exc.errors}
        result = evaluate(pol, body.tool, body.args, control.context(a, None, body.untrusted_input))
        return {"ok": True, **result, "summary": describe(a["name"], body.tool, body.args)}

    # ---- reading the recording (admin) ---------------------------------------------
    @app.get("/v1/runs", dependencies=[Depends(admin)])
    def list_runs(agent_id: str | None = None, limit: int = 100):
        return store.list_runs(agent_id, min(limit, 1000))

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(admin)])
    def get_run(run_id: str):
        return store.get_run(run_id) or _404("run")

    @app.get("/v1/runs/{run_id}/timeline", dependencies=[Depends(admin)])
    def timeline(run_id: str):
        return views.run_timeline(store, run_id) or _404("run")

    @app.get("/v1/events", dependencies=[Depends(admin)])
    def list_events(run_id: str | None = None, agent_id: str | None = None, type: str | None = None, limit: int = 500):
        return store.list_events(run_id, agent_id, type, min(limit, 5000))

    @app.get("/v1/verify", dependencies=[Depends(admin)])
    def verify(agent_id: str | None = None):
        return store.verify(agent_id)

    @app.get("/v1/flags", dependencies=[Depends(admin)])
    def flags(agent_id: str | None = None, run_id: str | None = None, limit: int = 200):
        return store.list_flags(agent_id, run_id, limit=min(limit, 2000))

    @app.get("/v1/overview", dependencies=[Depends(admin)])
    def overview():
        return views.overview(store, pending())

    @app.get("/v1/map", dependencies=[Depends(admin)])
    def agent_map():
        return views.agent_map(store)

    @app.get("/v1/reports/{agent_id}", dependencies=[Depends(admin)])
    def report(agent_id: str, start: Day, end: Day, format: Literal["md", "html"] = "md"):
        text = views.report(store, get_agent_or_404(agent_id), start, end, format)
        return HTMLResponse(text) if format == "html" else PlainTextResponse(text, media_type="text/markdown")

    # ---- approvals ------------------------------------------------------------------
    @app.get("/v1/approvals", dependencies=[Depends(admin)])
    def list_approvals(status: str | None = None, agent_id: str | None = None, limit: int = 200):
        pending()  # expire overdue ones first
        return store.list_approvals(status, agent_id, min(limit, 1000))

    @app.get("/v1/approvals/{approval_id}")
    def get_approval(approval_id: str, creds: Creds = Depends(_scheme)):
        token = _bearer(creds)
        a = store.get_approval(approval_id) or _404("approval")
        if not is_admin(token):
            caller = store.agent_by_key(token)
            if not caller or caller["id"] != a["agent_id"]:
                raise HTTPException(401, "not allowed")
        return control.poll(a)

    @app.post("/v1/approvals/{approval_id}/decision", dependencies=[Depends(admin)])
    def decide(approval_id: str, body: DecisionIn):
        a, status = control.decide(approval_id, body.approve, body.by, body.note)
        if a is None:
            _404("approval")
        if status != "ok":
            raise HTTPException(409, f"approval is {status}")
        return a

    # ---- agents (API key) -------------------------------------------------------------
    @app.post("/v1/runs", status_code=201)
    def start_run(body: RunIn, a: dict = Depends(agent)):
        if body.parent_run_id and not store.get_run(body.parent_run_id):
            raise HTTPException(404, "parent run not found")
        return store.create_run(a["id"], body.name, body.parent_run_id)

    @app.post("/v1/runs/{run_id}/end")
    def end_run(run_id: str, body: RunEnd, a: dict = Depends(agent)):
        own_run(a, run_id)
        return store.end_run(run_id, body.status)

    @app.post("/v1/events", status_code=201)
    def add_event(body: EventIn, a: dict = Depends(agent)):
        own_run(a, body.run_id)
        e = control.record(a, body.run_id, body.type, body.payload)
        return {"id": e["id"], "seq": e["seq"], "hash": e["hash"]}

    @app.post("/v1/check")
    def check(body: CheckIn, a: dict = Depends(agent)):
        own_run(a, body.run_id)
        return control.check(a, body.run_id, body.tool, body.args, body.untrusted_input)

    # ---- client page (token link, read-only + pause request) ---------------------------
    @app.get("/v1/client/{token}")
    def client(token: str, day: Day | None = None, tz_offset_min: int = 0):
        return views.client_summary(store, client_agent(token), day, max(-840, min(840, tz_offset_min)))

    @app.post("/v1/client/{token}/pause")
    def client_pause(token: str):
        a = client_agent(token)
        if any(p["kind"] == "pause_request" for p in store.list_approvals("pending", a["id"])):
            return {"status": "already requested"}
        control.pause_request(a)
        return {"status": "requested"}

    # ---- dashboard --------------------------------------------------------------------
    if (DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        @app.get("/client/{token}", include_in_schema=False)
        def index(token: str | None = None):
            return FileResponse(DIST / "index.html")

        @app.get("/favicon.svg", include_in_schema=False)
        def favicon():
            return FileResponse(DIST / "favicon.svg")
    else:
        @app.get("/", include_in_schema=False)
        def no_dashboard():
            return HTMLResponse("<body style='background:#070a12;color:#e6eaf2;font-family:system-ui;padding:40px'>"
                                "<h2>Dashboard not built</h2><p>Run <code>cd dashboard &amp;&amp; npm install &amp;&amp; "
                                "npm run build</code>, then restart <code>blackbox serve</code>.</p></body>")

    return app


class BackgroundServer:
    """Runs the real API over HTTP on a free loopback port (used by demo and selftest)."""

    def __init__(self, settings: Settings):
        import socket
        import uvicorn
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.app = create_app(settings)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="warning", access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("API server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)
