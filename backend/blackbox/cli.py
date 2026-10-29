"""Command line: blackbox init | serve | agents add/list | verify | demo | selftest."""
import argparse
import sys

from .config import ENV_FILE, Settings, ensure_env_file
from .db import Store


def cmd_init(_args):
    wrote = ensure_env_file()
    settings = Settings.load()
    Store(settings.db_path)
    print(f"{'Created' if wrote else 'Kept'} admin token in {ENV_FILE} (BLACKBOX_ADMIN_TOKEN, not shown here).")
    print(f"Database ready: {settings.db_path}")


def cmd_serve(args):
    import uvicorn
    from .app import create_app
    settings = Settings.load()
    if not settings.admin_token:
        sys.exit("No admin token yet. Run `blackbox init` first.")
    host, port = args.host or settings.host, args.port or settings.port
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {host} exposes Blackbox beyond this machine.")
    print(f"Blackbox API on http://{host}:{port}  (health: /health)")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")


def cmd_agents(args):
    store = Store(Settings.load().db_path)
    if args.action == "list":
        for a in store.list_agents():
            print(f"{a['id']}  {a['name']:<24} owner={a['owner'] or '-'}  runs={a['run_count']}  "
                  f"events={a['event_count']}  last={a['last_activity'] or '-'}")
        return
    manifest = {"tools": [t.strip() for t in (args.tools or "").split(",") if t.strip()], "limits": {}}
    agent, key = store.create_agent(args.name, args.owner or "", manifest)
    print(f"Agent created: {agent['id']} ({agent['name']})")
    print(f"API key (shown once, only its hash is stored): {key}")


def cmd_verify(args):
    reports = Store(Settings.load().db_path).verify(args.agent)
    for r in reports:
        print(f"{'OK    ' if r['ok'] else 'BROKEN'} {r['agent_id']}  {r['name']:<24} {r['events']} events")
        for p in r["problems"]:
            print(f"         - {p}")
    if not reports:
        print("No agents recorded yet.")
    sys.exit(0 if all(r["ok"] for r in reports) else 1)


def cmd_demo(_args):
    from .demo import run_demo
    if not Settings.load().admin_token:
        ensure_env_file()
    run_demo()


def cmd_selftest(_args):
    from .selftest import run_selftest
    sys.exit(0 if run_selftest() else 1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="blackbox", description="Flight recorder and control layer for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create .env (admin token) and the database").set_defaults(fn=cmd_init)
    s = sub.add_parser("serve", help="run the API (127.0.0.1 by default)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    a = sub.add_parser("agents", help="add or list agents")
    a.add_argument("action", choices=["add", "list"])
    a.add_argument("--name")
    a.add_argument("--owner")
    a.add_argument("--tools", help="comma-separated tools the agent declares it uses")
    a.set_defaults(fn=cmd_agents)
    v = sub.add_parser("verify", help="recompute the hash chains and report broken links")
    v.add_argument("--agent", help="only this agent id")
    v.set_defaults(fn=cmd_verify)
    sub.add_parser("demo", help="seed 3 scripted agents and run them (offline)").set_defaults(fn=cmd_demo)
    sub.add_parser("selftest", help="end-to-end check on a temp database").set_defaults(fn=cmd_selftest)
    args = p.parse_args(argv)
    if args.cmd == "agents" and args.action == "add" and not args.name:
        p.error("agents add needs --name")
    args.fn(args)


if __name__ == "__main__":
    main()
