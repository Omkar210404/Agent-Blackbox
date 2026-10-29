"""SQLite storage: agents, runs and a per-agent hash-chained event log."""
import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
EVENT_TYPES = ("model_call", "tool_call", "tool_result", "memory_write", "policy_decision",
               "approval_request", "approval_decision", "error", "note")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
  api_key_hash TEXT NOT NULL UNIQUE, manifest TEXT NOT NULL DEFAULT '{}',
  chain_head TEXT NOT NULL DEFAULT '%s', event_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), parent_run_id TEXT REFERENCES runs(id),
  name TEXT NOT NULL, started TEXT NOT NULL, ended TEXT, status TEXT NOT NULL DEFAULT 'running');
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), agent_id TEXT NOT NULL REFERENCES agents(id),
  seq INTEGER NOT NULL, ts TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL,
  previous_hash TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(agent_id, seq));
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id);
""" % GENESIS

_write_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def hash_key(api_key: str) -> str:
    # Keys are 256-bit random values, so a plain SHA-256 is enough (no password stretching needed).
    return hashlib.sha256(api_key.encode()).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def event_hash(e: dict) -> str:
    """SHA-256 over the event's content plus the previous event's hash."""
    parts = [e["id"], e["agent_id"], e["run_id"] or "", e["seq"], e["ts"], e["type"], e["payload"], e["previous_hash"]]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()

    # ---- agents -------------------------------------------------------------
    def create_agent(self, name: str, owner: str = "", manifest: dict | None = None, agent_id: str | None = None):
        api_key = "bbk_" + secrets.token_urlsafe(32)
        row = {"id": agent_id or new_id("agt"), "name": name, "owner": owner, "created": now(),
               "api_key_hash": hash_key(api_key), "manifest": canonical(manifest or {})}
        with self.conn() as c:
            c.execute("INSERT INTO agents (id,name,owner,created,api_key_hash,manifest) "
                      "VALUES (:id,:name,:owner,:created,:api_key_hash,:manifest)", row)
        return self.get_agent(row["id"]), api_key

    def rotate_key(self, agent_id: str) -> str:
        api_key = "bbk_" + secrets.token_urlsafe(32)
        with self.conn() as c:
            c.execute("UPDATE agents SET api_key_hash=? WHERE id=?", (hash_key(api_key), agent_id))
        return api_key

    def agent_by_key(self, api_key: str):
        with self.conn() as c:
            r = c.execute("SELECT * FROM agents WHERE api_key_hash=?", (hash_key(api_key),)).fetchone()
        return self._agent(r)

    def get_agent(self, agent_id: str):
        with self.conn() as c:
            r = c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        return self._agent(r)

    def list_agents(self):
        with self.conn() as c:
            rows = c.execute("""SELECT a.*, (SELECT COUNT(*) FROM runs r WHERE r.agent_id=a.id) AS run_count,
                                (SELECT MAX(ts) FROM events e WHERE e.agent_id=a.id) AS last_activity
                                FROM agents a ORDER BY a.created""").fetchall()
        return [self._agent(r) for r in rows]

    @staticmethod
    def _agent(r):
        if r is None:
            return None
        d = dict(r)
        d.pop("api_key_hash", None)  # never leaves the database
        d["manifest"] = json.loads(d["manifest"])
        return d

    # ---- runs ---------------------------------------------------------------
    def create_run(self, agent_id: str, name: str, parent_run_id: str | None = None):
        rid = new_id("run")
        with self.conn() as c:
            c.execute("INSERT INTO runs (id,agent_id,parent_run_id,name,started) VALUES (?,?,?,?,?)",
                      (rid, agent_id, parent_run_id, name, now()))
        return self.get_run(rid)

    def end_run(self, run_id: str, status: str):
        with self.conn() as c:
            c.execute("UPDATE runs SET ended=?, status=? WHERE id=?", (now(), status, run_id))
        return self.get_run(run_id)

    def get_run(self, run_id: str):
        with self.conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    def list_runs(self, agent_id: str | None = None, limit: int = 100):
        q, args = "SELECT * FROM runs", []
        if agent_id:
            q, args = q + " WHERE agent_id=?", [agent_id]
        with self.conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY started DESC LIMIT ?", (*args, limit))]

    # ---- events -------------------------------------------------------------
    def add_event(self, agent_id: str, run_id: str | None, type_: str, payload: dict):
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        with _write_lock, self.conn() as c:
            c.execute("BEGIN IMMEDIATE")  # also serialises writers in other processes
            try:
                head = c.execute("SELECT chain_head, event_count FROM agents WHERE id=?", (agent_id,)).fetchone()
                e = {"id": new_id("evt"), "agent_id": agent_id, "run_id": run_id, "seq": head["event_count"] + 1,
                     "ts": now(), "type": type_, "payload": canonical(payload), "previous_hash": head["chain_head"]}
                e["hash"] = event_hash(e)
                c.execute("INSERT INTO events (id,run_id,agent_id,seq,ts,type,payload,previous_hash,hash) "
                          "VALUES (:id,:run_id,:agent_id,:seq,:ts,:type,:payload,:previous_hash,:hash)", e)
                c.execute("UPDATE agents SET chain_head=?, event_count=? WHERE id=?", (e["hash"], e["seq"], agent_id))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return {**e, "payload": payload}

    def list_events(self, run_id=None, agent_id=None, type_=None, limit: int = 500):
        q, where, args = "SELECT * FROM events", [], []
        for col, val in (("run_id", run_id), ("agent_id", agent_id), ("type", type_)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        if where:
            q += " WHERE " + " AND ".join(where)
        with self.conn() as c:
            rows = c.execute(q + " ORDER BY ts, seq LIMIT ?", (*args, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    # ---- verification -------------------------------------------------------
    def verify(self, agent_id: str | None = None):
        """Recompute every agent's chain. Returns one report per agent."""
        reports = []
        with self.conn() as c:
            agents = c.execute("SELECT id,name,chain_head,event_count FROM agents" +
                               (" WHERE id=?" if agent_id else "") + " ORDER BY created",
                               (agent_id,) if agent_id else ()).fetchall()
            for a in agents:
                problems, prev, expected_seq, n = [], GENESIS, 1, 0
                for r in c.execute("SELECT * FROM events WHERE agent_id=? ORDER BY seq", (a["id"],)):
                    e, n = dict(r), n + 1
                    where = f"event #{e['seq']} ({e['id']}, {e['type']})"
                    if e["seq"] != expected_seq:
                        problems.append(f"{where}: events #{expected_seq}..#{e['seq'] - 1} are missing")
                    if e["previous_hash"] != prev:
                        problems.append(f"{where}: link broken, previous_hash does not match the event before it")
                    if event_hash(e) != e["hash"]:
                        problems.append(f"{where}: content changed after it was recorded (hash mismatch)")
                    prev, expected_seq = e["hash"], e["seq"] + 1
                if prev != a["chain_head"] or n != a["event_count"]:
                    problems.append(f"chain head mismatch: expected {a['event_count']} events, found {n} "
                                    "(events at the end were removed or added outside Blackbox)")
                reports.append({"agent_id": a["id"], "name": a["name"], "events": n,
                                "ok": not problems, "problems": problems})
        return reports
