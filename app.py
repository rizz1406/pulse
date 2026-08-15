"""
Flask backend for the Health Tracker web app.
Serves the single-page UI and JSON APIs. Gemini key stays server-side.
A simple passcode gates access so only you can use it.
"""

import base64
import functools
import json
import re
import threading
import urllib.request

from flask import (
    Flask, request, jsonify, session, send_from_directory, redirect
)

import config
import db
import storage
import parser
import goals
import portions

from datetime import datetime

config.validate()  # fail fast if the key is missing (never ship a broken deploy)

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = config.SECRET_KEY

# IMPORTANT: do NOT initialize the database at import time. Under gunicorn the
# module is imported in the master process, then forked into workers. libsql's
# native threads don't survive the fork, causing deadlocks. Instead we init the
# tables lazily on the first request, which runs inside the worker.
_db_ready = False
_db_init_lock = threading.Lock()
_SCHEMA_MARKER = "app_schema_v1"


def _schema_is_current():
    with db.connect() as c:
        row = c.execute(
            "SELECT 1 ok FROM sqlite_master WHERE type='table' AND name=?",
            (_SCHEMA_MARKER,),
        ).fetchone()
    return bool(row)


@app.before_request
def _ensure_db():
    global _db_ready
    if request.endpoint in {"index", "static", "me", "login", "logout"}:
        return
    if not _db_ready:
        with _db_init_lock:
            if not _db_ready:
                if not _schema_is_current():
                    storage.init_db()
                    goals.init_goal_table()
                    portions.init_portion_table()
                    with db.connect() as c:
                        c.execute(
                            f"CREATE TABLE IF NOT EXISTS {_SCHEMA_MARKER} "
                            "(id INTEGER PRIMARY KEY)"
                        )
                _db_ready = True


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if config.APP_PASSCODE and not session.get("authed"):
            return jsonify({"error": "auth"}), 401
        return f(*args, **kwargs)
    return wrapper


