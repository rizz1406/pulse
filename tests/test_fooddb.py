import unittest
import fooddb


class TestFoodDB(unittest.TestCase):
    """Tests for the local food database — serving-based nutrition logic."""

    # ── Per-serving items: eggs ──
    def test_one_boiled_egg(self):
        """1 large boiled egg = 72 kcal (USDA)."""
        d = fooddb.parse_local("boiled egg")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 72)
        self.assertEqual(d["protein_g"], 6)
        self.assertEqual(d["matched_food"], "boiled egg")
        self.assertEqual(d["serving_g"], 50)
        self.assertEqual(d["qty"], 1)
        self.assertEqual(d["source"], "local")

    def test_two_boiled_eggs(self):
        """2 boiled eggs = 144 kcal."""
        d = fooddb.parse_local("2 boiled eggs")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 144)
        self.assertEqual(d["protein_g"], 12)
        self.assertEqual(d["matched_food"], "boiled egg")
        self.assertEqual(d["qty"], 2)

    def test_three_eggs(self):
        """3 eggs via unit extraction."""
        d = fooddb.parse_local("3 eggs")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 216)
        self.assertEqual(d["protein_g"], 18)

    def test_one_boiled_egg_spelled_out(self):
        """'one boiled egg' behaves like '1 boiled egg' (72 kcal, qty 1)."""
        d = fooddb.parse_local("one boiled egg")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 72)
        self.assertEqual(d["protein_g"], 6)
        self.assertEqual(d["qty"], 1)
        self.assertEqual(d["matched_food"], "boiled egg")

    def test_two_roti_spelled_out(self):
        """'two roti' behaves like '2 roti' (340 kcal, qty 2)."""
        d = fooddb.parse_local("two roti")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 340)
        self.assertEqual(d["qty"], 2)

    def test_fried_egg(self):
        """1 fried egg = 91 kcal."""
        d = fooddb.parse_local("fried egg")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 91)

    # ── Per-serving items: chapati/roti ──
    def test_one_chapati(self):
        """1 chapati = 170 kcal (60g cooked)."""
        d = fooddb.parse_local("chapati")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 170)
        self.assertEqual(d["matched_food"], "chapati")
        self.assertEqual(d["serving_g"], 60)

    def test_two_chapati(self):
        """2 chapatis = 340 kcal."""
        d = fooddb.parse_local("2 chapati")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 340)
        self.assertEqual(d["qty"], 2)

    def test_two_roti(self):
        """2 roti = 340 kcal."""
        d = fooddb.parse_local("2 roti")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 340)

    # ── Per-100g items with default serving ──
    def test_chicken_breast_default_serving(self):
        """120g chicken breast (default serving) = 198 kcal."""
        d = fooddb.parse_local("chicken breast")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 198)  # 165 * 1.2
        self.assertEqual(d["serving_g"], 120)
        self.assertEqual(d["qty"], 1)

    def test_chicken_breast_explicit_grams(self):
        """200g chicken breast = 330 kcal."""
        d = fooddb.parse_local("200g chicken breast")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 330)  # 165 * 2
        self.assertEqual(d["source"], "local")

    def test_rice_default_serving(self):
        """150g rice (default serving) = 195 kcal."""
        d = fooddb.parse_local("rice")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 195)  # 130 * 1.5

    def test_rice_explicit_grams(self):
        """300g rice = 390 kcal."""
        d = fooddb.parse_local("300g rice")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 390)  # 130 * 3

    # ── The exact case from the user's report ──
    def test_two_boiled_eggs_plus_two_chapati(self):
        """'2 boiled eggs + 2 chapati' should be ~484 kcal, NOT 490.

        2 boiled eggs = 144 kcal
        2 chapati = 340 kcal
        Total = 484 kcal
        """
        d = fooddb.parse_local("2 boiled eggs + 2 chapati")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 484)
        self.assertIn("Boiled Egg", d["item_name"])
        self.assertIn("Chapati", d["item_name"])
        self.assertEqual(d["source"], "local")
        # Audit trail present
        self.assertIn("matched_food", d)
        self.assertIn("serving_g", d)
        self.assertIn("qty", d)

    def test_two_eggs_plus_two_chapati(self):
        """'2 eggs + 2 chapati' via shorthand."""
        d = fooddb.parse_local("2 eggs + 2 chapati")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 484)

    # ── Multi-item parsing ──
    def test_multi_item_with_comma(self):
        """Comma-separated items."""
        d = fooddb.parse_local("2 chapati, chicken curry, 2 boiled eggs")
        self.assertIsNotNone(d)
        self.assertGreater(d["calories"], 400)
        self.assertIn("Chapati", d["item_name"])
        self.assertIn("Boiled Egg", d["item_name"])

    def test_multi_item_with_and(self):
        """'and' separated items."""
        d = fooddb.parse_local("2 roti and paneer")
        self.assertIsNotNone(d)
        # 2 roti = 340, paneer 60g = 159
        self.assertEqual(d["calories"], 499)

    def test_multi_item_with_plus(self):
        """'+' separated items."""
        d = fooddb.parse_local("rice + dal + 2 boiled eggs")
        self.assertIsNotNone(d)
        self.assertGreater(d["calories"], 300)

    # ── Quantity extraction ──
    def test_qty_with_x_multiplier(self):
        """'2x banana' = 2 bananas."""
        d = fooddb.parse_local("2x banana")
        self.assertIsNotNone(d)
        # banana per-100g=89, serving=120g, qty=2 → 89*1.2*2 = 213.6 ≈ 214
        self.assertEqual(d["calories"], 214)

    def test_qty_half_bowl(self):
        """'half bowl rice' = 0.5 servings."""
        d = fooddb.parse_local("half bowl rice")
        self.assertIsNotNone(d)
        # rice per-100g=130, serving=150g, qty=0.5 → 130*1.5*0.5 = 97.5 ≈ 98
        self.assertEqual(d["calories"], 98)

    def test_qty_quarter(self):
        """'quarter plate biryani' = 0.25 servings."""
        d = fooddb.parse_local("quarter plate biryani")
        self.assertIsNotNone(d)
        # biryani per-100g=180, serving=250g, qty=0.25 → 180*2.5*0.25 = 112.5 → 112
        self.assertEqual(d["calories"], 112)

    # ── Explicit grams/ml ──
    def test_explicit_grams_dal(self):
        """'200g dal' = 232 kcal."""
        d = fooddb.parse_local("200g dal")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 232)  # 116 * 2

    def test_explicit_ml_juice(self):
        """'250ml juice' = 113 kcal."""
        d = fooddb.parse_local("250ml juice")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 112)  # 45 * 2.5 = 112.5 → rounds to 112

    # ── Word matching fallback ──
    def test_word_match_chicken_curry(self):
        """'chicken curry with rice' matches via word overlap."""
        d = fooddb.parse_local("chicken curry with rice")
        self.assertIsNotNone(d)
        self.assertGreater(d["calories"], 0)

    def test_not_found(self):
        """Unknown food returns None."""
        d = fooddb.parse_local("quantum physics textbook")
        self.assertIsNone(d)

    # ── Audit trail ──
    def test_audit_trail_present(self):
        """Every result has audit fields."""
        d = fooddb.parse_local("2 eggs")
        self.assertIn("source", d)
        self.assertIn("matched_food", d)
        self.assertIn("serving_g", d)
        self.assertIn("qty", d)
        self.assertEqual(d["source"], "local")

    def test_audit_trail_multi(self):
        """Multi-item results have audit trail."""
        d = fooddb.parse_local("2 eggs + 2 chapati")
        self.assertIn("source", d)
        self.assertIn("matched_food", d)
        self.assertIn("local DB:", d["confidence_notes"])

    # ── parse_food tier routing ──
    def test_parse_food_tier1_local(self):
        """parse_food routes to local DB first."""
        d = fooddb.parse_food("2 boiled eggs")
        self.assertIsNotNone(d)
        self.assertEqual(d["type"], "food")
        self.assertEqual(d["source"], "local")

    def test_parse_food_returns_none_for_unknown(self):
        """Unknown food returns None (caller should try Gemini)."""
        d = fooddb.parse_food("quantum entanglement")
        self.assertIsNone(d)

    # ── Regression: quantity handling fixes ──
    def test_coke_alias_330ml(self):
        """"330ml Coke" resolves locally via the coke alias (= 139 kcal)."""
        d = fooddb.parse_local("330ml Coke")
        self.assertIsNotNone(d)
        self.assertEqual(d["source"], "local")
        self.assertEqual(d["calories"], 139)  # 42 × 3.3
        self.assertEqual(d["matched_food"], "coke")

    def test_two_glasses_milk(self):
        """"2 glasses milk" = 2 default servings of milk (244 kcal), not 1."""
        d = fooddb.parse_local("2 glasses milk")
        self.assertIsNotNone(d)
        self.assertEqual(d["qty"], 2)
        self.assertEqual(d["calories"], 244)

    def test_generic_count_biryani(self):
        """"2 biryani" = 2 × default serving (900 kcal), not a dropped count."""
        d = fooddb.parse_local("2 biryani")
        self.assertIsNotNone(d)
        self.assertEqual(d["qty"], 2)
        self.assertEqual(d["calories"], 900)  # 180 × 2.5 × 2

    def test_two_eggs_not_generic_count(self):
        """"2 eggs" still uses the per-serving egg definition, not the generic path."""
        d = fooddb.parse_local("2 eggs")
        self.assertEqual(d["qty"], 2)
        self.assertEqual(d["calories"], 144)  # 72 × 2, never 2 × 100g

    def test_litre_waterish_not_food(self):
        """Generic count must not hijack gram/ml inputs (already consumed earlier)."""
        d = fooddb.parse_local("2 200g rice")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 260)  # gram mode still wins


