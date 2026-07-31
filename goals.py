"""
Goal engine — stores the user's body stats + objective, calculates daily
calorie/macro targets, and recalculates automatically when weight changes.

Uses Mifflin-St Jeor BMR, an activity multiplier for TDEE, and a deficit/
surplus based on the chosen objective. Protein scales with bodyweight to
preserve muscle on a cut.
"""

import sqlite3
from datetime import datetime

import config

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


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_goal_table():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS goal (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                height_cm REAL, age INTEGER, sex TEXT,
                activity TEXT, objective TEXT,
                start_weight REAL, updated TEXT
            )""")


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


def latest_weight():
    """Most recent logged body weight, or None."""
    with _conn() as c:
        r = c.execute("SELECT weight_kg FROM weight ORDER BY id DESC LIMIT 1").fetchone()
    return r["weight_kg"] if r else None


def save_goal(height_cm, age, sex, activity, objective, current_weight):
    with _conn() as c:
        c.execute(
            "INSERT INTO goal (id, height_cm, age, sex, activity, objective, "
            "start_weight, updated) VALUES (1,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET height_cm=excluded.height_cm, "
            "age=excluded.age, sex=excluded.sex, activity=excluded.activity, "
            "objective=excluded.objective, updated=excluded.updated",
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


def get_goal():
    with _conn() as c:
        r = c.execute("SELECT * FROM goal WHERE id=1").fetchone()
    return dict(r) if r else None


def current_targets():
    """
    The live targets: uses the most recent logged weight (falls back to the
    weight saved at goal setup). Returns None if no goal set yet.
    """
    g = get_goal()
    if not g:
        return None
    weight = latest_weight() or g["start_weight"]
    t = calculate(weight, g["height_cm"], g["age"], g["sex"],
                  g["activity"], g["objective"])
    t["weight"] = weight
    t["objective"] = g["objective"]
    return t