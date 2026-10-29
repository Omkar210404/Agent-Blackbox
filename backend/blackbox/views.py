"""Read-side views: overview, agent map, run timeline, client summary and the audit report."""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape

from .control import describe, iso
from .db import Store
from .scanner import worse


def _rows(store: Store, sql: str, *args):
    with store.conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def overview(store: Store, pending: list) -> dict:
    now = datetime.now(timezone.utc)
    since = iso(now - timedelta(hours=24))
    chains = {r["agent_id"]: r for r in store.verify()}
    flags = store.list_flags(since=since)
    hours = [(now - timedelta(hours=23 - i)).strftime("%Y-%m-%dT%H") for i in range(24)]
    buckets = defaultdict(lambda: defaultdict(int))
    for r in _rows(store, "SELECT agent_id, substr(ts,1,13) h, type, COUNT(*) n FROM events WHERE ts>=? "
                          "GROUP BY agent_id, h, type", since):
        buckets[r["h"]]["events"] += r["n"]
        buckets[r["h"]][r["agent_id"]] += r["n"]
    denied = {r["agent_id"]: r["n"] for r in _rows(
        store, "SELECT agent_id, COUNT(*) n FROM events WHERE type='policy_decision' AND ts>=? "
               "AND json_extract(payload,'$.decision')='deny' GROUP BY agent_id", since)}
    agents = []
    for a in store.list_agents():
        mine = [f for f in flags if f["agent_id"] == a["id"]]
        waiting = [p for p in pending if p["agent_id"] == a["id"]]
        top = "none"
        for f in mine:
            top = worse(top, f["severity"])
        chain_ok = chains.get(a["id"], {}).get("ok", True)
        health = ("paused" if a["paused"] else "broken" if not chain_ok else
                  "attention" if waiting or top == "high" or denied.get(a["id"]) else "ok")
        agents.append({**a, "health": health, "chain_ok": chain_ok, "open_flags": len(mine), "top_flag": top,
                       "pending_approvals": len(waiting), "denied_24h": denied.get(a["id"], 0),
                       "activity": [buckets[h][a["id"]] for h in hours]})
    return {"agents": agents, "hours": [h[11:13] + ":00" for h in hours],
            "activity": [buckets[h]["events"] for h in hours],
            "totals": {"agents": len(agents), "events_24h": sum(buckets[h]["events"] for h in hours),
                       "pending": len(pending), "flags_24h": len(flags), "denied_24h": sum(denied.values()),
                       "chains_ok": all(r["ok"] for r in chains.values())},
            "recent_flags": flags[:8]}


