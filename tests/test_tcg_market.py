import re
import unittest
from datetime import date

from pipeline import tcg_market

SEALED = re.compile("booster box|booster pack|starter deck|\\bdisplay\\b|\\bcollection\\b")
EXCLUDE = re.compile("\\bcase\\b|playmat|sleeve|lot of")
TODAY = date(2026, 11, 13)


def product(pid, name, single=False, presale=False, released=None):
    p = {"productId": pid, "name": name, "url": "https://x"}
    if single:
        p["extendedData"] = [{"name": "Number", "value": "001"}]
    info = {}
    if presale:
        info["isPresale"] = True
    if released:
        info["releasedOn"] = released + "T00:00:00"
    if info:
        p["presaleInfo"] = info
    return p


class SealedTests(unittest.TestCase):
    def rows(self, products, prices):
        return tcg_market.sealed_rows(products, prices, "Some Set", "One Piece", SEALED, EXCLUDE,
                                      min_market=8, current_days=160, today=TODAY)

    def test_keeps_sealed_with_a_price(self):
        rows = self.rows([product(1, "The Dominance of God Booster Box")], {1: {"marketPrice": 228.55}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["game"], "One Piece")
        self.assertEqual(rows[0]["market"], 228.55)

    def test_drops_singles(self):
        self.assertEqual(self.rows([product(1, "Monkey D. Luffy Leader", single=True)], {1: {"marketPrice": 40}}), [])

    def test_drops_excluded_and_accessories(self):
        products = [product(1, "The Dominance of God Booster Box Case"), product(2, "Luffy Playmat")]
        self.assertEqual(self.rows(products, {1: {"marketPrice": 3000}, 2: {"marketPrice": 25}}), [])

    def test_drops_no_or_cheap_price(self):
        products = [product(1, "Sleeved Booster Pack"), product(2, "Starter Deck")]
        self.assertEqual(self.rows(products, {1: {}, 2: {"marketPrice": 5}}), [])

    def test_presale_is_current(self):
        rows = self.rows([product(1, "New Booster Box", presale=True, released="2027-01-01")], {1: {"marketPrice": 90}})
        self.assertTrue(rows[0]["presale"])
        self.assertTrue(rows[0]["current"])

    def test_old_release_is_not_current(self):
        rows = self.rows([product(1, "Old Booster Box", released="2024-01-01")], {1: {"marketPrice": 90}})
        self.assertFalse(rows[0]["current"])


class SingleTests(unittest.TestCase):
    def test_single_detection(self):
        self.assertTrue(tcg_market.is_single(product(1, "x", single=True)))
        self.assertFalse(tcg_market.is_single(product(1, "x")))


if __name__ == "__main__":
    unittest.main()
