import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()