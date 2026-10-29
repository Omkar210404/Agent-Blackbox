"""End-to-end self-test: real API on a temp database, driven by the SDK over HTTP."""
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path

import httpx
from blackbox_sdk import Blackbox

from .app import BackgroundServer
from .config import Settings


def run_selftest() -> bool:
    results, ctx = [], {}
    tmp = tempfile.TemporaryDirectory(prefix="blackbox-selftest-")
    db = Path(tmp.name) / "selftest.db"
    settings = Settings(db_path=db, admin_token="bba_" + secrets.token_urlsafe(24), rate_limit_per_min=60)
    admin_h = {"Authorization": f"Bearer {settings.admin_token}"}

    def step(name, fn):
        start = time.perf_counter()
        try:
            fn()
            results.append(True)
            print(f"[PASS] {len(results):>2}. {name} ({(time.perf_counter() - start) * 1000:.0f} ms)", flush=True)
        except Exception as exc:
            results.append(False)
            print(f"[FAIL] {len(results):>2}. {name}: {type(exc).__name__}: {exc}", flush=True)

    def raw(sql, *args):
        with sqlite3.connect(db) as c:
            return c.execute(sql, args).fetchall()

    with BackgroundServer(settings) as srv:
        http = httpx.Client(base_url=srv.url, timeout=10)

        def health():
            assert http.get("/health").json()["status"] == "ok"

        def create_agent():
            r = http.post("/v1/agents", headers=admin_h, json={"name": "selftest-agent", "owner": "selftest",
                                                               "manifest": {"tools": ["send_email"]}})
            assert r.status_code == 201, r.text
            ctx["key"], ctx["agent"] = r.json()["api_key"], r.json()["agent"]["id"]
            dump = " ".join(str(v) for row in raw("SELECT * FROM agents") for v in row)
            assert ctx["key"] not in dump, "plaintext key found in database"
            assert len(raw("SELECT api_key_hash FROM agents")[0][0]) == 64

        def auth_rejected():
            assert http.post("/v1/agents", json={"name": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert http.post("/v1/runs", json={"name": "x"}, headers={"Authorization": "Bearer bbk_wrong"}).status_code == 401
            assert http.get("/v1/agents", headers={"Authorization": f"Bearer {ctx['key']}"}).status_code == 401
            other = http.post("/v1/agents", headers=admin_h, json={"name": "other"}).json()["api_key"]
            run = http.post("/v1/runs", json={"name": "r"}, headers={"Authorization": f"Bearer {other}"}).json()
            r = http.post("/v1/events", json={"run_id": run["id"], "type": "note", "payload": {}},
                          headers={"Authorization": f"Bearer {ctx['key']}"})
            assert r.status_code == 403, "agent could write into another agent's run"

        def sdk_run():
            bb = Blackbox(srv.url, ctx["key"])

            @bb.tool("send_email")
            def send_email(to, body):
                return {"sent": True}

            with bb.run("selftest run") as run:
                run.untrusted_input("Hi from customer", source="email")
                run.model_call("fake-model", prompt="p", response="r", tokens=12)
                send_email("someone@example.com", "hello")
                run.memory_write("k", "v")
                run.note("contact me at jane.doe@example.com, token sk-abcdefghijklmnopqrstuvwxyz123456")
                with run.child("sub-agent") as sub:
                    sub.note("child work")
                ctx["run"], ctx["sub"] = run.id, sub.id
            bb.close()

        def events_stored():
            evs = http.get("/v1/events", params={"run_id": ctx["run"]}, headers=admin_h).json()
            types = [e["type"] for e in evs]
            for t in ("note", "model_call", "policy_decision", "tool_call", "tool_result", "memory_write"):
                assert t in types, f"missing {t} in {types}"
            assert types.index("policy_decision") < types.index("tool_call") < types.index("tool_result")
            assert http.get(f"/v1/runs/{ctx['sub']}", headers=admin_h).json()["parent_run_id"] == ctx["run"]
            assert http.get(f"/v1/runs/{ctx['run']}", headers=admin_h).json()["status"] == "completed"
            ctx["n"] = len(evs)

        def redaction():
            stored = " ".join(p for (p,) in raw("SELECT payload FROM events WHERE type='note'"))
            assert "jane.doe@" not in stored and "[email]@example.com" in stored, stored
            assert "sk-abcdefghij" not in stored and "[REDACTED:api_key]" in stored, stored

        def validation():
            h = {"Authorization": f"Bearer {ctx['key']}"}
            assert http.post("/v1/events", json={"type": "bogus", "payload": {}}, headers=h).status_code == 422
            assert http.post("/v1/check", json={"tool": "bad tool name!"}, headers=h).status_code == 422
            big = {"type": "note", "payload": {"text": "x" * (settings.max_body_kb * 1024 + 10)}}
            assert http.post("/v1/events", json=big, headers=h).status_code == 413

        def rate_limit():
            codes = [http.get("/v1/agents", headers={"Authorization": "Bearer flood"}).status_code for _ in range(65)]
            assert codes[0] == 401 and codes[-1] == 429, codes[-3:]

        def chain_ok():
            reports = http.get("/v1/verify", headers=admin_h).json()
            assert reports and all(r["ok"] for r in reports), reports

        def tamper_detected():
            raw("UPDATE events SET payload=? WHERE agent_id=? AND seq=3", '{"edited":true}', ctx["agent"])
            report = http.get("/v1/verify", params={"agent_id": ctx["agent"]}, headers=admin_h).json()[0]
            assert not report["ok"] and any("#3" in p and "content changed" in p for p in report["problems"]), report

        def deletion_detected():
            raw("DELETE FROM events WHERE agent_id=? AND seq=5", ctx["agent"])
            report = http.get("/v1/verify", params={"agent_id": ctx["agent"]}, headers=admin_h).json()[0]
            assert any("#6" in p and "link broken" in p for p in report["problems"]), report["problems"]

        step("API starts on a temp database", health)
        step("admin creates an agent; API key stored only as a SHA-256 hash", create_agent)
        step("bad admin token, bad agent key and cross-agent writes are rejected", auth_rejected)
        step("SDK records a run: input, model call, checked tool call, memory write, note, sub-run", sdk_run)
        step("events are stored in order (check -> tool_call -> tool_result) and the run is closed", events_stored)
        step("emails and API-key-like strings are redacted before storage", redaction)
        step("invalid event type, bad tool name and oversized body are rejected", validation)
        step("rate limit returns 429 after 60 requests/min from one caller", rate_limit)
        step("hash chain verifies for every agent", chain_ok)
        step("a tampered event is detected", tamper_detected)
        step("a deleted event is detected", deletion_detected)
        http.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed" + ("" if passed == len(results) else "  <-- FAILURES"))
    try:
        tmp.cleanup()
    except OSError:
        pass  # Windows may still hold the WAL file for a moment; it is a temp dir anyway
    return passed == len(results)
