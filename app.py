"""
Flask backend for the Health Tracker web app.
Serves the single-page UI and JSON APIs. Gemini key stays server-side.
A simple passcode gates access so only you can use it.
"""

import base64
import functools

from flask import (
    Flask, request, jsonify, session, send_from_directory, redirect
)

import config
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


@app.before_request
def _ensure_db():
    global _db_ready
    if not _db_ready:
        storage.init_db()
        goals.init_goal_table()
        portions.init_portion_table()
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


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
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
    """Accept text or audio/image, parse, and return a preview (not yet saved)."""
    payload = []
    raw = ""
    if request.is_json:
        text = (request.get_json(force=True).get("text") or "").strip()
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

    result["_raw"] = raw
    return jsonify(result)


@app.route("/api/clarify", methods=["POST"])
@login_required
def clarify():
    """Re-estimate a food entry after the user taps a clarifying answer.
    Supports up to 2 chained questions (e.g. oil, then amount)."""
    d = request.get_json(force=True)
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
    d = request.get_json(force=True)
    t = d.get("type")
    if t == "food":
        storage.save_food(d)
        portions.remember(d)  # learn this user's portion
    elif t == "workout":
        storage.save_workout(d)
    elif t == "weight":
        storage.save_weight(d.get("weight_kg", 0), d.get("notes", ""))
    elif t == "water":
        storage.add_water(d.get("ml", 250))
    else:
        return jsonify({"error": "bad type"}), 400
    return jsonify({"ok": True, "streak": storage.current_streak()})


@app.route("/api/edit", methods=["POST"])
@login_required
def edit():
    """Edit an already-logged entry."""
    d = request.get_json(force=True)
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


@app.route("/api/relog", methods=["POST"])
@login_required
def relog():
    d = request.get_json(force=True)
    saved = storage.relog_meal(d.get("item_name", ""))
    if not saved:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "streak": storage.current_streak()})


@app.route("/api/progress")
@login_required
def progress():
    days = int(request.args.get("days", 60))
    data = storage.weight_trend(days)
    t = goals.current_targets()
    if t:
        data["target_cal"] = t["calories"]
        data["objective"] = t.get("objective")
    return jsonify(data)


@app.route("/api/delete", methods=["POST"])
@login_required
def delete():
    d = request.get_json(force=True)
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
    d = request.get_json(force=True)
    try:
        goals.save_goal(
            height_cm=float(d["height_cm"]),
            age=int(d["age"]),
            sex=d.get("sex", "male"),
            activity=d.get("activity", "moderate"),
            objective=d.get("objective", "cut_steady"),
            current_weight=float(d["weight_kg"]),
        )
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"bad input: {e}"}), 400
    return jsonify({"ok": True, "targets": goals.current_targets()})


@app.route("/api/preview_targets", methods=["POST"])
@login_required
def preview_targets():
    """Live-calculate targets from form inputs without saving (for the UI)."""
    d = request.get_json(force=True)
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
    data = storage.today_data()
    # If a goal is set, override the flat config targets with the live ones.
    t = goals.current_targets()
    if t:
        data["cal_target"] = t["calories"]
        data["protein_target"] = t["protein"]
        data["carb_target"] = t["carbs"]
        data["fat_target"] = t["fat"]
        data["goal_weight"] = t["weight"]
    return jsonify(data)


@app.route("/api/water", methods=["POST"])
@login_required
def water():
    d = request.get_json(force=True)
    if d.get("undo"):
        return jsonify({"ok": True, "water": storage.undo_water()})
    try:
        ml = max(0, min(int(d.get("ml", 250)), 5000))
    except (TypeError, ValueError):
        return jsonify({"error": "bad ml"}), 400
    return jsonify({"ok": True, "water": storage.add_water(ml)})


@app.route("/api/weekly")
@login_required
def weekly():
    days = int(request.args.get("days", 7))
    return jsonify(storage.weekly_summary(days))


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
    days = int(request.args.get("days", 30))
    return jsonify(storage.analytics(days))


# ─────────────────────────────────────────────────────────────
# ERRORS
# ─────────────────────────────────────────────────────────────
@app.errorhandler(500)
def _server_error(e):
    return jsonify({"error": "Something went wrong server-side. Try again."}), 500


# ─────────────────────────────────────────────────────────────
# STATIC
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)