def _days_arg(default=30):
    """Safe 'days' query param: int in 1..365, or the default if malformed."""
    try:
        return max(1, min(int(request.args.get("days", default)), 365))
    except (TypeError, ValueError):
        return default


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    if not config.APP_PASSCODE or data.get("passcode") == config.APP_PASSCODE:
        session["authed"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Wrong passcode"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    return jsonify({"authed": bool(session.get("authed")) or not config.APP_PASSCODE})


# ─────────────────────────────────────────────────────────────
# CORE APIs
# ─────────────────────────────────────────────────────────────
@app.route("/api/log", methods=["POST"])
@login_required
def log():
    """Accept text or audio/image, parse, and return a preview (not yet saved).
    If skip_clarification is set, bypass clarification and log immediately
    using the default_fallback pill text."""
    payload = []
    raw = ""
    skip_clarify = False
    if request.is_json:
        body = request.get_json(force=True) or {}
        text = (body.get("text") or "").strip()
        skip_clarify = bool(body.get("skip_clarification"))
        if not text:
            return jsonify({"error": "empty"}), 400
        payload.append(f"User input: {text}")
        raw = text
    else:
        text = (request.form.get("text") or "").strip()
        if text:
            payload.append(f"User input: {text}")
            raw = text
        file = request.files.get("media")
        if file:
            blob = file.read()
            if len(blob) > 8 * 1024 * 1024:
                return jsonify({"error": "Media too large — max 8MB"}), 413
            mime = file.mimetype or "application/octet-stream"
            payload.append({"mime_type": mime, "data": blob})
            if not raw:
                raw = "[voice note]" if mime.startswith("audio") else "[photo]"
    if not payload:
        return jsonify({"error": "empty"}), 400

    try:
        result = parser.parse(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # If skip_clarification and the result has a default_fallback, resolve it now
    if skip_clarify and result.get("default_fallback"):
        try:
            resolved = parser.parse_with_pill(
                result["default_fallback"], result.get("item_name", ""))
            if resolved and resolved.get("calories", 0) > 0:
                resolved["needs_clarification"] = False
                resolved["_raw"] = raw
                return jsonify(resolved)
        except Exception:
            pass  # fall through to return the clarification response

    result["_raw"] = raw
    return jsonify(result)


@app.route("/api/pill", methods=["POST"])
@login_required
def pill():
    """Resolve a clarification pill selection to full nutrition."""
    d = request.get_json(force=True) or {}
    pill_text = d.get("pill_text", "")
    food_name = d.get("food_name", "")
    if not pill_text:
        return jsonify({"error": "missing pill_text"}), 400
    try:
        result = parser.parse_with_pill(pill_text, food_name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not result or result.get("calories", 0) == 0:
        return jsonify({"error": "Could not find nutrition for this option"}), 404
    result["needs_clarification"] = False
    return jsonify(result)


@app.route("/api/clarify", methods=["POST"])
@login_required
def clarify():
    """Re-estimate a food entry after the user taps a clarifying answer.
    Supports up to 2 chained questions (e.g. oil, then amount)."""
    d = request.get_json(force=True) or {}
    # 'original' accumulates prior Q&A so each round keeps full context.
    original = d.get("original", "")
    question = d.get("question", "")
    answer = d.get("answer", "")
    rnd = int(d.get("round", 1))
    try:
        result = parser.reparse_food_with_answer(original, question, answer, clarify_round=rnd)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    # carry forward the combined context + round so a 2nd question can chain
    result["_raw"] = original
    result["_context"] = f"{original} | {question} {answer}"
    result["_round"] = rnd + 1
    return jsonify(result)


@app.route("/api/confirm", methods=["POST"])
@login_required
def confirm():
    """Persist a parsed entry the user approved."""
    d = request.get_json(force=True) or {}
    t = d.get("type")
    if t == "food":
        for k in ("item_name", "calories", "protein_g", "carbs_g", "fat_g"):
            if k not in d:
                return jsonify({"error": f"missing field: {k}"}), 400
        try:
            values = [float(d[k]) for k in
                      ("calories", "protein_g", "carbs_g", "fat_g")]
        except (TypeError, ValueError):
            return jsonify({"error": "invalid nutrition values"}), 400
        if any(v < 0 for v in values) or values[0] > 10000 or any(v > 1000 for v in values[1:]):
            return jsonify({"error": "nutrition values out of range"}), 400
        storage.save_food(d)
        portions.remember(d)  # learn this user's portion
    elif t == "workout":
        if not d.get("exercise_name"):
            return jsonify({"error": "missing exercise_name"}), 400
        storage.save_workout(d)
    elif t == "weight":
        kg = d.get("weight_kg")
        if not isinstance(kg, (int, float)) or not (0 < float(kg) < 500):
            return jsonify({"error": "invalid weight_kg"}), 400
        photo = d.get("photo", "")
        if photo and len(photo) > 3 * 1024 * 1024:
            return jsonify({"error": "Photo too large — max 3MB"}), 413
        storage.save_weight(float(kg), d.get("notes", ""), photo)
    elif t == "water":
        try:
            ml = max(0, min(int(d.get("ml", 250)), 5000))
        except (TypeError, ValueError):
            return jsonify({"error": "bad ml"}), 400
        storage.add_water(ml)
    else:
        return jsonify({"error": "bad type"}), 400
    return jsonify({"ok": True, "streak": storage.current_streak()})


@app.route("/api/edit", methods=["POST"])
@login_required
def edit():
    """Edit an already-logged entry."""
    d = request.get_json(force=True) or {}
    kind, eid, fields = d.get("kind"), d.get("id"), d.get("fields", {})
    if kind == "food":
        storage.update_food(eid, fields)
        # re-learn from the corrected numbers
        if "item_name" in fields or "calories" in fields:
            row = storage.get_food(eid)
            if row:
                portions.remember(row)
    elif kind == "workout":
        storage.update_workout(eid, fields)
    else:
        return jsonify({"error": "bad kind"}), 400
    return jsonify({"ok": True})


@app.route("/api/recents")
@login_required
def recents():
    return jsonify({"meals": storage.recent_meals(8)})


@app.route("/api/autocomplete")
@login_required
def autocomplete():
    """Search local DB + recent meals for autocomplete suggestions."""
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 1:
        return jsonify({"suggestions": []})
    results = []
    seen = set()
    # Saved package-label foods take priority.
    for item in storage.custom_foods():
        name = item["name"]
        if q in name.lower():
            seen.add(name.lower())
            results.append({"name": name, "calories": item["calories"],
                            "source": "custom"})
    # 1. Local food DB — prefix + substring match
    from fooddb import FOODS
    for name in FOODS:
        if q in name.lower() and name.lower() not in seen:
            seen.add(name.lower())
            results.append({"name": name.title(), "source": "local"})
    # 2. Recent meals — most frequent first
    recent = storage.recent_meals(20)
    for m in recent:
        name = m.get("item_name", "")
        if q in name.lower() and name.lower() not in seen:
            seen.add(name.lower())
            results.append({
                "name": name,
                "calories": m.get("calories", 0),
                "source": "recent",
            })
    return jsonify({"suggestions": results[:10]})


@app.route("/api/custom_food", methods=["GET", "POST"])
@login_required
def custom_food():
    if request.method == "GET":
        return jsonify({"foods": storage.custom_foods()})
    d = request.get_json(force=True) or {}
    try:
        serving_g = float(d.get("serving_g", 0))
        values = [float(d.get(k, 0)) for k in
                  ("calories", "protein_g", "carbs_g", "fat_g")]
    except (TypeError, ValueError):
        return jsonify({"error": "invalid custom food values"}), 400
    if not str(d.get("name", "")).strip() or not (0 < serving_g <= 5000):
        return jsonify({"error": "name and serving_g are required"}), 400
    if any(v < 0 for v in values):
        return jsonify({"error": "nutrition values must be non-negative"}), 400
    storage.save_custom_food(d)
    return jsonify({"ok": True})


@app.route("/api/relog", methods=["POST"])
@login_required
def relog():
    d = request.get_json(force=True) or {}
    saved = storage.relog_meal(d.get("item_name", ""))
    if not saved:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "streak": storage.current_streak()})


@app.route("/api/progress")
@login_required
def progress():
    days = _days_arg(60)
    data = storage.weight_trend(days)
    t = goals.current_targets()
    if t:
        data["target_cal"] = t["calories"]
        data["objective"] = t.get("objective")
        data["calorie_adjustment"] = t.get("calorie_adjustment", 0)
    data["coach"] = goals.weight_coach()
    g = goals.get_goal()
    data["bmi"] = goals.bmi(data.get("current"), g.get("height_cm") if g else None)
    return jsonify(data)


@app.route("/api/delete", methods=["POST"])
@login_required
def delete():
    d = request.get_json(force=True) or {}
    storage.delete_entry(d.get("kind"), d.get("id"))
    return jsonify({"ok": True})


@app.route("/api/goal", methods=["GET"])
@login_required
def get_goal():
    return jsonify({
        "goal": goals.get_goal(),
        "targets": goals.current_targets(),
    })


@app.route("/api/goal", methods=["POST"])
@login_required
def set_goal():
    d = request.get_json(force=True) or {}
    try:
        weight = float(d["weight_kg"])
        height = float(d["height_cm"])
        age = int(d["age"])
        if not (0 < weight < 500 and 0 < height < 300 and 0 < age < 120):
            return jsonify({"error": "bad input: out of range"}), 400
        goals.save_goal(
            height_cm=height,
            age=age,
            sex=d.get("sex", "male"),
            activity=d.get("activity", "moderate"),
            objective=d.get("objective", "cut_steady"),
            current_weight=weight,
        )
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"bad input: {e}"}), 400
    return jsonify({"ok": True, "targets": goals.current_targets()})


