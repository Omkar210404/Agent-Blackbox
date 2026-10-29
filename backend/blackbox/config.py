"""Settings loaded from the project's .env file (never printed or logged)."""
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("BLACKBOX_HOME") or Path(__file__).resolve().parents[2])
ENV_FILE = ROOT / ".env"


def read_env_text(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def read_env(path: Path = ENV_FILE) -> dict:
    return read_env_text(path.read_text(encoding="utf-8")) if path.exists() else {}


def _flag(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    db_path: Path = ROOT / "data" / "blackbox.db"
    admin_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8377
    redact: bool = True
    max_body_kb: int = 256
    rate_limit_per_min: int = 300
    cors_origins: list = field(default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])

    @classmethod
    def load(cls) -> "Settings":
        env = {**read_env(), **{k: v for k, v in os.environ.items() if k.startswith("BLACKBOX_")}}
        s = cls()
        if env.get("BLACKBOX_DB"):
            p = Path(env["BLACKBOX_DB"])
            s.db_path = p if p.is_absolute() else ROOT / p
        s.admin_token = env.get("BLACKBOX_ADMIN_TOKEN", "")
        s.host = env.get("BLACKBOX_HOST", s.host)
        s.port = int(env.get("BLACKBOX_PORT", s.port))
        s.redact = _flag(env.get("BLACKBOX_REDACT", "true"))
        s.max_body_kb = int(env.get("BLACKBOX_MAX_BODY_KB", s.max_body_kb))
        s.rate_limit_per_min = int(env.get("BLACKBOX_RATE_LIMIT_PER_MIN", s.rate_limit_per_min))
        if "BLACKBOX_CORS_ORIGINS" in env:
            s.cors_origins = [o.strip() for o in env["BLACKBOX_CORS_ORIGINS"].split(",") if o.strip()]
        return s


def ensure_env_file() -> bool:
    """Create .env from .env.example with a fresh admin token. Returns True if a token was written."""
    example = ROOT / ".env.example"
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else (
        example.read_text(encoding="utf-8") if example.exists() else "BLACKBOX_ADMIN_TOKEN=\n")
    if read_env_text(text).get("BLACKBOX_ADMIN_TOKEN"):
        return False
    token = "bba_" + secrets.token_urlsafe(32)
    lines, done = [], False
    for line in text.splitlines():
        if line.strip().startswith("BLACKBOX_ADMIN_TOKEN="):
            line, done = f"BLACKBOX_ADMIN_TOKEN={token}", True
        lines.append(line)
    if not done:
        lines.append(f"BLACKBOX_ADMIN_TOKEN={token}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
