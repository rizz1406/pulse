import os
import tempfile
import unittest
from unittest import mock

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import parser  # noqa: E402
import storage  # noqa: E402
import portions  # noqa: E402
import fooddb  # noqa: E402
import goals  # noqa: E402
import config  # noqa: E402


# Hermetic pinning — config may already be imported by another test module
# reading real .env values; force our own isolation regardless of order.
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "parser.db")
config.LOCAL_TZ = __import__("zoneinfo").ZoneInfo("UTC")

# Some tests swap parser._generate for Mocks and never restore it; capture
# the real function here so error-mapping tests can pin it back.
_REAL_GENERATE = parser._generate


class TestParser(unittest.TestCase):
    """Parser logic with the AI client mocked out — no key needed."""

    def setUp(self):
        storage.init_db()
        portions.init_portion_table()

    def _fake_generate(self, response):
        parser._generate = mock.Mock(return_value=response)
        return parser._generate

    def test_parse_food_ai_estimates(self):
        """AI-estimated food returns nutrition from the model."""
        self._fake_generate({
            "type": "food", "item_name": "2 boiled eggs",
            "calories": 144, "protein_g": 12, "carbs_g": 0, "fat_g": 10,
            "confidence_notes": "2 large eggs, boiled",
        })
        d = parser.parse(["2 boiled eggs"])
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["source"], "ai_estimate")
        self.assertEqual(d["calories"], 144)

    def test_parse_food_zero_macros_goes_to_fallback(self):
        """Unknown food + no AI macros → groq_fallback source."""
        self._fake_generate({
            "type": "food", "item_name": "Quinoa Salad",
            "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "confidence_notes": "Mixed grain salad with vegetables",
        })
        d = parser.parse(["User input: quinoa salad"])
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["calories"], 0)
        self.assertEqual(d["source"], "groq_fallback")

    def test_shape_food_uses_ai_macros(self):
        """_shape_food uses AI macros when no audit provided."""
        d = parser._shape_food({
            "item_name": "Salad", "calories": 0,
            "protein_g": 10, "carbs_g": 20, "fat_g": 5,
        }, allow_clarify=True)
        self.assertEqual(d["calories"], 10 * 4 + 20 * 4 + 5 * 9)

    def test_parse_food_ai_with_pills(self):
        """Food with no macros + ambiguity check returns pills."""
        self._fake_generate({
            "type": "food", "item_name": "Pav Bhaji", "calories": 0,
            "needs_clarification": True, "clarify_question": "How much butter?",
            "clarify_options": ["Lite", "Regular", "Extra butter"],
        })
        with mock.patch.object(parser, "_check_ambiguity",
                               return_value={"requires_clarification": False}):
            d = parser.parse(["User input: pav bhaji"])
        self.assertEqual(d["type"], "food")
        self.assertTrue(d["needs_clarification"])
        self.assertEqual(len(d["clarify_options"]), 3)

    def test_parse_ai_estimate_directly(self):
        """Unknown food + AI macros → direct preview, no ambiguity re-check."""
        self._fake_generate({
            "type": "food", "item_name": "Chicken Shami Kabab",
            "quantity": 2, "calories": 240, "protein_g": 26,
            "carbs_g": 8, "fat_g": 12,
            "serving_note": "2 medium kebabs, pan-tossed",
        })
        with mock.patch.object(parser, "_check_ambiguity") as amb:
            d = parser.parse(["User input: shami kabab"])
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["source"], "ai_estimate")
        self.assertEqual(d["calories"], 240)
        self.assertFalse(d["needs_clarification"])
        amb.assert_not_called()

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
        self._fake_generate({
            "type": "food", "item_name": "2 eggs", "calories": 144,
            "protein_g": 12, "carbs_g": 0, "fat_g": 10,
        })
        d = parser.parse(["2 boiled eggs"])
        self.assertIn("source", d)
        self.assertIn("matched_food", d)
        self.assertIn("serving_g", d)
        self.assertIn("qty", d)

    def test_generate_maps_rate_limit_error(self):
        """429/quota exceptions surface a specific message (not a generic one)."""
        client = mock.Mock()
        client.chat.completions.create.side_effect = \
            Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
        with mock.patch.object(parser, "_get_client", return_value=client), \
                mock.patch.object(parser, "_generate", _REAL_GENERATE):
            with self.assertRaises(parser.ParseError) as ctx:
                parser._generate(["x"], "prompt")
        self.assertIn("rate limit", str(ctx.exception).lower())

    def test_generate_maps_model_not_found_error(self):
        """Unknown model strings surface a model error (e.g. env misconfig)."""
        client = mock.Mock()
        client.chat.completions.create.side_effect = \
            Exception("404 NOT_FOUND: models/gemini-9.9-flash not found")
        with mock.patch.object(parser, "_get_client", return_value=client), \
                mock.patch.object(parser, "_generate", _REAL_GENERATE):
            with self.assertRaises(parser.ParseError) as ctx:
                parser._generate(["x"], "prompt")
        self.assertIn("model", str(ctx.exception).lower())

    def test_generate_returns_fallback_on_other_api_error(self):
        """Non-quota/model errors return a clean error dict, not an exception."""
        client = mock.Mock()
        client.chat.completions.create.side_effect = \
            Exception("upstream weirdness happened")
        with mock.patch.object(parser, "_get_client", return_value=client), \
                mock.patch.object(parser, "_generate", _REAL_GENERATE):
            d = parser._generate(["x"], "prompt")
        self.assertEqual(d["type"], "chat")
        self.assertIn("error", d)

    def test_generate_parses_groq_json_response(self):
        """A normal Groq response's content is parsed into a dict."""
        client = mock.Mock()
        msg = mock.Mock()
        msg.content = '{"type": "food", "item_name": "paneer tikka"}'
        client.chat.completions.create.return_value = \
            mock.Mock(choices=[mock.Mock(message=msg)])
        with mock.patch.object(parser, "_get_client", return_value=client), \
                mock.patch.object(parser, "_generate", _REAL_GENERATE):
            d = parser._generate(["x"], "prompt")
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["item_name"], "paneer tikka")
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            parser.GROQ_MODEL)


