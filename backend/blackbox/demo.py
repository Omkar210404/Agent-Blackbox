"""`blackbox demo`: seed the scripted agents in demo/agents.py, apply their policies and run them.

A background thread plays the shop owner answering approval requests, as if tapping the Telegram buttons.
"""
import copy
import importlib.util
import threading

from blackbox_sdk import Blackbox

from .app import BackgroundServer
from .config import ROOT, Settings
from .db import Store
from .telegram import Telegram


def _load_agents():
    spec = importlib.util.spec_from_file_location("blackbox_demo_agents", ROOT / "demo" / "agents.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _keys(store: Store, specs: dict) -> dict:
    keys = {}
    for slug, spec in specs.items():
        if store.get_agent(spec["agent_id"]):
            keys[slug] = store.rotate_key(spec["agent_id"])  # keys are hashed, so a rerun issues new ones
        else:
            keys[slug] = store.create_agent(**spec)[1]
    return keys


def _simulated_human(control, answers: dict, stop: threading.Event, seen: set):
    """Answers pending approvals like an owner tapping Approve / Block on Telegram."""
    while not stop.wait(0.1):
        for a in control.store.list_approvals(status="pending"):
            if a["id"] in seen or a["tool"] not in answers:
                continue
            seen.add(a["id"])
            telegram = control.notifier.enabled
            print("      " + ("Telegram message sent:" if telegram else
                              "Telegram not configured, simulated message:"))
            for line in Telegram.message_text(a).splitlines():
                print(f"        | {line}")
            if telegram:
                continue  # a real person answers on their phone
            ok = answers[a["tool"]]
            control.decide(a["id"], ok, "Asha (owner, simulated Telegram)", via="telegram (simulated)")
            print(f"      Owner tapped {'Approve' if ok else 'Block'}.")


def run_demo(settings: Settings | None = None) -> None:
    settings = copy.copy(settings or Settings.load())
    settings.approval_timeout_s = 24 * 3600  # demo: the n8n-style refund request waits a day for you
    store, agents = Store(settings.db_path), _load_agents()
    keys = _keys(store, agents.AGENTS)
    print("Blackbox demo: 3 scripted agents, no model, no API keys, nothing leaves this machine.\n")
    stop, seen = threading.Event(), set(a["id"] for a in store.list_approvals(status="pending"))
    with BackgroundServer(settings) as srv:
        control = srv.app.state.control
        for slug, spec in agents.AGENTS.items():
            text = (ROOT / "demo" / "policies" / f"{slug}.yaml").read_text(encoding="utf-8")
            current = store.get_policy(spec["agent_id"])
            if not current or current["yaml"] != text:
                control.set_policy(store.get_agent(spec["agent_id"]), text, "demo setup")
        human = threading.Thread(target=_simulated_human, args=(control, agents.HUMAN_ANSWERS, stop, seen), daemon=True)
        human.start()
        for slug, fn in agents.RUNNERS.items():
            bb = Blackbox(srv.url, keys[slug], poll_interval=0.2, approval_timeout=30)
            fn(bb)
            bb.close()
        agents.n8n_style_refund(srv.url, keys["shop"])
        stop.set()
        human.join(2)
    flags = store.list_flags(limit=1000)
    high = [f for f in flags if f["severity"] == "high"]
    print(f"\nAnomaly flags so far: {len(flags)} ({len(high)} high). Latest high ones:")
    for f in high[:4]:
        print(f"  ! {f['agent_name']}: {f['message']}")
    print()
    for r in store.verify():
        print(f"  chain {'OK    ' if r['ok'] else 'BROKEN'} {r['name']:<22} {r['events']} events")
    print(f"\nDatabase: {settings.db_path}\nOpen the dashboard with: blackbox serve")