def agent_map(store: Store) -> dict:
    agents = store.list_agents()
    nodes, edges = [], []
    for a in agents:
        nodes.append({"id": a["id"], "kind": "agent", "label": a["name"], "count": a["event_count"],
                      "meta": {"owner": a["owner"], "paused": a["paused"]}})
    tools = defaultdict(lambda: {"count": 0, "allow": 0, "deny": 0, "needs_approval": 0, "last": ""})
    for r in _rows(store, "SELECT agent_id, json_extract(payload,'$.tool') t, json_extract(payload,'$.decision') d, "
                          "COUNT(*) n, MAX(ts) last FROM events WHERE type='policy_decision' GROUP BY agent_id, t, d"):
        t = tools[(r["agent_id"], r["t"])]
        t["count"] += r["n"]
        t[r["d"]] = t.get(r["d"], 0) + r["n"]
        t["last"] = max(t["last"], r["last"])
    for (aid, tool), t in tools.items():
        nid = f"tool:{aid}:{tool}"
        nodes.append({"id": nid, "kind": "tool", "label": tool, "count": t["count"], "agent_id": aid, "meta": t})
        edges.append({"source": aid, "target": nid, "count": t["count"], "denied": t["deny"], "last": t["last"]})
    sources = defaultdict(lambda: {"count": 0, "severity": "none", "last": ""})
    for r in _rows(store, "SELECT agent_id, json_extract(payload,'$.source') s, json_extract(payload,'$.scan.severity') sev, "
                          "COUNT(*) n, MAX(ts) last FROM events WHERE type='note' AND json_extract(payload,'$.kind')='input' "
                          "GROUP BY agent_id, s, sev"):
        s = sources[(r["agent_id"], r["s"] or "input")]
        s["count"] += r["n"]
        s["severity"] = worse(s["severity"], r["sev"] or "none")
        s["last"] = max(s["last"], r["last"])
    for (aid, src), s in sources.items():
        nid = f"src:{aid}:{src}"
        nodes.append({"id": nid, "kind": "source", "label": src.replace("_", " "), "count": s["count"],
                      "agent_id": aid, "meta": s})
        edges.append({"source": nid, "target": aid, "count": s["count"], "denied": 0, "last": s["last"],
                      "risk": s["severity"]})
    for r in _rows(store, "SELECT agent_id, COUNT(*) n, MAX(ts) last FROM events WHERE type='memory_write' GROUP BY agent_id"):
        nid = f"mem:{r['agent_id']}"
        nodes.append({"id": nid, "kind": "memory", "label": "Memory", "count": r["n"], "agent_id": r["agent_id"],
                      "meta": {"last": r["last"]}})
        edges.append({"source": r["agent_id"], "target": nid, "count": r["n"], "denied": 0, "last": r["last"]})
    humans = defaultdict(int)
    for r in _rows(store, "SELECT agent_id, decided_by, COUNT(*) n, MAX(decided_at) last FROM approvals "
                          "WHERE decided_by IS NOT NULL AND decided_by NOT LIKE 'system%' GROUP BY agent_id, decided_by"):
        nid = f"human:{r['decided_by']}"
        humans[nid] += r["n"]
        edges.append({"source": nid, "target": r["agent_id"], "count": r["n"], "denied": 0, "last": r["last"]})
    for nid, n in humans.items():
        nodes.append({"id": nid, "kind": "human", "label": nid[6:], "count": n, "meta": {}})
    for r in _rows(store, "SELECT p.agent_id pa, c.agent_id ca, COUNT(*) n FROM runs c JOIN runs p ON p.id=c.parent_run_id "
                          "WHERE p.agent_id != c.agent_id GROUP BY pa, ca"):
        edges.append({"source": r["pa"], "target": r["ca"], "count": r["n"], "denied": 0, "last": "", "sub": True})
    return {"nodes": nodes, "edges": edges}


def run_timeline(store: Store, run_id: str):
    run = store.get_run(run_id)
    if not run:
        return None
    agent = store.get_agent(run["agent_id"])
    events = store.list_events(run_id=run_id, limit=5000)
    prev = _rows(store, "SELECT * FROM runs WHERE agent_id=? AND started<? ORDER BY (name=?) DESC, started DESC LIMIT 1",
                 run["agent_id"], run["started"], run["name"])
    children = _rows(store, "SELECT * FROM runs WHERE parent_run_id=? ORDER BY started", run_id)
    parent = store.get_run(run["parent_run_id"]) if run["parent_run_id"] else None
    return {"run": run, "agent": agent, "events": events, "flags": store.list_flags(run_id=run_id),
            "approvals": [a for a in store.list_approvals(agent_id=run["agent_id"]) if a["run_id"] == run_id],
            "children": children, "parent": parent,
            "previous": ({"run": prev[0], "events": store.list_events(run_id=prev[0]["id"], limit=5000)} if prev else None)}


def _day_bounds(day: str | None, tz_offset_min: int):
    offset = timedelta(minutes=tz_offset_min)
    local_now = datetime.now(timezone.utc) + offset
    d = datetime.strptime(day, "%Y-%m-%d") if day else local_now.replace(tzinfo=None)
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - offset
    return iso(start), iso(start + timedelta(days=1)), start + offset


