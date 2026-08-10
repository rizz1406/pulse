<div align="center">

# 🥗 Pulse

### A private, AI-powered health & macro tracker you can actually live in.

Tell it what you ate or lifted — by text, voice, or photo — and it parses the
nutrition, tracks your goals, and shows you modern-fitness-app analytics.
**Free to run.** Local food database handles ~80% of inputs with zero API calls.

`Flask` · `Gemini` · `FatSecret` · `Local DB` · `SQLite` · `Vanilla JS` · `Liquid-glass UI`

</div>

---

## ✨ What it does

- **Natural-language logging** — type *"2 eggs and toast"*, *"chicken curry big bowl with 2 rotis"*, or Hinglish like *"do ande khaye"*. No forms, no barcode scanning.
- **Voice & photo** — speak a meal (browser speech recognition, free) or snap a photo of your plate; the AI estimates it.
- **Three-tier AI parsing** — local food database (instant, free) → FatSecret search (free 5k/day) → Gemini AI (best quality). Most inputs never hit an API.
- **Smart clarifying questions** — when a dish is ambiguous (how oily? how much rice?), it asks one quick tappable question for a sharper estimate.
- **Water tracking** — quick-add buttons (+250ml, +500ml), undo, today's progress bar toward your goal.
- **Auto-calculated goals** — enter your stats once; it computes your daily calorie & macro targets and **recalculates automatically as you log new weights**.
- **Weekly summary** — avg kcal, workouts, water intake, weight delta, most-logged meals.
- **CSV export** — download your meals, workouts, weight, or water data anytime.
- **Modern analytics** — calorie & macro trends, macro-split donut, workout volume, body-weight progress, and a pace check that tells you if your cut is on track.
- **Streaks, quick-add favourites, edit & undo** — the small things that make a tracker survive past week one.
- **Portion learning** — remembers *your* typical portions over time, so estimates get personal.
- **Offline cache** — last-good data served from localStorage when the network drops.
- **Liquid-glass UI** — frosted panels, an ambient aurora background, animated rings, and a satisfying log-success burst.

## 🧱 Tech stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | **Flask** (Python) | Simple, holds the AI key safely server-side |
| AI (text) | **Groq** `llama-3.3-70b` (free tier) | Fast classification of meals/workouts/chat |
| AI (photos) | **Gemini Flash** (free tier) | Groq has no vision — Gemini reads food photos |
| Food data | **FatSecret** (free 5k/day) | Purpose-built nutrition search API |
| Local DB | **150+ built-in foods** | Unlimited, instant, no API key needed |
| Voice | **Web Speech API** | Browser-side transcription — free, no server processing |
| Storage | **SQLite + Turso** | Fast, powers all analytics; Turso for cloud persistence |
| Frontend | **Vanilla JS + Chart.js** | No framework overhead; single-file, instant load |
| Hosting | **Render** (free tier) | Always-on HTTPS, phone-accessible |

## 📁 Structure

```
pulse/
├── app.py            # Flask routes, auth, APIs
├── parser.py         # Multi-tier parsing engine
├── fooddb.py         # Local food DB + FatSecret API
├── storage.py        # SQLite + analytics queries
├── goals.py          # Target calculator (BMR/TDEE, auto-recalc on weigh-in)
├── portions.py       # Portion-learning memory
├── config.py         # Settings (secrets via env vars)
├── static/
│   ├── index.html    # The entire UI — glass, charts, animations
│   ├── style.css     # All styles (liquid-glass, aurora, rings)
│   └── app.js        # All logic (auth, input, charts, offline cache)
└── tests/
    ├── test_fooddb.py
    ├── test_goals.py
    ├── test_parser.py
    └── test_storage.py
```

## 🚀 Run locally

```bash
pip install -r requirements.txt
export GROQ_API_KEY="your-groq-key"
export GEMINI_API_KEY="your-gemini-key"   # only for photo recognition
export APP_PASSCODE="your-passcode"
python app.py
```
Open **http://localhost:5000**, enter your passcode, and log your first meal.

**No API key?** The local food database works without any keys — just type your meals and it parses them instantly.

**Photo recognition** needs `GEMINI_API_KEY` (free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)) — Groq has no vision model, so Gemini is used for photos only. Text stays on Groq.

## ☁️ Deploy to Render (free, mobile-accessible)

1. Push this repo to GitHub
2. Go to [render.com](https://render.com), create a free account
3. New → Web Service → connect your GitHub repo
4. Set environment variables in the Render dashboard:
   - `GROQ_API_KEY` — text parsing AI
   - `GEMINI_API_KEY` — photo recognition (free at aistudio.google.com/apikey)
   - `APP_PASSCODE` — your login passcode
   - `TURSO_DATABASE_URL` — your Turso database URL (for persistent data)
   - `TURSO_AUTH_TOKEN` — your Turso auth token
   - `FATSECRET_CLIENT_ID` / `FATSECRET_CLIENT_SECRET` — optional, for FatSecret backup
5. Deploy — your app is live at `https://your-app.onrender.com`

**Turso (free database):** Sign up at [turso.tech](https://turso.tech), create a database, and copy the URL + token. This keeps your data alive across Render deploys.

## 🔒 Privacy

Single-user by design. Your Gemini key stays server-side (never in the browser),
a passcode gates the whole app, and your data lives in one SQLite file you control.

## 📊 A note on accuracy

AI nutrition estimates are *estimates* — good, but not a food scale. Pulse gets
you close with the local database, smart questions, and portion-learning; the
**weekly weigh-in** is the ground truth that keeps you honest. Log consistently,
weigh in weekly, and the trend tells you the truth.

---

<div align="center">
<sub>Built with persistence, one debugging session at a time. 💪</sub>
</div>