import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Set explicitly so a stray ANTHROPIC_BASE_URL in the shell can't hijack the app's client.
ANTHROPIC_API_BASE = os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com")
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-opus-5-5")
AGENT_EFFORT = os.getenv("AGENT_EFFORT", "medium")  # low | medium | high | xhigh | max
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "12"))

CURRENCY = os.getenv("CURRENCY", "gbp")
MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "local")  # local | cognee
POLICY_FILE = ROOT / os.getenv("POLICY_FILE", "policy.yaml")

DATA_DIR = ROOT / "data"
TRIGGERS_DIR = DATA_DIR / "triggers"
DB_PATH = DATA_DIR / "audit.db"
MEMORY_PATH = DATA_DIR / "memory.jsonl"
WEB_DIR = ROOT / "web"

PORT = int(os.getenv("PORT", "8000"))


def assert_test_mode() -> None:
    """Rule 3 of the event: test mode only. Refuse to run against a live key."""
    if STRIPE_SECRET_KEY and not STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")):
        raise SystemExit("STRIPE_SECRET_KEY is not a test-mode key. Test mode only.")
