"""
Configuration. For local testing, paste values below.
On Render (hosting), set these as Environment Variables instead — never commit real keys.
"""

import os
from zoneinfo import ZoneInfo

# ── Required ─────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "PASTE_YOUR_GEMINI_KEY")
# Gemini model — auto-tracks current free Flash. Fallbacks: gemini-2.5-flash-lite, gemini-3.5-flash
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
# ── Simple passcode so only you can use the app ──────────────
# Set a value you'll remember. Leave "" to disable the lock (not recommended when hosted).
APP_PASSCODE = os.getenv("APP_PASSCODE", "rizz1406")


# Secret for signing the login session cookie. Set a long random string when hosting.
SECRET_KEY = os.getenv("SECRET_KEY", "x7k2mq9vBn4pLw8sReT3yUicjHf6aZdQ0oM5")

# ── Local settings ───────────────────────────────────────────
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Asia/Kolkata"))
DB_PATH = os.getenv("DB_PATH", "health.db")

# ── Daily targets (0 = hide ring/bar) ────────────────────────
DAILY_CAL_TARGET = int(os.getenv("DAILY_CAL_TARGET", "2000"))
DAILY_PROTEIN_TARGET = int(os.getenv("DAILY_PROTEIN_TARGET", "120"))