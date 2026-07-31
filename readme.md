<div align="center">

# 🥗 Pulse

### A private, AI-powered health & macro tracker you can actually live in.

Tell it what you ate or lifted — by text, voice, or photo — and it parses the
nutrition with AI, tracks your goals, and shows you modern-fitness-app analytics.
Built to be fast, beautiful, and free to run.

`Flask` · `Gemini` · `SQLite` · `Vanilla JS` · `Liquid-glass UI`

</div>

---

## ✨ What it does

- **Natural-language logging** — type *"2 eggs and toast"*, *"chicken curry big bowl with 2 rotis"*, or Hinglish like *"do ande khaye"*. No forms, no barcode scanning.
- **Voice & photo** — speak a meal or snap a photo of your plate; the AI transcribes and estimates it.
- **Smart clarifying questions** — when a dish is ambiguous (how oily? how much rice?), it asks one quick tappable question for a sharper estimate.
- **Auto-calculated goals** — enter your stats once; it computes your daily calorie & macro targets and **recalculates automatically as you log new weights**.
- **Modern analytics** — calorie & macro trends, macro-split donut, workout volume, body-weight progress, and a pace check that tells you if your cut is on track.
- **Streaks, quick-add favourites, edit & undo** — the small things that make a tracker survive past week one.
- **Portion learning** — remembers *your* typical portions over time, so estimates get personal.
- **Liquid-glass UI** — frosted panels, an ambient aurora background, animated rings, and a satisfying log-success burst.

## 🧱 Tech stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | **Flask** (Python) | Simple, holds the AI key safely server-side |
| AI | **Google Gemini** (Flash) | Multimodal — parses text, audio & images; free tier |
| Storage | **SQLite** | Fast, zero-config, powers all analytics; one portable file |
| Frontend | **Vanilla JS + Chart.js** | No framework overhead; single-file, instant load |
| Hosting | **Render** (free tier) | Always-on HTTPS, persistent disk, phone-accessible |

## 📁 Structure

```
pulse/
├── app.py            # Flask routes, auth, APIs
├── parser.py         # Gemini engine (single-call classify + extract)
├── storage.py        # SQLite + analytics queries
├── goals.py          # Target calculator (BMR/TDEE, auto-recalc on weigh-in)
├── portions.py       # Portion-learning memory
├── config.py         # Settings (secrets via env vars)
└── static/
    └── index.html    # The entire UI — glass, charts, animations
```

## 🚀 Run locally

```bash
pip install -r requirements.txt
# set your key + a passcode (or edit config.py)
export GEMINI_API_KEY="your-key"
export APP_PASSCODE="your-passcode"
python app.py
```
Open **http://localhost:5000**, enter your passcode, and log your first meal.

## ☁️ Deploy (free, phone-accessible)

Deploys to **Render** in a few clicks — `render.yaml` and `Procfile` are included.
Set `GEMINI_API_KEY` and `APP_PASSCODE` as environment variables in the Render
dashboard (never commit real keys). A 1 GB persistent disk keeps your history
across restarts.

## 🔒 Privacy

Single-user by design. Your Gemini key stays server-side (never in the browser),
a passcode gates the whole app, and your data lives in one SQLite file you control.

## 📊 A note on accuracy

AI nutrition estimates are *estimates* — good, but not a food scale. Pulse gets
you close with smart questions and portion-learning; the **weekly weigh-in** is
the ground truth that keeps you honest. Log consistently, weigh in weekly, and
the trend tells you the truth.

---

<div align="center">
<sub>Built with persistence, one debugging session at a time. 💪</sub>
</div>