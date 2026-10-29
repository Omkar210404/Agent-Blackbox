"""Command line: blackbox init | serve | agents | policy | verify | report | demo | selftest | scanner-eval."""
import argparse
import importlib.util
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from .config import ENV_FILE, ROOT, Settings, ensure_env_file
from .db import Store


def cmd_init(_args):
    wrote = ensure_env_file()
    settings = Settings.load()
    Store(settings.db_path)
    print(f"{'Created' if wrote else 'Kept'} admin token in {ENV_FILE} (BLACKBOX_ADMIN_TOKEN, not shown here).")
    print(f"Database ready: {settings.db_path}")


def cmd_serve(args):
    import uvicorn
    from .app import DIST, create_app
    settings = Settings.load()
    if not settings.admin_token:
        sys.exit("No admin token yet. Run `blackbox init` first.")
    host, port = args.host or settings.host, args.port or settings.port
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {host} exposes Blackbox beyond this machine.")
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    if not (DIST / "index.html").exists():
        print("Dashboard not built yet: cd dashboard && npm install && npm run build")
    elif not args.no_browser:
        # The token travels in the URL fragment (never sent to the server, never printed); the page removes it.
        threading.Timer(1.5, webbrowser.open, [f"{url}/#token={settings.admin_token}"]).start()
        print("Opening the dashboard in your browser (signed in automatically).")
    print(f"Blackbox on {url}  (dashboard: /, API: /v1, health: /health)")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")


def cmd_agents(args):
    store = Store(Settings.load().db_path)
    if args.action == "list":
        for a in store.list_agents():
            print(f"{a['id']}  {a['name']:<24} owner={a['owner'] or '-'}  runs={a['run_count']}  "
                  f"events={a['event_count']}  paused={a['paused']}  last={a['last_activity'] or '-'}")
        return
    manifest = {"tools": [t.strip() for t in (args.tools or "").split(",") if t.strip()], "limits": {}}
    agent, key = store.create_agent(args.name, args.owner or "", manifest)
    print(f"Agent created: {agent['id']} ({agent['name']})")
    print(f"API key (shown once, only its hash is stored): {key}")


def cmd_policy(args):
    from .control import Control
    from .policy import PolicyError
    settings = Settings.load()
    store = Store(settings.db_path)
    agent = store.get_agent(args.agent) or sys.exit(f"No agent {args.agent}")
    try:
        Control(store, settings).set_policy(agent, Path(args.file).read_text(encoding="utf-8"), "cli")
    except PolicyError as exc:
        print("Policy NOT saved:")
        for e in exc.errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"Policy saved for {agent['name']} (recorded in its log).")


def cmd_verify(args):
    reports = Store(Settings.load().db_path).verify(args.agent)
    for r in reports:
        print(f"{'OK    ' if r['ok'] else 'BROKEN'} {r['agent_id']}  {r['name']:<24} {r['events']} events")
        for p in r["problems"]:
            print(f"         - {p}")
    if not reports:
        print("No agents recorded yet.")
    sys.exit(0 if all(r["ok"] for r in reports) else 1)


def cmd_report(args):
    from .views import report
    store = Store(Settings.load().db_path)
    agent = store.get_agent(args.agent) or sys.exit(f"No agent {args.agent}")
    today = date.today().isoformat()
    text = report(store, agent, args.start or today, args.end or today, args.format)
    out = Path(args.out or f"report-{agent['id']}.{args.format}")
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}")


def cmd_demo(_args):
    from .demo import run_demo
    if not Settings.load().admin_token:
        ensure_env_file()
    run_demo()


def cmd_selftest(_args):
    from .selftest import run_selftest
    sys.exit(0 if run_selftest() else 1)


def cmd_scanner_eval(_args):
    from .scanner import scan
    spec = importlib.util.spec_from_file_location("samples", ROOT / "demo" / "scanner_samples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    caught = [t for t in mod.INJECTIONS if scan(t)["flagged"]]
    false_pos = [t for t in mod.BENIGN if scan(t)["flagged"]]
    print("Scanner on SYNTHETIC samples (hand-written, see demo/scanner_samples.py). Flagged = medium or high.\n")
    print(f"Injection-style texts flagged: {len(caught)}/{len(mod.INJECTIONS)}")
    print(f"Benign texts wrongly flagged:  {len(false_pos)}/{len(mod.BENIGN)}\n")
    print("Missed injections:")
    for t in mod.INJECTIONS:
        if t not in caught:
            print(f"  - {t[:100]}")
    print("\nFalse positives:")
    for t in false_pos:
        print(f"  - {t[:80]!r}  -> {scan(t)['hits'][0]['rule']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="blackbox", description="Flight recorder and control layer for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create .env (admin token) and the database").set_defaults(fn=cmd_init)
    s = sub.add_parser("serve", help="run the API and dashboard (127.0.0.1 by default)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--no-browser", action="store_true", help="do not open the dashboard")
    s.set_defaults(fn=cmd_serve)
    a = sub.add_parser("agents", help="add or list agents")
    a.add_argument("action", choices=["add", "list"])
    a.add_argument("--name")
    a.add_argument("--owner")
    a.add_argument("--tools", help="comma-separated tools the agent declares it uses")
    a.set_defaults(fn=cmd_agents)
    po = sub.add_parser("policy", help="set an agent's policy from a YAML file")
    po.add_argument("action", choices=["set"])
    po.add_argument("--agent", required=True)
    po.add_argument("--file", required=True)
    po.set_defaults(fn=cmd_policy)
    v = sub.add_parser("verify", help="recompute the hash chains and report broken links")
    v.add_argument("--agent", help="only this agent id")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("report", help="export an audit report (md or printable html)")
    r.add_argument("--agent", required=True)
    r.add_argument("--start", help="YYYY-MM-DD (default today, UTC)")
    r.add_argument("--end", help="YYYY-MM-DD (default today, UTC)")
    r.add_argument("--format", choices=["md", "html"], default="md")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_report)
    sub.add_parser("demo", help="seed 3 scripted agents and run them (offline)").set_defaults(fn=cmd_demo)
    sub.add_parser("selftest", help="end-to-end check on a temp database").set_defaults(fn=cmd_selftest)
    sub.add_parser("scanner-eval", help="measure the injection scanner on synthetic samples").set_defaults(
        fn=cmd_scanner_eval)
    args = p.parse_args(argv)
    if args.cmd == "agents" and args.action == "add" and not args.name:
        p.error("agents add needs --name")
    args.fn(args)


if __name__ == "__main__":
    main()
