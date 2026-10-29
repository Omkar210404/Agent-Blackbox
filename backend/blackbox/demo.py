"""`blackbox demo`: seed the scripted agents in demo/agents.py and run them against the real API."""
import importlib.util

from blackbox_sdk import Blackbox

from .app import BackgroundServer
from .config import ROOT, Settings
from .db import Store


def _load_agents():
    spec = importlib.util.spec_from_file_location("blackbox_demo_agents", ROOT / "demo" / "agents.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _keys(store: Store, AGENTS: dict) -> dict:
    keys = {}
    for slug, spec in AGENTS.items():
        if store.get_agent(spec["agent_id"]):
            keys[slug] = store.rotate_key(spec["agent_id"])  # keys are hashed, so a rerun issues new ones
        else:
            keys[slug] = store.create_agent(**spec)[1]
    return keys


def run_demo(settings: Settings | None = None) -> None:
    settings = settings or Settings.load()
    store, agents = Store(settings.db_path), _load_agents()
    keys = _keys(store, agents.AGENTS)
    print("Blackbox demo: 3 scripted agents, no model, no API keys, nothing leaves this machine.\n")
    with BackgroundServer(settings) as srv:
        for slug, fn in agents.RUNNERS.items():
            bb = Blackbox(srv.url, keys[slug])
            fn(bb)
            bb.close()
    print("\nPhase 1 records everything but enforces nothing yet: the hijacked send_email to")
    print("rival-supplies.example was ALLOWED and recorded. Policies that block it come next.\n")
    for r in store.verify():
        print(f"  chain {'OK    ' if r['ok'] else 'BROKEN'} {r['name']:<22} {r['events']} events")
    print(f"\nDatabase: {settings.db_path}")