@app.route("/api/preview_targets", methods=["POST"])
@login_required
def preview_targets():
    """Live-calculate targets from form inputs without saving (for the UI)."""
    d = request.get_json(force=True) or {}
    try:
        t = goals.calculate(
            float(d["weight_kg"]), float(d["height_cm"]), int(d["age"]),
            d.get("sex", "male"), d.get("activity", "moderate"),
            d.get("objective", "cut_steady"),
        )
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "incomplete"}), 400
    return jsonify(t)


@app.route("/api/today")
@login_required
def today():
    with db.connect() as c:
        data = storage.today_data(c)
        goal = goals.get_goal(c)
        t = goals.current_targets(c, goal) if goal else None
        coach = goals.weight_coach(c, goal) if goal else {"active": False}
    # If a goal is set, override the flat config targets with the live ones.
    if t:
        data["cal_target"] = t["calories"]
        data["protein_target"] = t["protein"]
        data["carb_target"] = t["carbs"]
        data["fat_target"] = t["fat"]
        data["goal_weight"] = t["weight"]
        data["calorie_adjustment"] = t.get("calorie_adjustment", 0)
    data["coach"] = coach
    return jsonify(data)


@app.route("/api/water", methods=["POST"])
@login_required
def water():
    d = request.get_json(force=True) or {}
    if d.get("undo"):
        removed, total = storage.undo_water()
        if not removed:
            return jsonify({"error": "Nothing to undo"}), 400
        return jsonify({"ok": True, "water": total})
    try:
        ml = max(0, min(int(d.get("ml", 250)), 5000))
    except (TypeError, ValueError):
        return jsonify({"error": "bad ml"}), 400
    return jsonify({"ok": True, "water": storage.add_water(ml)})


