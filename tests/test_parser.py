import os
import tempfile
import unittest
from unittest import mock

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import parser  # noqa: E402
import storage  # noqa: E402
import portions  # noqa: E402
import fooddb  # noqa: E402
import config  # noqa: E402


# Hermetic pinning — config may already be imported by another test module
# reading real .env values; force our own isolation regardless of order.
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "parser.db")
config.LOCAL_TZ = __import__("zoneinfo").ZoneInfo("UTC")

# Some tests swap parser._generate for Mocks and never restore it; capture
# the real function here so error-mapping tests can pin it back.
_REAL_GENERATE = parser._generate


class TestParser(unittest.TestCase):
    """Parser logic with the Gemini client mocked out — no key needed."""

    def setUp(self):
        storage.init_db()
        portions.init_portion_table()

    def _fake_generate(self, response):
        parser._generate = mock.Mock(return_value=response)
        return parser._generate

    def test_parse_food_local_takes_precedence(self):
        """Local DB parse takes precedence over Gemini for known foods."""
        d = parser.parse(["2 boiled eggs"])
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["source"], "local")
        self.assertEqual(d["calories"], 144)
        self.assertEqual(d["protein_g"], 12)

    def test_parse_food_falls_back_to_gemini_for_unknown(self):
        """Unknown food goes to Gemini for classification."""
        self._fake_generate({
            "type": "food", "item_name": "Quinoa Salad",
            "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "confidence_notes": "Mixed grain salad with vegetables",
        })
        with mock.patch.object(parser.fooddb, "parse_food", return_value=None):
            d = parser.parse(["User input: quinoa salad"])
            self.assertEqual(d["type"], "food")
            # Gemini returned 0 calories — no DB lookup succeeded
            self.assertEqual(d["calories"], 0)
            self.assertEqual(d["source"], "gemini_fallback")

    def test_parse_food_gemini_with_db_lookup(self):
        """Gemini classifies, then DB provides nutrition."""
        self._fake_generate({
            "type": "food", "item_name": "boiled egg",
            "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "confidence_notes": "2 large eggs, boiled",
        })
        # Don't mock fooddb.parse_food — let it do the real lookup
        d = parser.parse(["User input: 2 boiled eggs"])
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["calories"], 144)
        self.assertEqual(d["source"], "local")

    def test_shape_food_returns_audit_when_provided(self):
        """_shape_food uses audit dict when provided (DB result)."""
        audit = {
            "type": "food", "item_name": "Test", "calories": 200,
            "protein_g": 15, "carbs_g": 20, "fat_g": 8,
            "fiber_g": 0, "sugar_g": 0, "confidence_notes": "test",
            "needs_clarification": False, "clarify_question": "",
            "clarify_options": [], "source": "local",
            "matched_food": "test", "serving_g": 100, "qty": 1,
        }
        d = parser._shape_food({}, allow_clarify=True, audit=audit)
        self.assertEqual(d, audit)

    def test_shape_food_fallback_without_audit(self):
        """_shape_food falls back to Gemini values when no audit."""
        d = parser._shape_food({
            "item_name": "Salad", "calories": 0,
            "protein_g": 10, "carbs_g": 20, "fat_g": 5,
        }, allow_clarify=True, audit=None)
        self.assertEqual(d["calories"], 10 * 4 + 20 * 4 + 5 * 9)

    def test_parse_food(self):
        self._fake_generate({
            "type": "food", "item_name": "Biryani", "calories": 0,
            "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "needs_clarification": True, "clarify_question": "How oily?",
            "clarify_options": ["Light", "Medium", "Rich"],
        })
        with mock.patch.object(parser.fooddb, "parse_food", return_value=None):
            d = parser.parse(["User input: biryani"])
            self.assertEqual(d["type"], "food")
            self.assertTrue(d["needs_clarification"])
            self.assertEqual(len(d["clarify_options"]), 3)

    def test_parse_workout(self):
        self._fake_generate({"type": "workout", "exercise_name": "Bench Press",
                             "weight_kg": 60, "sets": 3, "reps": 8, "notes": ""})
        with mock.patch.object(parser.fooddb, "parse_food", return_value=None):
            d = parser.parse(["User input: bench pressed"])
        self.assertEqual(d["exercise_name"], "Bench Press")
        self.assertEqual(d["sets"], 3)

    def test_parse_weight_float(self):
        self._fake_generate({"type": "weight", "weight_kg": 76.4, "notes": ""})
        with mock.patch.object(parser.fooddb, "parse_food", return_value=None):
            d = parser.parse(["User input: I weigh 76.4"])
        self.assertAlmostEqual(d["weight_kg"], 76.4)

    def test_parse_water(self):
        self._fake_generate({"type": "water", "ml": 2000})
        with mock.patch.object(parser.fooddb, "parse_food", return_value=None):
            d = parser.parse(["User input: drank 2 litre paani"])
        self.assertEqual(d["type"], "water")
        self.assertEqual(d["ml"], 2000)

    def test_parse_chat_fallback(self):
        self._fake_generate({"type": "chat", "reply": "Nice! Log a meal?"})
        with mock.patch.object(parser.fooddb, "parse_food", return_value=None):
            d = parser.parse(["User input: hello"])
        self.assertEqual(d["type"], "chat")
        self.assertIn("Log a meal", d["reply"])

    def test_clarify_chains_context(self):
        seen = {}

        def fake_generate(payload, prompt):
            seen["payload"] = payload
            return {"type": "food", "item_name": "Curry", "calories": 0,
                    "protein_g": 0, "carbs_g": 0, "fat_g": 0,
                    "needs_clarification": False}
        parser._generate = mock.Mock(side_effect=fake_generate)
        d = parser.reparse_food_with_answer("chicken curry", "How oily?", "Rich", 1)
        self.assertEqual(d["type"], "food")
        self.assertFalse(d["needs_clarification"])
        self.assertIn("Rich", seen["payload"][0])

    def test_no_second_question_after_round_two(self):
        self._fake_generate({
            "type": "food", "item_name": "X", "calories": 0,
            "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "needs_clarification": True,
        })
        d = parser.parse_food(["User input: x"], clarify_round=2)
        self.assertFalse(d["needs_clarification"])

    def test_audit_fields_present_on_food_result(self):
        """All food results have audit trail fields."""
        d = parser.parse(["2 boiled eggs"])
        self.assertIn("source", d)
        self.assertIn("matched_food", d)
        self.assertIn("serving_g", d)
        self.assertIn("qty", d)

    def test_generate_maps_rate_limit_error(self):
        """429/quota exceptions surface a specific message (not a generic one)."""
        client = mock.Mock()
        client.models.generate_content.side_effect = \
            Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
        with mock.patch.object(parser, "_get_client", return_value=client), \
                mock.patch.object(parser, "_generate", _REAL_GENERATE):
            with self.assertRaises(parser.ParseError) as ctx:
                parser._generate(["x"], "prompt")
        self.assertIn("rate limit", str(ctx.exception).lower())

    def test_generate_maps_model_not_found_error(self):
        """Unknown model strings surface a model error (e.g. env misconfig)."""
        client = mock.Mock()
        client.models.generate_content.side_effect = \
            Exception("404 NOT_FOUND: models/gemini-9.9-flash not found")
        with mock.patch.object(parser, "_get_client", return_value=client), \
                mock.patch.object(parser, "_generate", _REAL_GENERATE):
            with self.assertRaises(parser.ParseError) as ctx:
                parser._generate(["x"], "prompt")
        self.assertIn("model", str(ctx.exception).lower())


