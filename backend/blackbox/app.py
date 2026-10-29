"""Blackbox HTTP API (versioned under /v1)."""
import hashlib
import hmac
import threading
import time
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import __version__
from .config import Settings
from .db import EVENT_TYPES, Store
from .redact import redact

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:\-]{1,64}$")]
Id = Annotated[str, StringConstraints(pattern=r"^[a-z]{3}_[0-9a-f]{16}$")]


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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    store = Store(settings.db_path)
    limiter = RateLimiter(settings.rate_limit_per_min)
    app = FastAPI(title="Blackbox", version=__version__)
    app.state.store, app.state.settings = store, settings
    clean = (lambda v: redact(v)) if settings.redact else (lambda v: v)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse({"detail": "Content-Length header required"}, 411)
            if int(length) > settings.max_body_kb * 1024:
                return JSONResponse({"detail": f"body larger than {settings.max_body_kb} KB"}, 413)
        if request.url.path.startswith("/v1/"):
            who = request.headers.get("authorization") or (request.client.host if request.client else "?")
            if not limiter.allow(hashlib.sha256(who.encode()).hexdigest()[:16]):
                return JSONResponse({"detail": "rate limit exceeded, slow down"}, 429)
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])

    def admin(creds: Creds = Depends(_scheme)):
        token = _bearer(creds)
        if not settings.admin_token or not hmac.compare_digest(token.encode(), settings.admin_token.encode()):
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

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    # ---- admin --------------------------------------------------------------
    @app.post("/v1/agents", dependencies=[Depends(admin)], status_code=201)
    def create_agent(body: AgentIn):
        created, api_key = store.create_agent(body.name, body.owner, body.manifest.model_dump())
        return {"agent": created, "api_key": api_key, "note": "The API key is shown once and stored only as a hash."}

    @app.get("/v1/agents", dependencies=[Depends(admin)])
    def list_agents():
        return store.list_agents()

    @app.get("/v1/agents/{agent_id}", dependencies=[Depends(admin)])
    def get_agent(agent_id: str):
        return store.get_agent(agent_id) or _404("agent")

    @app.get("/v1/runs", dependencies=[Depends(admin)])
    def list_runs(agent_id: str | None = None, limit: int = 100):
        return store.list_runs(agent_id, min(limit, 1000))

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(admin)])
    def get_run(run_id: str):
        return store.get_run(run_id) or _404("run")

    @app.get("/v1/events", dependencies=[Depends(admin)])
    def list_events(run_id: str | None = None, agent_id: str | None = None, type: str | None = None, limit: int = 500):
        return store.list_events(run_id, agent_id, type, min(limit, 5000))

    @app.get("/v1/verify", dependencies=[Depends(admin)])
    def verify(agent_id: str | None = None):
        return store.verify(agent_id)

    # ---- agents -------------------------------------------------------------
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
        e = store.add_event(a["id"], body.run_id, body.type, clean(body.payload))
        return {"id": e["id"], "seq": e["seq"], "hash": e["hash"]}

    @app.post("/v1/check")
    def check(body: CheckIn, a: dict = Depends(agent)):
        own_run(a, body.run_id)
        declared = a["manifest"].get("tools") or []
        reason = "Allowed: Blackbox is recording only, no policy is enforced yet."
        if declared and body.tool not in declared:
            reason += f" Note: '{body.tool}' is not in this agent's declared tools."
        payload = {"tool": body.tool, "args": body.args, "decision": "allow", "reason": reason,
                   "in_manifest": body.tool in declared}
        if body.untrusted_input:
            payload["untrusted_input"] = body.untrusted_input
        e = store.add_event(a["id"], body.run_id, "policy_decision", clean(payload))
        return {"decision": "allow", "reason": reason, "check_id": e["id"], "approval_id": None}

    return app


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


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
