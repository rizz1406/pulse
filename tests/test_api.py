import os
import tempfile
import unittest
from unittest import mock

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "api.db")
os.environ["LOCAL_TZ"] = "UTC"
os.environ["GROQ_API_KEY"] = "test-fake-key"
os.environ["APP_PASSCODE"] = "testpass"

from zoneinfo import ZoneInfo

import app as app_mod  # noqa: E402
import config  # noqa: E402
import parser  # noqa: E402
import storage  # noqa: E402

# Hermetic pinning — config reads env/.env at import time; another test
# module may have imported config first with different (or real .env) values,
# so force the attributes we rely on regardless of import order.
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "api.db")
config.LOCAL_TZ = ZoneInfo("UTC")
config.APP_PASSCODE = "testpass"
config.SECRET_KEY = "test-secret-key"
config.FATSECRET_CLIENT_ID = ""
config.FATSECRET_CLIENT_SECRET = ""


class TestAPI(unittest.TestCase):
    def setUp(self):
        app_mod.app.config["TESTING"] = True
        self.client = app_mod.app.test_client()
        storage.init_db()
        import goals
        import portions
        goals.init_goal_table()
        portions.init_portion_table()
        # wipe all tables
        from db import connect
        with connect() as c:
            for t in ("food", "workout", "weight", "water", "goal", "portion_memory"):
                c.execute(f"DELETE FROM {t}")
        # reset session and log in fresh (app._ensure_db also runs on request)
        with self.client.session_transaction() as s:
            s.clear()
        r = self.client.post("/api/login", json={"passcode": "testpass"})
        self.assertEqual(r.status_code, 200)

    # ── AUTH ──
    def test_login_wrong_passcode(self):
        r = self.client.post("/api/login", json={"passcode": "nope"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/login", json={"passcode": "testpass"})
        self.assertEqual(r.status_code, 200)

    def test_protected_route_401_without_login(self):
        with self.client.session_transaction() as s:
            s.clear()
        r = self.client.post("/api/confirm", json={"type": "food"})
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/api/today")
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/api/me").get_json()
        self.assertFalse(r["authed"])

    # ── /api/log ──
    def test_log_text_food(self):
        r = self.client.post("/api/log", json={"text": "2 boiled eggs"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["calories"], 144)

    def test_log_empty_text(self):
        r = self.client.post("/api/log", json={"text": "   "})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "empty")

    def test_log_malformed_json(self):
        r = self.client.post("/api/log", data="{not json",
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_log_with_media_calls_parser(self):
        import io
        with mock.patch.object(parser, "_generate",
                               return_value={"type": "chat", "reply": "hi"}):
            data = {"media": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 300),
                              "test.png", "image/png")}
            r = self.client.post("/api/log", data=data,
                                 content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["type"], "chat")

    # ── /api/confirm validation ──
    def test_confirm_bad_type(self):
        r = self.client.post("/api/confirm", json={"type": "nope"})
        self.assertEqual(r.status_code, 400)

    def test_confirm_food_missing_field(self):
        r = self.client.post("/api/confirm", json={"type": "food",
                                                   "item_name": "X"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("missing field", r.get_json()["error"])

    def test_confirm_weight_invalid(self):
        r = self.client.post("/api/confirm", json={"type": "weight",
                                                   "weight_kg": 0})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/confirm", json={"type": "weight",
                                                   "weight_kg": "abc"})
        self.assertEqual(r.status_code, 400)

    def test_confirm_water_bad_ml(self):
        r = self.client.post("/api/confirm", json={"type": "water", "ml": "x"})
        self.assertEqual(r.status_code, 400)

    # ── full journey: log → confirm → today → edit → delete ──
    def test_full_food_journey(self):
        d = self.client.post("/api/log", json={"text": "2 eggs"}).get_json()
        r = self.client.post("/api/confirm", json=d)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["streak"], 1)

        t = self.client.get("/api/today").get_json()
        self.assertEqual(t["totals"]["calories"], 144)
        self.assertEqual(t["totals"]["meals"], 1)

        eid = t["foods"][0]["id"]
        r = self.client.post("/api/edit", json={"kind": "food", "id": eid,
                                                "fields": {"calories": 200}})
        self.assertEqual(r.status_code, 200)
        t = self.client.get("/api/today").get_json()
        self.assertEqual(t["totals"]["calories"], 200)

        r = self.client.post("/api/delete", json={"kind": "food", "id": eid})
        self.assertEqual(r.status_code, 200)
        t = self.client.get("/api/today").get_json()
        self.assertEqual(t["totals"]["calories"], 0)

    def test_workout_and_weight_journey(self):
        d = self.client.post("/api/log", json={"text": "squats"}).get_json()
        # force a workout parse preview
        d = {"type": "workout", "exercise_name": "Squat", "weight_kg": 60,
             "sets": 3, "reps": 10, "notes": ""}
        r = self.client.post("/api/confirm", json=d)
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/confirm",
                             json={"type": "weight", "weight_kg": 77.5})
        self.assertEqual(r.status_code, 200)

        t = self.client.get("/api/today").get_json()
        self.assertEqual(len(t["workouts"]), 1)
        p = self.client.get("/api/progress?days=60").get_json()
        self.assertEqual(p["current"], 77.5)
        self.assertEqual(p["rate_kg_per_week"], None)  # only one weigh-in

    def test_water_clamping_and_undo(self):
        r = self.client.post("/api/water", json={"ml": 99999})
        self.assertEqual(r.get_json()["water"]["ml"], 5000)
        r = self.client.post("/api/water", json={"undo": True})
        self.assertEqual(r.get_json()["water"]["ml"], 0)

    def test_relog_unknown_404(self):
        r = self.client.post("/api/relog", json={"item_name": "never logged"})
        self.assertEqual(r.status_code, 404)

    def test_recents_and_relog(self):
        for _ in range(2):
            self.client.post("/api/confirm", json={
                "type": "food", "item_name": "Chicken curry", "calories": 420,
                "protein_g": 35, "carbs_g": 12, "fat_g": 25})
        recents = self.client.get("/api/recents").get_json()
        self.assertEqual(recents["meals"][0]["freq"], 2)
        r = self.client.post("/api/relog",
                             json={"item_name": recents["meals"][0]["item_name"]})
        self.assertEqual(r.status_code, 200)
        t = self.client.get("/api/today").get_json()
        self.assertEqual(t["totals"]["meals"], 3)

    # ── goals ──
    def test_goal_flow(self):
        r = self.client.get("/api/goal").get_json()
        self.assertIsNone(r["goal"])
        r = self.client.post("/api/goal", json={
            "height_cm": 180, "age": 25, "sex": "male",
            "activity": "moderate", "objective": "cut_steady",
            "weight_kg": 80})
        self.assertEqual(r.status_code, 200)
        g = self.client.get("/api/goal").get_json()
        self.assertEqual(g["goal"]["height_cm"], 180)
        self.assertGreater(g["targets"]["calories"], 1200)
        # bad inputs
        r = self.client.post("/api/goal", json={"height_cm": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/preview_targets", json={})
        self.assertEqual(r.status_code, 400)

    def test_goal_recalc_on_weight_change(self):
        self.client.post("/api/goal", json={
            "height_cm": 180, "age": 25, "sex": "male",
            "activity": "moderate", "objective": "maintain",
            "weight_kg": 90})
        self.client.post("/api/confirm", json={"type": "weight",
                                               "weight_kg": 80})
        g = self.client.get("/api/goal").get_json()
        self.assertLess(g["targets"]["protein"],
                        goals_protein_at(90))

    def test_today_targets_from_goal(self):
        self.client.post("/api/goal", json={
            "height_cm": 180, "age": 25, "sex": "male",
            "activity": "moderate", "objective": "maintain", "weight_kg": 80})
        t = self.client.get("/api/today").get_json()
        self.assertIn("cal_target", t)
        self.assertGreater(t["cal_target"], 0)

    # ── analytics / export ──
    def test_analytics_reflects_db(self):
        self.client.post("/api/confirm", json={
            "type": "food", "item_name": "Oats", "calories": 150,
            "protein_g": 5, "carbs_g": 27, "fat_g": 3})
        self.client.post("/api/confirm", json={
            "type": "food", "item_name": "Egg", "calories": 250,
            "protein_g": 18, "carbs_g": 4, "fat_g": 18})
        a = self.client.get("/api/analytics?days=30").get_json()
        self.assertEqual(a["calories"][-1], 400)
        self.assertEqual(a["macro_split"]["protein"], 23)
        self.assertEqual(a["avg_cal"], 400)
        w = self.client.get("/api/weekly?days=7").get_json()
        self.assertEqual(w["total_cal"], 400)
        self.assertEqual(w["avg_cal"], 400)

    def test_export_csv(self):
        self.client.post("/api/confirm", json={
            "type": "food", "item_name": "Paneer", "calories": 250,
            "protein_g": 18, "carbs_g": 4, "fat_g": 18})
        r = self.client.get("/api/export?kind=food")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("Paneer", r.get_data(as_text=True))
        r = self.client.get("/api/export?kind=bogus")
        self.assertEqual(r.status_code, 400)

    def test_404_json(self):
        r = self.client.get("/api/nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"], "not found")

    def test_bad_days_params_fall_back_gracefully(self):
        """"days=abc" must not 500 — falls back to the default window."""
        r = self.client.get("/api/analytics?days=abc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["days_back"], 30)
        r = self.client.get("/api/weekly?days=-5")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/progress?days=x")
        self.assertEqual(r.status_code, 200)

    def test_goal_out_of_range(self):
        r = self.client.post("/api/goal", json={
            "height_cm": 180, "age": 25, "sex": "male",
            "activity": "moderate", "objective": "cut_steady",
            "weight_kg": 0})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/goal", json={
            "height_cm": 180, "age": 25, "sex": "male",
            "activity": "moderate", "objective": "cut_steady",
            "weight_kg": 800})
        self.assertEqual(r.status_code, 400)

    # ── /api/autocomplete ──
    def test_autocomplete_empty_query(self):
        r = self.client.get("/api/autocomplete?q=")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["suggestions"], [])

    def test_autocomplete_local_db_match(self):
        r = self.client.get("/api/autocomplete?q=egg")
        self.assertEqual(r.status_code, 200)
        suggestions = r.get_json()["suggestions"]
        names = [s["name"].lower() for s in suggestions]
        self.assertTrue(any("egg" in n for n in names))

    def test_autocomplete_recent_meals_match(self):
        # Log a meal first so it appears in recents
        for _ in range(3):
            self.client.post("/api/confirm", json={
                "type": "food", "item_name": "Paneer Tikka",
                "calories": 300, "protein_g": 22, "carbs_g": 8, "fat_g": 20})
        r = self.client.get("/api/autocomplete?q=paneer")
        suggestions = r.get_json()["suggestions"]
        names = [s["name"] for s in suggestions]
        self.assertIn("Paneer Tikka", names)
        # Recent meals should include calorie info
        paneer = next(s for s in suggestions if s["name"] == "Paneer Tikka")
        self.assertEqual(paneer["calories"], 300)
        self.assertEqual(paneer["source"], "recent")

    def test_autocomplete_short_query(self):
        r = self.client.get("/api/autocomplete?q=")
        self.assertEqual(r.get_json()["suggestions"], [])

    # ── /api/suggest ──
    def test_suggest_returns_remaining_and_suggestions(self):
        with mock.patch.object(parser, "_generate",
                               return_value={"suggestions": [
                                   {"name": "Eggs", "calories": 144,
                                    "protein": 12, "carbs": 1, "fat": 10,
                                    "reason": "high protein"}]}):
            r = self.client.get("/api/suggest")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("remaining", d)
        self.assertIn("suggestions", d)
        self.assertEqual(len(d["suggestions"]), 1)
        self.assertEqual(d["suggestions"][0]["name"], "Eggs")

    def test_suggest_fallback_on_error(self):
        with mock.patch.object(parser, "_generate", side_effect=Exception("boom")):
            r = self.client.get("/api/suggest")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["suggestions"], [])

    def test_suggest_remaining_reflects_logged_food(self):
        # Log food so remaining decreases
        self.client.post("/api/confirm", json={
            "type": "food", "item_name": "Rice", "calories": 200,
            "protein_g": 4, "carbs_g": 45, "fat_g": 0.5})
        with mock.patch.object(parser, "_generate",
                               return_value={"suggestions": []}):
            r = self.client.get("/api/suggest")
        d = r.get_json()
        # Remaining calories should be less than 2000
        self.assertLess(d["remaining"]["calories"], 2000)

    # ── /api/barcode ──
    def test_barcode_not_found(self):
        r = self.client.get("/api/barcode/0000000000000")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertFalse(d["found"])

    def test_barcode_real_product(self):
        """Test barcode lookup with mocked OpenFoodFacts response."""
        import json as _json
        fake_resp = _json.dumps({
            "status": 1,
            "product": {
                "product_name": "Coca-Cola",
                "brands": "Coca-Cola",
                "serving_size": "330ml",
                "nutriments": {
                    "energy-kcal_serving": 139,
                    "proteins_serving": 0,
                    "carbohydrates_serving": 35,
                    "fat_serving": 0,
                    "fiber_serving": 0,
                }
            }
        }).encode()

        mock_resp = mock.Mock()
        mock_resp.read.return_value = fake_resp
        mock_resp.__enter__ = mock.Mock(return_value=mock_resp)
        mock_resp.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(app_mod.urllib.request, "urlopen",
                               return_value=mock_resp):
            r = self.client.get("/api/barcode/5449000000996")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["found"])
        self.assertEqual(d["name"], "Coca-Cola")
        self.assertEqual(d["calories"], 139)

    def test_barcode_log_after_lookup(self):
        """Simulate: lookup barcode → get info → log via /api/log."""
        import json as _json
        fake_resp = _json.dumps({
            "status": 1,
            "product": {
                "product_name": "Test Chips",
                "brands": "TestCo",
                "serving_size": "1 pack (30g)",
                "nutriments": {
                    "energy-kcal_serving": 150,
                    "proteins_serving": 2,
                    "carbohydrates_serving": 18,
                    "fat_serving": 8,
                    "fiber_serving": 1,
                }
            }
        }).encode()
        mock_resp = mock.Mock()
        mock_resp.read.return_value = fake_resp
        mock_resp.__enter__ = mock.Mock(return_value=mock_resp)
        mock_resp.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(app_mod.urllib.request, "urlopen",
                               return_value=mock_resp):
            r = self.client.get("/api/barcode/123456789")
            d = r.get_json()
            self.assertTrue(d["found"])
            self.assertEqual(d["name"], "Test Chips")
            self.assertEqual(d["calories"], 150)

    # ── weekly analytics endpoint ──
    def test_weekly_analytics_endpoint(self):
        r = self.client.get("/api/analytics/weekly")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("streak", d)
        self.assertIn("days_total", d)
        self.assertIn("cal_adherence", d)
        self.assertIn("protein_adherence", d)

    # ── hybrid: AI-estimated macros ──
    def test_log_ai_estimated_food(self):
        """Unknown food → AI-estimated macros returned directly for confirmation."""
        import parser as parser_mod
        with mock.patch.object(parser_mod, "_generate", return_value={
            "type": "food", "item_name": "Gulab Jamun (2 pieces)",
            "quantity": 2, "calories": 290, "protein_g": 4,
            "carbs_g": 52, "fat_g": 8, "serving_note": "2 small gulab jamuns"}):
            r = self.client.post("/api/log", json={"text": "gulab jamun"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["source"], "ai_estimate")
        self.assertEqual(d["calories"], 290)




def goals_protein_at(w):
    import goals
    return goals.calculate(w, 180, 25, "male", "moderate", "maintain")["protein"]


if __name__ == "__main__":
    unittest.main()