class TestServingConversion(unittest.TestCase):
    """Verify that serving-based calculations are correct across the board."""

    def test_egg_serving_size(self):
        """Large egg = 50g, 72 kcal (USDA)."""
        d = fooddb.parse_local("egg")
        self.assertEqual(d["serving_g"], 50)
        self.assertEqual(d["calories"], 72)

    def test_chapati_serving_size(self):
        """1 chapati = 60g cooked, 170 kcal."""
        d = fooddb.parse_local("chapati")
        self.assertEqual(d["serving_g"], 60)
        self.assertEqual(d["calories"], 170)

    def test_rice_serving_scaling(self):
        """Rice: per-100g=130, default serving=150g → 195 kcal."""
        d = fooddb.parse_local("rice")
        self.assertEqual(d["calories"], 195)

    def test_grams_override_serving(self):
        """'200g rice' overrides default serving: 130 * 2 = 260 kcal."""
        d = fooddb.parse_local("200g rice")
        self.assertEqual(d["calories"], 260)

    def test_combo_multiplies_correctly(self):
        """'3 eggs' = 3 × 72 = 216 kcal."""
        d = fooddb.parse_local("3 eggs")
        self.assertEqual(d["calories"], 216)

    def test_multi_item_sums_correctly(self):
        """Multi-item: sum of individual servings."""
        d = fooddb.parse_local("2 eggs + 2 chapati")
        # 2 eggs = 144, 2 chapati = 340
        self.assertEqual(d["calories"], 484)


class TestLocalDBAccuracy(unittest.TestCase):
    """Spot-check that local DB values are within ±15% of USDA reference."""

    def test_egg_within_tolerance(self):
        d = fooddb.parse_local("boiled egg")
        usda = 72
        self.assertAlmostEqual(d["calories"], usda, delta=usda * 0.15)

    def test_chapati_within_tolerance(self):
        d = fooddb.parse_local("chapati")
        ref = 170
        self.assertAlmostEqual(d["calories"], ref, delta=ref * 0.15)

    def test_chicken_breast_within_tolerance(self):
        d = fooddb.parse_local("chicken breast")
        usda = 165 * 1.2  # 120g serving
        self.assertAlmostEqual(d["calories"], usda, delta=usda * 0.15)

    def test_rice_within_tolerance(self):
        d = fooddb.parse_local("rice")
        ref = 130 * 1.5  # 150g serving
        self.assertAlmostEqual(d["calories"], ref, delta=ref * 0.15)


if __name__ == "__main__":
    unittest.main()