class TestFatSecret(unittest.TestCase):
    """Tests for FatSecret matching logic (semantic scoring, not highest-cal)."""

    def test_semantic_score_exact_match(self):
        """Exact word match scores higher than partial."""
        score1 = fooddb._fs_semantic_score({"cornflakes"}, "Cornflakes")
        score2 = fooddb._fs_semantic_score({"cornflakes"}, "Kellogg's Corn Flakes")
        self.assertGreater(score1, score2)  # brand penalty

    def test_semantic_score_brand_penalty(self):
        """Brand names are penalized."""
        score_generic = fooddb._fs_semantic_score({"chips"}, "Potato Chips")
        score_branded = fooddb._fs_semantic_score({"chips"}, "Lays Classic Potato Chips")
        self.assertGreater(score_generic, score_branded)

    def test_parse_serving_description_per100g(self):
        """Parse standard per-100g description."""
        desc = "Per 100g - Calories: 231kcal | Fat: 3.57g | Carbs: 47.15g | Protein: 7.58g"
        result = fooddb._fs_parse_serving_description(desc)
        self.assertIsNotNone(result)
        self.assertEqual(result["cal_per100g"], 231)
        self.assertAlmostEqual(result["p_per100g"], 7.6, places=1)
        self.assertEqual(result["ref_amount"], 100.0)

    def test_parse_serving_description_per_cup(self):
        """Parse per-cup description and normalize to per-100g."""
        desc = "Per 1 cup - Calories: 150kcal | Fat: 2g | Carbs: 30g | Protein: 4g"
        result = fooddb._fs_parse_serving_description(desc)
        self.assertIsNotNone(result)
        # 1 cup ≈ 240ml/g, so normalize: 150 * (100/240) ≈ 63
        self.assertGreater(result["cal_per100g"], 0)
        self.assertLess(result["cal_per100g"], 150)

    def test_fs_pick_best_semantic_not_highest_cal(self):
        """Best result is chosen by semantic match, not highest calories."""
        results = [
            {"food_name": "Special K Cereal", "food_description":
             "Per 100g - Calories: 370kcal | Fat: 1g | Carbs: 84g | Protein: 7g"},
            {"food_name": "Cornflakes", "food_description":
             "Per 100g - Calories: 357kcal | Fat: 0.3g | Carbs: 84g | Protein: 7g"},
        ]
        best = fooddb._fs_pick_best(results, {"cornflakes"})
        self.assertIsNotNone(best)
        self.assertEqual(best[2], "Cornflakes")  # semantic match, not Special K

    # ── Regression: FatSecret serving/unit handling ──
    def test_ml_description_normalized_to_per100(self):
        """"Per 330ml" values scale to per-100ml, never left as per-330ml."""
        desc = "Per 330ml - Calories: 139kcal | Fat: 0g | Carbs: 36.4g | Protein: 0g"
        r = fooddb._fs_parse_serving_description(desc)
        self.assertEqual(r["cal_per100g"], 42)   # 139 × 100/330
        self.assertEqual(r["per_serving"]["cal"], 139)

    def test_burger_count_description_not_scaled_by_100(self):
        """"Per 1 burger (215g)" must not be multiplied by 100 (invent calories)."""
        desc = "Per 1 burger (215g) - Calories: 505kcal | Fat: 25g | Carbs: 40g | Protein: 25g"
        r = fooddb._fs_parse_serving_description(desc)
        self.assertEqual(r["cal_per100g"], 505)
        self.assertEqual(r["per_serving"]["cal"], 505)

    def test_cup_keeps_per_serving_values(self):
        """"Per 1 cup" keeps the exact listed per-cup values."""
        desc = "Per 1 cup - Calories: 150kcal | Fat: 2g | Carbs: 30g | Protein: 4g"
        r = fooddb._fs_parse_serving_description(desc)
        self.assertEqual(r["per_serving"]["cal"], 150)
        self.assertEqual(r["cal_per100g"], 62)  # 150 × 100/240

    def test_real_serving_grams_extracted(self):
        """"Per 200g" exposes the real 200g serving instead of 100g."""
        desc = "Per 200g - Calories: 400kcal | Fat: 10g | Carbs: 50g | Protein: 20g"
        r = fooddb._fs_parse_serving_description(desc)
        self.assertEqual(r["real_serving_g"], 200)
        self.assertEqual(r["cal_per100g"], 200)

    def test_fs_plural_stemming(self):
        """"big macs" matches "Big Mac" via plural stemming."""
        from unittest import mock
        results = [
            {"food_name": "Big Mac", "food_description":
             "Per 100g - Calories: 257kcal | Fat: 15g | Carbs: 20g | Protein: 12g"},
            {"food_name": "Big Mac Meal", "food_description":
             "Per 100g - Calories: 350kcal | Fat: 18g | Carbs: 35g | Protein: 14g"},
        ]
        best = fooddb._fs_pick_best(results, {"big", "macs"})
        self.assertEqual(best[2], "Big Mac")

    def test_parse_fatsecret_uses_real_serving(self):
        """parse_fatsecret uses the description's real serving (200g), not 100g."""
        from unittest import mock
        results = [{"food_name": "Paneer Tikka", "food_description":
                    "Per 200g - Calories: 400kcal | Fat: 10g | Carbs: 50g | Protein: 20g"}]
        with mock.patch.object(fooddb, "config") as cfg, \
                mock.patch.object(fooddb, "_fs_search", return_value=results):
            cfg.FATSECRET_CLIENT_ID = "test"
            d = fooddb.parse_fatsecret("paneer tikka")
        self.assertIsNotNone(d)
        self.assertEqual(d["serving_g"], 200)
        self.assertEqual(d["calories"], 400)

    def test_parse_fatsecret_milkshake_gram_mode(self):
        """'330ml X' scales per-100ml values to the full 330ml."""
        from unittest import mock
        results = [{"food_name": "Chocolate Shake", "food_description":
                    "Per 330ml - Calories: 132kcal | Fat: 5g | Carbs: 18g | Protein: 4g"}]
        with mock.patch.object(fooddb, "config") as cfg, \
                mock.patch.object(fooddb, "_fs_search", return_value=results):
            cfg.FATSECRET_CLIENT_ID = "test"
            d = fooddb.parse_fatsecret("330ml chocolate shake")
        self.assertIsNotNone(d)
        # normalized per-100 = 40 kcal; 330ml → ×3.3 → 132
        self.assertEqual(d["calories"], 132)

    def test_parse_fatsecret_one_boiled_egg_not_pie(self):
        """Regression: 'one boiled egg' must search 'boiled egg', never match
        'Cherry Pie (One Crust)' (FatSecret ranks 'One Crust' pies when 'one'
        leaks into the query)."""
        from unittest import mock
        results = [
            {"food_name": "Cherry Pie (One Crust)", "food_description":
             "Per 100g - Calories: 258kcal | Fat: 12g | Carbs: 36g | Protein: 3g"},
            {"food_name": "Boiled Egg", "food_description":
             "Per 100g - Calories: 155kcal | Fat: 10.61g | Carbs: 1.12g | Protein: 12.58g"},
        ]
        with mock.patch.object(fooddb, "config") as cfg, \
                mock.patch.object(fooddb, "_fs_search", return_value=results) as fs:
            cfg.FATSECRET_CLIENT_ID = "test"
            d = fooddb.parse_fatsecret("one boiled egg")
        self.assertIsNotNone(d)
        fs.assert_called_once_with("boiled egg", limit=5)  # qty word stripped
        self.assertEqual(d["matched_food"], "Boiled Egg")
        self.assertEqual(d["calories"], 155)

    def test_fatsecret_unavailable_returns_none(self):
        """FatSecret failure (no creds / API down) → None, caller falls through."""
        from unittest import mock
        with mock.patch.object(fooddb, "config") as cfg:
            cfg.FATSECRET_CLIENT_ID = ""
            self.assertIsNone(fooddb.parse_fatsecret("big mac"))
        with mock.patch.object(fooddb, "config") as cfg, \
                mock.patch.object(fooddb, "_fs_search", return_value=[]):
            cfg.FATSECRET_CLIENT_ID = "test"
            self.assertIsNone(fooddb.parse_fatsecret("big mac"))


if __name__ == "__main__":
    unittest.main()
