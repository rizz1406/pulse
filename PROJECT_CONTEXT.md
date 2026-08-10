# Project Context: Health Bot (Pulse)

## Purpose
A private, AI-powered health & macro tracker with natural-language logging (text, voice, photo). Single-user by design with passcode auth. Built for mobile-friendly daily use.

## Tech Stack
- **Backend**: Flask (Python) — holds AI keys server-side
- **AI**: Gemini 3.5 Flash (free tier) — multimodal parsing for photos/voice/ambiguous text
- **Food Data**: FatSecret API (free 5,000/day) — backup search for branded/specific foods
- **Local DB**: 150+ built-in foods (USDA-verified) — unlimited, instant, zero API calls
- **Voice**: Browser Web Speech API (free, no server)
- **Storage**: SQLite (local) + Turso libsql (cloud) — powers all analytics
- **Frontend**: Vanilla JS + Chart.js — single-page liquid-glass UI, offline cache via localStorage
- **Hosting**: Render (free tier) + Turso for persistent DB

## Project Structure
```
health-bot/
├── app.py              # Flask routes, auth, JSON APIs
├── parser.py           # Multi-tier parsing engine (Gemini + local DB)
├── fooddb.py           # Local food DB + FatSecret API + serving logic
├── storage.py          # SQLite layer + analytics queries
├── goals.py            # Target calculator (BMR/TDEE, auto-recalc on weigh-in)
├── portions.py         # Portion-learning memory (user's typical servings)
├── config.py           # Settings via env vars (no secrets in code)
├── static/
│   ├── index.html      # Full UI (glass, charts, animations)
│   ├── style.css       # Liquid-glass, aurora, rings, CSS variables
│   └── app.js          # Auth, input, charts, offline cache, all interactions
└── tests/
    ├── test_fooddb.py  # Local DB serving logic + FatSecret scoring
    ├── test_parser.py  # Parser flow with mocked Gemini
    ├── test_goals.py   # Target calc + auto-recalc on weight change
    └── test_storage.py # CRUD, streak, weekly, exports, analytics
```

## Backend/Frontend Architecture
- **Flask** serves static files from `/static` and JSON APIs under `/api/*`
- **Auth**: Simple passcode stored in session cookie (`SECRET_KEY` signs it)
- **DB init**: Lazy on first request (avoids libsql fork deadlock under gunicorn)
- **Frontend**: Single-page app with tabs (Today / Analytics / Progress / Goal)
- **Offline**: `getJSON()` caches successful responses in localStorage; serves stale data when offline

## Database/Storage
- **Tables**: `food`, `workout`, `weight`, `water`, `goal`, `portion_memory`
- **Backend**: `db.py` wraps both `sqlite3` (local) and `libsql` (Turso) with identical cursor interface
- **WAL mode + busy_timeout** for local SQLite concurrency
- **No ORM** — raw SQL with parameterized queries

## Food Parsing Flow (Three-Tier)
```
User input (text/voice/photo)
       │
       ▼
┌──────────────────┐
│ Local DB (Tier 1)│ ──► ~80% of personal logs, instant, zero API
│ fooddb.parse_food│     Per-serving (eggs, roti) + per-100g (rice, chicken)
└──────────────────┘
       │ miss
       ▼
┌──────────────────┐
│ FatSecret (Tier 2)│ ──► Free 5k/day, branded/specific items
│ fooddb.parse_fatsecret│  Semantic scoring (NOT highest calories)
└──────────────────┘
       │ miss
       ▼
┌──────────────────┐
│ Gemini (Tier 3)  │ ──► Classifies input + extracts structure ONLY
│ parser._generate │     NEVER invents nutrition values
└──────────────────┘
       │
       ▼
Result normalized → audit trail (source, matched_food, serving_g, qty)
```

**Key files**: `parser.py` (orchestrates), `fooddb.py` (local + FatSecret), `portions.py` (learned hints)

## Local DB → FatSecret → Gemini Flow
1. **Local** (`fooddb.parse_local`): Exact/word match against 150+ foods. Handles multi-item (`+`, `and`, `with`, comma). Returns audit dict.
2. **FatSecret** (`fooddb.parse_fatsecret`): Searches API, picks best by semantic score (word overlap + bigrams - brand penalty). Normalizes serving descriptions to per-100g.
3. **Gemini** (`parser._generate`): Receives prompt instructing it to **only classify & extract structure** (food name, qty, unit). Sets calories/macros to 0. Caller then calls `fooddb.parse_food(identified_name)` for actual nutrition.

## Serving/Portion Calculation
Two entry types in `FOODS` dict (`fooddb.py:42-160`):
- **Per-100g** (default): `cal, p, c, f` per 100g + `serving_g` (default portion).  
  Formula: `per100g × (serving_g/100) × qty` — unless user says "200g" (gram_mode=True) then `per100g × (grams/100)`.
- **Per-serving** (`_per_serving=True`): `cal, p, c, f` for ONE unit + `serving_g` (weight of one unit).  
  Formula: `serving_value × qty` — no gram scaling.

**Quantity extraction** (`fooddb.py:217-286`): regex for `Nx`, `N unit` (roti, egg, bowl, etc.), `half/quarter`, explicit `g`/`ml`, generic leading count.

**Multi-item** (`fooddb.py:485-523`): splits on `,`, `;`, `+`, `and`, `with`, parses each, sums nutrition.

