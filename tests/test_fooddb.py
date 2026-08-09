import unittest
import fooddb


class TestFoodDB(unittest.TestCase):
    def test_parse_local_combo(self):
        d = fooddb.parse_local("2 eggs")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 310)
        self.assertEqual(d["protein_g"], 26)

    def test_parse_local_single(self):
        d = fooddb.parse_local("banana")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 89)  # per100g

    def test_parse_local_with_qty(self):
        d = fooddb.parse_local("2 roti")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 180)

    def test_parse_local_hinglish(self):
        d = fooddb.parse_local("chicken curry with rice")
        self.assertIsNotNone(d)
        self.assertGreater(d["calories"], 0)

    def test_parse_local_not_found(self):
        d = fooddb.parse_local("quantum physics textbook")
        self.assertIsNone(d)

    def test_parse_food_tier1_local(self):
        d = fooddb.parse_food("2 boiled eggs")
        self.assertIsNotNone(d)
        self.assertEqual(d["type"], "food")
        self.assertGreater(d["calories"], 0)

    def test_parse_food_returns_none_for_unknown(self):
        d = fooddb.parse_food("quantum entanglement")
        self.assertIsNone(d)

    def test_parse_food_fatsecret_disabled(self):
        d = fooddb.parse_food("some random food xyz")
        self.assertIsNone(d)

    def test_parse_multi_local(self):
        d = fooddb.parse_multi_local("2 chapati, Lady finger curry, 2 boiled eggs")
        self.assertIsNotNone(d)
        self.assertGreater(d["calories"], 500)
        self.assertIn("Chapati", d["item_name"])
        self.assertIn("Lady Finger", d["item_name"])
        self.assertIn("2 Boiled Eggs", d["item_name"])

    def test_parse_multi_local_with_and(self):
        d = fooddb.parse_multi_local("2 roti and paneer")
        self.assertIsNotNone(d)
        self.assertEqual(d["calories"], 445)  # 180 + 265


if __name__ == "__main__":
    unittest.main()