def client_summary(store: Store, agent: dict, day: str | None, tz_offset_min: int = 0) -> dict:
    """Plain-language summary for the business owner, built from templates over real events."""
    start, end, local_day = _day_bounds(day, tz_offset_min)
    evs = _rows(store, "SELECT * FROM events WHERE agent_id=? AND ts>=? AND ts<? ORDER BY seq", agent["id"], start, end)
    for e in evs:
        e["payload"] = json.loads(e["payload"])
    checks = [e for e in evs if e["type"] == "policy_decision"]
    done = [e for e in evs if e["type"] == "tool_result" and e["payload"].get("ok")]
    blocked = [{"what": describe(agent["name"], e["payload"]["tool"], e["payload"].get("args") or {}),
                "why": e["payload"].get("reason", "")} for e in checks if e["payload"].get("decision") == "deny"]
    approvals = [a for a in store.list_approvals(agent_id=agent["id"]) if start <= a["created"] < end]
    outcome = {"approved": "You (or your team) approved it.", "denied": "It was blocked by a person.",
               "expired": "Nobody answered in time, so it was blocked automatically.", "pending": "Still waiting for an answer."}
    asked = [{"what": a["summary"], "outcome": outcome.get(a["status"], a["status"]), "status": a["status"],
              "by": a["decided_by"] or ""} for a in approvals if a["kind"] == "tool_call"]
    tools = defaultdict(int)
    for e in done:
        tools[e["payload"].get("tool", "?")] += 1
    warnings = [f["message"] for f in store.list_flags(agent_id=agent["id"], since=start)
                if f["severity"] == "high" and f["ts"] < end]
    pause_pending = any(a["kind"] == "pause_request" and a["status"] == "pending"
                        for a in store.list_approvals(agent_id=agent["id"], status="pending"))
    return {"agent": {"name": agent["name"], "owner": agent["owner"], "paused": agent["paused"]},
            "day": local_day.strftime("%A %d %B %Y"), "actions": len(done), "checked": len(checks),
            "by_tool": dict(tools), "asked": asked, "blocked": blocked, "warnings": warnings[:5],
            "memory_writes": sum(1 for e in evs if e["type"] == "memory_write"), "pause_pending": pause_pending}