@app.route("/api/weekly")
@login_required
def weekly():
    days = _days_arg(7)
    return jsonify(storage.weekly_summary(days))


@app.route("/api/recap")
@login_required
def recap():
    """AI summary of the last 7 days: habits, wins, and one actionable tip."""
    s = storage.weekly_summary(7)
    if not s["active_days"]:
        return jsonify({"recap": None})
    prompt = (
        "You are a concise nutrition coach. Summarize this user's last 7 days "
        "in 3-4 short lines, all plain text (no markdown, no emojis). "
        "Data: avg_cal={avg_cal}, total_cal={total_cal}, active_days={active_days}, "
        "workouts={workouts}, water_ml={water_ml}, weight_change={weight_change}, "
        "top_meals={top_meals}. "
        "Cover: one thing going well, one thing to watch, and one specific "
        "actionable tip for next week. Keep it friendly and specific."
    ).format(**s)
    try:
        resp = parser._get_client().chat.completions.create(
            model=parser.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=180,
        )
        if not resp.choices:
            return jsonify({"recap": None})
        recap_text = (resp.choices[0].message.content or "").strip()
        return jsonify({"recap": recap_text or None})
    except Exception:
        return jsonify({"recap": None})


@app.route("/api/export")
@login_required
def export_data():
    kind = request.args.get("kind", "food")
    csv_text = storage.export_csv(kind)
    if csv_text is None:
        return jsonify({"error": "bad kind"}), 400
    from flask import Response
    fname = f"pulse-{kind}-{datetime.now().strftime('%Y-%m-%d')}.csv"
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.route("/api/analytics")
@login_required
def analytics():
    days = _days_arg(30)
    return jsonify(storage.analytics(days))


@app.route("/api/analytics/weekly")
@login_required
def analytics_weekly():
    """Weekly macro streak & target progress — 7-day breakdown."""
    return jsonify(storage.weekly_macro_analytics(7))


