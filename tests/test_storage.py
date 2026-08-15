import os
import tempfile
import unittest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["LOCAL_TZ"] = "UTC"

import config  # noqa: E402
import storage  # noqa: E402
import db  # noqa: E402
import portions  # noqa: E402


# Hermetic pinning — config may already be imported by another test module
# reading real .env values; force our own isolation regardless of order.
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "storage.db")
config.LOCAL_TZ = __import__("zoneinfo").ZoneInfo("UTC")


def clean():
    with db.connect() as c:
        for t in ("food", "workout", "weight", "water", "steps", "goal", "portion_memory", "custom_food"):
            c.execute(f"DELETE FROM {t}")


class TestStorage(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        portions.init_portion_table()
        import goals
        goals.init_goal_table()
        clean()

    def test_food_roundtrip(self):
        eid = storage.save_food({"item_name": "2 eggs",
                                 "calories": 140, "protein_g": 12, "carbs_g": 1,
                                 "fat_g": 10, "fiber_g": 0, "sugar_g": 0,
                                 "confidence_notes": "boiled", "_raw": "2 eggs"})
        self.assertGreater(eid, 0)
        d = storage.today_data()
        self.assertEqual(d["totals"]["calories"], 140)
        self.assertEqual(d["totals"]["meals"], 1)
        self.assertEqual(d["foods"][0]["item_name"], "2 eggs")

    def test_custom_food_scales_exact_label_values(self):
        self.assertTrue(storage.save_custom_food({
            "name": "My Whey", "serving_g": 30, "calories": 120,
            "protein_g": 24.5, "carbs_g": 3, "fat_g": 1.5,
            "fiber_g": None, "sugar_g": 1,
        }))
        d = storage.parse_custom_food("60g my whey")
        self.assertEqual(d["calories"], 240)
        self.assertEqual(d["protein_g"], 49.0)
        self.assertEqual(d["source"], "custom")
        self.assertIsNone(d["fiber_g"])

    def test_decimal_macros_survive_storage(self):
        storage.save_food({"item_name": "Half banana", "calories": 44,
                           "protein_g": 0.5, "carbs_g": 11.5, "fat_g": 0})
        d = storage.today_data()
        self.assertEqual(d["totals"]["protein"], 0.5)
        self.assertEqual(d["totals"]["carbs"], 11.5)

    def test_workout_and_weight(self):
        storage.save_workout({"exercise_name": "Squat", "weight_kg": 60,
                              "sets": 3, "reps": 10, "notes": "", "_raw": "squats"})
        storage.save_weight(77.5, "weigh-in")
        d = storage.today_data()
        self.assertEqual(d["workouts"][0]["exercise_name"], "Squat")
        t = storage.weight_trend(60)
        self.assertEqual(t["current"], 77.5)

    def test_today_reports_daily_weigh_in_status(self):
        self.assertTrue(storage.today_data()["weigh_in_due"])
        storage.save_weight(76.5, "morning")
        today = storage.today_data()
        self.assertFalse(today["weigh_in_due"])
        self.assertEqual(today["today_weight"], 76.5)

    def test_progress_photos(self):
        storage.save_weight(77.5, "weigh-in", "data:image/jpeg;base64,AAAA")
        storage.save_weight(76.9, "weigh-in")
        t = storage.weight_trend(60)
        self.assertEqual(len(t["photos"]), 1)
        self.assertEqual(t["photos"][0]["weight_kg"], 77.5)
        self.assertEqual(t["photos"][0]["photo"], "data:image/jpeg;base64,AAAA")

    def test_edit_and_delete(self):
        eid = storage.save_food({"item_name": "Rice", "calories": 200,
                                 "protein_g": 4, "carbs_g": 44, "fat_g": 1})
        storage.update_food(eid, {"calories": 250, "item_name": "Brown rice"})
        row = storage.get_food(eid)
        self.assertEqual(row["calories"], 250)
        self.assertEqual(row["item_name"], "Brown rice")
        storage.delete_entry("food", eid)
        self.assertIsNone(storage.get_food(eid))

    def test_streak(self):
        storage.save_food({"item_name": "X", "calories": 100, "protein_g": 1,
                           "carbs_g": 1, "fat_g": 1})
        self.assertGreaterEqual(storage.current_streak(), 1)

    def test_recents_and_relog(self):
        meal = {"item_name": "Chicken curry", "calories": 420, "protein_g": 35,
                "carbs_g": 12, "fat_g": 25}
        storage.save_food(dict(meal))
        storage.save_food(dict(meal))
        recents = storage.recent_meals()
        self.assertEqual(recents[0]["freq"], 2)
        saved = storage.relog_meal("chicken curry")
        self.assertIsNotNone(saved)
        self.assertEqual(storage.today_data()["totals"]["meals"], 3)

    def test_water(self):
        storage.add_water(250)
        storage.add_water(500)
        self.assertEqual(storage.today_water()["ml"], 750)
        removed, _ = storage.undo_water()
        self.assertTrue(removed)
        self.assertEqual(storage.today_water()["ml"], 250)
        # second undo removes the last remaining entry
        removed2, _ = storage.undo_water()
        self.assertTrue(removed2)
        self.assertEqual(storage.today_water()["ml"], 0)
        # undoing again with nothing left → removed=False
        removed3, _ = storage.undo_water()
        self.assertFalse(removed3)

    def test_steps_replace_daily_total_and_export(self):
        self.assertEqual(storage.save_steps(4200), 4200)
        self.assertEqual(storage.save_steps(5300), 5300)
        today = storage.today_data()
        self.assertEqual(today["steps_today"], 5300)
        self.assertEqual(today["step_target"], 5000)
        csv_text = storage.export_csv("steps")
        self.assertEqual(csv_text.count("\n"), 2)
        self.assertIn("5300", csv_text)

    def test_lean_bulk_weekly_report(self):
        storage.save_food({"item_name": "Meal", "calories": 2900,
                           "protein_g": 150, "carbs_g": 350, "fat_g": 80})
        storage.save_workout({"exercise_name": "Squat", "weight_kg": 80,
                              "sets": 3, "reps": 5})
        storage.save_steps(5200)
        report = storage.lean_bulk_report(
            {"objective": "lean_bulk", "calories": 3000, "protein": 153},
            {"rate_kg_per_week": 0.2, "average_7d": 76.7,
             "target_min": 0.15, "target_max": 0.25},
            step_target=6000,
        )
        self.assertTrue(report["active"])
        self.assertEqual(report["avg_calories"], 2900)
        self.assertEqual(report["avg_protein"], 150)
        self.assertEqual(report["workouts"], 1)
        self.assertEqual(report["avg_steps"], 5200)
        self.assertEqual(report["step_target"], 6000)
        self.assertEqual(report["step_days_hit"], 0)
        self.assertEqual(report["weight_rate"], 0.2)

    def test_weekly_summary(self):
        storage.save_food({"item_name": "Dal", "calories": 300, "protein_g": 15,
                           "carbs_g": 40, "fat_g": 8})
        storage.save_food({"item_name": "Dal", "calories": 300, "protein_g": 15,
                           "carbs_g": 40, "fat_g": 8})
        storage.save_workout({"exercise_name": "Push-ups", "weight_kg": 0,
                              "sets": 3, "reps": 15})
        storage.add_water(1000)
        w = storage.weekly_summary(7)
        self.assertEqual(w["avg_cal"], 600)  # per-day avg; both meals on one day
        self.assertEqual(w["total_cal"], 600)
        self.assertEqual(w["workouts"], 1)
        self.assertEqual(w["water_ml"], 1000)
        self.assertEqual(w["top_meals"][0]["count"], 2)

    def test_export_csv(self):
        storage.save_food({"item_name": "Oats", "calories": 150, "protein_g": 5,
                           "carbs_g": 27, "fat_g": 3})
        csv_text = storage.export_csv("food")
        self.assertTrue(csv_text.startswith("id,ts,day,item_name"))
        self.assertIn("Oats", csv_text)
        self.assertIsNone(storage.export_csv("nope"))

    def test_macro_analytics(self):
        storage.save_food({"item_name": "Paneer", "calories": 250, "protein_g": 18,
                           "carbs_g": 4, "fat_g": 18})
        a = storage.analytics(30)
        self.assertEqual(a["macro_split"]["protein"], 18)
        self.assertEqual(a["total_workouts"], 0)

    def test_numeric_coercion_save_and_update(self):
        """String numbers from a client must not poison numeric columns."""
        eid = storage.save_food({"item_name": "Oats", "calories": "150",
                                 "protein_g": "5", "carbs_g": 27, "fat_g": "x"})
        row = storage.get_food(eid)
        self.assertEqual(row["calories"], 150)
        self.assertEqual(row["protein_g"], 5)
        self.assertEqual(row["fat_g"], 0)  # garbage → 0
        storage.update_food(eid, {"calories": "abc"})
        self.assertEqual(storage.get_food(eid)["calories"], 0)
        storage.update_food(eid, {"calories": "220"})
        self.assertEqual(storage.get_food(eid)["calories"], 220)


if __name__ == "__main__":
    unittest.main()