def report(store: Store, agent: dict, start_day: str, end_day: str, fmt: str = "md") -> str:
    start, _, _ = _day_bounds(start_day, 0)
    _, end, _ = _day_bounds(end_day, 0)
    evs = store.list_events(agent_id=agent["id"], limit=100000)
    evs = [e for e in evs if start <= e["ts"] < end]
    chain = store.verify(agent["id"])[0]
    decisions = [e for e in evs if e["type"] == "policy_decision"]
    counts = defaultdict(int)
    for e in decisions:
        counts[e["payload"].get("decision")] += 1
    approvals = [a for a in store.list_approvals(agent_id=agent["id"], limit=10000) if start <= a["created"] < end]
    flags = [f for f in store.list_flags(agent_id=agent["id"], limit=10000) if start <= f["ts"] < end]
    key = [e for e in evs if e["type"] in ("tool_call", "memory_write", "error") or
           (e["type"] == "policy_decision" and e["payload"].get("decision") != "allow") or
           (e["type"] == "note" and e["payload"].get("kind") in ("policy_change", "permission_change"))]
    policy = store.get_policy(agent["id"])

    def line(e):
        p = e["payload"]
        if e["type"] == "policy_decision":
            return f"{p.get('decision', '').upper()} {p.get('tool')}: {p.get('reason', '')}"
        if e["type"] == "tool_call":
            return f"called {p.get('tool')} with {json.dumps(p.get('args', {}), ensure_ascii=False)[:160]}"
        if e["type"] == "memory_write":
            return f"memory '{p.get('key')}' = {str(p.get('value'))[:120]}"
        return p.get("text") or p.get("message") or json.dumps(p)[:160]

    rows = {
        "summary": [("Agent", f"{agent['name']} ({agent['id']})"), ("Owner", agent["owner"] or "-"),
                    ("Period (UTC)", f"{start_day} to {end_day}"), ("Events", len(evs)),
                    ("Tool calls checked", len(decisions)),
                    ("Allowed / needed approval / denied",
                     f"{counts['allow']} / {counts['needs_approval']} / {counts['deny']}"),
                    ("Approvals", len(approvals)), ("Flags", len(flags)),
                    ("Policy", f"last changed {policy['updated'][:16]} by {policy['updated_by']}" if policy else "none set"),
                    ("Generated", iso(datetime.now(timezone.utc))[:19] + " UTC")],
        "approvals": [(a["created"][:19], a["summary"], a["status"], a["decided_by"] or "-", (a["decided_at"] or "-")[:19])
                      for a in approvals],
        "flags": [(f["ts"][:19], f["severity"], f["kind"], f["message"]) for f in flags],
        "key": [(e["ts"][:19], f"#{e['seq']}", e["type"], line(e)) for e in key[:400]],
    }
    verdict = (f"PASSED: {chain['events']} events, every hash link verified." if chain["ok"]
               else f"FAILED: {len(chain['problems'])} problem(s).")
    if fmt == "html":
        return _report_html(agent, rows, verdict, chain)
    out = [f"# Audit report: {agent['name']}", "", "| | |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows["summary"]]
    out += ["", "## Hash-chain verification", "", verdict] + [f"- {p}" for p in chain["problems"]]
    for title, key_, head in (("Approvals", "approvals", "| Requested | What | Outcome | Decided by | When |"),
                              ("Flags", "flags", "| Time | Severity | Kind | Message |"),
                              ("Key events", "key", "| Time | Seq | Type | Detail |")):
        out += ["", f"## {title}", ""]
        if not rows[key_]:
            out.append("None in this period.")
            continue
        out += [head, "|" + "---|" * (head.count("|") - 1)]
        out += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |" for r in rows[key_]]
    return "\n".join(out) + "\n"


def _report_html(agent, rows, verdict, chain) -> str:
    def table(head, data):
        if not data:
            return "<p class='muted'>None in this period.</p>"
        th = "".join(f"<th>{escape(h)}</th>" for h in head)
        trs = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in data)
        return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"

    ok = chain["ok"]
    problems = "".join(f"<li>{escape(p)}</li>" for p in chain["problems"])
    summary = "".join(f"<div><dt>{escape(k)}</dt><dd>{escape(str(v))}</dd></div>" for k, v in rows["summary"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit report: {escape(agent['name'])}</title><style>
:root{{--bg:#070a12;--card:#0e1320;--ink:#e6eaf2;--muted:#8c96a8;--line:#1e2636;--ok:#34d399;--bad:#f87171;--accent:#7dd3fc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,sans-serif;padding:32px 16px}}
main{{max-width:1000px;margin:0 auto}}h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:16px;margin:32px 0 10px;color:var(--accent)}}
.muted{{color:var(--muted)}}dl{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:20px 0}}
dl div{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}dt{{color:var(--muted);font-size:12px}}dd{{margin:0;font-weight:600}}
.verdict{{padding:12px 14px;border-radius:10px;border:1px solid;font-weight:600}}.ok{{color:var(--ok);border-color:var(--ok)}}.bad{{color:var(--bad);border-color:var(--bad)}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-weight:600}}td{{word-break:break-word}}.print{{float:right;background:var(--accent);color:#04121c;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}}
@media print{{body{{background:#fff;color:#111;padding:0;font-size:11px}}dl div{{background:#fff;border-color:#ccc}}h2{{color:#111}}
th,td{{border-color:#ddd}}.muted,th,dt{{color:#555}}.print{{display:none}}.ok{{color:#0a7a4f}}.bad{{color:#b00020}}tr{{break-inside:avoid}}}}
</style></head><body><main>
<button class="print" onclick="print()">Save as PDF</button>
<h1>Audit report: {escape(agent['name'])}</h1><div class="muted">Blackbox flight recorder · {escape(agent['id'])}</div>
<dl>{summary}</dl>
<h2>Hash-chain verification</h2><div class="verdict {'ok' if ok else 'bad'}">{'&#10003;' if ok else '&#10007;'} {escape(verdict)}</div><ul>{problems}</ul>
<h2>Approvals</h2>{table(["Requested", "What", "Outcome", "Decided by", "When"], rows["approvals"])}
<h2>Flags</h2>{table(["Time", "Severity", "Kind", "Message"], rows["flags"])}
<h2>Key events</h2>{table(["Time", "Seq", "Type", "Detail"], rows["key"])}
</main></body></html>"""