@app.route("/api/suggest")
@login_required
def suggest():
    """AI meal suggestions based on remaining macros for today."""
    import goals as goals_mod
    t = goals_mod.current_targets()
    today = storage.today_data()
    totals = today.get("totals", {})
    cal_target = (t["calories"] if t else config.DAILY_CAL_TARGET) or 2000
    prot_target = (t["protein"] if t else config.DAILY_PROTEIN_TARGET) or 150
    carb_target = (t["carbs"] if t else 300) or 300
    fat_target = (t["fat"] if t else 80) or 80

    remaining = {
        "calories": max(0, cal_target - totals.get("calories", 0)),
        "protein": max(0, prot_target - totals.get("protein", 0)),
        "carbs": max(0, carb_target - totals.get("carbs", 0)),
        "fat": max(0, fat_target - totals.get("fat", 0)),
    }

    recent = storage.recent_meals_for_suggest(15)
    recent_str = "\n".join(
        f"- {m['item_name']}: {m['calories']}kcal, "
        f"{m['protein_g']}p, {m['carbs_g']}c, {m['fat_g']}f"
        for m in recent
    )

    prompt = (
        f"The user has {remaining['calories']}kcal remaining today, "
        f"with {remaining['protein']}g protein, {remaining['carbs']}g carbs, "
        f"and {remaining['fat']}g fat left to hit their targets.\n\n"
        f"Here are their most commonly logged meals:\n{recent_str}\n\n"
        "Suggest 3 meals from this list that best fill their remaining macros. "
        "Prioritize protein. Return JSON:\n"
        '{"suggestions": [{"name": "...", "calories": N, "protein": N, '
        '"carbs": N, "fat": N, "reason": "short reason"}]}'
    )

    try:
        result = parser._generate([f"Remaining: {remaining}"], prompt)
        suggestions = result.get("suggestions", [])
    except Exception:
        suggestions = []

    return jsonify({"remaining": remaining, "suggestions": suggestions})


@app.route("/api/barcode/<code>")
@login_required
def barcode_lookup(code):
    """Look up a barcode via OpenFoodFacts API."""
    m = re.search(r'(\d{8,14})', code)
    clean = m.group(1) if m else re.sub(r'\D', '', code)
    if not clean or len(clean) < 8:
        return jsonify({"found": False, "error": "Not a valid barcode number"})
    url = f"https://world.openfoodfacts.org/api/v2/product/{clean}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Pulse/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != 1:
            return jsonify({"found": False, "error": "Product not found"})
        p = data["product"]
        nutriments = p.get("nutriments", {})
        return jsonify({
            "found": True,
            "name": p.get("product_name", "Unknown product"),
            "brand": p.get("brands", ""),
            "serving_size": p.get("serving_size", "1 serving"),
            "calories": round(nutriments.get("energy-kcal_serving", 0) or nutriments.get("energy-kcal_100g", 0)),
            "protein": round(nutriments.get("proteins_serving", 0) or nutriments.get("proteins_100g", 0), 1),
            "carbs": round(nutriments.get("carbohydrates_serving", 0) or nutriments.get("carbohydrates_100g", 0), 1),
            "fat": round(nutriments.get("fat_serving", 0) or nutriments.get("fat_100g", 0), 1),
            "fiber": round(nutriments.get("fiber_serving", 0) or nutriments.get("fiber_100g", 0), 1),
        })
    except Exception as e:
        return jsonify({"found": False, "error": str(e)})


# ─────────────────────────────────────────────────────────────
# ERRORS
# ─────────────────────────────────────────────────────────────
@app.errorhandler(500)
def _server_error(e):
    return jsonify({"error": "Something went wrong server-side. Try again."}), 500


@app.errorhandler(400)
def _bad_request(e):
    return jsonify({"error": "bad request"}), 400


@app.errorhandler(404)
def _not_found(e):
    return jsonify({"error": "not found"}), 404


# ─────────────────────────────────────────────────────────────
# STATIC
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
