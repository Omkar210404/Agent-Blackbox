"""SQLite storage: agents, runs and a per-agent hash-chained event log."""
import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
CREATE INDEX IF NOT EXISTS idx_events_agent_type ON events(agent_id, type, ts);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id);
CREATE TABLE IF NOT EXISTS policies (agent_id TEXT PRIMARY KEY REFERENCES agents(id), yaml TEXT NOT NULL,
  updated TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), run_id TEXT, kind TEXT NOT NULL,
  tool TEXT NOT NULL DEFAULT '', args TEXT NOT NULL DEFAULT '{}', summary TEXT NOT NULL, reasons TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL, expires TEXT NOT NULL, notified TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS flags (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), run_id TEXT, event_id TEXT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_flags_agent ON flags(agent_id, ts);
""" % GENESIS
AGENT_COLUMNS = {"paused": "INTEGER NOT NULL DEFAULT 0", "client_token_hash": "TEXT"}

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
            have = {r["name"] for r in c.execute("PRAGMA table_info(agents)")}
            for col, ddl in AGENT_COLUMNS.items():
                if col not in have:  # upgrade databases made by older versions
                    c.execute(f"ALTER TABLE agents ADD COLUMN {col} {ddl}")

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
        d.pop("api_key_hash", None)  # secrets never leave the database
        d["has_client_link"] = bool(d.pop("client_token_hash", None))
        d["manifest"] = json.loads(d["manifest"])
        d["paused"] = bool(d.get("paused"))
        return d

    def set_paused(self, agent_id: str, paused: bool):
        with self.conn() as c:
            c.execute("UPDATE agents SET paused=? WHERE id=?", (int(paused), agent_id))

    def new_client_token(self, agent_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.conn() as c:
            c.execute("UPDATE agents SET client_token_hash=? WHERE id=?", (hash_key(token), agent_id))
        return token

    def agent_by_client_token(self, token: str):
        with self.conn() as c:
            r = c.execute("SELECT * FROM agents WHERE client_token_hash=?", (hash_key(token),)).fetchone()
        return self._agent(r)

    # ---- policies -------------------------------------------------------------
    def get_policy(self, agent_id: str):
        with self.conn() as c:
            r = c.execute("SELECT * FROM policies WHERE agent_id=?", (agent_id,)).fetchone()
        return dict(r) if r else None

    def set_policy(self, agent_id: str, text: str, by: str):
        with self.conn() as c:
            c.execute("INSERT INTO policies (agent_id,yaml,updated,updated_by) VALUES (?,?,?,?) ON CONFLICT(agent_id) "
                      "DO UPDATE SET yaml=excluded.yaml, updated=excluded.updated, updated_by=excluded.updated_by",
                      (agent_id, text, now(), by))

    # ---- approvals ------------------------------------------------------------
    def create_approval(self, agent_id, run_id, kind, tool, args, summary, reasons, timeout_s):
        aid = new_id("apr")
        created = datetime.now(timezone.utc)
        with self.conn() as c:
            c.execute("INSERT INTO approvals (id,agent_id,run_id,kind,tool,args,summary,reasons,created,expires) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (aid, agent_id, run_id, kind, tool, canonical(args), summary, canonical(reasons),
                       created.isoformat(timespec="milliseconds"),
                       (created + timedelta(seconds=timeout_s)).isoformat(timespec="milliseconds")))
        return self.get_approval(aid)

    def get_approval(self, approval_id: str):
        with self.conn() as c:
            r = c.execute("SELECT a.*, g.name AS agent_name FROM approvals a JOIN agents g ON g.id=a.agent_id "
                          "WHERE a.id=?", (approval_id,)).fetchone()
        return self._approval(r)

    def list_approvals(self, status: str | None = None, agent_id: str | None = None, limit: int = 200):
        q, where, args = ("SELECT a.*, g.name AS agent_name FROM approvals a JOIN agents g ON g.id=a.agent_id", [], [])
        if status:
            where.append("a.status=?"); args.append(status)
        if agent_id:
            where.append("a.agent_id=?"); args.append(agent_id)
        q += (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.created DESC LIMIT ?"
        with self.conn() as c:
            return [self._approval(r) for r in c.execute(q, (*args, limit))]

    def update_approval(self, approval_id: str, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.conn() as c:
            c.execute(f"UPDATE approvals SET {sets} WHERE id=?", (*cols.values(), approval_id))
        return self.get_approval(approval_id)

    def decide_approval(self, approval_id: str, status: str, by: str, note: str = ""):
        """Atomically move a pending approval to a final status. Returns None if it was already decided."""
        with self.conn() as c:
            cur = c.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, note=? "
                            "WHERE id=? AND status='pending'", (status, by, now(), note, approval_id))
            changed = cur.rowcount
        return self.get_approval(approval_id) if changed else None

    @staticmethod
    def _approval(r):
        if r is None:
            return None
        d = dict(r)
        d["args"], d["reasons"] = json.loads(d["args"]), json.loads(d["reasons"])
        return d

    # ---- flags ----------------------------------------------------------------
    def add_flag(self, agent_id, run_id, event_id, kind, severity, message):
        f = {"id": new_id("flg"), "agent_id": agent_id, "run_id": run_id, "event_id": event_id, "ts": now(),
             "kind": kind, "severity": severity, "message": message}
        with self.conn() as c:
            c.execute("INSERT INTO flags VALUES (:id,:agent_id,:run_id,:event_id,:ts,:kind,:severity,:message)", f)
        return f

    def list_flags(self, agent_id=None, run_id=None, since=None, limit: int = 500):
        q, where, args = "SELECT f.*, g.name AS agent_name FROM flags f JOIN agents g ON g.id=f.agent_id", [], []
        for col, val, op in (("f.agent_id", agent_id, "="), ("f.run_id", run_id, "="), ("f.ts", since, ">=")):
            if val:
                where.append(f"{col}{op}?"); args.append(val)
        q += (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY f.ts DESC LIMIT ?"
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, (*args, limit))]

    def recent_flag(self, agent_id: str, kind: str, since: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM flags WHERE agent_id=? AND kind=? AND ts>=? LIMIT 1",
                             (agent_id, kind, since)).fetchone() is not None

    # ---- counters used by policies and anomaly rules ----------------------------
    def count_decisions(self, agent_id: str, since: str, tool: str | None = None, decision: str | None = "allow") -> int:
        q = "SELECT COUNT(*) FROM events WHERE agent_id=? AND type='policy_decision' AND ts>=?"
        args = [agent_id, since]
        if tool:
            q += " AND json_extract(payload,'$.tool')=?"; args.append(tool)
        if decision:
            q += " AND json_extract(payload,'$.decision')=?"; args.append(decision)
        with self.conn() as c:
            return c.execute(q, args).fetchone()[0]

    def spent_since(self, agent_id: str, since: str) -> float:
        with self.conn() as c:
            return c.execute("SELECT COALESCE(SUM(json_extract(payload,'$.amount')),0) FROM events WHERE agent_id=? "
                             "AND type='policy_decision' AND json_extract(payload,'$.decision')='allow' AND ts>=?",
                             (agent_id, since)).fetchone()[0]

    def tool_seen_before(self, agent_id: str, tool: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM events WHERE agent_id=? AND type='policy_decision' "
                             "AND json_extract(payload,'$.tool')=? LIMIT 1", (agent_id, tool)).fetchone() is not None

    def run_inputs(self, run_id: str, since: str):
        """Untrusted inputs recorded in a run (with their scan), newest first."""
        with self.conn() as c:
            rows = c.execute("SELECT payload FROM events WHERE run_id=? AND type='note' AND ts>=? "
                             "AND json_extract(payload,'$.kind')='input' ORDER BY seq DESC", (run_id, since)).fetchall()
        return [json.loads(r[0]) for r in rows]

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
