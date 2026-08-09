import os
import tempfile
import unittest
from unittest import mock

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import parser  # noqa: E402
import storage  # noqa: E402
import portions  # noqa: E402


class TestParser(unittest.TestCase):
    """Parser logic with the Gemini client mocked out — no key needed."""

    def setUp(self):
        storage.init_db()
        portions.init_portion_table()

    def _fake_generate(self, response):
        parser._generate = mock.Mock(return_value=response)
        return parser._generate

    def test_shape_food_reconstructs_zero_calories(self):
        fake = self._fake_generate({
            "type": "food", "item_name": "Salad",
            "calories": 0, "protein_g": 10, "carbs_g": 20, "fat_g": 5,
        })
        d = parser.parse(["User input: salad"])
        self.assertEqual(d["calories"], 10 * 4 + 20 * 4 + 5 * 9)
        fake.assert_called_once()

    def test_parse_food(self):
        self._fake_generate({
            "type": "food", "item_name": "Biryani", "calories": 600,
            "protein_g": 25, "carbs_g": 70, "fat_g": 24,
            "needs_clarification": True, "clarify_question": "How oily?",
            "clarify_options": ["Light", "Medium", "Rich"],
        })
        d = parser.parse(["User input: biryani"])
        self.assertEqual(d["type"], "food")
        self.assertTrue(d["needs_clarification"])
        self.assertEqual(len(d["clarify_options"]), 3)

    def test_parse_workout(self):
        self._fake_generate({"type": "workout", "exercise_name": "Bench Press",
                             "weight_kg": 60, "sets": 3, "reps": 8, "notes": ""})
        d = parser.parse(["User input: bench pressed"])
        self.assertEqual(d["exercise_name"], "Bench Press")
        self.assertEqual(d["sets"], 3)

    def test_parse_weight_float(self):
        self._fake_generate({"type": "weight", "weight_kg": 76.4, "notes": ""})
        d = parser.parse(["User input: I weigh 76.4"])
        self.assertAlmostEqual(d["weight_kg"], 76.4)

    def test_parse_water(self):
        self._fake_generate({"type": "water", "ml": 2000})
        d = parser.parse(["User input: drank 2 litre paani"])
        self.assertEqual(d["type"], "water")
        self.assertEqual(d["ml"], 2000)

    def test_parse_chat_fallback(self):
        self._fake_generate({"type": "chat", "reply": "Nice! Log a meal?"})
        d = parser.parse(["User input: hello"])
        self.assertEqual(d["type"], "chat")
        self.assertIn("Log a meal", d["reply"])

    def test_clarify_chains_context(self):
        seen = {}

        def fake_generate(payload, prompt):
            seen["payload"] = payload
            return {"type": "food", "item_name": "Curry", "calories": 450,
                    "protein_g": 30, "carbs_g": 20, "fat_g": 26,
                    "needs_clarification": False}
        parser._generate = mock.Mock(side_effect=fake_generate)
        d = parser.reparse_food_with_answer("chicken curry", "How oily?", "Rich", 1)
        self.assertEqual(d["type"], "food")
        self.assertFalse(d["needs_clarification"])
        self.assertIn("Rich", seen["payload"][0])

    def test_no_second_question_after_round_two(self):
        self._fake_generate({
            "type": "food", "item_name": "X", "calories": 100,
            "protein_g": 5, "carbs_g": 10, "fat_g": 4,
            "needs_clarification": True,
        })
        d = parser.parse_food(["User input: x"], clarify_round=2)
        self.assertFalse(d["needs_clarification"])


if __name__ == "__main__":
    unittest.main()