**Audit trail** on every result: `source`, `matched_food`, `serving_g`, `qty`, `confidence_notes`.

## Food Logging
- `/api/log` (POST): accepts text or multipart (photo/audio) → returns preview (not saved)
- `/api/confirm` (POST): persists approved entry, calls `portions.remember()` to learn
- `/api/clarify` (POST): re-estimates after user answers clarifying question (up to 2 rounds)
- `/api/edit` (POST): updates logged entry, re-learns portion if name/calories changed
- `/api/relog` (POST): one-tap re-log of recent meal
- `/api/recents`: distinct recent meals (frequency-ordered) for quick-add chips

## Goals, Water, Workout, Weight
- **Goals** (`goals.py`): Mifflin-St Jeor BMR × activity → TDEE + objective delta. Protein = kg × obj_factor (2.0-2.2). Fat = kg × 0.8. Carbs = remainder. Auto-recalculates on new weight log.
- **Water** (`storage.py:120-144`): quick add (default 250ml), undo (removes last), target from `WATER_TARGET_ML`
- **Workout**: exercise_name, weight_kg (0=bodyweight), sets, reps, notes
- **Weight**: single kg value + notes; latest weight drives live goal targets

## Analytics/Charts
- `/api/today`: daily totals + macro bars + water + streak + entry list
- `/api/analytics?days=30`: continuous date axis (no gaps), calories/macros/workout volume/weight trend + macro split donut + avg kcal
- `/api/progress?days=60`: weight trend + pace assessment (ideal/slow/fast for cut)
- `/api/weekly?days=7`: avg kcal, total kcal, active days, workouts, water, weight delta, top 3 meals
- **Charts**: Chart.js line (calories, macros, weight), bar (workout volume), doughnut (macro split)

## Exports
- `/api/export?kind=food|workout|weight|water` → CSV download with timestamped filename
- `storage.export_csv()` builds CSV in memory using Python csv module

## API Structure
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/login` | Passcode auth |
| POST | `/api/logout` | Clear session |
| GET | `/api/me` | Auth status |
| POST | `/api/log` | Parse input → preview |
| POST | `/api/clarify` | Re-parse with user answer |
| POST | `/api/confirm` | Persist entry |
| POST | `/api/edit` | Update entry |
| POST | `/api/delete` | Delete entry |
| POST | `/api/relog` | Quick re-log recent meal |
| GET | `/api/recents` | Frequent meals for quick-add |
| GET | `/api/today` | Today's data + totals |
| GET | `/api/progress` | Weight trend + pace |
| GET | `/api/analytics` | 30-day charts data |
| GET | `/api/weekly` | 7-day summary |
| GET | `/api/goal` | Get saved goal |
| POST | `/api/goal` | Save goal (auto-logs weight) |
| POST | `/api/preview_targets` | Live calc without saving |
| POST | `/api/water` | Add/undo water |
| GET | `/api/export` | CSV download |

## Environment Variable Names Only
```
GEMINI_API_KEY       # Required
GEMINI_MODEL         # Optional (default: gemini-3.5-flash)
APP_PASSCODE         # Optional (empty = no lock)
SECRET_KEY           # Required for session signing
LOCAL_TZ             # Optional (default: Asia/Kolkata)
DB_PATH              # Optional (default: health.db)
DAILY_CAL_TARGET     # Fallback when no goal set
DAILY_PROTEIN_TARGET # Fallback when no goal set
WATER_TARGET_ML      # Default 2500
OLLAMA_URL           # Optional local AI (default: http://localhost:11434)
OLLAMA_MODEL         # Optional (default: qwen3:4b)
OLLAMA_TIMEOUT       # Optional (default: 90)
OLLAMA_PRIMARY       # Optional (default: 1 = use Ollama first for text)
FATSECRET_CLIENT_ID  # Optional
FATSECRET_CLIENT_SECRET # Optional
TURSO_DATABASE_URL   # Optional (enables cloud DB)
TURSO_AUTH_TOKEN     # Optional (paired with above)
```

## Tests
Run: `python -m pytest tests/` or `python -m unittest discover tests`

Coverage areas:
- `test_fooddb.py`: Local DB serving logic, multi-item, qty extraction, FatSecret semantic scoring, audit trail
- `test_parser.py`: Parser flow with mocked Gemini, local precedence, clarification chaining, audit fields
- `test_goals.py`: Target calc, objective ordering, auto-recalc on weight change, no duplicate weight logs
- `test_storage.py`: CRUD, streak, weekly summary, exports, analytics, water undo

## Confirmed Known Issues/Limitations
1. **Gemini quota**: Free tier has daily limits; photos/voice always use Gemini
2. **FatSecret rate limit**: 5,000 requests/day shared across all users
3. **Local DB coverage**: ~150 Indian/Asian-centric foods; Western/branded items need FatSecret/Gemini
4. **No multi-user**: Single passcode, single goal, single data set
5. **libsql fork issue**: DB init must be lazy (`@app.before_request`) to survive gunicorn workers
6. **No automated tests for Gemini**: Requires valid API key; tests mock the client
7. **Voice recognition**: Browser-dependent (Web Speech API); not available in all browsers
8. **Portion learning**: Keyed by 3 significant words; can conflate similar dishes
9. **No background jobs**: All processing synchronous in request
10. **Chart.js loaded from CDN**: Requires internet for charts to render