class TestServingConversion(unittest.TestCase):
    """Verify that serving-based calculations are correct across the board.
    These test the fooddb module directly (still used for barcode)."""

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


class TestAmbiguityCheck(unittest.TestCase):
    """Tests for the dynamic AI clarification layer."""

    def setUp(self):
        storage.init_db()
        portions.init_portion_table()

    def _fake_generate(self, response):
        parser._generate = mock.Mock(return_value=response)
        return parser._generate

    def test_unambiguous_food_skips_clarification(self):
        """Food with macros from AI skips ambiguity check."""
        self._fake_generate({
            "type": "food", "item_name": "2 boiled eggs",
            "calories": 144, "protein_g": 12, "carbs_g": 0, "fat_g": 10,
        })
        d = parser.parse(["2 boiled eggs"])
        self.assertEqual(d["type"], "food")
        self.assertFalse(d.get("needs_clarification", False))
        self.assertEqual(d["source"], "ai_estimate")

    def test_ambiguous_food_returns_pills(self):
        """Ambiguous food with no macros returns pills from ambiguity check."""
        self._fake_generate({
            "type": "food", "item_name": "chai",
            "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "confidence_notes": "Indian tea",
        })
        amb_result = {
            "requires_clarification": True,
            "question": "How was your chai prepared?",
            "pills": [
                {"label": "Milk + Sugar", "text": "1 cup chai with milk and sugar"},
                {"label": "Black", "text": "1 cup black tea no sugar"},
            ],
            "default_fallback": "1 cup chai with milk and sugar",
        }
        with mock.patch.object(parser, "_check_ambiguity", return_value=amb_result):
            d = parser.parse(["User input: chai"])
            self.assertEqual(d["type"], "food")
            self.assertTrue(d.get("needs_clarification"))
            self.assertIn("pills", d)
            self.assertEqual(len(d["pills"]), 2)
            self.assertEqual(d["pills"][0]["label"], "Milk + Sugar")
            self.assertIn("default_fallback", d)

    def test_unambiguous_groq_food_skips_pills(self):
        """Unambiguous food from Groq skips pills."""
        self._fake_generate({
            "type": "food", "item_name": "boiled egg",
            "calories": 144, "protein_g": 12, "carbs_g": 0, "fat_g": 10,
        })
        amb_result = {"requires_clarification": False}
        with mock.patch.object(parser, "_check_ambiguity", return_value=amb_result):
            d = parser.parse(["User input: 2 boiled eggs"])
            self.assertEqual(d["type"], "food")
            self.assertFalse(d.get("needs_clarification"))

    def test_parse_with_pill_resolves_via_ai(self):
        """parse_with_pill returns a shaped food dict via AI."""
        self._fake_generate({
            "type": "food", "item_name": "chai with milk", "calories": 120,
            "protein_g": 3, "carbs_g": 20, "fat_g": 4,
        })
        d = parser.parse_with_pill("1 cup chai with milk and sugar", "chai")
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["calories"], 120)


