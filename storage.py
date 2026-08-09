"""
Storage layer — SQLite source of truth. Powers all analytics.
Tables: food, workout, weight.
"""

import csv
import io
import sqlite3
from datetime import datetime, timedelta, date

import config
import db


def _conn():
    return db.connect()


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS food (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, day TEXT,
                item_name TEXT, calories INTEGER,
                protein_g INTEGER, carbs_g INTEGER, fat_g INTEGER,
                fiber_g INTEGER DEFAULT 0, sugar_g INTEGER DEFAULT 0,
                notes TEXT, raw_input TEXT
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS workout (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, day TEXT,
                exercise_name TEXT, weight_kg REAL,
                sets INTEGER, reps INTEGER,
                notes TEXT, raw_input TEXT
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS weight (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, day TEXT, weight_kg REAL, notes TEXT
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS water (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, day TEXT, ml INTEGER
            )""")


def _now():
    return datetime.now(config.LOCAL_TZ)


# ─────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────
def save_food(d):
    now = _now()
    with _conn() as c:
        c.execute(
            "INSERT INTO food (ts, day, item_name, calories, protein_g, carbs_g, "
            "fat_g, fiber_g, sugar_g, notes, raw_input) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d"),
             d["item_name"], d["calories"], d["protein_g"], d["carbs_g"], d["fat_g"],
             d.get("fiber_g", 0), d.get("sugar_g", 0), d.get("confidence_notes", ""),
             d.get("_raw", "")),
        )
        return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def save_workout(d):
    now = _now()
    with _conn() as c:
        c.execute(
            "INSERT INTO workout (ts, day, exercise_name, weight_kg, sets, reps, "
            "notes, raw_input) VALUES (?,?,?,?,?,?,?,?)",
            (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d"),
             d["exercise_name"], d.get("weight_kg") or 0, d["sets"], d["reps"],
             d.get("notes", ""), d.get("_raw", "")),
        )
        return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def save_weight(kg, notes=""):
    now = _now()
    with _conn() as c:
        c.execute("INSERT INTO weight (ts, day, weight_kg, notes) VALUES (?,?,?,?)",
                  (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d"), kg, notes))


def delete_entry(kind, entry_id):
    table = {"food": "food", "workout": "workout", "weight": "weight",
             "water": "water"}.get(kind)
    if not table:
        return False
    with _conn() as c:
        c.execute(f"DELETE FROM {table} WHERE id=?", (entry_id,))
    return True


# ─────────────────────────────────────────────────────────────
# WATER
# ─────────────────────────────────────────────────────────────
def add_water(ml=250):
    """Log a sip of water. Returns today's total ml."""
    now = _now()
    with _conn() as c:
        c.execute("INSERT INTO water (ts, day, ml) VALUES (?,?,?)",
                  (now.strftime("%Y-%m-%d %H:%M:%S"),
                   now.strftime("%Y-%m-%d"), int(ml)))
    return today_water()


def undo_water():
    """Remove the most recent water entry (fat-finger recovery)."""
    with _conn() as c:
        r = c.execute("SELECT id FROM water ORDER BY id DESC LIMIT 1").fetchone()
        if r:
            c.execute("DELETE FROM water WHERE id=?", (r["id"],))
    return today_water()


def today_water():
    day = _now().strftime("%Y-%m-%d")
    with _conn() as c:
        t = c.execute("SELECT COALESCE(SUM(ml),0) ml, COUNT(*) n FROM water WHERE day=?",
                      (day,)).fetchone()
    return {"ml": t["ml"], "count": t["n"]}


def update_food(entry_id, fields):
    """Edit a logged food entry. fields = dict of columns to update."""
    allowed = {"item_name", "calories", "protein_g", "carbs_g", "fat_g",
               "fiber_g", "sugar_g"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    vals.append(entry_id)
    with _conn() as c:
        c.execute(f"UPDATE food SET {','.join(sets)} WHERE id=?", vals)
    return True


def update_workout(entry_id, fields):
    allowed = {"exercise_name", "weight_kg", "sets", "reps", "notes"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    vals.append(entry_id)
    with _conn() as c:
        c.execute(f"UPDATE workout SET {','.join(sets)} WHERE id=?", vals)
    return True


def recent_meals(limit=8):
    """Distinct recent meals (most-eaten first) for one-tap re-logging."""
    with _conn() as c:
        rows = c.execute(
            "SELECT item_name, calories, protein_g, carbs_g, fat_g, fiber_g, "
            "sugar_g, COUNT(*) freq, MAX(id) last_id "
            "FROM food GROUP BY LOWER(item_name) "
            "ORDER BY freq DESC, last_id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def relog_meal(item_name):
    """Re-log the most recent version of a named meal. Returns saved dict or None."""
    with _conn() as c:
        r = c.execute(
            "SELECT item_name, calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g "
            "FROM food WHERE LOWER(item_name)=LOWER(?) ORDER BY id DESC LIMIT 1",
            (item_name,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["type"] = "food"
    d["confidence_notes"] = "re-logged"
    d["_raw"] = item_name
    save_food(d)
    return d



# ─────────────────────────────────────────────────────────────
# STREAK
# ─────────────────────────────────────────────────────────────
def current_streak():
    with _conn() as c:
        rows = c.execute("SELECT day FROM food UNION SELECT day FROM workout").fetchall()
    days = {r["day"] for r in rows}
    if not days:
        return 0
    today = _now().date()
    start = today if today.isoformat() in days else today - timedelta(days=1)
    if start.isoformat() not in days:
        return 0
    n, cur = 0, start
    while cur.isoformat() in days:
        n += 1
        cur -= timedelta(days=1)
    return n


# ─────────────────────────────────────────────────────────────
# READ — today + lists
# ─────────────────────────────────────────────────────────────
def today_data():
    day = _now().strftime("%Y-%m-%d")
    with _conn() as c:
        totals = c.execute(
            "SELECT COALESCE(SUM(calories),0) cal, COALESCE(SUM(protein_g),0) p, "
            "COALESCE(SUM(carbs_g),0) cb, COALESCE(SUM(fat_g),0) ft, "
            "COALESCE(SUM(fiber_g),0) fb, COALESCE(SUM(sugar_g),0) sg, COUNT(*) n "
            "FROM food WHERE day=?", (day,)).fetchone()
        foods = c.execute(
            "SELECT id, ts, item_name, calories, protein_g, carbs_g, fat_g "
            "FROM food WHERE day=? ORDER BY id DESC", (day,)).fetchall()
        workouts = c.execute(
            "SELECT id, ts, exercise_name, weight_kg, sets, reps, notes "
            "FROM workout WHERE day=? ORDER BY id DESC", (day,)).fetchall()
        waters = c.execute(
            "SELECT id, ts, ml FROM water WHERE day=? ORDER BY id DESC", (day,)).fetchall()
    return {
        "totals": {
            "calories": totals["cal"], "protein": totals["p"], "carbs": totals["cb"],
            "fat": totals["ft"], "fiber": totals["fb"], "sugar": totals["sg"],
            "meals": totals["n"],
        },
        "foods": [dict(r) for r in foods],
        "workouts": [dict(r) for r in workouts],
        "waters": [dict(r) for r in waters],
        "water_total": sum((r["ml"] for r in waters), 0),
        "water_target": config.WATER_TARGET_ML,
        "streak": current_streak(),
        "cal_target": config.DAILY_CAL_TARGET,
        "protein_target": config.DAILY_PROTEIN_TARGET,
    }


# ─────────────────────────────────────────────────────────────
# READ — analytics
# ─────────────────────────────────────────────────────────────
def analytics(days_back=30):
    today = _now().date()
    start = (today - timedelta(days=days_back - 1)).isoformat()

    with _conn() as c:
        cal_rows = c.execute(
            "SELECT day, SUM(calories) cal, SUM(protein_g) p, SUM(carbs_g) cb, "
            "SUM(fat_g) ft FROM food WHERE day>=? GROUP BY day ORDER BY day", (start,)
        ).fetchall()
        wo_rows = c.execute(
            "SELECT day, COUNT(*) n, COALESCE(SUM(weight_kg*sets*reps),0) volume "
            "FROM workout WHERE day>=? GROUP BY day ORDER BY day", (start,)
        ).fetchall()
        wt_rows = c.execute(
            "SELECT day, AVG(weight_kg) w FROM weight WHERE day>=? GROUP BY day ORDER BY day",
            (start,)
        ).fetchall()
        macro_split = c.execute(
            "SELECT COALESCE(SUM(protein_g),0) p, COALESCE(SUM(carbs_g),0) cb, "
            "COALESCE(SUM(fat_g),0) ft FROM food WHERE day>=?", (start,)
        ).fetchone()

    # Build a continuous date axis so charts don't skip empty days
    axis = [(today - timedelta(days=i)).isoformat() for i in range(days_back - 1, -1, -1)]
    cal_map = {r["day"]: r for r in cal_rows}
    wo_map = {r["day"]: r for r in wo_rows}
    wt_map = {r["day"]: r["w"] for r in wt_rows}

    calories = [cal_map[d]["cal"] if d in cal_map else 0 for d in axis]
    protein = [cal_map[d]["p"] if d in cal_map else 0 for d in axis]
    carbs = [cal_map[d]["cb"] if d in cal_map else 0 for d in axis]
    fat = [cal_map[d]["ft"] if d in cal_map else 0 for d in axis]
    workout_count = [wo_map[d]["n"] if d in wo_map else 0 for d in axis]
    workout_volume = [round(wo_map[d]["volume"]) if d in wo_map else 0 for d in axis]
    weights = [round(wt_map[d], 1) if d in wt_map else None for d in axis]

    logged_days = [c for c in calories if c > 0]
    avg_cal = round(sum(logged_days) / len(logged_days)) if logged_days else 0
    total_workouts = sum(workout_count)

    return {
        "labels": [d[5:] for d in axis],  # MM-DD
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "workout_count": workout_count,
        "workout_volume": workout_volume,
        "weights": weights,
        "macro_split": {
            "protein": macro_split["p"], "carbs": macro_split["cb"], "fat": macro_split["ft"],
        },
        "avg_cal": avg_cal,
        "total_workouts": total_workouts,
        "streak": current_streak(),
        "days_back": days_back,
    }


def weight_trend(days_back=60):
    """Weight entries + average calories for the progress view."""
    today = _now().date()
    start = (today - timedelta(days=days_back - 1)).isoformat()
    with _conn() as c:
        wrows = c.execute(
            "SELECT day, AVG(weight_kg) w FROM weight WHERE day>=? "
            "GROUP BY day ORDER BY day", (start,)).fetchall()
        crows = c.execute(
            "SELECT day, SUM(calories) cal FROM food WHERE day>=? "
            "GROUP BY day ORDER BY day", (start,)).fetchall()
    weights = [{"day": r["day"][5:], "kg": round(r["w"], 1)} for r in wrows]
    cal_map = {r["day"]: r["cal"] for r in crows}
    logged = [v for v in cal_map.values() if v]
    avg_cal = round(sum(logged) / len(logged)) if logged else 0

    rate = None
    if len(wrows) >= 2:
        first, last = wrows[0], wrows[-1]
        d1 = datetime.strptime(first["day"], "%Y-%m-%d").date()
        d2 = datetime.strptime(last["day"], "%Y-%m-%d").date()
        weeks = max((d2 - d1).days / 7, 0.1)
        rate = round((last["w"] - first["w"]) / weeks, 2)

    return {
        "weights": weights,
        "avg_cal": avg_cal,
        "rate_kg_per_week": rate,
        "current": round(wrows[-1]["w"], 1) if wrows else None,
        "start": round(wrows[0]["w"], 1) if wrows else None,
    }


def get_food(entry_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM food WHERE id=?", (entry_id,)).fetchone()
    return dict(r) if r else None


# ─────────────────────────────────────────────────────────────
# WEEKLY SUMMARY
# ─────────────────────────────────────────────────────────────
def weekly_summary(days_back=7):
    """Roll-up of the last N days: intake, workouts, water, weight delta,
    and the dishes eaten most often."""
    today = _now().date()
    start = (today - timedelta(days=days_back - 1)).isoformat()

    with _conn() as c:
        cal_rows = c.execute(
            "SELECT day, SUM(calories) cal FROM food WHERE day>=? GROUP BY day", (start,)
        ).fetchall()
        wo_count = c.execute(
            "SELECT COUNT(*) n FROM workout WHERE day>=?", (start,)).fetchone()["n"]
        wtr = c.execute(
            "SELECT COALESCE(SUM(ml),0) ml FROM water WHERE day>=?", (start,)
        ).fetchone()["ml"]
        wt_rows = c.execute(
            "SELECT day, AVG(weight_kg) w FROM weight WHERE day>=? GROUP BY day "
            "ORDER BY day", (start,)).fetchall()
        top = c.execute(
            "SELECT item_name, COUNT(*) count, MAX(calories) calories FROM food "
            "WHERE day>=? GROUP BY LOWER(item_name) ORDER BY count DESC, MAX(id) DESC "
            "LIMIT 3", (start,)).fetchall()

    logged = [r["cal"] for r in cal_rows if r["cal"]]
    weight_change = None
    if len(wt_rows) >= 2:
        weight_change = round(wt_rows[-1]["w"] - wt_rows[0]["w"], 1)

    return {
        "days": days_back,
        "avg_cal": round(sum(logged) / len(logged)) if logged else 0,
        "total_cal": sum(logged),
        "active_days": len(cal_rows),
        "workouts": wo_count,
        "water_ml": wtr,
        "weight_change": weight_change,
        "top_meals": [dict(r) for r in top],
        "streak": current_streak(),
    }


# ─────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────
def export_csv(kind):
    """CSV text for one table: 'food' | 'workout' | 'weight' | 'water'."""
    columns = {
        "food": ["id", "ts", "day", "item_name", "calories", "protein_g",
                 "carbs_g", "fat_g", "fiber_g", "sugar_g", "notes", "raw_input"],
        "workout": ["id", "ts", "day", "exercise_name", "weight_kg",
                    "sets", "reps", "notes", "raw_input"],
        "weight": ["id", "ts", "day", "weight_kg", "notes"],
        "water": ["id", "ts", "day", "ml"],
    }
    if kind not in columns:
        return None
    with _conn() as c:
        rows = c.execute(
            f"SELECT {','.join(columns[kind])} FROM {kind} ORDER BY id").fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(columns[kind])
    for r in rows:
        w.writerow([r[col] for col in columns[kind]])
    return out.getvalue()