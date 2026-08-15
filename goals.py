"""
Goal engine — stores the user's body stats + objective, calculates daily
calorie/macro targets, and recalculates automatically when weight changes.

Uses Mifflin-St Jeor BMR, an activity multiplier for TDEE, and a deficit/
surplus based on the chosen objective. Protein scales with bodyweight to
preserve muscle on a cut.
"""

import sqlite3
from datetime import datetime, timedelta

import config
import db

ACTIVITY = {
    "light": 1.375,     # 1-3 days/week
    "moderate": 1.55,   # 3-5 days/week
    "active": 1.725,    # 6-7 days/week
}

# kcal delta from maintenance
OBJECTIVE = {
    "cut_steady": -500,   # ~0.4 kg/week loss
    "cut_fast": -750,     # ~0.7 kg/week loss
    "maintain": 0,
    "lean_bulk": 250,     # slow muscle gain
}

# protein g per kg bodyweight
PROTEIN_PER_KG = {
    "cut_steady": 2.0, "cut_fast": 2.2, "maintain": 1.8, "lean_bulk": 2.0,
}

LEAN_BULK_MIN_GAIN = 0.15
LEAN_BULK_MAX_GAIN = 0.25
MAX_CALORIE_ADJUSTMENT = 500


def _conn():
    return db.connect()