class TestWeeklyAnalytics(unittest.TestCase):
    """Tests for weekly macro streak and target progress."""

    def setUp(self):
        storage.init_db()
        portions.init_portion_table()
        goals.init_goal_table()
        from db import connect
        with connect() as c:
            c.execute("DELETE FROM food")
            c.execute("DELETE FROM workout")
            c.execute("DELETE FROM weight")
            c.execute("DELETE FROM water")

    def test_weekly_macro_analytics_empty(self):
        result = storage.weekly_macro_analytics(7)
        self.assertEqual(result["days_logged"], 0)
        self.assertEqual(result["avg_cal"], 0)
        self.assertEqual(result["cal_adherence"], 0)
        self.assertEqual(result["protein_adherence"], 0)
        self.assertEqual(len(result["daily"]), 7)

    def test_weekly_macro_analytics_with_data(self):
        from datetime import timedelta
        today = storage._now().date()
        storage.save_food({
            "item_name": "Test Meal", "calories": 500,
            "protein_g": 30, "carbs_g": 50, "fat_g": 15,
            "fiber_g": 5, "sugar_g": 10,
        })
        result = storage.weekly_macro_analytics(7)
        self.assertEqual(result["days_logged"], 1)
        self.assertEqual(result["total_cal"], 500)
        self.assertEqual(result["total_protein"], 30)
        self.assertGreater(result["cal_adherence"], 0)
        self.assertGreater(result["protein_adherence"], 0)
        self.assertEqual(len(result["daily"]), 7)

    def test_weekly_streak_count(self):
        result = storage.weekly_macro_analytics(7)
        self.assertIn("streak", result)
        self.assertIsInstance(result["streak"], int)

    def test_target_adherence_capped(self):
        storage.save_food({
            "item_name": "Mega Meal", "calories": 10000,
            "protein_g": 500, "carbs_g": 800, "fat_g": 300,
        })
        result = storage.weekly_macro_analytics(7)
        self.assertLessEqual(result["cal_adherence"], 150)
        self.assertLessEqual(result["protein_adherence"], 150)


