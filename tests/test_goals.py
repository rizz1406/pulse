import os
import tempfile
import unittest
from datetime import datetime, timedelta

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["LOCAL_TZ"] = "UTC"

import config  # noqa: E402
import storage  # noqa: E402
import goals  # noqa: E402
import db  # noqa: E402
import portions  # noqa: E402


# Hermetic pinning — config may already be imported by another test module
# reading real .env values; force our own isolation regardless of order.
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "goals.db")
config.LOCAL_TZ = __import__("zoneinfo").ZoneInfo("UTC")


def clean():
    with db.connect() as c:
        for t in ("food", "workout", "weight", "water", "goal", "portion_memory"):
            c.execute(f"DELETE FROM {t}")


class TestGoals(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        goals.init_goal_table()
        portions.init_portion_table()
        clean()

    def test_calculate_basic(self):
        t = goals.calculate(80, 180, 25, "male", "moderate", "cut_steady")
        self.assertGreater(t["calories"], 1200)
        self.assertEqual(t["protein"], 160)          # 80 kg * 2.0
        self.assertEqual(t["fat"], 64)               # 80 * 0.8
        self.assertGreaterEqual(t["carbs"], 0)

    def test_objective_order(self):
        args = (80, 180, 25, "male", "moderate")
        cut = goals.calculate(*args, "cut_fast")["calories"]
        steady = goals.calculate(*args, "cut_steady")["calories"]
        maint = goals.calculate(*args, "maintain")["calories"]
        bulk = goals.calculate(*args, "lean_bulk")["calories"]
        self.assertLess(cut, steady)
        self.assertLess(steady, maint)
        self.assertLess(maint, bulk)

    def test_targets_none_without_goal(self):
        self.assertIsNone(goals.current_targets())

    def test_save_and_auto_recalc_on_new_weight(self):
        goals.save_goal(180, 25, "male", "moderate", "cut_steady", 90)
        t = goals.current_targets()
        self.assertIsNotNone(t)
        self.assertEqual(t["weight"], 90)
        p_before = t["protein"]
        # log a new, lighter weight → targets must shrink
        storage.save_weight(80, "weigh-in")
        t2 = goals.current_targets()
        self.assertEqual(t2["weight"], 80)
        self.assertLess(t2["protein"], p_before)

    def test_save_does_not_duplicate_daily_weight(self):
        goals.save_goal(180, 25, "male", "moderate", "maintain", 75)
        goals.save_goal(180, 25, "male", "moderate", "maintain", 75)
        with db.connect() as c:
            n = c.execute("SELECT COUNT(*) n FROM weight").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_bmi(self):
        self.assertEqual(goals.bmi(80, 180), 24.7)  # 80 / 1.8² = 24.69…
        self.assertEqual(goals.bmi(50, 160), 19.5)
        self.assertIsNone(goals.bmi(None, 180))
        self.assertIsNone(goals.bmi(80, None))
        self.assertIsNone(goals.bmi(80, 0))

    def _seed_bulk_trend(self, gain, count=15):
        goals.save_goal(180, 24, "male", "moderate", "lean_bulk", 76.5)
        today = datetime.now(config.LOCAL_TZ).date()
        first = today - timedelta(days=14)
        with db.connect() as c:
            c.execute("DELETE FROM weight")
            c.execute("UPDATE goal SET updated=?, last_adapted=NULL, "
                      "calorie_adjustment=0 WHERE id=1",
                      (first.strftime("%Y-%m-%d 00:00:00"),))
            for i in range(count):
                day = first + timedelta(days=round(i * 14 / max(count - 1, 1)))
                kg = 76.5 + gain * i / max(count - 1, 1)
                c.execute(
                    "INSERT INTO weight (ts, day, weight_kg, notes, photo) "
                    "VALUES (?,?,?,?,?)",
                    (f"{day.isoformat()} 07:00:00", day.isoformat(), kg, "test", ""),
                )

    def test_adaptive_coach_waits_for_enough_data(self):
        self._seed_bulk_trend(0.4, count=6)
        coach = goals.maybe_adapt_targets()
        self.assertFalse(coach["enough_data"])
        self.assertEqual(goals.current_targets()["calorie_adjustment"], 0)

    def test_adaptive_coach_keeps_ideal_gain_target(self):
        self._seed_bulk_trend(0.4)
        coach = goals.maybe_adapt_targets()
        self.assertTrue(coach["enough_data"])
        self.assertGreaterEqual(coach["rate_kg_per_week"], 0.15)
        self.assertLessEqual(coach["rate_kg_per_week"], 0.25)
        self.assertEqual(goals.current_targets()["calorie_adjustment"], 0)

    def test_adaptive_coach_adds_calories_for_slow_gain_once_weekly(self):
        self._seed_bulk_trend(0)
        before = goals.current_targets()["calories"]
        goals.maybe_adapt_targets()
        after = goals.current_targets()
        self.assertEqual(after["calorie_adjustment"], 150)
        self.assertEqual(after["calories"], before + 150)
        goals.maybe_adapt_targets()
        self.assertEqual(goals.current_targets()["calorie_adjustment"], 150)

    def test_adaptive_coach_reduces_calories_for_fast_gain(self):
        self._seed_bulk_trend(0.8)
        goals.maybe_adapt_targets()
        self.assertEqual(goals.current_targets()["calorie_adjustment"], -150)


if __name__ == "__main__":
    unittest.main()