def init_goal_table():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS goal (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                height_cm REAL, age INTEGER, sex TEXT,
                activity TEXT, objective TEXT,
                start_weight REAL, updated TEXT,
                calorie_adjustment INTEGER DEFAULT 0,
                last_adapted TEXT
            )""")
        for statement in (
            "ALTER TABLE goal ADD COLUMN calorie_adjustment INTEGER DEFAULT 0",
            "ALTER TABLE goal ADD COLUMN last_adapted TEXT",
        ):
            try:
                c.execute(statement)
            except (sqlite3.OperationalError, ValueError):
                pass


def calculate(weight_kg, height_cm, age, sex, activity, objective):
    """Return the daily targets dict for the given inputs."""
    s = 5 if sex == "male" else -161
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + s
    tdee = bmr * ACTIVITY.get(activity, 1.55)
    calories = round(tdee + OBJECTIVE.get(objective, -500))

    protein = round(weight_kg * PROTEIN_PER_KG.get(objective, 2.0))
    fat = round(weight_kg * 0.8)
    carbs = round((calories - (protein * 4 + fat * 9)) / 4)
    carbs = max(carbs, 0)

    return {
        "calories": calories,
        "protein": protein,
        "fat": fat,
        "carbs": carbs,
        "tdee": round(tdee),
        "bmr": round(bmr),
    }


def latest_weight(conn=None):
    """Most recent logged body weight, or None."""
    if conn is None:
        with _conn() as c:
            return latest_weight(c)
    r = conn.execute("SELECT weight_kg FROM weight ORDER BY id DESC LIMIT 1").fetchone()
    return r["weight_kg"] if r else None


def bmi(weight_kg, height_cm):
    """Body Mass Index, or None when either input is missing/invalid."""
    try:
        w = float(weight_kg)
        h = float(height_cm)
    except (TypeError, ValueError):
        return None
    if not w or not h:
        return None
    return round(w / ((h / 100.0) ** 2), 1)


def save_goal(height_cm, age, sex, activity, objective, current_weight):
    with _conn() as c:
        c.execute(
            "INSERT INTO goal (id, height_cm, age, sex, activity, objective, "
            "start_weight, updated, calorie_adjustment, last_adapted) "
            "VALUES (1,?,?,?,?,?,?,?,0,NULL) "
            "ON CONFLICT(id) DO UPDATE SET height_cm=excluded.height_cm, "
            "age=excluded.age, sex=excluded.sex, activity=excluded.activity, "
            "objective=excluded.objective, start_weight=excluded.start_weight, "
            "updated=excluded.updated, calorie_adjustment=0, last_adapted=NULL",
            (height_cm, age, sex, activity, objective, current_weight,
             datetime.now(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")),
        )
    # also record the current weight so targets track from it
    if current_weight:
        from storage import save_weight
        # only log if there isn't already a weight entry today
        with _conn() as c:
            today = datetime.now(config.LOCAL_TZ).strftime("%Y-%m-%d")
            exists = c.execute("SELECT 1 FROM weight WHERE day=?", (today,)).fetchone()
        if not exists:
            save_weight(current_weight, "goal setup")


def get_goal(conn=None):
    if conn is None:
        with _conn() as c:
            return get_goal(c)
    r = conn.execute("SELECT * FROM goal WHERE id=1").fetchone()
    return dict(r) if r else None


def current_targets(conn=None, goal=None):
    """
    The live targets: uses the most recent logged weight (falls back to the
    weight saved at goal setup). Returns None if no goal set yet.
    """
    g = goal if goal is not None else get_goal(conn)
    if not g:
        return None
    weight = latest_weight(conn) or g["start_weight"]
    t = calculate(weight, g["height_cm"], g["age"], g["sex"],
                  g["activity"], g["objective"])
    adjustment = int(g.get("calorie_adjustment") or 0)
    t["base_calories"] = t["calories"]
    t["calorie_adjustment"] = adjustment
    t["calories"] += adjustment
    t["carbs"] = max(round(
        (t["calories"] - (t["protein"] * 4 + t["fat"] * 9)) / 4
    ), 0)
    t["weight"] = weight
    t["objective"] = g["objective"]
    return t


def _lean_bulk_trend(g, conn=None):
    """Return a stable rate from two non-overlapping weigh-in windows."""
    today = datetime.now(config.LOCAL_TZ).date()
    goal_day = datetime.strptime(g["updated"][:10], "%Y-%m-%d").date()
    start = max(goal_day, today - timedelta(days=27)).isoformat()
    if conn is None:
        with _conn() as c:
            return _lean_bulk_trend(g, c)
    rows = conn.execute(
        "SELECT day, AVG(weight_kg) w FROM weight WHERE day>=? "
        "GROUP BY day ORDER BY day", (start,),
    ).fetchall()
    points = [(datetime.strptime(r["day"], "%Y-%m-%d").date(), float(r["w"]))
              for r in rows]
    span = (points[-1][0] - points[0][0]).days if len(points) > 1 else 0
    if len(points) < 7 or span < 14:
        return {"enough_data": False, "weigh_ins": len(points), "span_days": span}

    last_day = points[-1][0]
    recent_start = last_day - timedelta(days=6)
    earlier_start = last_day - timedelta(days=13)
    earlier_end = last_day - timedelta(days=7)
    earlier = [p for p in points if earlier_start <= p[0] <= earlier_end]
    recent = [p for p in points if p[0] >= recent_start]
    if len(earlier) < 2 or len(recent) < 2:
        return {"enough_data": False, "weigh_ins": len(points), "span_days": span}
    earlier_avg = sum(p[1] for p in earlier) / len(earlier)
    recent_avg = sum(p[1] for p in recent) / len(recent)
    earlier_day = sum(p[0].toordinal() for p in earlier) / len(earlier)
    recent_day = sum(p[0].toordinal() for p in recent) / len(recent)
    weeks = max((recent_day - earlier_day) / 7, 0.1)
    return {
        "enough_data": True,
        "weigh_ins": len(points),
        "span_days": span,
        "average_7d": round(recent_avg, 2),
        "previous_average_7d": round(earlier_avg, 2),
        "rate_kg_per_week": round((recent_avg - earlier_avg) / weeks, 2),
    }


def _recommended_adjustment(rate):
    if rate < 0.10:
        return 150
    if rate < LEAN_BULK_MIN_GAIN:
        return 100
    if rate <= LEAN_BULK_MAX_GAIN:
        return 0
    if rate <= 0.35:
        return -100
    return -150


def weight_coach(conn=None, goal=None):
    """Describe the lean-bulk trend and whether the next review is due."""
    g = goal if goal is not None else get_goal(conn)
    if not g or g["objective"] != "lean_bulk":
        return {"active": False}
    trend = _lean_bulk_trend(g, conn)
    today = datetime.now(config.LOCAL_TZ).date()
    last = g.get("last_adapted")
    next_review = ((datetime.strptime(last[:10], "%Y-%m-%d").date()
                    + timedelta(days=7)) if last else
                   (datetime.strptime(g["updated"][:10], "%Y-%m-%d").date()
                    + timedelta(days=14)))
    trend.update({
        "active": True,
        "target_min": LEAN_BULK_MIN_GAIN,
        "target_max": LEAN_BULK_MAX_GAIN,
        "calorie_adjustment": int(g.get("calorie_adjustment") or 0),
        "next_review": next_review.isoformat(),
        "review_due": today >= next_review,
    })
    if trend["enough_data"]:
        trend["recommended_change"] = _recommended_adjustment(
            trend["rate_kg_per_week"])
    else:
        trend["recommended_change"] = 0
    return trend


def maybe_adapt_targets():
    """Review a lean bulk at most weekly after 14 days of useful data."""
    coach = weight_coach()
    if not coach.get("active") or not coach.get("enough_data") \
            or not coach.get("review_due"):
        return coach
    g = get_goal()
    current = int(g.get("calorie_adjustment") or 0)
    updated = max(-MAX_CALORIE_ADJUSTMENT, min(
        MAX_CALORIE_ADJUSTMENT, current + coach["recommended_change"]))
    now = datetime.now(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute(
            "UPDATE goal SET calorie_adjustment=?, last_adapted=? WHERE id=1",
            (updated, now),
        )
    return weight_coach()