class TestVisionModel(unittest.TestCase):
    """Tests for vision model switching and photo food flow."""

    def setUp(self):
        storage.init_db()
        portions.init_portion_table()
        self._orig_generate = parser._generate
        parser._generate = _REAL_GENERATE

    def tearDown(self):
        parser._generate = self._orig_generate

    def test_generate_uses_gemini_for_image(self):
        """_generate routes images to Gemini vision (Groq has no vision)."""
        image_payload = [
            {"mime_type": "image/jpeg", "data": b"\x89PNG fake image data"},
        ]
        called_with = {}

        def fake_create(**kwargs):
            called_with["model"] = kwargs["model"]
            msg = mock.Mock()
            msg.content = '{"type": "food", "item_name": "test"}'
            choice = mock.Mock()
            choice.message = msg
            resp = mock.Mock()
            resp.choices = [choice]
            return resp

        mock_chat = mock.Mock()
        mock_chat.completions.create = fake_create
        mock_client = mock.Mock()
        mock_client.chat = mock_chat

        config.GEMINI_API_KEY = "test-gemini-key"
        try:
            with mock.patch.object(parser, "_get_vision_client",
                                   return_value=mock_client):
                parser._generate(image_payload, "Identify this food")
        finally:
            config.GEMINI_API_KEY = ""
        self.assertEqual(called_with["model"], config.GEMINI_VISION_MODEL)

    def test_generate_no_gemini_key_for_image(self):
        """Without GEMINI_API_KEY, images return a friendly chat error."""
        image_payload = [
            {"mime_type": "image/jpeg", "data": b"\x89PNG fake image data"},
        ]
        config.GEMINI_API_KEY = ""
        result = parser._generate(image_payload, "Identify this food")
        self.assertEqual(result["type"], "chat")
        self.assertEqual(result["error"], "no_gemini_key")

    def test_generate_uses_text_model_for_text_only(self):
        """_generate uses GROQ_MODEL for text-only payload."""
        called_with = {}

        def fake_create(**kwargs):
            called_with["model"] = kwargs["model"]
            msg = mock.Mock()
            msg.content = '{"type": "food", "item_name": "test"}'
            choice = mock.Mock()
            choice.message = msg
            resp = mock.Mock()
            resp.choices = [choice]
            return resp

        mock_chat = mock.Mock()
        mock_chat.completions.create = fake_create
        mock_client = mock.Mock()
        mock_client.chat = mock_chat

        with mock.patch.object(parser, "_get_client", return_value=mock_client):
            parser._generate(["2 eggs"], "Parse this")
        self.assertEqual(called_with["model"], parser.GROQ_MODEL)

    def test_photo_flow_returns_food_with_pills(self):
        """Photo payload → AI identifies food → pills returned."""
        image_payload = [
            {"mime_type": "image/jpeg", "data": b"\x89PNG fake image data"},
        ]
        with mock.patch.object(parser, "_generate") as mock_gen, \
             mock.patch.object(parser, "_check_ambiguity") as mock_amb:
            mock_gen.return_value = {
                "type": "food",
                "item_name": "Masala Omelette",
                "quantity": 1,
                "unit": "plate",
                "confidence_notes": "looks like 2-egg omelette with veggies",
            }
            mock_amb.return_value = {
                "requires_clarification": True,
                "pills": [
                    {"label": "2-egg omelette", "text": "2 egg omelette"},
                    {"label": "3-egg omelette", "text": "3 egg omelette"},
                ],
                "default_fallback": "Masala Omelette",
            }
            result = parser.parse(image_payload)
        self.assertEqual(result["type"], "food")
        self.assertTrue(result.get("needs_clarification"))
        self.assertEqual(len(result["pills"]), 2)

    def test_photo_flow_ambiguity_for_unknown_food(self):
        """Photo: unknown dish → ambiguity pills for user confirmation."""
        image_payload = [
            {"mime_type": "image/jpeg", "data": b"\x89PNG fake image data"},
        ]
        with mock.patch.object(parser, "_generate") as mock_gen, \
             mock.patch.object(parser, "_check_ambiguity") as mock_amb:
            mock_gen.return_value = {
                "type": "food",
                "item_name": "Mother's Special Curry",
                "quantity": 1,
                "unit": "plate",
            }
            mock_amb.return_value = {
                "requires_clarification": True,
                "pills": [
                    {"label": "Home-style", "text": "home style curry"},
                    {"label": "Creamy", "text": "creamy curry"},
                ],
                "default_fallback": "home style curry",
            }
            result = parser.parse(image_payload)
        mock_gen.assert_called_once()
        mock_amb.assert_called_once()
        self.assertEqual(result["type"], "food")
        self.assertTrue(result.get("needs_clarification"))
        self.assertEqual(len(result["pills"]), 2)


if __name__ == "__main__":
    unittest.main()
