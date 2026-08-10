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
        for t in ("food", "workout", "weight", "water", "goal", "portion_memory"):
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

    def test_workout_and_weight(self):
        storage.save_workout({"exercise_name": "Squat", "weight_kg": 60,
                              "sets": 3, "reps": 10, "notes": "", "_raw": "squats"})
        storage.save_weight(77.5, "weigh-in")
        d = storage.today_data()
        self.assertEqual(d["workouts"][0]["exercise_name"], "Squat")
        t = storage.weight_trend(60)
        self.assertEqual(t["current"], 77.5)

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
        storage.undo_water()
        self.assertEqual(storage.today_water()["ml"], 250)
        # delete the remaining entry by its actual ID
        with db.connect() as c:
            row = c.execute("SELECT id FROM water ORDER BY id DESC LIMIT 1").fetchone()
        storage.delete_entry("water", row["id"])
        self.assertEqual(storage.today_water()["ml"], 0)

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