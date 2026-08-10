"""
Configuration. For local testing, paste values in .env (never commit it).
On Render (hosting), set these as Environment Variables instead.
Values read from the environment always win over .env.
"""

import os

# Load .env if it exists (keeps the Groq key out of your shell history).
# python-dotenv is optional — if missing, we just rely on real env vars.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from zoneinfo import ZoneInfo

# ── Required ─────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# ── Vision (photos) — Gemini free tier. Groq has no vision model. ──
# Get a free key at https://aistudio.google.com/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Accepts GEMINI_VISION_MODEL or legacy GEMINI_MODEL env var.
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
# ── Simple passcode so only you can use the app ──────────────
# Set a value you'll remember in .env. Leave "" to disable the lock (not recommended when hosted).
APP_PASSCODE = os.getenv("APP_PASSCODE", "")

# Secret for signing the login session cookie. Set a long random string in .env when hosting.
SECRET_KEY = os.getenv("SECRET_KEY", "x7k2mq9vBn4pLw8sReT3yUicjHf6aZdQ0oM5")

# ── Local settings ───────────────────────────────────────────
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Asia/Kolkata"))
DB_PATH = os.getenv("DB_PATH", "health.db")

# ── Daily targets (0 = hide ring/bar) ────────────────────────
DAILY_CAL_TARGET = int(os.getenv("DAILY_CAL_TARGET", "2000"))
DAILY_PROTEIN_TARGET = int(os.getenv("DAILY_PROTEIN_TARGET", "120"))
# Hydration goal
WATER_TARGET_ML = int(os.getenv("WATER_TARGET_ML", "2500"))

# ── Local AI (Ollama) — free & unlimited, used for plain text ──
# Runs a small model on your own machine: no API key, no quotas,
# unlimited logs. Groq stays as the automatic fallback for
# photos/voice or whenever the local model is unavailable.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "90"))
# Set OLLAMA_PRIMARY=0 to always use Groq for text too.
OLLAMA_PRIMARY = os.getenv("OLLAMA_PRIMARY", "1") != "0"

# ── FatSecret (free 5,000/day, food search backup) ──
FATSECRET_CLIENT_ID = os.getenv("FATSECRET_CLIENT_ID", "")
FATSECRET_CLIENT_SECRET = os.getenv("FATSECRET_CLIENT_SECRET", "")

_PLACEHOLDERS = {"", "PASTE_YOUR_GROQ_KEY", "your-key", "none"}


def validate():
    """Fail fast at startup if the app can't actually work."""
    if GROQ_API_KEY.strip().lower() in _PLACEHOLDERS:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Put your key in .env "
            "(e.g. GROQ_API_KEY=your-key) and restart."
        )
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY must not be empty.")
    # Gemini is optional: photo recognition needs it, everything else works without it.